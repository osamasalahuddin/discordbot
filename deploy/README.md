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

## Adding players from Discord (`/addplayer`)

The player list is `data/players.json`. `/addplayer` (discord user, ladder
name, aoe2insights profile link, optional start date - default today) appends
to it, then commits and pushes **from the server's checkout**. The next
`fetch_incremental_update.py` on the PC starts with `git pull --ff-only`, so the
player is tracked from that run on, and `publish_update.ps1` rebases onto any
`/addplayer` commit before pushing. The player shows up in `/players` once that
ladder update reaches the server.

Server requirements:
- `aoe2bot.service` must run as the user whose git identity and SSH key can
  push to GitHub (`ubuntu`, like `aoe2-pull.service`). Check with
  `sudo -u ubuntu git -C /home/ubuntu/work/discordbot push --dry-run`.
- `pull.sh` and the bot share a lock (`.git/aoe2-repo.lock`), so the 15-minute
  reset never lands between the bot's commit and push.
- Restart the bot once after deploying this (`sudo systemctl restart aoe2bot`),
  since it's a `code/` change.

`/linkplayer` (ladder player, discord user) stores the member's Discord user id
on an existing entry the same way - for the players that were tracked before
`/addplayer` existed, or to correct a link. The id is permanent, so renames
don't break it. The builders ignore it, so no rebuild is involved.

To fix a mistake, edit or remove the entry in `data/players.json` by hand and
commit; the builders pick up the change on the next rebuild.

## Optional: also restart the bot on each pull

Only needed if you later make the bot cache data at startup. Give `ubuntu`
permission for that one command:

```bash
echo 'ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart aoe2bot' | sudo tee /etc/sudoers.d/aoe2bot
sudo chmod 440 /etc/sudoers.d/aoe2bot
```

Then uncomment the `sudo systemctl restart aoe2bot` line in `pull.sh`.
