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

Before the first run, make the deletion list's folder. The image's user (10001) writes the
list; the owner account reads it through the folder's group, to copy it off the server.
The setgid bit (the `2`) gives every new file that group, and the app writes it `640`:

```bash
install -d -m 2750 -o 10001 -g <owner account's group> <repo>/ops/deletions
```

**Leave the bucket's versioning off.** With it on, the backup's pruning only adds a delete
marker, and every pruned dump stays as an old version, which breaks the notice's "paling
lama 14 hari". The deletion list doesn't need it: its upload only ever adds ids.

Cron, as the owner account:

```bash
17 3 * * *   TRACKER_BACKUP_ENV=<backup settings file> <repo>/ops/backup/backup_tracker.sh
*/15 * * * * TRACKER_BACKUP_ENV=<backup settings file> <repo>/ops/backup/export_deletions.sh
20 4 * * *   docker compose -f <repo>/ops/compose.yaml --profile tools run --rm cli purge
```

`export_deletions.sh` runs `deletions-export` into `ops/deletions/deleted-ids.txt`, which
`compose.yaml` mounts into the `cli` container, then merges it with the bucket's copy under
`tracker-deletions/` and uploads the result, so the bucket's list only ever grows. If the
bucket can't be read, it uploads nothing and fails. The list must outlive both an old
backup and the server itself, so:
- `tracker-deletions/` sits beside `tracker/`, where the backup prunes, never inside it;
- if a lifecycle rule ever does the 14-day expiry, it must cover `tracker/` only;
- the settings file needs `DELETIONS_HEALTHCHECK_URL`, a second heartbeat check that runs
  every 15 minutes, so a failing copy alerts.

The same script first runs `deletions-run`, which deletes everyone who confirmed
`hapus semua` (ADR-023). If that would make more than two deletions in 24 hours, it deletes
none, and the script still exports and uploads, then fails so the heartbeat alerts: a
**deletion hold**. Run `deletions-run` by hand to see the ids, ask each person or check how
they came to confirm, then `tracker delete <id> --yes` for real requests and
`tracker deletion-cancel <id>` for the rest. The day counts a reapply too, so expect a
hold in the day after a restore.

**If a deletion turns out to be forged,** the id is in three places that each bring it
back. With the export cron line paused, take it out of all three, then restore that person
from a backup:
1. `DELETE FROM deleted_person WHERE person_id = <id>`, as the owner;
2. the line in `ops/deletions/deleted-ids.txt`;
3. the line in the bucket's `tracker-deletions/deleted-ids.txt`: `rclone cat` it, remove the line, and `rclone rcat` it back. Take it out of the server's file first, or the next run's merge puts it back.

The drill, timed, with WAHA stopped (`docker compose stop waha`):

1. Copy the newest `tracker-*.dump.age` from S3 to a machine that holds the age private
   key. Decrypt it there, never on the server.
2. Restore into an empty database.
3. **Before anything else, and before anyone is added**, run `deletions-reapply` with every
   id either copy holds: `sort -un` the server's `ops/deletions/deleted-ids.txt` and the
   bucket's `tracker-deletions/deleted-ids.txt` into one file. If the server is gone, use the
   bucket's alone. It refuses a missing or empty list; `--empty-ok` is only for a database
   nobody has ever left. It also moves the id counter past every listed id, because the
   restore set it back.
4. Compare row counts with the live database, and check that every id in the list is gone.
   Check the 14-day promise too: `rclone lsf --s3-versions :s3:<bucket>/tracker` lists no
   dump older than 15 days, and no old versions at all.
5. Run `tracker export` for one person: it must work with WAHA stopped.
6. Record how long it took. Delete the restored copy.

On a server rebuilt after a loss, the restore is the real thing: add the
`export_deletions.sh` cron line only after step 3.

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
