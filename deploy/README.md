# Auto-updating the ladder

## Where each piece runs

| Step | Machine | Why |
|---|---|---|
| Fetch from aoe2insights + rebuild ladder | Windows PC | needs the manually-prepared debug Chrome (Cloudflare) |
| Publish (`scripts/publish_update.ps1`) | Windows PC | commits the rebuilt JSON, pushes to GitHub |
| Pull (`deploy/pull.sh` via timer) | Ubuntu server | fast-forwards the repo |
| Serve `/balance` etc. | Ubuntu server | `aoe2bot.service` |

Everything lives in this repo - the pipeline scripts sit in `data/` alongside
the JSON they produce, and every path resolves from the script's own location,
so there is no separate working copy and it runs on Windows or Linux.

No bot restart is needed after a data update: `bot.py` reads the JSON files on
every command. Restart only after changing `code/`.

## Windows PC — one-time

Edit `scripts/publish_update.ps1` if your paths differ, then schedule it:

```powershell
# Runs daily at 3am. Only useful while the debug Chrome is left open & logged in.
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\publish_update.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -TaskName "aoe2-ladder-publish" -Action $action -Trigger $trigger
```

Or just run it by hand after a session:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\publish_update.ps1
```

## Ubuntu server — one-time

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

Only needed if you later make the bot cache data at startup. Give `ubuntu`
permission for that one command:

```bash
echo 'ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart aoe2bot' | sudo tee /etc/sudoers.d/aoe2bot
sudo chmod 440 /etc/sudoers.d/aoe2bot
```

Then uncomment the `sudo systemctl restart aoe2bot` line in `pull.sh`.
