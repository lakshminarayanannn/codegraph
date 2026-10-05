<#
.SYNOPSIS
  Builds the MiniPlant fixture on Windows with Visual Studio's MSBuild: native C++, the real C++/CLI wrapper
  and the C# projects (the C# stand-in for C++/CLI is not used on Windows), then the compilation database.
.NOTES
  Run from a "Developer PowerShell for VS 2022" (C++ desktop workload + C++/CLI support installed).
#>
param([string]$Fixture = (Join-Path (Split-Path -Parent $PSScriptRoot) "fixtures\MiniPlant"))
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot

msbuild (Join-Path $Fixture "MiniPlant.sln") /restore /p:Configuration=Debug /p:Platform=x64 /m /v:minimal
if ($LASTEXITCODE -ne 0) { throw "fixture build failed" }

& (Join-Path $Here "extractors\native\make_compdb.ps1") -Solution (Join-Path $Fixture "MiniPlant.sln") `
    -Out (Join-Path $Here "out\fixture\compile_commands.json") -Configuration Debug -Platform x64
