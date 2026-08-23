# Convenience wrapper: forwards all args to the reader inside the venv.
#   ./run.ps1 --url <chapter-url> --play
#   ./run.ps1 --text data/chapters/foo.txt --dry-run
Set-Location -Path $PSScriptRoot
& ".\.venv\Scripts\python.exe" -m src.reader @args
