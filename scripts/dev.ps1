# Run the dashboard backend and frontend together (dev mode).
#
# Usage:
#   pwsh scripts/dev.ps1
#
# Backend:  http://127.0.0.1:8787
# Frontend: http://127.0.0.1:5173  (proxies /api and /ws → 8787)

$repo = Resolve-Path "$PSScriptRoot\.."
Set-Location $repo

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "venv not found at $py — create one and pip install -r requirements.txt"
    exit 1
}

# Start FastAPI
$api = Start-Process -FilePath $py `
    -ArgumentList @("-m", "uvicorn", "dashboard_v2.api.main:app", "--host", "127.0.0.1", "--port", "8787", "--reload") `
    -PassThru -WindowStyle Normal

# Start Vite
$web = Start-Process -FilePath "npm.cmd" `
    -ArgumentList @("--prefix", "dashboard_v2\web", "run", "dev") `
    -PassThru -WindowStyle Normal

Write-Host "API  → http://127.0.0.1:8787  (PID $($api.Id))" -ForegroundColor Cyan
Write-Host "WEB  → http://127.0.0.1:5173  (PID $($web.Id))" -ForegroundColor Cyan
Write-Host "Press Ctrl+C in this window to stop." -ForegroundColor Yellow

try {
    Wait-Process -Id $api.Id, $web.Id
} finally {
    foreach ($p in @($api, $web)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
}
