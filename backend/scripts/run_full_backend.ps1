# Run both the API and the Voice Agent in separate windows
$BackendDir = Join-Path $PSScriptRoot ".."
Set-Location $BackendDir
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$BackendDir'; python main.py" -Wait: $false
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$BackendDir'; python agent.py dev" -Wait: $false

Write-Host "------------------------------------------------" -ForegroundColor Green
Write-Host "Full Backend is starting!" -ForegroundColor Green
Write-Host "1. Dashboard API is launching in window 1"
Write-Host "2. AI Voice Agent is launching in window 2"
Write-Host "------------------------------------------------"
