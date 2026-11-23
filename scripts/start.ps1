# Starts the Web Agent on Windows, each part in its own window.
#   .\scripts\start.ps1           API + Celery worker + Celery Beat (needs Redis/Memurai running)
#   .\scripts\start.ps1 -Inline   API only, tasks run inside it (no Redis)
#   .\scripts\start.ps1 -MLflow   also open the MLflow UI on http://localhost:5000
#   .\scripts\start.ps1 -Show     show the browser window (not headless)
param([switch]$Inline, [switch]$MLflow, [switch]$Show)

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Run .\scripts\setup.ps1 first." -ForegroundColor Yellow; exit 1 }
if (-not $env:GROQ_API_KEY) { $env:GROQ_API_KEY = [Environment]::GetEnvironmentVariable("GROQ_API_KEY", "User") }
if (-not $env:GROQ_API_KEY) { Write-Host "GROQ_API_KEY is not set. See scripts\setup.ps1." -ForegroundColor Yellow }

$port = if ($env:WA_PORT) { $env:WA_PORT } else { "8020" }
$envLine = "Set-Location '$root';"
if ($Inline) { $envLine += " `$env:WA_INLINE='1';" }
if ($Show) { $envLine += " `$env:WA_HEADLESS='0';" }

function Open($title, $cmd) {
    # Turn off QuickEdit so a click in the window does not pause the program.
    $noQuick = "try { Add-Type -Name K -Namespace W -MemberDefinition '[DllImport(""kernel32.dll"")] public static extern System.IntPtr GetStdHandle(int h); [DllImport(""kernel32.dll"")] public static extern bool GetConsoleMode(System.IntPtr h, out uint m); [DllImport(""kernel32.dll"")] public static extern bool SetConsoleMode(System.IntPtr h, uint m);'; `$h=[W.K]::GetStdHandle(-10); `$m=0; [void][W.K]::GetConsoleMode(`$h,[ref]`$m); [void][W.K]::SetConsoleMode(`$h, (`$m -band (-bnot 0x40)) -bor 0x80) } catch {};"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='$title'; $noQuick $envLine $cmd"
}

if (-not $Inline) {
    $redis = Get-Service -Name Memurai -ErrorAction SilentlyContinue
    if ($redis -and $redis.Status -ne "Running") { Write-Host "Memurai (Redis) is not running. Start it or use -Inline." -ForegroundColor Yellow }
    Open "Web Agent worker (Celery)" "& '$py' -m celery -A webagent.worker.celery_app worker --pool=solo -l info -Q webagent"
    # Beat starts the watches (repeated tasks) every minute.
    Open "Web Agent scheduler (Celery Beat)" "& '$py' -m celery -A webagent.worker.celery_app beat -l info -s data\celerybeat-schedule"
}
Open "Web Agent API (FastAPI :$port)" "& '$py' -m uvicorn webagent.api.main:app --host 127.0.0.1 --port $port"
if ($MLflow) {
    Open "MLflow UI (:5000)" "& '$root\.venv\Scripts\mlflow.exe' ui --backend-store-uri sqlite:///data/mlflow.db --port 5000"
}
Start-Sleep -Seconds 5
Start-Process "http://localhost:$port"
Write-Host "Web Agent started on http://localhost:$port  (close the windows to stop)."
