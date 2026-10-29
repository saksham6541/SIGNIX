$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$server = Start-Process -FilePath "$root/.venv/Scripts/python.exe" -ArgumentList "$PSScriptRoot/start_demo.py" -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput "$PSScriptRoot/server.log" -RedirectStandardError "$PSScriptRoot/server-error.log"
$server.Id | Set-Content "$PSScriptRoot/server.pid"
