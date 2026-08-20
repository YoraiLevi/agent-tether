#Requires -Version 5.1
<#
.SYNOPSIS
  agent-tether - durable, attachable terminals for agent CLIs.

.DESCRIPTION
  Installed under the NAME of an agent CLI (claude.cmd, codex.cmd, ...), this
  script intercepts every invocation and decides what the caller actually wants.

  It is a ROUTER, not a wrapper. Wrapping everything would be catastrophic:
  `claude -p "summarize"` must return text on stdout. Put a terminal
  multiplexer in the middle of that and the caller gets a TUI instead of an
  answer - and it fails silently, which is the worst way to fail.

  Lanes:
    pass-through : -p/--print, management subcommands, --help/--version,
                   already inside a tethered session, or TETHER_DISABLE=1
    human        : stdout is a TTY -> create-or-attach a durable session
    agent        : no TTY -> create the session DETACHED, print its id, exit 0

.NOTES
  Repo: https://github.com/YoraiLevi/agent-tether
  License: MIT
#>
# NO param block, and no [CmdletBinding()], on purpose.
#
# PowerShell binds `-File script.ps1 <args>` against the script's parameters,
# and prefix-matches them. A caller running `claude --version` would have
# `--version` matched against -Verbose/-Version/... and rejected as ambiguous
# before our code ever ran. Since we shim arbitrary third-party CLIs, ANY flag
# they define could collide with a PowerShell parameter name.
#
# So the caller's arguments are never bound: they land in $args untouched, and
# the agent name arrives out-of-band in an environment variable set by the shim.
$ErrorActionPreference = 'Stop'

$Agent   = $env:TETHER_SHIM_AGENT
$Command = $null
$Rest    = @($args)
if (-not $Agent) {
    $Command = $Rest | Select-Object -First 1
    if ($Rest.Count -gt 1) { $Rest = @($Rest[1..($Rest.Count - 1)]) } else { $Rest = @() }
}
$env:TETHER_SHIM_AGENT = $null

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

$script:InstallDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$script:StateRoot  = if ($env:TETHER_HOME) { $env:TETHER_HOME } else { Join-Path $env:LOCALAPPDATA 'agent-tether' }
$script:SockDir    = Join-Path $script:StateRoot 'sock'
$script:SessionDir = Join-Path $script:StateRoot 'sessions'
$script:LayoutDir  = Join-Path $script:StateRoot 'layouts'
$script:ZellijCfg  = Join-Path $script:InstallDir 'zellij\tether.kdl'
$script:AgentTable = Join-Path $script:InstallDir 'src\agents.json'
$script:ShimMarker = 'agent-tether-shim'

function Initialize-TetherState {
    foreach ($d in @($script:StateRoot, $script:SockDir, $script:SessionDir, $script:LayoutDir)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
}

# ---------------------------------------------------------------------------
# Agent table
# ---------------------------------------------------------------------------

function Get-AgentSpec {
    param([string]$Name)
    if (Test-Path $script:AgentTable) {
        $table = Get-Content -Raw -Path $script:AgentTable | ConvertFrom-Json
        if ($table.agents.PSObject.Properties.Name -contains $Name) { return $table.agents.$Name }
    }
    # Unknown agent: still tetherable, we just cannot choose or resume its id.
    return [pscustomobject]@{
        binary          = $Name
        printFlags      = @('-p', '--print')
        infoFlags       = @('-h', '--help', '-v', '--version')
        subcommands     = @()
        sessionStartSub = @()
        setIdFlag       = $null
        resumeFlag      = $null
        resumeSub       = $null
        continueFlag    = $null
        backgroundFlags = @()
        idIsPath        = $false
    }
}

function Get-SpecList {
    param($Spec, [string]$Field)
    if (-not $Spec.PSObject.Properties.Name.Contains($Field)) { return @() }
    $v = $Spec.$Field
    if ($null -eq $v) { return @() }
    return @($v)
}

function Get-SpecValue {
    param($Spec, [string]$Field)
    if (-not $Spec.PSObject.Properties.Name.Contains($Field)) { return $null }
    return $Spec.$Field
}

# ---------------------------------------------------------------------------
# Chain-through binary resolution
#
# We must never call ourselves. We also must not hardcode a vendor path,
# because other software may legitimately shadow the same executable - the
# whole point is that those shims stay reachable THROUGH us. So: walk PATH in
# order, skip anything carrying our marker, take the first real hit.
# ---------------------------------------------------------------------------

function Test-IsTetherShim {
    param([string]$Path)
    try {
        $text = Get-Content -Path $Path -TotalCount 8 -ErrorAction Stop -Raw
        return ($text -like "*$($script:ShimMarker)*")
    } catch { return $false }
}

function Resolve-NextBinary {
    param([string]$Name, $Spec)

    $envKey = "TETHER_TARGET_$($Name.ToUpper().Replace('-','_'))"
    $override = [Environment]::GetEnvironmentVariable($envKey)
    if ($override) {
        if (Test-Path $override) { return $override }
        throw "$envKey points at a missing file: $override"
    }

    $configured = Get-SpecValue $Spec 'binary'
    if ($configured -and $configured -match '[\\/]' -and (Test-Path $configured)) { return $configured }

    $exts = @('.exe', '.cmd', '.bat', '.ps1', '')
    foreach ($dir in ($env:PATH -split ';')) {
        if ([string]::IsNullOrWhiteSpace($dir)) { continue }
        foreach ($ext in $exts) {
            $candidate = Join-Path $dir ("$Name$ext")
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
            if (($ext -in @('.cmd', '.bat', '.ps1')) -and (Test-IsTetherShim $candidate)) { continue }
            return $candidate
        }
    }
    throw "no '$Name' executable found on PATH behind the agent-tether shim"
}

# ---------------------------------------------------------------------------
# Lane routing
# ---------------------------------------------------------------------------

function Test-HasTty {
    # A human at a terminal has a console attached. A spawned process (pipe,
    # service, CI) does not. This is the load-bearing signal.
    return (-not [Console]::IsOutputRedirected)
}

function Get-Lane {
    param($Spec, [string[]]$Argv)

    if ($env:TETHER_DISABLE -eq '1') { return 'passthrough' }   # escape hatch
    if ($env:TETHER_ACTIVE  -eq '1') { return 'passthrough' }   # recursion guard
    if ($env:ZELLIJ_SESSION_NAME)    { return 'passthrough' }   # already inside one

    $headless    = Get-SpecList $Spec 'headlessFlags'
    $interactive = Get-SpecList $Spec 'interactiveFlags'
    $infoFlags   = Get-SpecList $Spec 'infoFlags'

    # interactiveFlags WIN over headlessFlags. agy's `-i/--prompt-interactive`
    # reads like a headless prompt flag but opens a real session; treating it
    # as headless would leave it untethered.
    $isInteractive = $false
    foreach ($a in $Argv) { if ($interactive -contains $a) { $isInteractive = $true } }

    foreach ($a in $Argv) {
        if ($infoFlags -contains $a) { return 'passthrough' }
        if (-not $isInteractive -and ($headless -contains $a)) { return 'passthrough' }
    }

    # Subcommand handling. Three disjoint sets, because "-p means print" is
    # false often enough to be dangerous:
    #   headlessSubcommands - codex exec, opencode run, droid exec, mimo run
    #   subcommands         - management: claude mcp, codex login, ...
    #   sessionStartSub     - codex resume: a session start, so DO tether it
    $subs         = Get-SpecList $Spec 'subcommands'
    $headlessSubs = Get-SpecList $Spec 'headlessSubcommands'
    $startSubs    = Get-SpecList $Spec 'sessionStartSub'
    $first = $Argv | Where-Object { $_ -and ($_ -notlike '-*') } | Select-Object -First 1
    if ($first) {
        if ($headlessSubs -contains $first) { return 'passthrough' }
        if (($subs -contains $first) -and ($startSubs -notcontains $first)) { return 'passthrough' }
    }

    # Decision 1a: intercept the vendor's own background flag. The result is a
    # background agent you can ATTACH to, which is strictly more capability.
    foreach ($a in $Argv) {
        if ((Get-SpecList $Spec 'backgroundFlags') -contains $a) { return 'agent' }
    }

    if (Test-HasTty) { return 'human' }
    return 'agent'
}

# ---------------------------------------------------------------------------
# Session identity
#
# Decision 2c: naming depends on the lane.
#   - provider session id present -> derive from it, so a vendor-issued resume
#     lands back on the SAME tether session instead of forking a new one
#   - human -> per-directory, so `claude` in a project is idempotent
#   - agent -> fresh uuid, because an orchestrator may want many at once
# ---------------------------------------------------------------------------

function Get-ProvidedSessionId {
    param($Spec, [string[]]$Argv)
    $setId  = Get-SpecValue $Spec 'setIdFlag'
    $resume = Get-SpecValue $Spec 'resumeFlag'
    $sub    = Get-SpecValue $Spec 'resumeSub'
    for ($i = 0; $i -lt $Argv.Count; $i++) {
        $a = $Argv[$i]
        $next = if ($i + 1 -lt $Argv.Count) { $Argv[$i + 1] } else { $null }
        if ($setId  -and $a -eq $setId  -and $next) { return $next }
        if ($resume -and $a -eq $resume -and $next) { return $next }
        if ($sub    -and $a -eq $sub    -and $next) { return $next }
    }
    return $null
}

function Get-Slug {
    param([string]$Text, [int]$Max = 24)
    $s = ($Text -replace '[^A-Za-z0-9]+', '-').Trim('-').ToLower()
    if ($s.Length -gt $Max) { $s = $s.Substring(0, $Max).Trim('-') }
    if (-not $s) { $s = 'x' }
    return $s
}

function Get-ShortHash {
    param([string]$Text, [int]$Len = 8)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text.ToLower()))
        $hex = -join ($bytes | ForEach-Object { $_.ToString('x2') })
        return $hex.Substring(0, $Len)
    } finally { $sha.Dispose() }
}

function New-SessionName {
    param([string]$Name, [string]$Lane, [string]$ProvidedId, [string]$Cwd)
    if ($ProvidedId) { return "tether-$Name-$(Get-ShortHash $ProvidedId 12)" }
    if ($Lane -eq 'human') {
        $leaf = Split-Path -Leaf $Cwd
        return "tether-$Name-$(Get-Slug $leaf)-$(Get-ShortHash $Cwd 8)"
    }
    return "tether-$Name-$((New-Guid).ToString('N').Substring(0, 12))"
}

# ---------------------------------------------------------------------------
# Registry
#
# One file per session, so concurrent spawns never contend for a lock.
# This is also where we stash the CALLER's environment. Orca (and anything
# like it) identifies a pane by env vars planted at spawn time; lose them and
# its telemetry loses pane attribution and the sidebar goes blank.
# ---------------------------------------------------------------------------

# ORCHESTRATOR identity only. Deliberately NOT vendor session vars such as
# CLAUDE_CODE_*: those describe the conversation of whoever launched us, and
# carrying them into a NEW agent would make the child believe it is part of the
# parent's session. Orca guards the same way - it deletes pane identity from
# inherited env unless a caller supplies it explicitly.
$script:CarryEnvPrefixes = @('ORCA_', 'CONDUCTOR_', 'GHOSTX_', 'TETHER_CARRY_')

function Get-CarriedEnv {
    $out = @{}
    foreach ($e in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        foreach ($p in $script:CarryEnvPrefixes) {
            if ([string]$e.Key -like "$p*") { $out[[string]$e.Key] = [string]$e.Value; break }
        }
    }
    return $out
}

function Save-SessionRecord {
    param([hashtable]$Record)
    Initialize-TetherState
    $path = Join-Path $script:SessionDir "$($Record.session).json"
    ($Record | ConvertTo-Json -Depth 6) | Set-Content -Path $path -Encoding UTF8
}

function Get-SessionRecord {
    param([string]$Session)
    $path = Join-Path $script:SessionDir "$Session.json"
    if (Test-Path $path) { return (Get-Content -Raw $path | ConvertFrom-Json) }
    return $null
}

function Get-AllSessionRecords {
    if (-not (Test-Path $script:SessionDir)) { return @() }
    return @(Get-ChildItem -Path $script:SessionDir -Filter *.json -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Content -Raw $_.FullName | ConvertFrom-Json })
}

# ---------------------------------------------------------------------------
# Zellij plumbing
# ---------------------------------------------------------------------------

function ConvertTo-KdlString {
    param([string]$Value)
    $escaped = $Value.Replace('\', '\\').Replace('"', '\"')
    return '"' + $escaped + '"'
}

function New-GeneratedLayout {
    param([string]$Session, [string]$Binary, [string[]]$Argv, [string]$Cwd)
    Initialize-TetherState
    $lines = @()
    $lines += '// generated by agent-tether - do not edit by hand'
    $lines += 'layout {'
    $lines += "    cwd $(ConvertTo-KdlString $Cwd)"
    $lines += "    pane command=$(ConvertTo-KdlString $Binary) {"
    if ($Argv -and $Argv.Count -gt 0) {
        $quoted = ($Argv | ForEach-Object { ConvertTo-KdlString $_ }) -join ' '
        $lines += "        args $quoted"
    }
    $lines += '    }'
    $lines += '}'
    $path = Join-Path $script:LayoutDir "$Session.kdl"
    ($lines -join "`r`n") | Set-Content -Path $path -Encoding UTF8
    return $Session
}

function Get-ZellijOptionArgs {
    param([string]$LayoutName)
    return @(
        'options',
        '--default-layout',    $LayoutName,
        '--layout-dir',        $script:LayoutDir,
        '--show-startup-tips', 'false'
    )
}

function Set-CarriedEnv {
    param($EnvMap)
    if (-not $EnvMap) { return }
    if ($EnvMap -is [System.Management.Automation.PSCustomObject]) {
        foreach ($p in $EnvMap.PSObject.Properties) {
            [Environment]::SetEnvironmentVariable($p.Name, [string]$p.Value)
        }
    } else {
        foreach ($k in $EnvMap.Keys) { [Environment]::SetEnvironmentVariable($k, [string]$EnvMap[$k]) }
    }
}

function Get-LiveSessions {
    <#
      `zellij ls -s` is NOT a liveness check. It lists resurrectable sessions
      too - and on Windows the resurrection cache lives under %LOCALAPPDATA%
      and cannot be redirected, so sessions from OTHER socket namespaces leak
      into this listing as well. Using it would report a dead session, or
      somebody else's session, as live.

      Only a session with a socket marker in OUR socket dir is actually live.
      We still call `zellij ls` first, because that is what probes each socket
      and reaps the stale markers a crash leaves behind.
    #>
    $env:ZELLIJ_SOCKET_DIR = $script:SockDir
    & zellij ls 2>&1 | Out-Null
    $markerDir = Join-Path $script:SockDir 'contract_version_1'
    if (-not (Test-Path $markerDir)) { return @() }
    return @(Get-ChildItem -LiteralPath $markerDir -File -ErrorAction SilentlyContinue |
             ForEach-Object { $_.Name })
}

function Test-SessionLive {
    param([string]$Session)
    return ((Get-LiveSessions) -contains $Session)
}

function Test-SessionRecoverable {
    param([string]$Session)
    $env:ZELLIJ_SOCKET_DIR = $script:SockDir
    $raw = & zellij ls 2>$null
    if (-not $raw) { return $false }
    $plain = ($raw -join "`n") -replace "`e\[[0-9;]*m", ''
    foreach ($line in ($plain -split "`n")) {
        if ($line -match "^\s*$([regex]::Escape($Session))\s" -and $line -match 'EXITED') { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------

function Get-RawTail {
    <#
      Recover the caller's ORIGINAL argument text.

      A .cmd shim hands %* to PowerShell, which re-parses it. That round trip
      mangles quoting - and the lane where quoting matters most is exactly the
      one we must not break: `claude -p "a long prompt"`. So for pass-through
      we do not reconstruct arguments from the parsed array at all. We take the
      raw text after our `--` marker straight off this process's command line
      and hand it to the child verbatim.
    #>
    $cl = [Environment]::CommandLine
    $idx = $cl.IndexOf(' -- ')
    if ($idx -lt 0) { return '' }
    return $cl.Substring($idx + 4)
}

function Invoke-PassThrough {
    param([string]$Binary, [string[]]$Argv)
    $env:TETHER_ACTIVE = '1'

    $raw = Get-RawTail
    if ([string]::IsNullOrEmpty($raw)) {
        & $Binary @Argv
        exit $LASTEXITCODE
    }

    # UseShellExecute=$false inherits our stdin/stdout/stderr handles, so the
    # child talks to the caller directly - no buffering, no TTY loss, and the
    # Arguments string is passed through without being re-quoted.
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName        = $Binary
    $psi.Arguments       = $raw
    $psi.UseShellExecute = $false
    $psi.WorkingDirectory = (Get-Location).Path
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()
    exit $proc.ExitCode
}

function Start-TetheredSession {
    param(
        [string]$Session, [string]$Binary, [string[]]$Argv,
        [string]$Cwd, [switch]$Detached
    )
    Initialize-TetherState
    $layout = New-GeneratedLayout -Session $Session -Binary $Binary -Argv $Argv -Cwd $Cwd
    $env:ZELLIJ_SOCKET_DIR = $script:SockDir
    $env:TETHER_ACTIVE = '1'
    $createFlag = if ($Detached) { '-b' } else { '-c' }
    $zargs = @('--config', $script:ZellijCfg, 'attach', $createFlag, $Session) + (Get-ZellijOptionArgs $layout)
    & zellij @zargs
    return $LASTEXITCODE
}

function Join-TetheredSession {
    param([string]$Session, [switch]$ForceRunCommands)
    $env:ZELLIJ_SOCKET_DIR = $script:SockDir
    $env:TETHER_ACTIVE = '1'
    $zargs = @('--config', $script:ZellijCfg, 'attach', $Session)
    if ($ForceRunCommands) { $zargs += '--force-run-commands' }
    & zellij @zargs
    return $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# Shim entry point
# ---------------------------------------------------------------------------

function Invoke-Shim {
    param([string]$Name, [string[]]$Argv)

    $spec   = Get-AgentSpec $Name
    $binary = Resolve-NextBinary -Name $Name -Spec $spec
    $lane   = Get-Lane -Spec $spec -Argv $Argv

    if ($lane -eq 'passthrough') { Invoke-PassThrough -Binary $binary -Argv $Argv }

    Initialize-TetherState
    $cwd        = (Get-Location).Path
    $providedId = Get-ProvidedSessionId -Spec $spec -Argv $Argv
    $session    = New-SessionName -Name $Name -Lane $lane -ProvidedId $providedId -Cwd $cwd

    # If this exact session is already live, just join it. This is what makes
    # `claude` idempotent per directory, and what makes a vendor-issued
    # `--resume <id>` land back on the surviving session rather than fork one.
    if (Test-SessionLive $session) {
        if ($lane -eq 'human') {
            Write-TetherHint $session
            exit (Join-TetheredSession -Session $session)
        }
        Write-Output $session
        exit 0
    }

    # Not live but recoverable -> resume rather than start something empty.
    if (Test-SessionRecoverable $session) {
        $rec = Get-SessionRecord $session
        if ($rec) { Set-CarriedEnv $rec.env }
        if ($lane -eq 'human') {
            Write-TetherHint $session
            exit (Join-TetheredSession -Session $session -ForceRunCommands)
        }
        $null = Join-TetheredSession -Session $session -ForceRunCommands
        Write-Output $session
        exit 0
    }

    # Fresh session. Mint a provider session id when the vendor lets us choose
    # one, so a single identifier names BOTH layers - the tether session and
    # the agent's own conversation. Without this, a restored terminal comes
    # back running a brand-new empty agent, which looks like success.
    $launchArgv = @($Argv)
    $setIdFlag  = Get-SpecValue $spec 'setIdFlag'
    if ($setIdFlag -and -not $providedId) {
        $providedId = (New-Guid).ToString()
        $launchArgv = @($setIdFlag, $providedId) + $launchArgv
        $session    = New-SessionName -Name $Name -Lane $lane -ProvidedId $providedId -Cwd $cwd
    }

    Save-SessionRecord @{
        session           = $session
        agent             = $Name
        binary            = $binary
        cwd               = $cwd
        lane              = $lane
        providerSessionId = $providedId
        argv              = $launchArgv
        env               = Get-CarriedEnv
        createdAt         = (Get-Date).ToString('o')
    }

    if ($lane -eq 'human') {
        Write-TetherHint $session
        exit (Start-TetheredSession -Session $session -Binary $binary -Argv $launchArgv -Cwd $cwd)
    }

    $null = Start-TetheredSession -Session $session -Binary $binary -Argv $launchArgv -Cwd $cwd -Detached
    # Lane B contract: exactly one line on stdout, the session id, so a
    # spawning agent can parse it without heuristics.
    Write-Output $session
    exit 0
}

function Write-TetherHint {
    param([string]$Session)
    if ($env:TETHER_QUIET -eq '1') { return }
    Write-Host "[tether] $Session  -  Ctrl q to detach, session keeps running" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Management commands
# ---------------------------------------------------------------------------

function Invoke-Ls {
    Initialize-TetherState
    $live    = Get-LiveSessions
    $records = Get-AllSessionRecords
    # @( ) matters: a single record would otherwise come back as a bare object
    # and `+=` would fail with op_Addition.
    $rows = @(foreach ($r in $records) {
        $state = if ($live -contains $r.session) { 'live' }
                 elseif (Test-SessionRecoverable $r.session) { 'recoverable' }
                 else { 'gone' }
        [pscustomobject]@{
            State   = $state
            Agent   = $r.agent
            Session = $r.session
            Lane    = $r.lane
            Project = (Split-Path -Leaf $r.cwd)
            Created = $r.createdAt
        }
    })
    # A live session with no record (created outside tether) still deserves a row.
    $known = @($records | ForEach-Object { $_.session })
    foreach ($s in $live) {
        if ($known -notcontains $s) {
            $rows += [pscustomobject]@{
                State = 'live'; Agent = '?'; Session = $s
                Lane = '?'; Project = '?'; Created = ''
            }
        }
    }
    if (-not $rows) { Write-Host 'no tethered sessions'; return }
    $rows | Sort-Object State, Agent, Session | Format-Table -AutoSize
}

function Invoke-Attach {
    param([string]$Session)
    if (-not $Session) { throw 'usage: tether attach <session>' }
    if (Test-SessionLive $Session) { exit (Join-TetheredSession -Session $Session) }
    if (Test-SessionRecoverable $Session) {
        $rec = Get-SessionRecord $Session
        if ($rec) { Set-CarriedEnv $rec.env }
        exit (Join-TetheredSession -Session $Session -ForceRunCommands)
    }
    throw "no live or recoverable session named '$Session'"
}

function Get-ResumeArgv {
    <#
      Build the argv that makes a vendor rehydrate its OWN transcript.

      This deliberately does NOT reuse the original launch argv. Replaying it
      is the silent-empty-restore trap: grok's own help says --session-id
      "Does not resume existing sessions", and gemini exits fatally on a
      duplicate. A restored terminal running a brand-new empty agent looks
      exactly like success, which is why we rebuild rather than replay.

      Returns $null when we cannot restore the CONVERSATION - the caller then
      falls back to restoring only the terminal, and says so out loud.
    #>
    param($Spec, [string]$ProviderSessionId)
    if (-not $ProviderSessionId) { return $null }

    $resumeSub  = Get-SpecValue $Spec 'resumeSub'
    if ($resumeSub) { return @($resumeSub, $ProviderSessionId) }

    $resumeFlag = Get-SpecValue $Spec 'resumeFlag'
    if ($resumeFlag) { return @($resumeFlag, $ProviderSessionId) }

    return $null
}

function Invoke-Restore {
    # After a host crash, bring every recoverable session back DETACHED.
    # Detached on purpose: restoring should not seize a terminal, and you may
    # be restoring a dozen at once.
    Initialize-TetherState
    $records = Get-AllSessionRecords
    $conversation = 0; $terminalOnly = 0; $already = 0; $skipped = 0

    foreach ($r in $records) {
        if (Test-SessionLive $r.session) { $already++; continue }

        Set-CarriedEnv $r.env
        $env:ZELLIJ_SOCKET_DIR = $script:SockDir
        $env:TETHER_ACTIVE = '1'

        $spec = Get-AgentSpec $r.agent
        $resumeArgv = Get-ResumeArgv -Spec $spec -ProviderSessionId $r.providerSessionId

        if ($resumeArgv) {
            # Rebuild: correct conversation. The old session's scrollback is
            # NOT carried across - a correct transcript beats a pretty terminal.
            & zellij delete-session $r.session 2>&1 | Out-Null
            $null = Start-TetheredSession -Session $r.session -Binary $r.binary `
                        -Argv $resumeArgv -Cwd $r.cwd -Detached
            Write-Host "restored  $($r.session)  [$($r.agent)]  conversation + terminal" -ForegroundColor Green
            $conversation++
            continue
        }

        if (Test-SessionRecoverable $r.session) {
            # No id or no resume form: zellij can still bring the TERMINAL
            # back, but the agent inside will be a new, empty one. Say so.
            & zellij --config $script:ZellijCfg attach -b $r.session --force-run-commands 2>&1 | Out-Null
            Write-Host "restored  $($r.session)  [$($r.agent)]  TERMINAL ONLY - agent starts fresh" -ForegroundColor Yellow
            $terminalOnly++
            continue
        }

        Write-Host "skip      $($r.session)  (nothing to resurrect)" -ForegroundColor DarkYellow
        $skipped++
    }

    Write-Host ''
    Write-Host "$conversation with conversation, $terminalOnly terminal-only, $already already live, $skipped skipped"
    if ($terminalOnly -gt 0) {
        Write-Host ''
        Write-Host 'Terminal-only restores happen when the vendor never told us its session id.' -ForegroundColor DarkGray
        Write-Host 'Only claude, grok, gemini and pi let us choose the id up front. See docs/DESIGN.md.' -ForegroundColor DarkGray
    }
}

function Invoke-Reap {
    # Decision 3c: serialization stays ON so crashes are survivable, and a
    # reaper clears finished sessions so listings do not become a graveyard.
    param([switch]$All)
    Initialize-TetherState
    $live = Get-LiveSessions
    $n = 0
    foreach ($r in Get-AllSessionRecords) {
        if ($live -contains $r.session) { continue }
        if (-not $All -and (Test-SessionRecoverable $r.session)) { continue }
        $env:ZELLIJ_SOCKET_DIR = $script:SockDir
        & zellij delete-session $r.session 2>&1 | Out-Null
        Remove-Item -LiteralPath (Join-Path $script:SessionDir "$($r.session).json") -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $script:LayoutDir  "$($r.session).kdl")  -Force -ErrorAction SilentlyContinue
        Write-Host "reaped  $($r.session)"
        $n++
    }
    Write-Host "$n reaped"
}

function Invoke-Kill {
    param([string]$Session)
    if (-not $Session) { throw 'usage: tether kill <session>' }
    $env:ZELLIJ_SOCKET_DIR = $script:SockDir
    & zellij kill-session $Session 2>&1 | Out-Null
    & zellij delete-session $Session 2>&1 | Out-Null
    Remove-Item -LiteralPath (Join-Path $script:SessionDir "$Session.json") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $script:LayoutDir  "$Session.kdl")  -Force -ErrorAction SilentlyContinue
    Write-Host "killed $Session"
}

function Invoke-Doctor {
    Write-Host 'agent-tether doctor'
    Write-Host '-------------------'
    Write-Host "install dir : $($script:InstallDir)"
    Write-Host "state dir   : $($script:StateRoot)"
    Write-Host "socket dir  : $($script:SockDir)"
    Write-Host "zellij cfg  : $($script:ZellijCfg)  $(if (Test-Path $script:ZellijCfg) {'OK'} else {'MISSING'})"
    Write-Host "agent table : $($script:AgentTable)  $(if (Test-Path $script:AgentTable) {'OK'} else {'MISSING'})"

    $z = Get-Command zellij -ErrorAction SilentlyContinue
    if ($z) { Write-Host "zellij      : $($z.Source)  $(& zellij --version)" }
    else    { Write-Host 'zellij      : NOT FOUND on PATH' -ForegroundColor Red }

    Write-Host ''
    Write-Host 'shims and what they chain to:'
    if (Test-Path $script:AgentTable) {
        $table = Get-Content -Raw $script:AgentTable | ConvertFrom-Json
        foreach ($name in $table.agents.PSObject.Properties.Name) {
            $shim = (Get-Command "$name" -ErrorAction SilentlyContinue).Source
            $isShim = if ($shim -and (Test-IsTetherShim $shim)) { 'tethered' } else { 'not shimmed' }
            $target = try { Resolve-NextBinary -Name $name -Spec (Get-AgentSpec $name) } catch { '(none found)' }
            Write-Host ("  {0,-12} {1,-12} -> {2}" -f $name, $isShim, $target)
        }
    }
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

$argv = @()
if ($Rest) { $argv = @($Rest | Where-Object { $_ -ne '--' }) }

if ($Agent) { Invoke-Shim -Name $Agent -Argv $argv; exit 0 }

switch ($Command) {
    'ls'      { Invoke-Ls; break }
    'attach'  { Invoke-Attach -Session ($argv | Select-Object -First 1); break }
    'restore' { Invoke-Restore; break }
    'reap'    { Invoke-Reap -All:($argv -contains '--all'); break }
    'kill'    { Invoke-Kill -Session ($argv | Select-Object -First 1); break }
    'doctor'  { Invoke-Doctor; break }
    default {
        Write-Host 'agent-tether - durable, attachable terminals for agent CLIs'
        Write-Host ''
        Write-Host 'usage: tether <command>'
        Write-Host '  ls                 list tethered sessions and their state'
        Write-Host '  attach <session>   attach, resurrecting first if needed'
        Write-Host '  restore            bring every recoverable session back, detached'
        Write-Host '  reap [--all]       delete finished sessions (--all includes recoverable)'
        Write-Host '  kill <session>     end a session and forget it'
        Write-Host '  doctor             show install state and what each shim chains to'
    }
}

