<#
.SYNOPSIS
  One-time setup on Windows: checks prerequisites, creates the Python environment, generates passwords,
  builds the extractors. Run from a "Developer PowerShell for VS". Idempotent.
.NOTES
  Install first: .NET 10 SDK, Python 3.10+, Neo4j Community 5.26+ (zip, with Java 21), optionally LLVM 18.
  Neo4j as a Windows service:  <neo4j>\bin\neo4j.bat windows-service install
#>
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot
Set-Location $Here

function Require([string]$Command, [string]$Hint) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { throw "$Command not found: $Hint" }
}
Require "dotnet" "install the .NET 10 SDK (https://dotnet.microsoft.com/download)"
Require "python" "install Python 3.10+ and tick 'Add to PATH'"
if (-not ((dotnet --list-sdks) -match "^10\.")) { throw ".NET 10 SDK not found (dotnet --list-sdks)" }
foreach ($tool in "msbuild", "dumpbin") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Warning "$tool not on PATH: open a 'Developer PowerShell for VS' for the compilation database and dumpbin exports"
    }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== creating .venv"
    python -m venv .venv
}
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -q -e ".[fast,test]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not (Test-Path ".env")) {
    $writer = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    $reader = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    @("# Local secrets for codegraph (git-ignored).", "CODEGRAPH_NEO4J_PASSWORD=cg-$writer", "CODEGRAPH_NEO4J_RO_PASSWORD=ro-$reader") |
        Set-Content -Encoding utf8 ".env"
    Write-Host "== wrote .env with generated Neo4j passwords. Before Neo4j's FIRST start run:"
    Write-Host "   <neo4j>\bin\neo4j-admin.bat dbms set-initial-password cg-$writer"
}

Write-Host "== building extractors"
dotnet build extractors\CodeGraph.sln -c Release -v:quiet -nologo
if ($LASTEXITCODE -ne 0) { throw "building the extractors failed" }

Write-Host ""
Write-Host "Ready. Next: docs\RUNBOOK_WINDOWS.md (copy config\codegraph.yaml to config\codegraph.local.yaml and fill it in)."
Write-Host "MCP: claude mcp add codegraph -- $Here\.venv\Scripts\python.exe -m mcp_server --config $Here\config\codegraph.local.yaml"
