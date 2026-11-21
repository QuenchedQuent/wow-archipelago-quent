. "$PSScriptRoot\Common.ps1"

if (Get-ServerProcess 'worldserver') {
    Write-Note 'Log out of WoW first so your character is saved.'
    Read-Host '    Press Enter to stop the server' | Out-Null
}

Write-Step 'Stopping servers'
Get-ServerProcess 'worldserver' | Stop-Process -Force
Get-ServerProcess 'authserver'  | Stop-Process -Force
Stop-MySQL
Write-Ok 'Stopped.'
