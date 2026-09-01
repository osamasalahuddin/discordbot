# Run on the WINDOWS PC (not the server).
#
# 1. Runs the incremental fetch + full ladder rebuild.
# 2. Copies the bot-relevant outputs into this git repo.
# 3. Commits and pushes, so the server picks them up on its next pull.
#
# Precondition: the debug Chrome must already be open and past any Cloudflare
# check:  powershell -File E:\Work\Claude\data\launch_chrome_debug.ps1
# then leave that window on https://www.aoe2insights.com/ .

$ErrorActionPreference = "Stop"

$src  = "E:\Work\Claude\data"
$repo = "E:\Work\Claude\discordbot"
$py   = "python"   # or a full path to the python you use for the scrapers

$files = @("unranked_ladder.json", "map_elo.json", "openclosed_elo.json")

Write-Host "== Fetch + rebuild ladder =="
& $py "$src\fetch_incremental_update.py"
if ($LASTEXITCODE -ne 0) { throw "fetch_incremental_update.py failed (exit $LASTEXITCODE). Is the debug Chrome open and past Cloudflare?" }

Write-Host "== Copy outputs into repo =="
foreach ($f in $files) { Copy-Item "$src\$f" "$repo\data\$f" -Force }

Write-Host "== Commit + push =="
Set-Location $repo
git add ($files | ForEach-Object { "data/$_" })
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No ladder changes since last publish - nothing to push."
} else {
    git commit -m ("ladder update {0:yyyy-MM-dd HH:mm}" -f (Get-Date))
    git push
    Write-Host "Pushed. Server will pull it within its poll interval."
}
