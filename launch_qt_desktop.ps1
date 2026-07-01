$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\pythonw.exe")) {
    py -3.10 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
}

$env:WHISPER_MODEL_SIZE = "small"
$env:WHISPER_DEVICE = "auto"
$env:WHISPER_COMPUTE_TYPE = "int8_float16"
$env:TARGET_LANGUAGE = "zh"

$nvidiaRoot = Join-Path $PSScriptRoot ".venv\Lib\site-packages\nvidia"
foreach ($relative in @("cublas\bin", "cudnn\bin", "cuda_runtime\bin", "cuda_nvrtc\bin")) {
    $dllDir = Join-Path $nvidiaRoot $relative
    if (Test-Path $dllDir) {
        $env:PATH = "$dllDir;$env:PATH"
    }
}

Start-Process -FilePath ".\.venv\Scripts\pythonw.exe" -ArgumentList "desktop_qt_launcher.pyw" -WorkingDirectory $PSScriptRoot
