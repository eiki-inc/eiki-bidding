param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

& $Python `
    (Join-Path $Root "scripts\collect_bids.py") `
    --config (Join-Path $Root "config\sources.json") `
    --url-config (Join-Path $Root "config\target_urls.json") `
    --output (Join-Path $Root "data\bids.json") `
    --log (Join-Path $Root "data\collector.log")
