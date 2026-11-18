# Run when downloads start failing (YouTube changes often): updates yt-dlp and its solver.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
& .venv\Scripts\python.exe -m pip install -q -U "yt-dlp[default]"
& .venv\Scripts\python.exe -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
