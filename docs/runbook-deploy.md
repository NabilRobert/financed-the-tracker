# Deploy runbook

How the tracker goes onto the server, and how each server-side line of the
[real-data gate](planning/slice-plan.md#the-real-data-gate) is checked. Until every line
passes and Arthur signs it, **only invented messages** go through the bot.

Run these as the server's owner account. Nothing here names the server: its address and
secrets stay in the server's own `.env` files.

## What runs

| Container | Reachable from | Why |
|---|---|---|
| `app` | The private network, the monitoring stack's network as `tracker-app`, and the server's loopback on port 8099 for Arthur's checks | The tracker |
| `waha` | The private network, and the server's loopback on port 3000 over an SSH tunnel | WhatsApp, as a linked device |

Nothing is published to the internet. WAHA connects out to WhatsApp, and calls the app at
`http://app:8099/webhook` over the private network. There's no Caddy and no subdomain.

## 1. Prepare

1. The tracker's database and its two logins (`ops/db/roles.sql`), with passwords from a
   secret file. The server's Postgres must accept them from the Docker bridge only.
2. `ops/.env.app` with what `tracker/channel/settings.py` lists, and `ops/.env.owner` with
   `TRACKER_MIGRATOR_DATABASE_URL`. Both readable by the owner account only (`chmod 600`).
   `.env.app` must not contain the owner's login.
3. In the shell that runs compose:
   - `WAHA_API_KEY_SHA512`: `sha512:` followed by the hex SHA-512 of the plain key. The
     plain key goes in `.env.app` as `WAHA_API_KEY`, and only ever in a header: WAHA also
     accepts it in a URL, where logs would keep it.
   - `WAHA_UNUSED_PANEL_PASSWORD`: a random value nobody uses. Without it, WAHA prints
     generated panel passwords to its log at startup.
4. `mkdir -m 700 ops/waha-sessions && sudo chown 10001:10001 ops/waha-sessions`.
5. `sudo install -d -m 755 -o 10001 /var/log/tracker`, for the log Vector reads. Vector runs as
   uid 65534, so the folder must be 755 and `app.log` 644. A tighter umask would hide the log
   from it with no error.
6. The monitoring stack is up first: the app joins its network, `systems-monitoring_default`.
7. Postgres accepts the two logins from the Docker bridge's subnet only: `listen_addresses`,
   `pg_hba.conf` lines for that subnet, and a ufw rule for 5432 from it. Servers & security
   owns this.
8. AIDE excludes `ops/waha-sessions`, or its daily report lists the WhatsApp session files.

## 2. Build and migrate

```bash
export TRACKER_VERSION=$(git rev-parse HEAD)
docker compose -f ops/compose.yaml build app
docker compose -f ops/compose.yaml --profile tools run --rm migrate
docker compose -f ops/compose.yaml up -d
```

## 3. Link the number (Arthur only)

Over an SSH tunnel to port 3000, create the session from `ops/waha/session.example.json`
with the real HMAC key, then fetch the QR code and scan it with the bot's phone:
`GET /api/default/auth/qr` with the `X-Api-Key` header. Only the dedicated SIM's phone,
never a personal one.

## 4. The live check

On the linked number, with invented messages only, confirm each item under
["The live check"](planning/slice-plan.md#the-live-check). Record what differed from the
fixtures, and fix the adapter before going further.

## 5. Gate checks on the server

| Gate line | Check |
|---|---|
| 1 | A forged request: post a body with a wrong `X-Webhook-Hmac` to the app's loopback port. Expect 403, and no new row |
| 2 | Run the isolation suite against a scratch copy of the deployed schema |
| 3 | Send a sentinel message such as `zzsentinelzz 98.765`, then search the app's and WAHA's container logs, the systems-monitoring logs and any trace store for `zzsentinelzz` and `98765`. Expect nothing |
| 4, 5, 21 | The restore drill below |
| 8 | `docker compose exec app env` shows no owner login, and `docker inspect` shows user 10001 and a read-only root |
| 15 | From outside the server, a probe of ports 3000, 8099 and 5432: expect nothing to answer |
| 16 | `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:3000/api/sessions` with no key: expect 401 |
| 17 | `stat -c '%a %u' ops/waha-sessions` shows `700 10001`. Search a backup's listing for `.sessions`: expect nothing |
| 3 (WAHA) | `docker logs tracker-waha-1` holds no password, key or message text |
| 3 (monitoring) | `stat -c '%a' /var/log/tracker /var/log/tracker/app.log` shows `755` and `644`, and the monitoring stack shows the app's lines |

## 6. Backups and the restore drill

Cron, as the owner account:

```bash
17 3 * * *  TRACKER_BACKUP_ENV=<backup settings file> <repo>/ops/backup/backup_tracker.sh
*/15 * * * * docker compose -f <repo>/ops/compose.yaml --profile tools run --rm cli deletions-export /deletions/deleted-ids.txt
20 4 * * *  docker compose -f <repo>/ops/compose.yaml --profile tools run --rm cli purge
```

`deleted-ids.txt` lives outside anything a backup covers, because restoring an old backup
must not also restore an old list. Mount its folder into the `cli` container for these runs.

The drill, timed, with WAHA stopped (`docker compose stop waha`):

1. Copy the newest `tracker-*.dump.age` from S3 to a machine that holds the age private
   key. Decrypt it there, never on the server.
2. Restore into an empty database, then run `deletions-reapply` with the current list.
3. Compare row counts with the live database, and check that every id in the list is gone.
4. Run `tracker export` for one person: it must work with WAHA stopped.
5. Record how long it took. Delete the restored copy.

## 7. Secret rotation, rehearsed once

Change each of these, restart, and confirm the bot still answers and the old value fails:
the webhook HMAC key (in WAHA's session and `.env.app` together), WAHA's API key, and the
app's database password.

## 8. Monitoring

Through the systems-monitoring session, which agreed these on 2026-11-03:
- a probe of `http://tracker-app:8099/healthz` over its own network, and the drift check
  on its `version`;
- from `/var/log/tracker/app.log`, read without the Docker socket:
  - `channel_stopped`, as a page: perhaps a ban;
  - a dead-man alert when no `message_handled` line has appeared for a day;
  - an alert on repeated `webhook_failed` or `reply_failed` lines.

The backup's heartbeat goes to an outside service, as MCP-backbone's does, set in
`BACKUP_HEALTHCHECK_URL`. It alerts there, not through Telegram.

Fire each alert on purpose once, and confirm it arrives.
