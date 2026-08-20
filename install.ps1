#Requires -Version 5.1
<#
.SYNOPSIS
  Install agent-tether shims.

.DESCRIPTION
  Generates a .cmd shim per agent CLI and puts them on PATH ahead of the real
  binaries. Nothing is overwritten: the shims live in their own directory and
  work by PATH precedence alone, so uninstalling is just removing that
  directory from PATH.

  CHAINING. The shims do not hardcode a vendor path. At run time each one walks
  PATH, skips any file carrying the agent-tether marker, and calls the next
  match. So if other software also shadows `claude`, both survive: whichever is
  earlier on PATH runs first and the other is still reached through it. Order
  is yours to choose with -PathPosition.

.PARAMETER ShimDir
  Where to write the shims. You choose. Default: %LOCALAPPDATA%\agent-tether\bin

.PARAMETER Agents
  Which CLIs to shim. Default: every agent in src\agents.json that is actually
  present on this machine.

.PARAMETER PathPosition
  first : prepend to user PATH (shims win)
  last  : append (only used if nothing else provides the name)
  none  : write the shims, touch nothing; you place them yourself

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Agents claude,codex -PathPosition first
  .\install.ps1 -ShimDir D:\tools\shims -PathPosition none
#>
[CmdletBinding()]
param(
    [string]$ShimDir,
    [string[]]$Agents,
    [ValidateSet('first', 'last', 'none')]
    [string]$PathPosition = 'first',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$InstallDir = $PSScriptRoot
if (-not $ShimDir) { $ShimDir = Join-Path $env:LOCALAPPDATA 'agent-tether\bin' }
$AgentTable = Join-Path $InstallDir 'src\agents.json'

Write-Host 'agent-tether installer' -ForegroundColor Cyan
Write-Host '======================'
Write-Host "source   : $InstallDir"
Write-Host "shim dir : $ShimDir"
Write-Host ''

# --- preflight ------------------------------------------------------------

$zellij = Get-Command zellij -ErrorAction SilentlyContinue
if (-not $zellij) {
    Write-Host 'zellij was not found on PATH.' -ForegroundColor Red
    Write-Host 'agent-tether is a thin layer over zellij and cannot work without it.'
    Write-Host 'Install it with:  cargo install --locked zellij'
    if (-not $Force) { exit 1 }
} else {
    Write-Host "zellij   : $($zellij.Source) ($(& zellij --version))" -ForegroundColor Green
}

if (-not (Test-Path $AgentTable)) { throw "missing agent table: $AgentTable" }
$table = Get-Content -Raw $AgentTable | ConvertFrom-Json
$known = $table.agents.PSObject.Properties.Name

# --- decide which agents to shim -----------------------------------------

function Find-RealBinary {
    param([string]$Name)
    foreach ($dir in ($env:PATH -split ';')) {
        if ([string]::IsNullOrWhiteSpace($dir)) { continue }
        foreach ($ext in @('.exe', '.cmd', '.bat', '')) {
            $c = Join-Path $dir ("$Name$ext")
            if (Test-Path -LiteralPath $c -PathType Leaf) {
                try {
                    $head = Get-Content -LiteralPath $c -TotalCount 8 -Raw -ErrorAction Stop
                    if ($head -like '*agent-tether-shim*') { continue }
                } catch { }
                return $c
            }
        }
    }
    return $null
}

if (-not $Agents) {
    $Agents = @()
    foreach ($name in $known) {
        if (Find-RealBinary $name) { $Agents += $name }
    }
    if (-not $Agents) {
        Write-Host 'No known agent CLIs found on PATH. Pass -Agents explicitly to shim anyway.' -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ''
Write-Host 'will shim:' -ForegroundColor Cyan
foreach ($a in $Agents) {
    $real = Find-RealBinary $a
    if (-not $real) { $real = '(not currently on PATH)' }
    Write-Host ("  {0,-12} -> {1}" -f $a, $real)
}

# --- write shims ----------------------------------------------------------

if (-not (Test-Path $ShimDir)) { New-Item -ItemType Directory -Path $ShimDir -Force | Out-Null }

$launcher = if (Get-Command pwsh -ErrorAction SilentlyContinue) { 'pwsh' } else { 'powershell' }
$router   = Join-Path $InstallDir 'src\tether.ps1'

# The agent name travels in an environment variable, NOT as a parameter.
# PowerShell prefix-matches script parameters, so a caller running
# `claude --version` would have --version matched against -Verbose/-Version
# and rejected as ambiguous before our code ran. Any third-party flag could
# collide. Out-of-band is the only safe channel.
foreach ($a in $Agents) {
    $shimPath = Join-Path $ShimDir "$a.cmd"
    $content = @(
        '@echo off',
        ':: agent-tether-shim - generated, safe to delete',
        (':: routes "{0}" through a durable zellij session' -f $a),
        ('set "TETHER_SHIM_AGENT={0}"' -f $a),
        ('"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" %*' -f $launcher, $router),
        'exit /b %ERRORLEVEL%'
    ) -join "`r`n"
    Set-Content -LiteralPath $shimPath -Value $content -Encoding ASCII
    Write-Host "  wrote $shimPath" -ForegroundColor DarkGray
}

# the management command
$tetherCmd = Join-Path $ShimDir 'tether.cmd'
@(
    '@echo off',
    ':: agent-tether-shim - management entry point',
    'set "TETHER_SHIM_AGENT="',
    ('"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" %*' -f $launcher, $router),
    'exit /b %ERRORLEVEL%'
) -join "`r`n" | Set-Content -LiteralPath $tetherCmd -Encoding ASCII
Write-Host "  wrote $tetherCmd" -ForegroundColor DarkGray

# --- PATH -----------------------------------------------------------------

if ($PathPosition -eq 'none') {
    Write-Host ''
    Write-Host 'PATH untouched (-PathPosition none). Add this yourself when ready:' -ForegroundColor Yellow
    Write-Host "  $ShimDir"
} else {
    $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
    $parts = @($userPath -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $ShimDir.TrimEnd('\')) })
    $parts = if ($PathPosition -eq 'first') { @($ShimDir) + $parts } else { $parts + @($ShimDir) }
    [Environment]::SetEnvironmentVariable('PATH', ($parts -join ';'), 'User')
    Write-Host ''
    Write-Host "PATH updated ($PathPosition). Open a NEW terminal for it to take effect." -ForegroundColor Green
}

Write-Host ''
Write-Host 'Done. Verify with:' -ForegroundColor Cyan
Write-Host '  tether doctor'
Write-Host ''
Write-Host 'To undo:  .\uninstall.ps1'
