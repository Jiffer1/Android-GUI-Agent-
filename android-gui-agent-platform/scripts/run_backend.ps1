Set-Location "$PSScriptRoot\..\backend"
Write-Host "Starting backend on http://localhost:8000 ..."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
