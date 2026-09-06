# Run on the WINDOWS PC (not the server).
#
# 1. Runs the incremental fetch, which chains into run_full_refresh.py and
#    rebuilds the ladder, per-map Elo, open/closed Elo and the graphs in place.
# 2. Commits and pushes, so the server picks it all up on its next pull.
#
# Everything happens inside this repo - there is no separate working copy.
#
# Precondition: the debug Chrome must already be open and past any Cloudflare
# check:  powershell -File <repo>\data\launch_chrome_debug.ps1
# then leave that window on https://www.aoe2insights.com/ .

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$data = Join-Path $repo "data"
$py   = "python"   # or a full path to the python you use for the scrapers

Write-Host "== Fetch + rebuild ladder (in place) =="
& $py (Join-Path $data "fetch_incremental_update.py")
if ($LASTEXITCODE -ne 0) {
    throw "fetch_incremental_update.py failed (exit $LASTEXITCODE). Is the debug Chrome open and past Cloudflare?"
}

Write-Host "== Commit + push =="
Set-Location $repo
git add data
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No changes since last publish - nothing to push."
} else {
    git commit -m ("ladder update {0:yyyy-MM-dd HH:mm}" -f (Get-Date))
    git push
    Write-Host "Pushed. Server will pull it within its poll interval."
}
