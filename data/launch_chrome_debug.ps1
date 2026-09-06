# One-time (or once-per-reboot) setup: launches a dedicated Chrome instance with
# remote debugging enabled, using its own separate profile so it doesn't touch your
# normal Chrome windows/logins. Cookies persist in this profile across launches, so
# once you solve the Cloudflare check on aoe2insights.com in this window, you
# shouldn't need to solve it again unless the site re-challenges you.
#
# Usage: just run this script, then in the Chrome window that opens, navigate to
# https://www.aoe2insights.com/ and make sure it loads cleanly (solve the CAPTCHA if
# one appears). Leave that window open. Then fetch_incremental_update.py can drive it
# directly.

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
# Kept next to this script (and git-ignored) so the solved challenge and cookies persist
# without ever being committed.
$profileDir = Join-Path $PSScriptRoot "chrome_debug_profile"
$debugPort = 9222

if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

Write-Host "Launching Chrome with remote debugging on port $debugPort ..."
Start-Process -FilePath $chromePath -ArgumentList @(
    "--remote-debugging-port=$debugPort",
    "--user-data-dir=$profileDir",
    "--no-first-run",
    "--no-default-browser-check",
    "https://www.aoe2insights.com/"
)

Write-Host "Chrome launching. Once the page loads (solve the CAPTCHA if one appears),"
Write-Host "leave this window open and run fetch_incremental_update.py."
