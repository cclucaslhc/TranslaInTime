from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

import numpy as np
import sounddevice as sd

from app.main import RuntimeConfig, SAMPLE_RATE, Translator, caption_signature


EventCallback = Callable[[str, dict], None]


@dataclass
class DesktopSettings:
    source_language: str = "en"
    target_language: str = "zh"
    chunk_seconds: float = 1.2
    speed_mode: bool = True


def downsample_to_16k(audio: np.ndarray, input_rate: int) -> np.ndarray:
    if input_rate == SAMPLE_RATE:
        return np.asarray(audio, dtype=np.float32)
    ratio = input_rate / SAMPLE_RATE
    output_length = int(len(audio) / ratio)
    if output_length <= 0:
        return np.empty(0, dtype=np.float32)
    source_x = np.arange(len(audio), dtype=np.float32)
    target_x = np.arange(output_length, dtype=np.float32) * ratio
    return np.interp(target_x, source_x, audio).astype(np.float32)


class RealtimeTranslatorEngine:
    def __init__(self, callback: EventCallback):
        self.callback = callback
        self.config = RuntimeConfig()
        self.translator = Translator(self.config)
        self.translator_lock = threading.Lock()
        self.buffer_lock = threading.Lock()

        self.settings = DesktopSettings()
        self.running = False
        self.processing = False
        self.stream: sd.InputStream | None = None
        self.input_rate = SAMPLE_RATE
        self.audio_parts: deque[np.ndarray] = deque()
        self.total_samples = 0
        self.new_samples = 0
        self.max_window_samples = int(5.0 * SAMPLE_RATE)
        self.last_level_at = 0.0
        self.packet_count = 0
        self.duplicate_count = 0
        self.last_signature = ""
        self.noise_floor = 0.002
        self.speech_active = False
        self.speech_started_at = 0.0
        self.last_speech_at = 0.0
        self.last_process_at = 0.0
        self.last_final_at = 0.0
        self.pending_final: dict | None = None
        self.pending_signature = ""
        self.candidate_started_at = 0.0
        self.last_candidate_notice_at = 0.0
        self.finalize_pause_seconds = 1.05
        self.max_candidate_wait_seconds = 4.0
        self.min_speech_seconds = 0.65
        self.min_process_interval = 0.75
        self.min_rms_for_speech = 0.008
        self.min_peak_for_speech = 0.035

    def warmup(self) -> None:
        threading.Thread(target=self._warmup_worker, daemon=True).start()

    def _warmup_worker(self) -> None:
        try:
            with self.translator_lock:
                self.translator.prime()
            self.emit(
                "warmup_done",
                {
                    "model": self.config.model_size,
                    "device": self.translator.device,
                    "compute_type": self.translator.compute_type,
                },
            )
        except Exception as exc:
            self.emit("error", {"message": f"GPU warmup failed: {exc}"})

    def start(self, settings: DesktopSettings) -> None:
        if self.running:
            return
        self.settings = settings
        self.packet_count = 0
        self.speech_active = False
        self.speech_started_at = 0.0
        self.last_speech_at = 0.0
        self.last_process_at = 0.0
        self.last_final_at = 0.0
        self.pending_final = None
        self.pending_signature = ""
        self.candidate_started_at = 0.0
        self.last_candidate_notice_at = 0.0
        self._reset_noise_floor()
        with self.buffer_lock:
            self.audio_parts.clear()
            self.total_samples = 0
            self.new_samples = 0
            self.processing = False

        info = sd.query_devices(kind="input")
        self.input_rate = int(info.get("default_samplerate") or SAMPLE_RATE)
        self.stream = sd.InputStream(
            samplerate=self.input_rate,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=0,
            latency="low",
        )
        self.running = True
        self.stream.start()
        self.emit("started", {"device_name": info.get("name", "Default microphone"), "sample_rate": self.input_rate})

    def stop(self) -> None:
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        with self.buffer_lock:
            self.audio_parts.clear()
            self.total_samples = 0
            self.new_samples = 0
            self.processing = False
        self.emit("stopped", {})

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        if status:
            self.emit("log", {"message": f"Audio status: {status}"})
        audio = np.asarray(indata[:, 0], dtype=np.float32).copy()
        audio16 = downsample_to_16k(audio, self.input_rate)
        if audio16.size == 0:
            return
        audio16 = np.clip(np.nan_to_num(audio16, copy=False), -1.0, 1.0)
        self.packet_count += 1
        level = self._audio_level(audio16)
        was_speech_active = self.speech_active
        had_pending_candidate = self.pending_final is not None
        self._update_voice_state(level, audio16.size)
        resumed_pending_candidate = had_pending_candidate and not was_speech_active and self.speech_active
        self._emit_level(level)

        if not self.speech_active and self.pending_final is not None:
            self._try_finalize_after_pause()

        with self.buffer_lock:
            if not self.running:
                return
            if not self.speech_active and self.pending_final is None:
                self.audio_parts.clear()
                self.total_samples = 0
                self.new_samples = 0
                return
            if resumed_pending_candidate:
                # Ignore silence accumulated while a candidate was waiting; schedule from actual continuation speech.
                self.new_samples = 0
            self.audio_parts.append(audio16)
            self.total_samples += audio16.size
            self.new_samples += audio16.size
            self._trim_audio_cache_locked()
        self._try_schedule_process()

    def _audio_level(self, audio: np.ndarray) -> dict:
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(np.square(audio))))
        dbfs = 20 * math.log10(max(rms, 1e-6))
        return {"peak": peak, "rms": rms, "dbfs": dbfs}

    def _reset_noise_floor(self) -> None:
        self.noise_floor = 0.002

    def _update_voice_state(self, level: dict, sample_count: int) -> None:
        now = time.perf_counter()
        rms = float(level["rms"])
        peak = float(level["peak"])
        speech_threshold = max(self.min_rms_for_speech, self.noise_floor * 4.0)
        is_speech = rms >= speech_threshold and peak >= self.min_peak_for_speech

        if is_speech:
            if not self.speech_active:
                self.speech_active = True
                self.speech_started_at = now
                if self.pending_final is None:
                    self.emit("speech_start", {})
                else:
                    self.emit("speech_resume", {})
            self.last_speech_at = now
            return

        if not self.speech_active:
            # Track ambient room noise slowly while no speech is active.
            self.noise_floor = min(0.02, self.noise_floor * 0.96 + rms * 0.04)
            return

        if now - self.last_speech_at >= self.finalize_pause_seconds:
            self.speech_active = False
            self.emit("speech_pause", {})

    def _emit_level(self, level: dict) -> None:
        now = time.perf_counter()
        if now - self.last_level_at < 0.12:
            return
        self.last_level_at = now
        payload = dict(level)
        payload.update(
            {
                "packets": self.packet_count,
                "speech_active": self.speech_active,
                "noise_floor": self.noise_floor,
            }
        )
        self.emit("level", payload)

    def _trim_audio_cache_locked(self) -> None:
        if self.total_samples <= self.max_window_samples:
            return
        joined = np.concatenate(list(self.audio_parts))
        keep = joined[-self.max_window_samples :]
        self.audio_parts.clear()
        self.audio_parts.append(keep)
        self.total_samples = keep.size

    def _try_schedule_process(self) -> None:
        chunk_samples = int(self.settings.chunk_seconds * SAMPLE_RATE)
        now = time.perf_counter()
        with self.buffer_lock:
            if (
                not self.running
                or not self.speech_active
                or self.processing
                or self.new_samples < chunk_samples
                or now - self.last_process_at < self.min_process_interval
            ):
                return
            joined = np.concatenate(list(self.audio_parts)) if self.audio_parts else np.empty(0, dtype=np.float32)
            audio = joined[-self.max_window_samples :]
            self.new_samples = 0
            self.processing = True
            self.last_process_at = now
        threading.Thread(target=self._process_audio, args=(audio,), daemon=True).start()

    def _process_audio(self, audio: np.ndarray) -> None:
        try:
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
            if audio.size < int(self.min_speech_seconds * SAMPLE_RATE) or peak < self.min_peak_for_speech or rms < self.min_rms_for_speech:
                self.emit("quiet", {"peak": peak})
                return
            self.emit("processing", {"audio_ms": round(audio.size / SAMPLE_RATE * 1000), "peak": peak, "final": False})
            with self.translator_lock:
                result = self.translator.process(
                    audio,
                    self.settings.source_language,
                    self.settings.target_language,
                    self.settings.speed_mode,
                )
            text = result.get("translation") or result.get("original") or ""
            if not text:
                self.emit("empty", result)
            else:
                self.pending_final = result
                self.pending_signature = caption_signature(text)
                self.candidate_started_at = 0.0
                self.last_candidate_notice_at = 0.0
                self.emit("provisional", result)
        except Exception as exc:
            self.emit("error", {"message": str(exc)})
        finally:
            with self.buffer_lock:
                self.processing = False
            if self.speech_active:
                self._try_schedule_process()
            else:
                self._try_finalize_after_pause()

    def _try_finalize_after_pause(self) -> None:
        if self.speech_active or self.pending_final is None:
            return
        now = time.perf_counter()
        if now - self.last_speech_at < self.finalize_pause_seconds:
            return
        result = self.pending_final
        if self.candidate_started_at <= 0:
            self.candidate_started_at = now
            self.last_candidate_notice_at = now
            self.emit("candidate", self._candidate_payload(result, forced=False))

        complete, reason = self._is_sentence_complete(result)
        forced = now - self.candidate_started_at >= self.max_candidate_wait_seconds
        if not complete and not forced:
            if now - self.last_candidate_notice_at >= 0.8:
                self.last_candidate_notice_at = now
                self.emit("candidate_wait", self._candidate_payload(result, forced=False, reason=reason))
            return

        text = result.get("translation") or result.get("original") or ""
        if self._is_duplicate(text):
            self.duplicate_count += 1
            self.emit("duplicate", {"count": self.duplicate_count})
        else:
            final_payload = dict(result)
            final_payload["completeReason"] = "forced" if forced else reason
            self.emit("final", final_payload)
            self.last_final_at = now
        self.pending_final = None
        self.pending_signature = ""
        self.candidate_started_at = 0.0
        self.last_candidate_notice_at = 0.0
        with self.buffer_lock:
            self.audio_parts.clear()
            self.total_samples = 0
            self.new_samples = 0

    def _candidate_payload(self, result: dict, forced: bool, reason: str = "") -> dict:
        payload = dict(result)
        payload["candidateAgeMs"] = round(max(0.0, time.perf_counter() - self.candidate_started_at) * 1000)
        payload["candidateMaxWaitMs"] = round(self.max_candidate_wait_seconds * 1000)
        payload["completeReason"] = "forced" if forced else reason
        return payload

    def _is_sentence_complete(self, result: dict) -> tuple[bool, str]:
        source_text = (result.get("original") or result.get("translation") or "").strip()
        text = re.sub(r"\s+", " ", source_text)
        if not text:
            return False, "empty"
        if text.endswith(("...", "…")):
            return False, "ellipsis"

        words = re.findall(r"[A-Za-z']+", text.lower())
        terminal_phrases = {
            "thank you",
            "thanks",
            "good morning",
            "good afternoon",
            "good evening",
            "see you",
        }
        lowered = text.lower().strip(" .,!?")
        if lowered in terminal_phrases:
            return True, "terminal_phrase"

        if len(words) < 3:
            return False, "too_short"

        dangling_endings = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "because",
            "been",
            "being",
            "but",
            "by",
            "can",
            "could",
            "did",
            "do",
            "does",
            "for",
            "from",
            "had",
            "has",
            "have",
            "if",
            "in",
            "is",
            "not",
            "of",
            "on",
            "or",
            "should",
            "so",
            "that",
            "the",
            "to",
            "was",
            "were",
            "when",
            "which",
            "while",
            "who",
            "will",
            "with",
            "without",
            "would",
        }
        if words[-1] in dangling_endings:
            return False, f"dangling_{words[-1]}"

        if re.search(r"[.!?]['\")\]]?$", text):
            return True, "punctuation"

        leading_subordinators = {
            "although",
            "because",
            "before",
            "if",
            "since",
            "unless",
            "until",
            "when",
            "whenever",
            "where",
            "while",
        }
        if words[0] in leading_subordinators and "," not in text:
            return False, f"open_{words[0]}"

        auxiliary_verbs = {
            "am",
            "are",
            "is",
            "was",
            "were",
            "be",
            "been",
            "being",
            "can",
            "could",
            "did",
            "do",
            "does",
            "had",
            "has",
            "have",
            "may",
            "might",
            "must",
            "shall",
            "should",
            "will",
            "would",
        }
        common_verbs = {
            "add",
            "allow",
            "ask",
            "build",
            "call",
            "change",
            "check",
            "come",
            "create",
            "delete",
            "do",
            "feel",
            "find",
            "finish",
            "focus",
            "get",
            "give",
            "go",
            "help",
            "keep",
            "know",
            "let",
            "look",
            "make",
            "mean",
            "move",
            "need",
            "open",
            "put",
            "read",
            "run",
            "say",
            "see",
            "set",
            "show",
            "start",
            "stop",
            "take",
            "tell",
            "think",
            "translate",
            "try",
            "use",
            "wait",
            "want",
            "work",
        }
        pronouns = {"i", "you", "he", "she", "it", "we", "they", "this", "that", "there"}
        has_subject = any(word in pronouns for word in words[:4])
        has_finite_verb = any(word in auxiliary_verbs or word in common_verbs or word.endswith(("ed", "ing")) for word in words[1:])
        if has_subject and has_finite_verb and len(words) >= 5:
            return True, "clause_shape"

        imperative_verbs = common_verbs - {"feel", "know", "mean", "think", "want"}
        if words[0] in imperative_verbs and len(words) >= 4:
            return True, "imperative_shape"

        return False, "no_terminal_signal"

    def _is_duplicate(self, text: str) -> bool:
        signature = caption_signature(text)
        if len(signature) < 2:
            return True
        previous = self.last_signature
        if not previous:
            self.last_signature = signature
            return False
        if signature == previous:
            return True
        if min(len(signature), len(previous)) >= 8 and (signature in previous or previous in signature):
            self.last_signature = signature if len(signature) > len(previous) else previous
            return True
        if SequenceMatcher(None, previous, signature).ratio() >= 0.86:
            self.last_signature = signature
            return True
        self.last_signature = signature
        return False

    def clear_history_state(self) -> None:
        self.duplicate_count = 0
        self.last_signature = ""
        self.pending_final = None
        self.pending_signature = ""
        self.candidate_started_at = 0.0
        self.last_candidate_notice_at = 0.0

    def emit(self, event: str, payload: dict) -> None:
        self.callback(event, payload)
