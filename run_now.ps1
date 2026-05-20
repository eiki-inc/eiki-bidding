param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

& $Python `
    (Join-Path $Root "scripts\collect_bids.py") `
    --config (Join-Path $Root "config\sources.json") `
    --output (Join-Path $Root "data\bids.json") `
    --csv (Join-Path $Root "data\bids.csv") `
    --log (Join-Path $Root "data\collector.log")
