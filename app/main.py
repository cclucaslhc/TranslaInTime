from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
import sysconfig
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np


def add_cuda_dll_directories() -> None:
    if os.name != "nt":
        return
    roots = [Path(sysconfig.get_paths()["purelib"])]
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        roots.extend([meipass, Path(sys.executable).resolve().parent])
    for relative in (
        "nvidia/cublas/bin",
        "nvidia/cudnn/bin",
        "nvidia/cuda_runtime/bin",
        "nvidia/cuda_nvrtc/bin",
    ):
        for root in roots:
            dll_dir = root / relative
            if dll_dir.exists():
                os.add_dll_directory(str(dll_dir))
                os.environ["PATH"] = f"{dll_dir}{os.pathsep}{os.environ.get('PATH', '')}"


add_cuda_dll_directories()

import argostranslate.translate
import ctranslate2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
SAMPLE_RATE = 16_000
LOGGER = logging.getLogger("translaintime")


@dataclass
class RuntimeConfig:
    model_size: str = os.getenv("WHISPER_MODEL_SIZE", "small")
    device: str = os.getenv("WHISPER_DEVICE", "auto")
    compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8_float16")
    default_target: str = os.getenv("TARGET_LANGUAGE", "zh")
    chunk_seconds: float = float(os.getenv("CHUNK_SECONDS", "1.2"))
    overlap_seconds: float = float(os.getenv("OVERLAP_SECONDS", "0.2"))
    max_window_seconds: float = float(os.getenv("MAX_WINDOW_SECONDS", "5.0"))
    speech_peak_threshold: float = float(os.getenv("SPEECH_PEAK_THRESHOLD", "0.003"))


class Translator:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._model: WhisperModel | None = None
        self._primed = False
        self.device = "cpu"
        self.compute_type = "int8"
        self.load_error: str | None = None

    def load(self) -> None:
        if self._model is not None:
            return

        preferred_device = self.config.device
        if preferred_device == "auto":
            preferred_device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"

        compute_type = self.config.compute_type
        if preferred_device == "cpu" and compute_type in {"float16", "int8_float16"}:
            compute_type = "int8"

        try:
            self._model = WhisperModel(
                self.config.model_size,
                device=preferred_device,
                compute_type=compute_type,
                download_root=str(ROOT / "models"),
            )
            self.device = preferred_device
            self.compute_type = compute_type
            self.load_error = None
        except Exception as exc:
            if preferred_device == "cpu":
                raise
            self.load_error = f"CUDA load failed, fell back to CPU: {exc}"
            LOGGER.warning(self.load_error)
            self._model = WhisperModel(
                self.config.model_size,
                device="cpu",
                compute_type="int8",
                download_root=str(ROOT / "models"),
            )
            self.device = "cpu"
            self.compute_type = "int8"

    def transcribe_or_translate(
        self,
        audio: np.ndarray,
        *,
        source_language: str,
        task: str,
    ) -> tuple[str, str | None]:
        self.load()
        assert self._model is not None

        language = None if source_language == "auto" else source_language
        segments, info = self._model.transcribe(
            audio,
            language=language,
            task=task,
            beam_size=1,
            best_of=1,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
            without_timestamps=True,
            no_speech_threshold=0.65,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.2,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, info.language

    def prime(self) -> None:
        self.load()
        if self._primed:
            return
        assert self._model is not None
        audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            task="transcribe",
            beam_size=1,
            best_of=1,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
            temperature=0.0,
            no_speech_threshold=0.95,
        )
        list(segments)
        self._primed = True

    def translate_text(self, text: str, source_language: str | None, target_language: str) -> tuple[str, str | None]:
        target_language = normalize_language_code(target_language)
        source_language = normalize_language_code(source_language or "en")

        if not text or source_language == target_language:
            return text, None

        try:
            translated = argostranslate.translate.translate(text, source_language, target_language)
        except Exception as exc:
            return text, f"Argos translation failed for {source_language}->{target_language}: {exc}"

        if translated == text and source_language != target_language:
            return text, f"Argos package {source_language}->{target_language} is not installed"
        return translated, None

    def process(
        self,
        audio: np.ndarray,
        source_language: str,
        target_language: str,
        speed_mode: bool,
    ) -> dict[str, Any]:
        target_language = normalize_language_code(target_language)
        source_language = normalize_language_code(source_language)
        started = time.perf_counter()

        if speed_mode:
            original, translated, detected, warning = self.fast_process(audio, source_language, target_language)
        elif target_language == "en":
            text, detected = self.transcribe_or_translate(
                audio,
                source_language=source_language,
                task="translate",
            )
            original = ""
            translated = text
            warning = None
        else:
            original, detected = self.transcribe_or_translate(
                audio,
                source_language=source_language,
                task="transcribe",
            )
            detected_norm = normalize_language_code(detected or source_language)
            if detected_norm == target_language:
                translated = original
                warning = None
            else:
                english, _ = self.transcribe_or_translate(
                    audio,
                    source_language=source_language,
                    task="translate",
                )
                if target_language == "zh":
                    translated, warning = self.translate_text(english, "en", "zh")
                else:
                    translated, warning = self.translate_text(english, "en", target_language)

        return {
            "type": "result",
            "original": original,
            "translation": translated,
            "detectedLanguage": detected,
            "targetLanguage": target_language,
            "device": self.device,
            "computeType": self.compute_type,
            "model": self.config.model_size,
            "loadWarning": self.load_error,
            "translateWarning": warning,
            "latencyMs": round((time.perf_counter() - started) * 1000),
            "speedMode": speed_mode,
        }

    def fast_process(
        self,
        audio: np.ndarray,
        source_language: str,
        target_language: str,
    ) -> tuple[str, str, str | None, str | None]:
        if source_language == target_language and target_language != "auto":
            text, detected = self.transcribe_or_translate(
                audio,
                source_language=source_language,
                task="transcribe",
            )
            return text, text, detected, None

        if target_language == "en":
            text, detected = self.transcribe_or_translate(
                audio,
                source_language=source_language,
                task="translate",
            )
            return "", text, detected, None

        original, detected = self.transcribe_or_translate(
            audio,
            source_language=source_language,
            task="transcribe",
        )
        detected_norm = normalize_language_code(detected or source_language)
        if detected_norm == target_language:
            return original, original, detected, None

        translated, warning = self.translate_text(original, detected_norm, target_language)
        return original, translated, detected, warning


def normalize_language_code(language: str) -> str:
    language = (language or "auto").lower()
    aliases = {
        "cn": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "chinese": "zh",
        "english": "en",
    }
    return aliases.get(language, language)


def caption_signature(text: str) -> str:
    text = text.casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


class AudioSession:
    def __init__(self, websocket: WebSocket, translator: Translator, config: RuntimeConfig):
        self.websocket = websocket
        self.translator = translator
        self.config = config
        self.audio_parts: deque[np.ndarray] = deque()
        self.pending_samples = 0
        self.new_samples_since_process = 0
        self.source_language = "auto"
        self.target_language = config.default_target
        self.chunk_samples = int(config.chunk_seconds * SAMPLE_RATE)
        self.overlap_samples = int(config.overlap_seconds * SAMPLE_RATE)
        self.max_window_samples = int(config.max_window_seconds * SAMPLE_RATE)
        self.speed_mode = True
        self.monitor_only = False
        self.dedupe = True
        self.dedupe_similarity = 0.86
        self.last_signature = ""
        self.packet_count = 0
        self.received_samples = 0
        self.last_level_at = 0.0
        self.running = True
        self.processing = asyncio.Lock()
        self.send_lock = asyncio.Lock()

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def configure(self, message: dict[str, Any]) -> None:
        self.source_language = normalize_language_code(message.get("sourceLanguage", "auto"))
        self.target_language = normalize_language_code(message.get("targetLanguage", self.config.default_target))
        self.speed_mode = bool(message.get("speedMode", True))
        self.monitor_only = bool(message.get("monitorOnly", False))
        self.dedupe = bool(message.get("dedupe", True))
        self.dedupe_similarity = float(message.get("dedupeSimilarity", 0.86))
        chunk_seconds = float(message.get("chunkSeconds", self.config.chunk_seconds))
        self.chunk_samples = int(max(0.6, min(8.0, chunk_seconds)) * SAMPLE_RATE)
        await self.send_json(
            {
                "type": "ready",
                "sampleRate": SAMPLE_RATE,
                "targetLanguage": self.target_language,
                "model": self.config.model_size,
                "chunkSeconds": round(self.chunk_samples / SAMPLE_RATE, 2),
                "speedMode": self.speed_mode,
                "monitorOnly": self.monitor_only,
            }
        )

    async def add_audio(self, payload: bytes) -> None:
        if not payload:
            return
        audio = np.frombuffer(payload, dtype=np.float32).copy()
        if audio.size == 0:
            return
        audio = np.nan_to_num(audio, copy=False)
        audio = np.clip(audio, -1.0, 1.0)
        self.packet_count += 1
        self.received_samples += audio.size
        await self.send_input_level(audio)
        if self.monitor_only:
            return
        self.audio_parts.append(audio)
        self.pending_samples += audio.size
        self.new_samples_since_process += audio.size
        self.trim_audio_cache()
        if self.new_samples_since_process >= self.chunk_samples and not self.processing.locked():
            asyncio.create_task(self.process_latest())

    async def send_input_level(self, audio: np.ndarray) -> None:
        now = time.perf_counter()
        if now - self.last_level_at < 0.18:
            return
        self.last_level_at = now
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        dbfs = 20 * math.log10(max(rms, 1e-6))
        await self.send_json(
            {
                "type": "input_level",
                "peak": round(peak, 5),
                "rms": round(rms, 5),
                "dbfs": round(dbfs, 1),
                "packetCount": self.packet_count,
                "receivedMs": round(self.received_samples / SAMPLE_RATE * 1000),
            }
        )

    async def process_latest(self) -> None:
        async with self.processing:
            audio = self.pop_window()
            if audio.size < int(0.45 * SAMPLE_RATE):
                return
            peak = float(np.max(np.abs(audio)))
            if peak < self.config.speech_peak_threshold:
                await self.send_json({"type": "level", "status": "quiet", "peak": round(peak, 5)})
                return

            try:
                await self.send_json(
                    {
                        "type": "processing",
                        "audioMs": round(audio.size / SAMPLE_RATE * 1000),
                        "peak": round(peak, 5),
                    }
                )
                result = await asyncio.to_thread(
                    self.translator.process,
                    audio,
                    self.source_language,
                    self.target_language,
                    self.speed_mode,
                )
                if not result.get("translation") and not result.get("original"):
                    await self.send_json(
                        {
                            "type": "empty_result",
                            "latencyMs": result.get("latencyMs"),
                            "detectedLanguage": result.get("detectedLanguage"),
                            "peak": round(peak, 5),
                        }
                    )
                    return
                if self.is_duplicate(result):
                    await self.send_json(
                        {
                            "type": "duplicate",
                            "latencyMs": result.get("latencyMs"),
                        }
                    )
                    return
                await self.send_json(result)
            except Exception as exc:
                LOGGER.exception("Processing failed")
                await self.send_json({"type": "error", "message": str(exc)})
        if self.new_samples_since_process >= self.chunk_samples and not self.monitor_only:
            asyncio.create_task(self.process_latest())

    def pop_window(self) -> np.ndarray:
        joined = np.concatenate(list(self.audio_parts)) if self.audio_parts else np.empty(0, dtype=np.float32)
        process = joined[-self.max_window_samples :] if joined.size > self.max_window_samples else joined
        self.new_samples_since_process = 0
        return process

    def trim_audio_cache(self) -> None:
        if self.pending_samples <= self.max_window_samples:
            return
        joined = np.concatenate(list(self.audio_parts)) if self.audio_parts else np.empty(0, dtype=np.float32)
        keep = joined[-self.max_window_samples :]
        self.audio_parts.clear()
        self.audio_parts.append(keep)
        self.pending_samples = keep.size

    def is_duplicate(self, result: dict[str, Any]) -> bool:
        if not self.dedupe:
            return False
        text = result.get("translation") or result.get("original") or ""
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
        similarity = SequenceMatcher(None, previous, signature).ratio()
        if similarity >= self.dedupe_similarity:
            self.last_signature = signature
            return True
        self.last_signature = signature
        return False


config = RuntimeConfig()
translator = Translator(config)
app = FastAPI(title="TranslaInTime")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "cudaDevices": ctranslate2.get_cuda_device_count(),
        "model": config.model_size,
        "targetLanguage": config.default_target,
    }


@app.post("/warmup")
async def warmup() -> dict[str, Any]:
    await asyncio.to_thread(translator.prime)
    return {
        "status": "ready",
        "device": translator.device,
        "computeType": translator.compute_type,
        "model": config.model_size,
        "warning": translator.load_error,
        "primed": True,
    }


@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session = AudioSession(websocket, translator, config)
    try:
        while True:
            message = await websocket.receive()
            if "text" in message and message["text"] is not None:
                data = json.loads(message["text"])
                if data.get("type") == "config":
                    await session.configure(data)
            elif "bytes" in message and message["bytes"] is not None:
                await session.add_audio(message["bytes"])
    except WebSocketDisconnect:
        return
