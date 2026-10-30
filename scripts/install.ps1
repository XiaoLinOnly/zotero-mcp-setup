#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet("launcher", "env")]
    [string]$Mode = "launcher",
    [string]$LibraryId = "",
    [ValidateSet("user", "group")]
    [string]$LibraryType = "user",
    [string]$DataDir = "",
    [string]$LauncherPath = "",
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Read-PlainSecret {
    param([string]$Prompt)
    $secure = Read-Host -AsSecureString $Prompt
    if (-not $secure -or $secure.Length -eq 0) {
        throw "API key must not be empty."
    }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Test-ZoteroDataDir {
    param([string]$Path)
    if (-not $Path) { return $false }
    return Test-Path (Join-Path $Path "zotero.sqlite")
}

$npx = Get-Command npx -ErrorAction SilentlyContinue
if (-not $npx) {
    throw "npx not found. Install Node.js 20+ first: https://nodejs.org/"
}

if (-not $LibraryId) {
    $LibraryId = Read-Host "Zotero userID (optional; press Enter to let zoteus use the key's personal library)"
}
if ($LibraryId -and $LibraryId -notmatch '^\d+$') {
    throw "LibraryId must be a positive integer."
}
if ($LibraryType -eq "group" -and -not $LibraryId) {
    throw "Group libraries need an explicit -LibraryId."
}

if (-not $DataDir) {
    $defaultDataDir = Join-Path $HOME "Zotero"
    if (Test-ZoteroDataDir $defaultDataDir) {
        $DataDir = $defaultDataDir
    }
    else {
        Write-Warning "Default Zotero data dir not found at $defaultDataDir (needs zotero.sqlite)."
        Write-Warning "Pass -DataDir '<path containing zotero.sqlite>' for a portable/custom install."
        $DataDir = ""
    }
}
elseif (-not (Test-ZoteroDataDir $DataDir)) {
    Write-Warning "$DataDir does not contain zotero.sqlite; attachments/full text may not resolve."
}

if (-not $LauncherPath) {
    $LauncherPath = Join-Path $HOME ".qoder-cn\zotero-mcp\zoteus.cmd"
}

if ($Mode -eq "env") {
    $entry = [ordered]@{
        zotero = [ordered]@{
            command = "cmd"
            args    = @("/c", "npx", "-y", "@oscardvs/zoteus")
            env     = [ordered]@{
                ZOTERO_API_KEY      = "YOUR_API_KEY_HERE"
                ZOTERO_LIBRARY_TYPE = $LibraryType
            }
        }
    }
    if ($LibraryId) {
        $entry.zotero.env.ZOTERO_LIBRARY_ID = $LibraryId
    }
    if ($DataDir) {
        $entry.zotero.env.ZOTERO_DATA_DIR = $DataDir
    }

    Write-Host ""
    Write-Host 'Add this under "mcpServers" in %USERPROFILE%\.qoder-cn\settings.json and replace <your-api-key>:'
    Write-Host "(the key is intentionally not printed or written by this script in env mode)"
    $entry | ConvertTo-Json -Depth 6
    return
}

$apiKey = $env:ZOTERO_API_KEY
if ($apiKey) {
    Write-Host "Using ZOTERO_API_KEY from the environment (not printed)."
}
else {
    $apiKey = Read-PlainSecret "Zotero API key (input is hidden)"
}
if ($apiKey.Length -lt 10) {
    throw "That API key looks too short; copy the full key from https://www.zotero.org/settings/keys"
}

$launcherDir = Split-Path -Parent $LauncherPath
if (-not (Test-Path $launcherDir)) {
    if ($DryRun) {
        Write-Host "Dry run: would create directory $launcherDir"
    }
    else {
        New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
    }
}

if ((Test-Path $LauncherPath) -and -not $Force -and -not $DryRun) {
    throw "$LauncherPath already exists. Re-run with -Force to overwrite (a .bak copy will be kept)."
}

if ((Test-Path $LauncherPath) -and $Force -and -not $DryRun) {
    $backup = "$LauncherPath.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item -LiteralPath $LauncherPath -Destination $backup
    Write-Host "Backup: $backup"
}

$lines = @(
    "@echo off",
    "setlocal",
    ('set "ZOTERO_API_KEY=' + $apiKey + '"'),
    ('set "ZOTERO_LIBRARY_TYPE=' + $LibraryType + '"')
)
if ($LibraryId) {
    $lines += ('set "ZOTERO_LIBRARY_ID=' + $LibraryId + '"')
}
if ($DataDir) {
    $lines += ('set "ZOTERO_DATA_DIR=' + $DataDir + '"')
}
$lines += "npx -y @oscardvs/zoteus %*"
$content = ($lines -join "`r`n") + "`r`n"

if ($DryRun) {
    Write-Host "Dry run: would write $LauncherPath"
    Write-Host ("Dry run: key would be stored as " + ("*" * $apiKey.Length))
}
else {
    # cmd.exe reads .cmd files in the OEM code page; UTF-8 can mangle non-ASCII paths.
    $oem = [System.Text.Encoding]::GetEncoding(
        [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
    [System.IO.File]::WriteAllText($LauncherPath, $content, $oem)
    Write-Host "Launcher written: $LauncherPath"
}

$entry = [ordered]@{
    zotero = [ordered]@{
        command = "cmd"
        args    = @("/c", $LauncherPath)
    }
}

Write-Host ""
Write-Host 'Add this under "mcpServers" in %USERPROFILE%\.qoder-cn\settings.json:'
$entry | ConvertTo-Json -Depth 6

Write-Host ""
Write-Host "Then restart Qoder or open a new chat session, and verify with:"
Write-Host ('  python scripts/probe_zotero_mcp.py --from-launcher "' + $LauncherPath + '"')
