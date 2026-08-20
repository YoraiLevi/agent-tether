#Requires -Version 5.1
<#
.SYNOPSIS
  Remove agent-tether shims and, optionally, its state.

.DESCRIPTION
  Reverses install.ps1 exactly: deletes the generated shims, removes the shim
  directory from user PATH, and leaves every real agent CLI untouched (they
  were never modified - the shims only ever worked by PATH precedence).

  Running sessions are NOT killed by default. They are ordinary zellij
  sessions and keep running; you can still reach them with
  `zellij --config <repo>\zellij\tether.kdl attach <name>` after uninstalling,
  as long as ZELLIJ_SOCKET_DIR points at the tether socket directory.
  Use -PurgeState to delete them and the registry as well.

.PARAMETER ShimDir
  Where the shims were installed. Default: %LOCALAPPDATA%\agent-tether\bin

.PARAMETER PurgeState
  Also kill tethered sessions and delete the state directory
  (%LOCALAPPDATA%\agent-tether or $env:TETHER_HOME). Destructive.

.EXAMPLE
  .\uninstall.ps1
  .\uninstall.ps1 -PurgeState
#>
[CmdletBinding()]
param(
    [string]$ShimDir,
    [switch]$PurgeState
)

$ErrorActionPreference = 'Stop'

if (-not $ShimDir) { $ShimDir = Join-Path $env:LOCALAPPDATA 'agent-tether\bin' }
$StateRoot = if ($env:TETHER_HOME) { $env:TETHER_HOME } else { Join-Path $env:LOCALAPPDATA 'agent-tether' }

Write-Host 'agent-tether uninstaller' -ForegroundColor Cyan
Write-Host '========================'
Write-Host "shim dir : $ShimDir"
Write-Host ''

# --- remove shims ---------------------------------------------------------

if (Test-Path $ShimDir) {
    $removed = 0
    Get-ChildItem -LiteralPath $ShimDir -Filter *.cmd -ErrorAction SilentlyContinue | ForEach-Object {
        $head = ''
        try { $head = Get-Content -LiteralPath $_.FullName -TotalCount 8 -Raw -ErrorAction Stop } catch { }
        if ($head -like '*agent-tether-shim*') {
            Remove-Item -LiteralPath $_.FullName -Force
            Write-Host "  removed $($_.Name)" -ForegroundColor DarkGray
            $removed++
        } else {
            Write-Host "  kept    $($_.Name)  (not ours)" -ForegroundColor Yellow
        }
    }
    Write-Host "$removed shim(s) removed"
    if (-not (Get-ChildItem -LiteralPath $ShimDir -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $ShimDir -Force
        Write-Host "  removed empty $ShimDir" -ForegroundColor DarkGray
    }
} else {
    Write-Host 'shim dir does not exist, nothing to remove'
}

# --- PATH -----------------------------------------------------------------

$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($userPath) {
    $parts = @($userPath -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $ShimDir.TrimEnd('\')) })
    $new = $parts -join ';'
    if ($new -ne $userPath) {
        [Environment]::SetEnvironmentVariable('PATH', $new, 'User')
        Write-Host 'removed shim dir from user PATH' -ForegroundColor Green
    } else {
        Write-Host 'shim dir was not on user PATH'
    }
}

# --- state ----------------------------------------------------------------

if ($PurgeState) {
    Write-Host ''
    Write-Host 'purging state (this kills tethered sessions)' -ForegroundColor Yellow
    $env:ZELLIJ_SOCKET_DIR = Join-Path $StateRoot 'sock'
    $sessions = & zellij ls -s 2>$null
    foreach ($s in @($sessions)) {
        $s = "$s".Trim()
        if (-not $s) { continue }
        & zellij kill-session $s 2>&1 | Out-Null
        & zellij delete-session $s 2>&1 | Out-Null
        Write-Host "  killed $s"
    }
    if (Test-Path $StateRoot) {
        Remove-Item -LiteralPath $StateRoot -Recurse -Force
        Write-Host "  removed $StateRoot"
    }
} else {
    Write-Host ''
    Write-Host "state kept at: $StateRoot" -ForegroundColor DarkGray
    Write-Host 'Running sessions are untouched. Re-run with -PurgeState to delete them.'
}

Write-Host ''
Write-Host 'Done. Open a NEW terminal for the PATH change to take effect.' -ForegroundColor Green
