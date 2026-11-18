# One-time setup for the farm on Windows. Safe to run again.
# Run from the project folder:   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
if (-not (Have "ffmpeg")) { Write-Host "[x] ffmpeg missing:  winget install Gyan.FFmpeg   (then open a new terminal)"; exit 1 }
if (-not (Have "node") -and -not (Have "deno")) { Write-Host "[x] Node.js missing:  winget install OpenJS.NodeJS.LTS"; exit 1 }

$py = $null
foreach ($c in @("py -3.12", "py -3.13", "py -3.11", "python")) {
    $parts = $c.Split(" ")
    if (Have $parts[0]) { $py = $parts; break }
}
if (-not $py) { Write-Host "[x] Python 3.10-3.13 missing:  winget install Python.Python.3.12"; exit 1 }

# keep caches on this drive
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
$env:HF_HOME = Join-Path $Root "models\hf"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $py[0] @($py | Select-Object -Skip 1) -m venv .venv
}
& .venv\Scripts\python.exe -m pip install -q -U pip
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt

# models: face finder + Whisper (tiny, base, small)
New-Item -ItemType Directory -Force -Path models | Out-Null
if (-not (Test-Path "models\yunet.onnx")) {
    Invoke-WebRequest "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" -OutFile "models\yunet.onnx"
}
& .venv\Scripts\python.exe -c "import config.settings; from faster_whisper import WhisperModel; [WhisperModel(m, device='cpu', compute_type='int8') for m in ('tiny','base','small')]; print('[ok] whisper models')"

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "[ok] created .env (fill in your keys)" }
& .venv\Scripts\python.exe main.py doctor
