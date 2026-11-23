# First-time setup on Windows: Python venv + packages. Uses the installed Microsoft Edge as the browser,
# so no browser download is needed. (Optional: .\.venv\Scripts\playwright install chromium)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }
Write-Host "Setup done. Set your Groq key once (it is saved in Windows, not in files):"
Write-Host '  [Environment]::SetEnvironmentVariable("GROQ_API_KEY", (Read-Host "Groq key"), "User")'
Write-Host "Then run .\scripts\start.ps1"
