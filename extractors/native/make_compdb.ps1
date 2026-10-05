<#
.SYNOPSIS
  Writes compile_commands.json for the C++ projects of a Visual Studio solution (SPEC 6.1).
.DESCRIPTION
  MSBuild does not produce a compilation database, so this script rebuilds the C++ projects with an MSBuild
  logger (CompileCommandsLogger, in this folder) that records every CL.exe command line. Build output goes
  to a scratch folder (-Scratch), so your normal bin/obj folders are not touched.

  Alternatives (SPEC 6.1 options 1 and 3), if this does not suit the codebase:
    * microsoft/msbuild-extractor-sample:  -Method extractor -ExtractorPath <MsBuildExtractor.exe>
    * any compile_commands.json you already have: point native.compdb at it in codegraph.yaml.
  Record which one works in docs/DECISIONS.md (VERIFY D-12).
.EXAMPLE
  extractors\native\make_compdb.ps1 -Solution D:\src\myproduct\Main.sln -Out out\compile_commands.json
.NOTES
  Run from a "Developer PowerShell for VS" (msbuild, cl.exe and INCLUDE on the environment).
#>
param(
    [Parameter(Mandatory = $true)][string]$Solution,
    [string]$Out = "out\compile_commands.json",
    [string]$Configuration = "Release",
    [string]$Platform = "x64",
    [string]$Scratch = (Join-Path $env:TEMP "codegraph-compdb"),
    [ValidateSet("logger", "extractor")][string]$Method = "logger",
    [string]$ExtractorPath = ""
)
$ErrorActionPreference = "Stop"
$OutFull = [IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force -Path (Split-Path $OutFull) | Out-Null

if ($Method -eq "extractor") {
    if (-not (Test-Path $ExtractorPath)) { throw "-ExtractorPath must point to the msbuild-extractor-sample executable" }
    # VERIFY: argument names against the extractor's --help on your machine.
    & $ExtractorPath --solution $Solution --configuration $Configuration --platform $Platform --output $OutFull
    if ($LASTEXITCODE -ne 0) { throw "msbuild-extractor-sample failed" }
    exit 0
}

# Build the logger once (netstandard2.0, loads into Visual Studio's msbuild.exe).
$LoggerProject = Join-Path $PSScriptRoot "CompileCommandsLogger/CompileCommandsLogger.csproj"
dotnet build $LoggerProject -c Release -v:quiet -nologo
if ($LASTEXITCODE -ne 0) { throw "building CompileCommandsLogger failed" }
$Logger = Join-Path $PSScriptRoot "CompileCommandsLogger/bin/Release/netstandard2.0/CompileCommandsLogger.dll"

# Rebuild so every translation unit is compiled (and therefore logged) - into a scratch folder.
$Scratch = [IO.Path]::GetFullPath($Scratch)
msbuild $Solution /t:Rebuild /restore /m /v:minimal `
    "/p:Configuration=$Configuration" "/p:Platform=$Platform" `
    "/p:OutDir=$Scratch\bin\" "/p:IntDir=$Scratch\obj\`$(MSBuildProjectName)\" `
    "-logger:$Logger;out=$OutFull"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "msbuild reported errors; compile_commands.json still lists every compile that started"
}
Write-Host "compile_commands.json: $OutFull"
