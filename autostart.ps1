# Fallback auto-start: make sure the local server is running.
# The page is opened by autostart.vbs (registry Run entry).
Start-Sleep -Seconds 8

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath "D:\software\Python3.12.8win64\python.exe" -ArgumentList "app.py" -WorkingDirectory $root -WindowStyle Hidden
}
