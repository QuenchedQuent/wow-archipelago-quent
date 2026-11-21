. "$PSScriptRoot\Common.ps1"

if (-not (Test-Path (Join-Path $Confs 'worldserver.conf'))) {
    throw 'Run Setup.bat first.'
}

Write-Step 'Starting database'
Start-MySQL
Write-Ok 'MySQL running.'

Write-Step 'Starting auth server'
Start-AuthServer
if (-not (Wait-Port $AuthPort 60)) { throw "Auth server did not start. See $LogsDir\Auth.log" }
Write-Ok 'Auth server running.'

Write-Step 'Starting world server (its console window stays open - do not close it while playing)'
$ws = Start-WorldServer
if (-not (Wait-Port $WorldPort 600 $ws)) { throw "World server did not start. See $LogsDir\Server.log" }
Write-Ok 'World server running.'

$s = Get-Settings
if ($s.WowPath -and (Test-Path (Join-Path $s.WowPath 'Wow.exe'))) {
    Start-Process -FilePath (Join-Path $s.WowPath 'Wow.exe') -WorkingDirectory $s.WowPath
    Write-Ok 'Launched WoW.'
}

Write-Host @"

Ready! Next:
  1. Log in with a NEW character (one per Archipelago seed).
  2. Open the Archipelago Launcher -> "World of Warcraft Client" and connect to your room.
     It links to the character you're logged in with automatically.
  3. Type /ap in game to open the tracker.

To shut down: log out of WoW, then run Stop.bat.
"@ -ForegroundColor Green
