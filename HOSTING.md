# Hosting TPCD Bot 24/7

## Current deployment (live since 2026-07-04)

Running on the Oracle Cloud Always Free instance shared with NITC Bot:

- Instance: `OCI_INSTANCE`, Ubuntu 24.04, VM.Standard.E2.1.Micro, IP `SERVER_IP`, user `ubuntu`
- Bot lives in `/opt/tpcd-bot` (venv, `.env`, SQLite at `data/tpcd.db`), runs as system user `tpcdbot`
- Service: `tpcd-bot.service` — enabled (starts on boot), `Restart=on-failure`

Handy commands (key: your downloaded OCI `.key` file):

```powershell
ssh -i path\to\key ubuntu@SERVER_IP "sudo journalctl -u tpcd-bot -n 30 --no-pager"   # logs
ssh -i path\to\key ubuntu@SERVER_IP "sudo systemctl restart tpcd-bot"               # restart
scp -i path\to\key ubuntu@SERVER_IP:/opt/tpcd-bot/data/tpcd.db .\tpcd-backup.db     # backup DB
```

To redeploy after code changes (commit first — deploys ship exactly what git tracks):

```powershell
git archive --format=tar.gz -o "$env:TEMP\tpcd-update.tar.gz" HEAD
scp -i path\to\key "$env:TEMP\tpcd-update.tar.gz" ubuntu@SERVER_IP:/tmp/
ssh -i path\to\key ubuntu@SERVER_IP "sudo tar -xzf /tmp/tpcd-update.tar.gz -C /opt/tpcd-bot && sudo chown -R tpcdbot:tpcdbot /opt/tpcd-bot && sudo systemctl restart tpcd-bot"
```

(Plain `tar --exclude` is NOT safe here: bsdtar's exclude patterns match any path component, so `--exclude=data` silently drops `bot/data/` too. git archive sidesteps that.) **Don't run the bot locally anymore** — the server copy owns the database now; a local copy would double-post and double-reply.

---

The bot is host-agnostic: one `python -m bot` process, config from `.env`, SQLite in `data/`. No web server, no exposed ports needed. The original two-phase plan (kept for reference):

## Phase 1: free bot host, now (no credit card, no GitHub Education needed)

| Host | Notes |
|---|---|
| [Bot-Hosting.net](https://bot-hosting.net/) | Free, 24/7, auto-restart on crash, file manager + SFTP, GitHub integration. Best first pick |
| [Wispbyte](https://wispbyte.com/free-discord-bot-hosting) | Free 24/7 Python hosting, no renewals. Backup option |
| [HeavenCloud](https://heavencloud.in/service/free-discord-bot-hosting) | ~715 MB RAM free tier, 24/7. Second backup |

Deploy steps (same shape on any of them):

1. Create a free account, make a Python server/container
2. Upload the project (or connect the GitHub repo) — everything except `.venv/` and `data/`
3. Create `.env` on the host with the real values (never commit it)
4. Set the start command: `pip install -r requirements.txt && python -m bot`
5. Start it, check logs for `Logged in as TPCD Bot` and `Synced 7 slash commands`

Caveats with free panels: occasional restarts (fine — the bot re-syncs, dedupes, and catches up on missed poll results at startup), and the node's clock is UTC (also fine — all scheduling is timezone-aware via `TIMEZONE=Asia/Kolkata`). Keep `data/tpcd.db` on the host's persistent storage; download a copy occasionally as backup.

## Phase 2: GitHub Education VPS, later

When the GitHub Student Pack lands, migrate to a real VPS and run **both** TPCD Bot and NITC Bot on one box:

- Pack options: DigitalOcean credit ($200/yr), Azure for Students ($100), or Heroku student credit
- A $4-6/month droplet runs both bots with room to spare, so the credit lasts 2+ years
- Migration = clone repo, copy `.env` + `data/tpcd.db`, run under systemd:

```ini
# /etc/systemd/system/tpcd-bot.service
[Unit]
Description=TPCD Discord Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/tpcd-bot
ExecStart=/opt/tpcd-bot/.venv/bin/python -m bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Same unit file pattern for NITC Bot with its own directory. `systemctl enable --now tpcd-bot` and it survives reboots.

## Interim fallback: your PC

`.venv\Scripts\python -m bot` in a terminal works whenever your PC is on. Not 24/7, but the startup sweep finalizes any polls that closed while offline, and the daily job fires if the PC is on at 9 AM. Fine for testing, not for production.
