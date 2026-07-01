param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 7860,
    [string]$ModelSize = "small",
    [string]$Device = "auto",
    [string]$ComputeType = "int8_float16",
    [string]$TargetLanguage = "zh",
    [switch]$UseHfMirror
)

$ErrorActionPreference = "Stop"

$env:WHISPER_MODEL_SIZE = $ModelSize
$env:WHISPER_DEVICE = $Device
$env:WHISPER_COMPUTE_TYPE = $ComputeType
$env:TARGET_LANGUAGE = $TargetLanguage
$nvidiaRoot = Join-Path $PWD ".venv\Lib\site-packages\nvidia"
$cudaDllDirs = @(
    "cublas\bin",
    "cudnn\bin",
    "cuda_runtime\bin",
    "cuda_nvrtc\bin"
)
foreach ($relative in $cudaDllDirs) {
    $dllDir = Join-Path $nvidiaRoot $relative
    if (Test-Path $dllDir) {
        $env:PATH = "$dllDir;$env:PATH"
    }
}

if ($UseHfMirror -and -not $env:HF_ENDPOINT) {
    $env:HF_ENDPOINT = "https://hf-mirror.com"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.10 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

Write-Host ""
Write-Host "Starting TranslaInTime demo at http://$HostName`:$Port"
Write-Host "Model=$ModelSize Device=$Device ComputeType=$ComputeType Target=$TargetLanguage HF_ENDPOINT=$env:HF_ENDPOINT"
Write-Host ""

& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host $HostName --port $Port
