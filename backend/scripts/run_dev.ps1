# Run the agent in development mode connected to the outbound call room
$BackendDir = Join-Path $PSScriptRoot ".."
Set-Location $BackendDir
python agent.py dev --room outbound-call-room
