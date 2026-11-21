param([switch]$GM)

. "$PSScriptRoot\Common.ps1"

$wasRunning = Test-Port $DbPort
Start-MySQL
try {
    while ($true) {
        $user = Read-Host 'Username (letters/digits, 3-16)'
        $pass = Read-Password 'Password (max 16)'
        try {
            New-GameAccount $user $pass -GM:$GM
            Write-Ok "Account $($user.ToUpper()) created$(if ($GM) { ' (GM)' })."
            break
        } catch { Write-Note $_.Exception.Message }
    }
} finally {
    if (-not $wasRunning) { Stop-MySQL }
}
