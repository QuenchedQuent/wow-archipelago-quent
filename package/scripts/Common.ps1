# Shared helpers for the WoW Archipelago Windows server package.
# Package layout (relative to the package root):
#   server\        worldserver.exe, authserver.exe, DLLs, configs\
#   mysql\         portable MySQL 8.4 (bin\, share\, lib\)
#   source\        AzerothCore data\sql (used by the DB auto-setup)
#   lua_scripts\   archipelago.lua
#   addon\         Archipelago WoW addon
#   data\ db\ logs\ my.ini settings.json   (created by Setup)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root     = Split-Path -Parent $PSScriptRoot
$Server   = Join-Path $Root 'server'
$Confs    = Join-Path $Server 'configs'
$MySQL    = Join-Path $Root 'mysql'
$DataDir  = Join-Path $Root 'data'
$DbDir    = Join-Path $Root 'db'
$LogsDir  = Join-Path $Root 'logs'
$Source   = Join-Path $Root 'source'
$LuaDir   = Join-Path $Root 'lua_scripts'
$AddonDir = Join-Path $Root 'addon\Archipelago'
$MyIni    = Join-Path $Root 'my.ini'
$Settings = Join-Path $Root 'settings.json'

$DbPort    = 3307
$AuthPort  = 3724
$WorldPort = 8085
$DbUser    = 'acore'
$DbPass    = 'acore'

function Fwd([string]$p) { $p -replace '\\', '/' }

function Write-Step([string]$text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok([string]$text)   { Write-Host "    $text" -ForegroundColor Green }
function Write-Note([string]$text) { Write-Host "    $text" -ForegroundColor Yellow }

function Test-Port([int]$port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $port); return $true } catch { return $false } finally { $c.Close() }
}

function Wait-Port([int]$port, [int]$seconds, $process = $null) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $port) { return $true }
        if ($process -and $process.HasExited) { return $false }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-Settings {
    if (Test-Path $Settings) { return Get-Content $Settings -Raw | ConvertFrom-Json }
    return [pscustomobject]@{ WowPath = '' }
}

function Save-Settings($s) { $s | ConvertTo-Json | Set-Content -Path $Settings -Encoding UTF8 }

# --- MySQL -------------------------------------------------------------------------

function Start-MySQL {
    if (Test-Port $DbPort) { return }
    $p = Start-Process -FilePath (Join-Path $MySQL 'bin\mysqld.exe') `
        -ArgumentList "--defaults-file=`"$MyIni`"" -WindowStyle Hidden -PassThru
    if (-not (Wait-Port $DbPort 90 $p)) {
        throw "MySQL did not start. See $LogsDir\mysql.err"
    }
}

function Stop-MySQL {
    if (-not (Test-Port $DbPort)) { return }
    & (Join-Path $MySQL 'bin\mysqladmin.exe') --host=127.0.0.1 --port=$DbPort --user=root shutdown
}

function Invoke-Sql([string]$sql, [switch]$Scalar) {
    $OutputEncoding = New-Object Text.UTF8Encoding $false
    $mysqlArgs = @('--host=127.0.0.1', "--port=$DbPort", '--user=root', '--batch', '--skip-column-names')
    $out = $sql | & (Join-Path $MySQL 'bin\mysql.exe') @mysqlArgs
    if ($LASTEXITCODE -ne 0) { throw "MySQL command failed." }
    if ($Scalar) { return ($out | Select-Object -First 1) }
    return $out
}

# --- processes ---------------------------------------------------------------------

function Get-ServerProcess([string]$name) {
    Get-Process -Name $name -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Server, [StringComparison]::OrdinalIgnoreCase) }
}

function Start-AuthServer([string]$windowStyle = 'Minimized') {
    if (Get-ServerProcess 'authserver') { return }
    Start-Process -FilePath (Join-Path $Server 'authserver.exe') -WorkingDirectory $Server `
        -ArgumentList '-c', "`"$Confs\authserver.conf`"" -WindowStyle $windowStyle | Out-Null
}

function Start-WorldServer([string]$windowStyle = 'Normal') {
    $existing = Get-ServerProcess 'worldserver'
    if ($existing) { return $existing }
    Start-Process -FilePath (Join-Path $Server 'worldserver.exe') -WorkingDirectory $Server `
        -ArgumentList '-c', "`"$Confs\worldserver.conf`"" -WindowStyle $windowStyle -PassThru
}

# --- accounts (SRP6, same math as AzerothCore's AccountMgr) ------------------------

Add-Type -AssemblyName System.Numerics

function New-SRP6([string]$user, [string]$pass) {
    $sha1 = [Security.Cryptography.SHA1]::Create()
    $salt = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($salt)
    $h1 = $sha1.ComputeHash([Text.Encoding]::UTF8.GetBytes(($user + ':' + $pass).ToUpperInvariant()))
    $h2 = $sha1.ComputeHash([byte[]]($salt + $h1))
    $x = New-Object Numerics.BigInteger (,[byte[]]($h2 + [byte]0))   # little-endian, force positive
    $N = [Numerics.BigInteger]::Parse('0894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7',
        [Globalization.NumberStyles]::AllowHexSpecifier)
    $v = [Numerics.BigInteger]::ModPow([Numerics.BigInteger]7, $x, $N).ToByteArray()
    $verifier = New-Object byte[] 32
    [Array]::Copy($v, $verifier, [Math]::Min(32, $v.Length))
    return @{ Salt = $salt; Verifier = $verifier }
}

function ConvertTo-Hex([byte[]]$bytes) { ($bytes | ForEach-Object { $_.ToString('X2') }) -join '' }

function New-GameAccount([string]$user, [string]$pass, [switch]$GM) {
    if ($user -notmatch '^[A-Za-z0-9]{3,16}$') { throw 'Username must be 3-16 letters or digits.' }
    if ($pass.Length -lt 1 -or $pass.Length -gt 16) { throw 'Password must be 1-16 characters.' }
    $u = $user.ToUpperInvariant()
    $exists = Invoke-Sql "SELECT COUNT(*) FROM acore_auth.account WHERE username = '$u';" -Scalar
    if ([int]$exists -gt 0) { throw "Account $u already exists." }
    $srp = New-SRP6 $u $pass
    Invoke-Sql ("INSERT INTO acore_auth.account (username, salt, verifier, expansion) VALUES " +
        "('$u', 0x$(ConvertTo-Hex $srp.Salt), 0x$(ConvertTo-Hex $srp.Verifier), 2);") | Out-Null
    if ($GM) {
        Invoke-Sql ("INSERT INTO acore_auth.account_access (id, gmlevel, RealmID) " +
            "SELECT id, 3, -1 FROM acore_auth.account WHERE username = '$u';") | Out-Null
    }
}

function Read-Password([string]$prompt) {
    $secure = Read-Host $prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
