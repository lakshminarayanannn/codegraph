<#
.SYNOPSIS
  Incremental update (SPEC 8.3): re-extract changed projects/modules and patch the graph in place.
.EXAMPLE
  scripts\incremental_index.ps1 -Config config\codegraph.local.yaml
  scripts\incremental_index.ps1 -Config config\codegraph.local.yaml -DryRun
#>
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot
$Py = if ($env:CODEGRAPH_PYTHON) { $env:CODEGRAPH_PYTHON }
      elseif ($IsLinux -or $IsMacOS) { Join-Path $Here ".venv/bin/python" }
      else { Join-Path $Here ".venv/Scripts/python.exe" }

dotnet build (Join-Path $Here "extractors/CodeGraph.sln") -c Release -v:quiet -nologo
if ($LASTEXITCODE -ne 0) { throw "building the extractors failed" }

$loaderArgs = @("-m", "loader", "incremental", "--config", $Config)
if ($DryRun) { $loaderArgs += "--dry-run" }
& $Py @loaderArgs
if ($LASTEXITCODE -ne 0) { throw "incremental update failed with exit code $LASTEXITCODE" }
