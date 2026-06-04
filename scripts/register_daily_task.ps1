param(
    [string]$TaskName = "PublicBidCollector",
    [string]$Time = "01:00",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $Root "scripts\collect_bids.py"
$ConfigPath = Join-Path $Root "config\sources.json"
$UrlConfigPath = Join-Path $Root "config\target_urls.json"
$OutputPath = Join-Path $Root "data\bids.json"
$LogPath = Join-Path $Root "data\collector.log"

$Argument = @(
    "`"$ScriptPath`"",
    "--config", "`"$ConfigPath`"",
    "--url-config", "`"$UrlConfigPath`"",
    "--output", "`"$OutputPath`"",
    "--log", "`"$LogPath`""
) -join " "

$Action = New-ScheduledTaskAction -Execute $Python -Argument $Argument -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 55)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Collect public procurement bid data daily and update data\bids.json for the HTML dashboard." `
    -Force | Out-Null

Write-Host "Registered: $TaskName / daily $Time"
