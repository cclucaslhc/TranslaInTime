$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.10 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

$dataArgs = @(
    "--add-data", "app;app",
    "--add-data", "static;static",
    "--add-data", "models;models"
)

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "TranslaInTime" `
    --collect-submodules "PySide6.QtCore" `
    --collect-submodules "PySide6.QtGui" `
    --collect-submodules "PySide6.QtWidgets" `
    --collect-all "sounddevice" `
    --collect-all "faster_whisper" `
    --collect-all "ctranslate2" `
    --collect-all "argostranslate" `
    --collect-all "nvidia" `
    --hidden-import "numpy" `
    --hidden-import "av" `
    --hidden-import "onnxruntime" `
    @dataArgs `
    "desktop_qt_launcher.pyw"

Write-Host "Built: dist\TranslaInTime\TranslaInTime.exe"
