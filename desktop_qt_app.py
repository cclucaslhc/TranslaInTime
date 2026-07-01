from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop_core import DesktopSettings, RealtimeTranslatorEngine


class EventBridge(QObject):
    event = Signal(str, dict)


class CaptionWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TranslaInTime")
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)

        self.bridge = EventBridge()
        self.bridge.event.connect(self.handle_engine_event)
        self.engine = RealtimeTranslatorEngine(self.bridge.event.emit)

        self.history_count = 0
        self._build_ui()
        self._bind_shortcuts()
        self.engine.warmup()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(22, 20, 22, 20)
        main.setSpacing(14)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("TranslaInTime")
        title.setObjectName("appTitle")
        subtitle = QLabel("Local English to Chinese live captions")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.status_badge = QLabel("Warming up")
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setAlignment(Qt.AlignCenter)
        header.addWidget(self.status_badge)
        main.addLayout(header)

        controls = QFrame()
        controls.setObjectName("panel")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(14, 12, 14, 12)
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setVerticalSpacing(7)

        self.source_combo = self._combo([("English", "en"), ("Auto", "auto"), ("Chinese", "zh")], "en")
        self.target_combo = self._combo([("Chinese", "zh"), ("English", "en")], "zh")
        self.chunk_combo = self._combo([("0.8 s", "0.8"), ("1.2 s", "1.2"), ("2.0 s", "2.0")], "1.2")
        self.speed_check = QCheckBox("Speed first")
        self.speed_check.setChecked(True)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("Stop")
        self.clear_button = QPushButton("Clear")
        self.stop_button.setEnabled(False)

        for column, (label_text, widget) in enumerate(
            [
                ("Source", self.source_combo),
                ("Target", self.target_combo),
                ("Chunk", self.chunk_combo),
            ]
        ):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            controls_layout.addWidget(label, 0, column)
            controls_layout.addWidget(widget, 1, column)

        controls_layout.addWidget(self.speed_check, 1, 3)
        controls_layout.addWidget(self.start_button, 1, 4)
        controls_layout.addWidget(self.stop_button, 1, 5)
        controls_layout.addWidget(self.clear_button, 1, 6)
        controls_layout.setColumnStretch(7, 1)
        main.addWidget(controls)

        body = QHBoxLayout()
        body.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(14)
        body.addLayout(left, 3)

        meters = QHBoxLayout()
        meters.setSpacing(12)
        self.mic_panel, self.mic_bar, self.mic_text = self._meter_panel("Microphone")
        self.asr_panel, self.asr_bar, self.asr_text = self._meter_panel("Recognition")
        meters.addWidget(self.mic_panel)
        meters.addWidget(self.asr_panel)
        left.addLayout(meters)

        caption_panel = QFrame()
        caption_panel.setObjectName("captionPanel")
        caption_layout = QVBoxLayout(caption_panel)
        caption_layout.setContentsMargins(22, 20, 22, 20)
        caption_label = QLabel("Live Translation")
        caption_label.setObjectName("fieldLabel")
        self.translation = QLabel("Click Start and speak English")
        self.translation.setObjectName("translation")
        self.translation.setWordWrap(True)
        self.translation.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.original = QLabel("")
        self.original.setObjectName("original")
        self.original.setWordWrap(True)
        self.metrics = QLabel("Filtered duplicates: 0")
        self.metrics.setObjectName("muted")
        caption_layout.addWidget(caption_label)
        caption_layout.addWidget(self.translation, 1)
        caption_layout.addWidget(self.original)
        caption_layout.addWidget(self.metrics)
        left.addWidget(caption_panel, 1)

        right = QFrame()
        right.setObjectName("panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        history_title = QLabel("Finalized History")
        history_title.setObjectName("sectionTitle")
        self.history = QListWidget()
        self.history.setObjectName("historyList")
        right_layout.addWidget(history_title)
        right_layout.addWidget(self.history, 1)
        body.addWidget(right, 2)
        main.addLayout(body, 1)

        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.clear_button.clicked.connect(self.clear_history)

        self.setStyleSheet(STYLESHEET)

    def _combo(self, values: list[tuple[str, str]], current: str) -> QComboBox:
        combo = QComboBox()
        for label, value in values:
            combo.addItem(label, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _meter_panel(self, title: str) -> tuple[QFrame, QProgressBar, QLabel]:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0, 100)
        text = QLabel("Idle")
        text.setObjectName("meterText")
        layout.addWidget(label)
        layout.addWidget(bar)
        layout.addWidget(text)
        return panel, bar, text

    def _bind_shortcuts(self) -> None:
        start_stop = QAction(self)
        start_stop.setShortcut(QKeySequence(Qt.Key_Space))
        start_stop.triggered.connect(self.toggle)
        self.addAction(start_stop)

        stop = QAction(self)
        stop.setShortcut(QKeySequence(Qt.Key_Escape))
        stop.triggered.connect(self.stop)
        self.addAction(stop)

        clear = QAction(self)
        clear.setShortcut(QKeySequence("Ctrl+L"))
        clear.triggered.connect(self.clear_history)
        self.addAction(clear)

    def settings(self) -> DesktopSettings:
        return DesktopSettings(
            source_language=str(self.source_combo.currentData()),
            target_language=str(self.target_combo.currentData()),
            chunk_seconds=float(self.chunk_combo.currentData()),
            speed_mode=self.speed_check.isChecked(),
        )

    @Slot()
    def start(self) -> None:
        try:
            self.engine.start(self.settings())
        except Exception as exc:
            QMessageBox.critical(self, "Microphone failed", str(exc))
            self.set_status("Mic failed", error=True)

    @Slot()
    def stop(self) -> None:
        self.engine.stop()

    @Slot()
    def toggle(self) -> None:
        if self.stop_button.isEnabled():
            self.stop()
        else:
            self.start()

    @Slot()
    def clear_history(self) -> None:
        self.history.clear()
        self.history_count = 0
        self.engine.clear_history_state()
        self.metrics.setText("Filtered duplicates: 0")

    @Slot(str, dict)
    def handle_engine_event(self, event: str, payload: dict) -> None:
        if event == "warmup_done":
            self.set_status("Ready")
            self.mic_text.setText("Microphone ready")
            self.asr_text.setText(f"{payload['model']} | {payload['device']} | {payload['compute_type']}")
        elif event == "started":
            self.set_status("Listening")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.translation.setText("Listening for English speech")
            self.original.setText(f"Input: {payload['device_name']} | {payload['sample_rate']} Hz")
        elif event == "stopped":
            self.set_status("Stopped")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.mic_bar.setValue(0)
            self.asr_bar.setValue(0)
            self.mic_text.setText("Microphone stopped")
            self.asr_text.setText("Recognition stopped")
        elif event == "level":
            peak = float(payload["peak"])
            self.mic_bar.setValue(min(100, int(peak * 140)))
            mode = "speech" if payload.get("speech_active") else "idle"
            self.mic_text.setText(
                f"{mode} | Peak {peak:.3f} | RMS {payload['rms']:.3f} | {payload['dbfs']:.1f} dBFS | packets {payload['packets']}"
            )
        elif event == "speech_start":
            self.set_status("Listening")
            self.asr_text.setText("Speech detected")
        elif event == "speech_resume":
            self.set_status("Continuing")
            self.asr_text.setText("Continuation detected, keeping current caption out of history")
        elif event == "speech_pause":
            self.set_status("Candidate")
            self.asr_text.setText("Pause detected, checking sentence completeness")
        elif event == "quiet":
            self.asr_bar.setValue(8)
            self.asr_text.setText(f"Too quiet | peak {float(payload['peak']):.4f}")
        elif event == "processing":
            self.set_status("Recognizing")
            self.asr_bar.setValue(55)
            self.asr_text.setText(f"Live preview | {payload['audio_ms']} ms | peak {payload['peak']:.3f}")
        elif event == "empty":
            self.set_status("No text")
            self.asr_bar.setValue(24)
            self.asr_text.setText("Whisper returned no text")
            self.translation.setText("No text detected. Keep speaking a complete English sentence.")
        elif event == "duplicate":
            self.metrics.setText(f"Filtered duplicates: {payload['count']}")
            self.asr_bar.setValue(75)
        elif event == "provisional":
            self.show_provisional(payload)
        elif event == "candidate":
            self.show_candidate(payload, waiting=False)
        elif event == "candidate_wait":
            self.show_candidate(payload, waiting=True)
        elif event == "final":
            self.show_final(payload)
        elif event == "log":
            self.add_history(f"[Log] {payload['message']}", "")
        elif event == "error":
            self.set_status("Error", error=True)
            self.asr_text.setText(payload["message"])
            self.add_history(f"[Error] {payload['message']}", "")

    def show_provisional(self, result: dict) -> None:
        translation = result.get("translation") or ""
        original = result.get("original") or ""
        latency = result.get("latencyMs")
        self.translation.setText(translation)
        self.original.setText(f"Original: {original}" if original else "")
        self.asr_bar.setValue(82)
        self.asr_text.setText(f"Live preview | {result.get('detectedLanguage') or 'auto'} -> {result.get('targetLanguage')} | {latency} ms")
        self.set_status("Preview")

    def show_candidate(self, result: dict, waiting: bool) -> None:
        translation = result.get("translation") or ""
        original = result.get("original") or ""
        age_ms = int(result.get("candidateAgeMs") or 0)
        max_wait_ms = int(result.get("candidateMaxWaitMs") or 4000)
        reason = result.get("completeReason") or "checking"
        if translation:
            self.translation.setText(translation)
        self.original.setText(f"Original: {original}" if original else "")
        self.asr_bar.setValue(88 if waiting else 86)
        self.set_status("Waiting" if waiting else "Candidate")
        remaining = max(0, max_wait_ms - age_ms)
        if waiting:
            self.asr_text.setText(f"Waiting for continuation | {reason} | force in {remaining / 1000:.1f}s")
        else:
            self.asr_text.setText(f"Candidate caption | {reason}")

    def show_final(self, result: dict) -> None:
        translation = result.get("translation") or ""
        original = result.get("original") or ""
        latency = result.get("latencyMs")
        self.translation.setText(translation)
        self.original.setText(f"Original: {original}" if original else "")
        self.asr_bar.setValue(100)
        reason = result.get("completeReason") or "complete"
        self.asr_text.setText(f"Final | {reason} | {result.get('detectedLanguage') or 'auto'} -> {result.get('targetLanguage')} | {latency} ms")
        self.set_status("Final")
        self.add_history(translation, original)

    def add_history(self, translation: str, original: str) -> None:
        self.history_count += 1
        item = QListWidgetItem()
        item.setText(f"{time.strftime('%H:%M:%S')}  {translation}\n{original}".strip())
        self.history.insertItem(0, item)
        while self.history.count() > 24:
            self.history.takeItem(self.history.count() - 1)

    def set_status(self, text: str, error: bool = False) -> None:
        self.status_badge.setText(text)
        self.status_badge.setProperty("error", error)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.engine.stop()
        event.accept()


STYLESHEET = """
QWidget#root {
    background: #101114;
    color: #f3f5f7;
    font-family: "Segoe UI", "Microsoft YaHei UI";
    font-size: 14px;
}
QLabel#appTitle {
    color: #f3f5f7;
    font-size: 30px;
    font-weight: 750;
}
QLabel#muted, QLabel#fieldLabel {
    color: #9aa4b2;
}
QLabel#sectionTitle {
    color: #f3f5f7;
    font-size: 16px;
    font-weight: 650;
}
QLabel#statusBadge {
    min-width: 112px;
    padding: 8px 14px;
    border: 1px solid #353c44;
    border-radius: 8px;
    background: #191d22;
    color: #d9e0e8;
}
QLabel#statusBadge[error="true"] {
    border-color: #e45f5f;
    color: #ffb4b4;
}
QFrame#panel, QFrame#captionPanel {
    background: #15181d;
    border: 1px solid #293039;
    border-radius: 8px;
}
QLabel#translation {
    color: #f3f5f7;
    font-size: 42px;
    font-weight: 760;
    line-height: 1.12;
}
QLabel#original {
    color: #aeb6c2;
    font-size: 16px;
}
QLabel#meterText {
    color: #aeb6c2;
    font-size: 13px;
}
QPushButton {
    min-height: 34px;
    padding: 0 16px;
    border: 1px solid #343b43;
    border-radius: 8px;
    background: #191d22;
    color: #f3f5f7;
}
QPushButton:hover {
    background: #20262d;
}
QPushButton#primaryButton {
    border-color: #3da376;
    background: #1d6f51;
}
QPushButton:disabled {
    color: #697382;
    background: #15181d;
}
QComboBox {
    min-height: 34px;
    min-width: 118px;
    padding: 0 10px;
    border: 1px solid #343b43;
    border-radius: 8px;
    background: #191d22;
    color: #f3f5f7;
}
QCheckBox {
    color: #d9e0e8;
}
QProgressBar {
    min-height: 10px;
    border: 0;
    border-radius: 5px;
    background: #0e1115;
}
QProgressBar::chunk {
    border-radius: 5px;
    background: #38b986;
}
QListWidget#historyList {
    border: 0;
    background: #101318;
    color: #eef3f7;
    padding: 6px;
}
QListWidget#historyList::item {
    border-bottom: 1px solid #293039;
    padding: 9px;
}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TranslaInTime")
    window = CaptionWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
