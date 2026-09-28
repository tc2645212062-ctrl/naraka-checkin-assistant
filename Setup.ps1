$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.venv'
$python = Join-Path $runtime 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $runtime) -Force | Out-Null
    & py -3.12 -m venv $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install it from python.org, then retry.' }
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and retry.' }
& $python -c "import cv2, rapidocr_onnxruntime, win32gui; from PySide6 import QtCore, QtWidgets; print('Runtime ready; PySide6', QtCore.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }
Write-Host "Setup complete in $runtime. Run Start.cmd."
