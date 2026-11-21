# One-time (re-runnable) setup for the WoW Archipelago Windows server package.
param([switch]$SkipClientData)

. "$PSScriptRoot\Common.ps1"

Write-Host 'WoW Archipelago server setup' -ForegroundColor White
Write-Host "Install folder: $Root"
New-Item -ItemType Directory -Force $LogsDir, $DataDir | Out-Null

# ---------------------------------------------------------------------------------
Write-Step '1/6  Database (portable MySQL on 127.0.0.1:3307)'
@"
[mysqld]
basedir=$(Fwd $MySQL)
datadir=$(Fwd $DbDir)
port=$DbPort
bind-address=127.0.0.1
mysqlx=0
log-error=$(Fwd "$LogsDir\mysql.err")
character-set-server=utf8mb4
max_allowed_packet=64M
innodb_buffer_pool_size=256M

[client]
port=$DbPort
"@ | Set-Content -Path $MyIni -Encoding ASCII

if (-not (Test-Path (Join-Path $DbDir 'mysql'))) {
    Write-Ok 'Initializing database folder...'
    & (Join-Path $MySQL 'bin\mysqld.exe') "--defaults-file=$MyIni" --initialize-insecure
    if ($LASTEXITCODE -ne 0) { throw "MySQL initialization failed. See $LogsDir\mysql.err" }
}
Start-MySQL
Invoke-Sql @"
CREATE USER IF NOT EXISTS '$DbUser'@'localhost' IDENTIFIED BY '$DbPass';
CREATE USER IF NOT EXISTS '$DbUser'@'127.0.0.1' IDENTIFIED BY '$DbPass';
CREATE DATABASE IF NOT EXISTS acore_world      DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS acore_characters DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS acore_auth       DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON acore_world.*      TO '$DbUser'@'localhost', '$DbUser'@'127.0.0.1';
GRANT ALL PRIVILEGES ON acore_characters.* TO '$DbUser'@'localhost', '$DbUser'@'127.0.0.1';
GRANT ALL PRIVILEGES ON acore_auth.*       TO '$DbUser'@'localhost', '$DbUser'@'127.0.0.1';
FLUSH PRIVILEGES;
"@ | Out-Null
Write-Ok 'MySQL ready.'

# ---------------------------------------------------------------------------------
Write-Step '2/6  Server data (maps, vmaps, mmaps, dbc)'
if (Test-Path (Join-Path $DataDir 'dbc')) {
    Write-Ok 'Already present.'
} elseif ($SkipClientData) {
    Write-Note "Skipped. Put dbc\, maps\, vmaps\, mmaps\ into $DataDir yourself."
} else {
    $release = Invoke-RestMethod 'https://api.github.com/repos/wowgaming/client-data/releases/latest' `
        -Headers @{ 'User-Agent' = 'wow-archipelago-setup' }
    $asset = $release.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1
    if (-not $asset) { throw 'Could not find the client data download.' }
    $zip = Join-Path $Root 'client-data.zip'
    Write-Ok "Downloading $($asset.name) ($([math]::Round($asset.size / 1MB)) MB, release $($release.tag_name))..."
    & curl.exe -L --fail --retry 3 -o $zip $asset.browser_download_url
    if ($LASTEXITCODE -ne 0) { throw 'Download failed.' }
    Write-Ok 'Extracting...'
    $tmp = Join-Path $Root 'client-data-tmp'
    New-Item -ItemType Directory -Force $tmp | Out-Null
    & tar.exe -xf $zip -C $tmp
    if ($LASTEXITCODE -ne 0) { throw 'Extraction failed.' }
    # The zip may or may not have a top-level folder; find the one holding dbc\.
    $dbc = Get-ChildItem $tmp -Recurse -Directory -Filter 'dbc' | Select-Object -First 1
    if (-not $dbc) { throw 'Downloaded data has no dbc folder.' }
    Get-ChildItem $dbc.Parent.FullName | Move-Item -Destination $DataDir -Force
    Remove-Item -Recurse -Force $tmp, $zip
    Write-Ok 'Done.'
}

# ---------------------------------------------------------------------------------
Write-Step '3/6  Server configuration'

function Set-ConfValue([string]$text, [string]$key, [string]$value) {
    $pattern = '(?m)^[ \t]*' + [regex]::Escape($key) + '[ \t]*=.*$'
    $line = "$key = $value"
    if ([regex]::IsMatch($text, $pattern)) {
        return [regex]::Replace($text, $pattern, { param($m) $line }.GetNewClosure())
    }
    return $text.TrimEnd() + "`r`n$line`r`n"
}

function Write-Conf([string]$name, [hashtable]$values) {
    $dist = Get-ChildItem $Confs -Recurse -Filter "$name.dist" | Select-Object -First 1
    if (-not $dist) { throw "Missing $name.dist in $Confs" }
    $text = Get-Content $dist.FullName -Raw
    foreach ($k in $values.Keys) { $text = Set-ConfValue $text $k $values[$k] }
    Set-Content -Path ($dist.FullName -replace '\.dist$', '') -Value $text -Encoding ASCII
}

$dbInfo = { param($db) "`"127.0.0.1;$DbPort;$DbUser;$DbPass;$db`"" }
$mysqlExe = "`"$(Fwd (Join-Path $MySQL 'bin\mysql.exe'))`""

Write-Conf 'worldserver.conf' @{
    'LoginDatabaseInfo'       = & $dbInfo 'acore_auth'
    'WorldDatabaseInfo'       = & $dbInfo 'acore_world'
    'CharacterDatabaseInfo'   = & $dbInfo 'acore_characters'
    'DataDir'                 = "`"$(Fwd $DataDir)`""
    'LogsDir'                 = "`"$(Fwd $LogsDir)`""
    'SourceDirectory'         = "`"$(Fwd $Source)`""
    'MySQLExecutable'         = $mysqlExe
    'BindIP'                  = '"127.0.0.1"'
    'Updates.EnableDatabases' = '7'
    'Updates.AutoSetup'       = '1'
}
Write-Conf 'authserver.conf' @{
    'LoginDatabaseInfo' = & $dbInfo 'acore_auth'
    'LogsDir'           = "`"$(Fwd $LogsDir)`""
    'SourceDirectory'   = "`"$(Fwd $Source)`""
    'MySQLExecutable'   = $mysqlExe
    'BindIP'            = '"127.0.0.1"'
}
Write-Conf 'mod_ale.conf' @{
    'ALE.ScriptPath' = "`"$(Fwd $LuaDir)`""
    'ALE.TraceBack'  = 'true'
}
Write-Ok 'Configs written to server\configs.'

# ---------------------------------------------------------------------------------
Write-Step '4/6  Building game databases (first time takes 5-15 minutes)'
if (Get-ServerProcess 'worldserver') {
    Write-Ok 'World server is already running; skipping. (Run Stop.bat first to re-run this step.)'
} else {
    # Starting the worldserver once runs AzerothCore's auto-setup and our custom SQL.
    $ws = Start-WorldServer 'Hidden'
    if (-not (Wait-Port $WorldPort 1800 $ws)) {
        if ($ws -and -not $ws.HasExited) { Stop-Process -Id $ws.Id -Force }
        Write-Host (Get-Content (Join-Path $LogsDir 'Server.log') -Tail 30 -ErrorAction SilentlyContinue | Out-String)
        $err = Get-Content (Join-Path $LogsDir 'worldserver-stderr.txt') -ErrorAction SilentlyContinue | Out-String
        if ($err.Trim()) { Write-Host $err -ForegroundColor Red }
        throw "World server failed to start. Full log: $LogsDir\Server.log"
    }
    Stop-Process -Id $ws.Id -Force
    Write-Ok 'Databases ready.'
}

# ---------------------------------------------------------------------------------
Write-Step '5/6  Game account'
$count = Invoke-Sql 'SELECT COUNT(*) FROM acore_auth.account;' -Scalar
if ([int]$count -gt 0 -and (Read-Host '    An account already exists. Create another? (y/N)') -notmatch '^[yY]') {
    Write-Ok 'Keeping existing accounts.'
} else {
    while ($true) {
        $user = Read-Host '    Username (letters/digits, 3-16)'
        $pass = Read-Password '    Password (max 16)'
        try { New-GameAccount $user $pass; Write-Ok "Account $($user.ToUpper()) created."; break }
        catch { Write-Note $_.Exception.Message }
    }
}

# ---------------------------------------------------------------------------------
Write-Step '6/6  WoW client (3.3.5a) - realmlist and addon'
$s = Get-Settings
$default = $s.WowPath
$wow = Read-Host "    Path to your WoW 3.3.5a folder (contains Wow.exe)$(if ($default) { " [$default]" }). Blank to skip"
if (-not $wow) { $wow = $default }
if ($wow -and (Test-Path (Join-Path $wow 'Wow.exe'))) {
    $ver = (Get-Item (Join-Path $wow 'Wow.exe')).VersionInfo.FileVersion
    if ($ver -notmatch '12340') { Write-Note "Wow.exe version is '$ver'; this server needs 3.3.5a (build 12340)." }

    $locales = Get-ChildItem (Join-Path $wow 'Data') -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^[a-z]{2}[A-Z]{2}$' }
    foreach ($l in $locales) {
        Set-Content -Path (Join-Path $l.FullName 'realmlist.wtf') -Value 'set realmlist 127.0.0.1' -Encoding ASCII
    }
    $cfg = Join-Path $wow 'WTF\Config.wtf'
    if (Test-Path $cfg) {
        (Get-Content $cfg) -replace '^SET realmlist .*$', 'SET realmlist "127.0.0.1"' | Set-Content $cfg -Encoding ASCII
    }
    $addons = Join-Path $wow 'Interface\AddOns'
    New-Item -ItemType Directory -Force $addons | Out-Null
    Copy-Item -Recurse -Force $AddonDir $addons
    $s.WowPath = $wow
    Save-Settings $s
    Write-Ok 'Realmlist set to 127.0.0.1 and Archipelago addon installed.'
} elseif ($wow) {
    Write-Note "No Wow.exe in '$wow'. Skipped - set realmlist and copy addon\Archipelago manually."
}

Stop-MySQL
Write-Host "`nSetup complete. Run Start.bat to play." -ForegroundColor Green
