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
   secret file. The server's Postgres accepts each from one subnet only (step 7).
2. `ops/.env.app` with what `tracker/channel/settings.py` lists, and `ops/.env.owner` with
   `TRACKER_MIGRATOR_DATABASE_URL`. Both readable by the owner account only (`chmod 600`).
   `.env.app` must not contain the owner's login.
3. `ops/.env`, copied from `ops/.env.example` and `chmod 600`. Compose reads it by itself,
   so cron's empty environment gets the same values as the deploy shell:
   - `TRACKER_NETWORK_SUBNET`: a free `/24` for the app's network, one no other Docker
     network on the server uses.
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
7. Postgres accepts each login from one subnet: `tracker_app` from `TRACKER_NETWORK_SUBNET`,
   and `tracker_owner` from the default bridge's subnet (`docker network inspect bridge`),
   where migrate and cli run. That's `listen_addresses`, two `pg_hba.conf` lines, and ufw
   rules for 5432 from those two subnets only. Never widen it to `172.16.0.0/12`, which lets
   every container on the server reach 5432. After the first start, check that
   `SELECT usename, client_addr FROM pg_stat_activity` shows `tracker_app` inside
   `TRACKER_NETWORK_SUBNET`. Servers & security owns this.
8. Docker Engine is 28 or later (`docker version --format '{{.Server.Version}}'`): older
   versions let hosts on the same network reach ports published on `127.0.0.1`.
9. AIDE excludes `ops/waha-sessions`, or its daily report lists the WhatsApp session files.

## 2. Build and migrate

```bash
sed -i "s/^TRACKER_VERSION=.*/TRACKER_VERSION=$(git rev-parse HEAD)/" ops/.env
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
| 3 | Send a sentinel message such as `zzsentinelzz 98.765`, then search the app's and WAHA's container logs, the systems-monitoring logs, any trace store and the server's Postgres log for `zzsentinelzz` and `98765`. Then stop `app`, send a second sentinel so WAHA's delivery fails, start `app`, and search WAHA's log again. Expect nothing |
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

**The bucket comes first,** set up by Arthur with his own admin login, never the server's
key (L2, [ADR-024](planning/phase-5-architecture-decisions.md#adr-024-the-server-cant-delete-its-own-backups)).
The server's key can write backups but never delete them, so a taken-over server can't
destroy them along with the database. The bucket expires old dumps instead:
1. **A bucket of its own.** Versioning is set for a whole bucket, and in a shared bucket
   another project's pruning would leave its old versions behind forever.
2. **Versioning on,** so overwriting a dump keeps the old copy.
3. **The lifecycle rule in `ops/backup/s3-lifecycle.json`.**
   - A dump in `tracker/` expires after 7 days.
   - An expired or overwritten copy goes 4 days after that.
   - S3 rounds each step up to midnight UTC, so no dump outlives 13 days. That's inside
     the notice's "paling lama 14 hari", with a day to spare for S3's own delay.
   - The rule never touches `tracker-deletions/`.
4. **A backup key of its own,** with `ops/backup/s3-backup-key-policy.json` attached, after
   replacing `BUCKET_NAME`.
   - It can write, read and list the tracker's two folders.
   - It's refused every delete, and every change to versioning, the lifecycle rule or the
     bucket's policy.
   - An attacker on the server can add files, or overwrite a dump. The original then stays
     as an old copy for 4 days: time enough, because an attack that also wrecks the
     database stops the bot, and the health probe and the no-messages alert both fire.
5. **Check that the key is refused a delete,** before the first backup: upload a test file
   with the backup key, try to delete it with the same key, and expect `AccessDenied`. The
   lifecycle rule removes the test file.

The backup settings file (`chmod 600`, owner account) holds the S3 and age settings,
`BACKUP_DATABASE_URL` (the owner login with no password in it, which the script refuses)
and `BACKUP_PGPASSFILE`, a `chmod 600` `.pgpass` holding the password, so it never appears
on `pg_dump`'s command line.

Cron, as the owner account:

```bash
17 3 * * *   TRACKER_BACKUP_ENV=<backup settings file> <repo>/ops/backup/backup_tracker.sh
*/15 * * * * TRACKER_BACKUP_ENV=<backup settings file> <repo>/ops/backup/export_deletions.sh
```

`export_deletions.sh` runs `deletions-export` into `ops/deletions/deleted-ids.txt`, which
`compose.yaml` mounts into the `cli` container, then merges it with the bucket's copy under
`tracker-deletions/` and uploads the result, so the bucket's list only ever grows. If the
bucket can't be read, it uploads nothing and fails. The list must outlive both an old
backup and the server itself, so:
- `tracker-deletions/` sits beside `tracker/`, where the lifecycle rule expires dumps,
  never inside it;
- the lifecycle rule covers `tracker/` only, and a test holds it there;
- the settings file needs `DELETIONS_HEALTHCHECK_URL`, a second heartbeat check that runs
  every 15 minutes, so a failing copy alerts.

The same script first runs `deletions-run`, which deletes everyone who confirmed
`hapus semua` (ADR-023). If that would make more than two deletions in 24 hours, it deletes
none, and the script still exports and uploads, then fails so the heartbeat alerts: a
**deletion hold**. Run `deletions-run` by hand to see the ids, ask each person or check how
they came to confirm, then `tracker delete <id> --yes` for real requests and
`tracker deletion-cancel <id>` for the rest. The day counts a reapply too, so expect a
hold in the day after a restore.

**Every deletion alerts too,** not only a hold (R12). A run that deletes anyone fails the
deletions heartbeat with `deletions_run deleted=N` in the ping's log: a count, never an id.
The next quiet run sets it back to up. When one arrives, check that each person in
`deleted_person` from the last 15 minutes asked to leave. Unexpected ones mean the app may be
taken over: follow "If a deletion turns out to be forged" below while the backups still hold
them. A run whose count can't be read fails too, rather than stay quiet.

**If a deletion turns out to be forged,** the id is in three places that each bring it
back. With the export cron line paused, take it out of all three, then restore that person
from a backup:
1. `DELETE FROM deleted_person WHERE person_id = <id>`, as the owner;
2. the line in `ops/deletions/deleted-ids.txt`;
3. the line in the bucket's `tracker-deletions/deleted-ids.txt`: `rclone cat` it, remove the line, and `rclone rcat` it back. Take it out of the server's file first, or the next run's merge puts it back.

`backup_tracker.sh` also runs the nightly purge of expired records, after the upload, so
retention shares the backup's heartbeat. Neither script ever deletes from the bucket.

The drill, timed, runs entirely on a machine that holds the age private key, never on the
server, so nothing in it touches the live database. WAHA isn't needed.

1. Copy there the newest `tracker-*.dump.age` from the bucket, the bucket's
   `tracker-deletions/deleted-ids.txt` and the server's `ops/deletions/deleted-ids.txt`, then
   `sort -un` the two lists into one file.
2. Start a throwaway Postgres on that machine. Run `ops/db/roles.sql` in it, give
   `tracker_owner` a password, and create an empty `tracker` database owned by it. Then
   restore without writing the decrypted dump to disk:
   `age -d -i <key> tracker-*.dump.age | pg_restore -h 127.0.0.1 -p <port> -U tracker_owner -d tracker`.
3. From a checkout of the repo, point the CLI at that copy, not the live one:
   `export TRACKER_MIGRATOR_DATABASE_URL=postgresql+asyncpg://tracker_owner:<password>@127.0.0.1:<port>/tracker`.
   **Before anything else, and before anyone is added**, run
   `uv run tracker deletions-reapply <merged list>`. It refuses a missing or empty list;
   `--empty-ok` is only for a database nobody has ever left. It also moves the id counter
   past every listed id, because the restore set it back.
4. Compare row counts with the live database, and check that every id in the list is gone.
   Check the 14-day promise too, with Arthur's own key: `rclone lsf --s3-versions --format tp :s3:<bucket>/tracker`
   lists no copy of any dump, current or old, more than 13 days old. Old copies younger
   than that are expected.
5. `uv run tracker export <id> check.csv` for one person: it needs only the database.
6. Record how long it took. Delete the throwaway database and the CSV.

**After losing the server,** the restore is the real thing, into the new server's Postgres:
- section 1 there first, including `roles.sql` and an empty `tracker` database;
- the same stream over an SSH tunnel to its Postgres:
  `ssh -L <port>:127.0.0.1:5432 <server>`, then step 2's `age -d … | pg_restore -h 127.0.0.1 -p <port> …`.
  Through the tunnel `tracker_owner` logs in from `127.0.0.1`, which step 7 refuses, so add
  `host tracker tracker_owner 127.0.0.1/32 scram-sha-256` to `pg_hba.conf` for the restore,
  reload, and remove it straight after;
- the bucket's list as `ops/deletions/deleted-ids.txt`, then
  `docker compose -f ops/compose.yaml --profile tools run --rm cli deletions-reapply /deletions/deleted-ids.txt`
  before the app starts or anyone is added;
- only then the `export_deletions.sh` cron line.

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
