# Run on the WINDOWS PC (not the server).
#
# 1. Runs the incremental fetch + full ladder rebuild, in place inside this repo.
# 2. Commits the refreshed data and pushes, so the server picks it up on its next pull.
#
# The scripts in data/ read and write this repo's own data/ directory, so there's no copy
# step and no second working tree to keep in sync - which is what previously let the
# published JSON drift away from the scripts that built it. To keep the working data
# somewhere else, set $env:DATA_DIR before running.
#
# Precondition: the debug Chrome must already be open and past any Cloudflare check:
#   powershell -File <repo>\data\launch_chrome_debug.ps1
# then leave that window on https://www.aoe2insights.com/ .

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$py   = "python"   # or a full path to the python you use for the scrapers

# What the bot reads, plus the inputs needed to reproduce it. graphs/ is deliberately
# left out - it would add a few hundred KB of binary churn every night.
$paths = @(
    "data/unranked_ladder.json",
    "data/map_elo.json",
    "data/openclosed_elo.json",
    "data/unranked_raw",
    "data/match_performance.json",
    "data/perf_chunks/known_ids.json"
)

Write-Host "== Fetch + rebuild ladder =="
& $py "$repo\data\fetch_incremental_update.py"
if ($LASTEXITCODE -ne 0) { throw "fetch_incremental_update.py failed (exit $LASTEXITCODE). Is the debug Chrome open and past Cloudflare?" }

Write-Host "== Commit + push =="
Set-Location $repo
git add $paths
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No ladder changes since last publish - nothing to push."
} else {
    git commit -m ("ladder update {0:yyyy-MM-dd HH:mm}" -f (Get-Date))
    git push
    Write-Host "Pushed. Server will pull it within its poll interval."
}
