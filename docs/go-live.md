# Go-live checklist

How the tracker goes from a SIM in a drawer to people using it, in order. Each step says
what it depends on. The detail of the server steps lives in
[runbook-deploy.md](runbook-deploy.md); this page puts them in sequence and adds the local
test before them.

| Part | Where | Who | Done when |
|---|---|---|---|
| [1. SIM day](#1-sim-day-a-local-pairing-test) | Arthur's Mac | Arthur | The live check's items are recorded, and the local copy is wiped |
| [2. Server deploy](#2-server-deploy) | The server | Arthur, with the server work | The app answers on the server with invented messages |
| [3. The gate](#3-the-security-gate) | The server and the repo | Arthur signs | Every gate line passes on the deployed commit |
| [4. Rollback](#4-rollback) | The server | Arthur | Only if needed |

**The gate's rule holds throughout:** until Arthur signs the
[real-data gate](planning/slice-plan.md#the-real-data-gate), only invented messages go
through the bot, Arthur's own included. Invited people come after that.

## 1. SIM day: a local pairing test

**Before you start:**
- The SIM is in a spare phone, with WhatsApp set up and two-step verification on. It has
  been used normally for a few days. Never your personal phone.
- Docker Desktop is running, and `docker ps` shows no other heavy job.

The test uses three containers on your Mac: Postgres, the app and WAHA. All of them are
bound to `127.0.0.1`, and everything lives in `~/tracker-local`, outside the repo, so
nothing can be committed by accident.

**Who gets replies:** once WAHA is linked, it sees every chat on the SIM's number. The bot
answers only numbers on its allow-list, and the list holds only yours. Anyone else who
messages the SIM gets silence, and nothing of theirs is stored except a daily count. The
SIM phone's own messages are ignored.

### 1.1 Folder and secrets

```bash
mkdir -m 700 ~/tracker-local && cd ~/tracker-local && mkdir -m 700 waha-sessions
for name in ADMIN_PASSWORD OWNER_PASSWORD APP_PASSWORD WAHA_API_KEY WAHA_WEBHOOK_HMAC_KEY PANEL_PASSWORD; do printf '%s=%s\n' "$name" "$(openssl rand -hex 32)"; done > secrets.env && chmod 600 secrets.env
git -C <path to your checkout> fetch && git -C <path to your checkout> worktree add --detach ~/tracker-local/code origin/main
```

In every new terminal for this test, run this first:

```bash
cd ~/tracker-local && set -a && . ./secrets.env && set +a && export TRACKER_MIGRATOR_DATABASE_URL="postgresql+asyncpg://tracker_owner:${OWNER_PASSWORD}@127.0.0.1:55442/tracker"
```

### 1.2 Postgres, with the two logins

```bash
docker network create tracker-local
docker run -d --name tracker-local-db --network tracker-local -p 127.0.0.1:55442:5432 -e POSTGRES_PASSWORD="$ADMIN_PASSWORD" -v tracker-local-pgdata:/var/lib/postgresql/data postgres:16
until docker exec tracker-local-db pg_isready -U postgres; do sleep 1; done
docker exec -i tracker-local-db psql -U postgres -v ON_ERROR_STOP=1 < code/ops/db/roles.sql
docker exec -i tracker-local-db psql -U postgres -v ON_ERROR_STOP=1 <<SQL
ALTER ROLE tracker_owner PASSWORD '${OWNER_PASSWORD}';
ALTER ROLE tracker_app PASSWORD '${APP_PASSWORD}';
CREATE DATABASE tracker OWNER tracker_owner;
SQL
(cd code && uv sync && uv run alembic upgrade head)
```

### 1.3 The app

```bash
docker build -t tracker-app:local code
cat > app.env <<EOF
TRACKER_DATABASE_URL=postgresql+asyncpg://tracker_app:${APP_PASSWORD}@tracker-local-db:5432/tracker
WAHA_URL=http://waha:3000
WAHA_SESSION=default
WAHA_API_KEY=${WAHA_API_KEY}
WAHA_WEBHOOK_HMAC_KEY=${WAHA_WEBHOOK_HMAC_KEY}
BANTUAN_CONTACT=Arthur
TRACKER_BIND_HOST=0.0.0.0
TRACKER_VERSION=$(git -C code rev-parse HEAD)
EOF
chmod 600 app.env
docker run -d --name tracker-local-app --network tracker-local --network-alias app -p 127.0.0.1:8099:8099 --env-file app.env --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true tracker-app:local
curl -s 127.0.0.1:8099/healthz
```

Expect `{"status":"ok",...}`. If the app refuses to start, `docker logs tracker-local-app`
names the setting that's wrong.

### 1.4 WAHA, with the same image and settings as the server

```bash
cat > waha.env <<EOF
WHATSAPP_DEFAULT_ENGINE=GOWS
WAHA_API_KEY=sha512:$(printf %s "$WAHA_API_KEY" | shasum -a 512 | cut -d' ' -f1)
WAHA_DASHBOARD_ENABLED=False
WHATSAPP_SWAGGER_ENABLED=False
WAHA_DASHBOARD_USERNAME=disabled
WAHA_DASHBOARD_PASSWORD=${PANEL_PASSWORD}
WHATSAPP_SWAGGER_USERNAME=disabled
WHATSAPP_SWAGGER_PASSWORD=${PANEL_PASSWORD}
WAHA_LOG_LEVEL=warn
WHATSAPP_DOWNLOAD_MEDIA=false
WAHA_EVENTS_DOWNLOAD_MEDIA=false
WAHA_API_DOWNLOAD_MEDIA=false
EOF
chmod 600 waha.env
docker run -d --name tracker-local-waha --network tracker-local --network-alias waha -p 127.0.0.1:3000:3000 --env-file waha.env -v "$HOME/tracker-local/waha-sessions:/app/.sessions" --user 10001:10001 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true "$(grep -o 'devlikeapro/waha:[^ ]*' code/ops/compose.yaml)"
```

The image comes from `ops/compose.yaml`, so the test runs the pinned digest the server
will run.

### 1.5 Pair by QR code

Create the session, with the webhook's HMAC key filled in. The key goes through a pipe, never
into a file:

```bash
sed "s/\"change-me\"/\"${WAHA_WEBHOOK_HMAC_KEY}\"/" code/ops/waha/session.example.json | curl -sS -X POST -H "X-Api-Key: ${WAHA_API_KEY}" -H 'Content-Type: application/json' --data @- http://127.0.0.1:3000/api/sessions
```

```bash
curl -sS -H "X-Api-Key: ${WAHA_API_KEY}" http://127.0.0.1:3000/api/sessions/default
```

Wait until that shows `"status":"SCAN_QR_CODE"`. Then fetch the QR code:

```bash
curl -sS -H "X-Api-Key: ${WAHA_API_KEY}" -H 'Accept: image/png' http://127.0.0.1:3000/api/default/auth/qr -o qr.png && open qr.png
```

On the **SIM phone**, open WhatsApp → Settings → Linked devices → Link a device, and scan
it. The code changes about every 20 seconds, so fetch it again if it has expired. Check
the status once more: expect `"status":"WORKING"`. Then `rm qr.png`.

### 1.6 Allow your number, and test

```bash
(cd code && uv run tracker add 62<your personal number, digits only>)
```

Expect `Added person 1.` From your **personal phone**, message the SIM's number. Send only
invented entries:

| Send | Expect the reply | What's recorded |
|---|---|---|
| `halo` | The consent notice, starting "Halo! Ini bot pencatat keuangan pribadi", which warns of the unofficial channel | That the notice was sent, with its version |
| `setuju` | "Terima kasih! Coba kirim: makan 25rb …" | Your consent, with the notice's version |
| `makan 25rb` | "Tercatat pengeluaran: Makan Rp25.000", then "Salah? Balas: batal" | A Rp25,000 spending entry |
| `gaji 5jt` | "Tercatat pemasukan: Gaji Rp5.000.000" | A Rp5,000,000 income entry |
| `makan 25` | "Maksudnya yang mana?", offering Rp25 or Rp25.000 | Nothing until you answer `2` |
| `batal` | "Dibatalkan: …" for the last entry | That entry is voided, not deleted |
| `rekap` | This month's spending, income and categories | Nothing |
| `bantuan` | The list of commands, ending with "Hubungi Arthur." | Nothing |
| `ekspor` | A file, `catatan-keuangan.csv`. If it never arrives, note it: WAHA may only send files in its paid tier, which is a live-check finding | Nothing |
| A photo | "Bot ini baru bisa membaca teks …" | Nothing: WAHA never downloads it |

Check what was stored, and that nothing leaked:

```bash
(cd code && uv run tracker export 1 ../check.csv) && cat check.csv && rm check.csv
docker logs tracker-local-app 2>&1 | grep -ciE 'makan|gaji|25000|5000000'
docker logs tracker-local-waha 2>&1 | grep -ciE 'makan|gaji|25000|5000000'
```

The CSV lists exactly the entries you sent, with the voided one marked. Both counts must be
`0`.

**Record the live check's answers** in a private note, never in this repo, for the items under
["The live check"](planning/slice-plan.md#the-live-check): whether you arrived as `@lid`, whether
the export file arrived, and what happened to a message sent while the app was stopped
(`docker stop tracker-local-app`, send one, then `docker start tracker-local-app`). Anything
that differs from the fixtures is an adapter fix, made before the server and reviewed
(part 3).

### 1.7 Stop, and keep the data

```bash
docker stop tracker-local-waha tracker-local-app tracker-local-db
```

That keeps everything: the ledger stays in the `tracker-local-pgdata` volume, and the pairing
in `~/tracker-local/waha-sessions`. While WAHA is stopped, the SIM phone works normally and
nobody gets a reply. To carry on later:

```bash
docker start tracker-local-db tracker-local-app tracker-local-waha
```

If WAHA has been stopped for a long time, WhatsApp may unlink it. Then pair again from step 1.5.

### 1.8 Before the server: unlink and wipe

The local copy never moves to the server. The server links the number afresh, so only one
WAHA ever holds the account:

```bash
curl -sS -X POST -H "X-Api-Key: ${WAHA_API_KEY}" http://127.0.0.1:3000/api/sessions/default/logout
docker rm -f tracker-local-waha tracker-local-app tracker-local-db && docker volume rm tracker-local-pgdata && docker network rm tracker-local
git -C <path to your checkout> worktree remove ~/tracker-local/code && rm -rf ~/tracker-local
```

Check that the SIM phone's "Linked devices" list is empty.

## 2. Server deploy

**Depends on:**

| What | Why | Status |
|---|---|---|
| The server's backup fix: the new age key in use, **and its decrypt test passing** | The tracker's nightly backup encrypts to it. A dump nobody can decrypt is no backup (gate line 4) | Key in use; decrypt test pending |
| The server work confirms it's ready for this data (P4) | Gate line 12, signed by the server work, not by Arthur alone | Open |
| The monitoring stack is up | The app joins its network, and the alerts in runbook section 8 live there | Check with the systems-monitoring session |
| Part 1, with any adapter fixes merged | The server runs what the live check proved | Waiting for the SIM |
| The bucket, set up by Arthur (L2, ADR-024) | Its own bucket, with versioning on, the lifecycle rule, and a backup key that can't delete. Chosen over Object Lock, which adds nothing against a taken-over server and can never be shortened if it's wrong. The key must be refused a delete before the first backup | Decided 2026-11-05; Arthur sets it up |

**The age key:** the tracker's backup settings file has its own `AGE_RECIPIENT`. Set it to
the new key's public key, the same one MCP-backbone now reads as `AGE_PUBLIC_KEY`. The
private key never goes on the server.

**The steps,** in this order, as the server's owner account. Each one links to the runbook,
which has the commands. The server is `<server>` throughout: its address stays out of the repo.

1. Check out the commit being deployed, the one CI is green on (gate line 7).
2. [Runbook section 1](runbook-deploy.md#1-prepare): the database and its two logins, the
   three env files, the folders, `pg_hba` and ufw for the two subnets, the Docker version
   check, and the AIDE exclusion.
3. [Runbook section 2](runbook-deploy.md#2-build-and-migrate): build, migrate, and start.
4. [Runbook section 6](runbook-deploy.md#6-backups-and-the-restore-drill): the bucket
   first (its own, versioning on, the lifecycle rule, the no-delete key, and the delete
   check), then the deletion folder, the backup settings file with the new `AGE_RECIPIENT`
   and the new key, both heartbeat checks, and both cron lines. Run `backup_tracker.sh` once by hand, and confirm its heartbeat.
5. [Runbook section 3](runbook-deploy.md#3-link-the-number-arthur-only): over
   `ssh -L 3000:127.0.0.1:3000 <server>`, create the session and scan the QR code, as in
   steps 1.5 above, with the server's keys.
6. [Runbook section 4](runbook-deploy.md#4-the-live-check): one round trip from your
   personal phone, with invented messages. Then `tracker add` for your number on the server.
7. [Runbook section 8](runbook-deploy.md#8-monitoring): the probe and the three log alerts,
   each fired once on purpose.
8. Part 3.

## 3. The security gate

Every line of the [gate](planning/slice-plan.md#the-real-data-gate) passes on the deployed
commit, then Arthur signs it in writing. The server-side checks are
[runbook section 5](runbook-deploy.md#5-gate-checks-on-the-server). Three need more than a
command.

### A tested restore (gate lines 4, 5 and 21)

The drill is [runbook section 6](runbook-deploy.md#6-backups-and-the-restore-drill), steps
1 to 6. It runs on your Mac, which holds the age private key, against a throwaway Postgres.
Nothing in it touches the live database or needs WAHA.

**It passes when all of these hold:**
- the newest dump decrypts with the new key, and streams into `pg_restore` with no errors;
- `deletions-reapply` on the merged list exits 0. It refuses an empty list unless nobody
  has ever left;
- the row counts match the live database's, and no id on the list is left;
- `rclone lsf --s3-versions`, with Arthur's own key, shows no copy of a dump, current or old, more than 13 days old;
- `tracker export <id>` writes a CSV that matches what that person sent;
- the time it took is recorded in the private note, with the date, the commit and the dump's name.

### The independent review (gate line 11)

The review covers the repository at `main`, per its [brief](planning/review-brief.md):
- one person reaching another's data;
- forged or replayed webhooks;
- content leaking into logs, spans, errors or exports;
- wrong amounts written without a question;
- deletion that leaves something behind or brings it back;
- the deploy files.

It doesn't cover the live server. The server checks in runbook section 5 do that.

**The review already done counts.** Its fixes were merged as #13. No fresh review is
needed, but three things close it:
1. the reviewer's final check on the merged `main`, which passed on 2026-11-05;
2. Arthur's calls on the two findings left to him: R6 is accepted in his own words, and L2
   is fixed (ADR-024). Both are done;
3. if any code (not docs) changes between that commit and the one deployed, such as an
   adapter fix from part 1, a review of that diff alone.

The report stays private. L6, L7, L9 and R12 were agreed for after the gate.

### WhatsApp's terms (gate line 13)

WAHA is an unofficial client, which WhatsApp's terms don't allow, so the number can be
banned at any time without warning. Arthur accepts that in writing, or the bot doesn't go
live.

**Arthur also signs:**
- line 19: a dedicated SIM, never his personal number;
- line 14: every row of the threat model is Done or listed under "What we accept".

## 4. Rollback

**To turn it off now,** on the server:

```bash
docker compose -f ops/compose.yaml stop waha app
```

After that, nothing is read or sent. The ledger, the backups and the 15-minute deletion
job carry on, because none of them needs WhatsApp. Keep the cron lines running: confirmed
deletions must still happen. The monitoring stack's probe and its no-messages alert will
fire. That's expected, so silence them while the bot is off.

**If the number is banned** (a `channel_stopped` alert with `FAILED`, or a ban notice on the
SIM phone):
1. Stop both containers as above.
2. Tell each person by another channel. Offer them their records:
   `docker compose -f ops/compose.yaml --profile tools run --rm cli export <id> /deletions/<id>.csv`
   writes them with WAHA stopped. Hand each file over privately, then delete it.
3. Delete what's in `ops/waha-sessions`. After a ban it's useless, and it holds the
   account's keys.
4. Don't link a new number in a hurry: the same use pattern on a fresh SIM invites the
   same ban. A new number is a new SIM day, from part 1.

**To unlink without a ban,** either log the session out over the tunnel:

```bash
curl -sS -X POST -H "X-Api-Key: <the API key>" http://127.0.0.1:3000/api/sessions/default/logout
```

Or, on the SIM phone, open Linked devices and log the device out.

**If a deploy breaks the app,** deploy the previous commit again: set `TRACKER_VERSION`,
build, and `up -d`. Migrations only go forward, so a broken schema means restoring from the
night before, then running `deletions-reapply`, as in runbook section 6.
