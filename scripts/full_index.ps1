<#
.SYNOPSIS
  Full rebuild (SPEC 15): extract everything -> merge -> import -> derive -> verify counts.
.EXAMPLE
  scripts\full_index.ps1 -Config config\codegraph.local.yaml
.NOTES
  Run from a "Developer PowerShell for VS" so dumpbin and the MSVC include paths (INCLUDE) are available.
  Build the solution first: the bridge reads the built C++/CLI DLLs and dumpbin reads the native DLLs.
#>
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [switch]$AllowCompileErrors,
    [switch]$AllowMissingBridge
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot
$Py = if ($env:CODEGRAPH_PYTHON) { $env:CODEGRAPH_PYTHON }
      elseif ($IsLinux -or $IsMacOS) { Join-Path $Here ".venv/bin/python" }
      else { Join-Path $Here ".venv/Scripts/python.exe" }
$Bin = Join-Path $Here "extractors"
$Clock = [Diagnostics.Stopwatch]::StartNew()

# Runs one step, shows its output, and returns only its exit code (allowed codes in -Ok).
function Invoke-Step([string]$Name, [scriptblock]$Command, [int[]]$Ok = @(0)) {
    Write-Host "== $Name"
    & $Command | Out-Host
    if ($Ok -notcontains $LASTEXITCODE) { throw "$Name failed with exit code $LASTEXITCODE" }
    return $LASTEXITCODE
}

Invoke-Step "1/6 build extractors" { dotnet build (Join-Path $Bin "CodeGraph.sln") -c Release -v:quiet -nologo } | Out-Null

$roslyn = Invoke-Step "2/6 C# (Roslyn)" { dotnet (Join-Path $Bin "CodeGraph.Roslyn/bin/Release/net10.0/CodeGraph.Roslyn.dll") --config $Config } -Ok @(0, 3)
if ($roslyn -eq 3 -and -not $AllowCompileErrors) {
    throw "compile-error gate failed (see out\csharp\manifest.json); rerun with -AllowCompileErrors to continue"
}

$Compdb = & $Py -c "import sys; from codegraph_common.config import Config; c=Config(sys.argv[1]); print(c.resolve(c.native['compdb']))" $Config
if (Test-Path $Compdb) {
    Invoke-Step "3/6 native C++ (libclang)" { & $Py -m codegraph_native --config $Config } | Out-Null
} else {
    Write-Host "== 3/6 native C++: no compilation database at $Compdb (run extractors\native\make_compdb.ps1)"
}

Invoke-Step "4/6 C++/CLI bridge" { dotnet (Join-Path $Bin "CodeGraph.Bridge/bin/Release/net10.0/CodeGraph.Bridge.dll") --config $Config } | Out-Null

$loaderArgs = @("-m", "loader", "full", "--config", $Config)
if ($AllowMissingBridge) { $loaderArgs += "--allow-missing-bridge" }
Invoke-Step "5/6 merge + import + derive" { & $Py @loaderArgs } | Out-Null
Invoke-Step "    read-only user" { & $Py -m loader users --config $Config } | Out-Null

Invoke-Step "6/6 verify" { & $Py -m loader status --config $Config } | Out-Null
Write-Host ("full index finished in {0:N0}s" -f $Clock.Elapsed.TotalSeconds)
