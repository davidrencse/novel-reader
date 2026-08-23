# ============================================================
#  Re:Zero Reader - one-time setup (Windows, PowerShell)
#  Creates a Python 3.11 venv, installs CUDA PyTorch + Coqui XTTS.
#  Run from the project folder:   ./setup.ps1
# ============================================================
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== Creating Python 3.11 virtual environment (.venv) ==" -ForegroundColor Cyan
py -3.11 -m venv .venv
$py = ".\.venv\Scripts\python.exe"

Write-Host "== Upgrading pip ==" -ForegroundColor Cyan
& $py -m pip install --upgrade pip wheel

# PyTorch 2.5.1 + CUDA 12.4 wheels. 2.5.x avoids the torch 2.6 weights_only
# change that breaks loading the XTTS checkpoint. RTX 4060 works with cu124.
Write-Host "== Installing PyTorch (CUDA 12.4) - this is a big download ==" -ForegroundColor Cyan
& $py -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124

Write-Host "== Installing Coqui XTTS + project dependencies ==" -ForegroundColor Cyan
& $py -m pip install -r requirements.txt

Write-Host "== Verifying CUDA is visible to PyTorch ==" -ForegroundColor Cyan
& $py check_env.py

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Test attribution (no model download):  ./run.ps1 --url CHAPTER_URL --dry-run"
Write-Host "Read it aloud (downloads model once):  ./run.ps1 --url CHAPTER_URL --play"
