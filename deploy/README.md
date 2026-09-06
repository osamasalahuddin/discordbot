# Auto-updating the ladder

## Where each piece runs

| Step | Machine | Why |
|---|---|---|
| Fetch from aoe2insights + rebuild ladder | Windows PC | needs the manually-prepared debug Chrome (Cloudflare) |
| Publish (`scripts/publish_update.ps1`) | Windows PC | commits the new JSON, pushes to GitHub |
| Pull (`deploy/pull.sh` via timer) | Ubuntu server | fast-forwards the repo |
| Serve `/balance` etc. | Ubuntu server | `aoe2bot.service` |

No bot restart is needed after a data update: `bot.py` re-reads the JSON files whenever
they change on disk (it keys its cache on mtime, so a `git pull` is picked up on the next
command).

## Windows PC — one-time

The pipeline reads and writes the repo's own `data/` directory, so there is no copy step
and no second working tree — set `$env:DATA_DIR` first if you want the working data kept
somewhere else. Adjust the repo path below to match your checkout, then schedule it:

```powershell
# Runs daily at 3am. Only useful while the debug Chrome is left open & logged in.
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File E:\Work\Claude\discordbot\scripts\publish_update.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -TaskName "aoe2-ladder-publish" -Action $action -Trigger $trigger
```

Or just run it by hand after a session:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Work\Claude\discordbot\scripts\publish_update.ps1
```

## Ubuntu server — one-time

The bot itself:

```bash
pip install -r /home/ubuntu/work/discordbot/code/requirements.txt

# Token goes in a root-owned env file, never in the unit or the repo:
printf 'DISCORD_BOT_TOKEN=%s\n' "<token>" | sudo tee /etc/aoe2bot.env >/dev/null
sudo chmod 600 /etc/aoe2bot.env

sudo cp /home/ubuntu/work/discordbot/deploy/aoe2bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aoe2bot
journalctl -u aoe2bot -n 20
```

The data-pull timer:

```bash
sudo cp /home/ubuntu/work/discordbot/deploy/aoe2-pull.service /etc/systemd/system/
sudo cp /home/ubuntu/work/discordbot/deploy/aoe2-pull.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aoe2-pull.timer
```

Check it:
```bash
systemctl list-timers aoe2-pull.timer
systemctl start aoe2-pull.service        # run once now
journalctl -u aoe2-pull.service -n 20
```

## Optional: also restart the bot on each pull

Not needed — the bot's cache invalidates itself on mtime. Only useful if you later make it
hold state that outlives a data change. Give `ubuntu` permission for that one command:

```bash
echo 'ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart aoe2bot' | sudo tee /etc/sudoers.d/aoe2bot
sudo chmod 440 /etc/sudoers.d/aoe2bot
```

Then uncomment the `sudo systemctl restart aoe2bot` line in `pull.sh`.
