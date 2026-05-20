param(
    [int]$Port = 8000,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Open http://localhost:$Port/ . Press Ctrl+C to stop."
& $Python -m http.server $Port
