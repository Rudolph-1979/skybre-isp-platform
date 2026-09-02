# Automated backups

`deploy/backup.sh` takes an hourly backup of the Postgres database and
keeps a rolling local history: the last **48 hourly** backups (2 days)
plus one **daily** snapshot per day for **30 days**. It can also copy
every backup off-site to Google Cloud Storage once that's set up (Phase 2
below) — until then it just backs up locally, which is still a real
improvement over no backups at all.

## Phase 1 — local hourly backups (do this now)

On the VPS, in `~/isp-platform`:

```bash
chmod +x deploy/backup.sh
mkdir -p ~/backups

# Run it once by hand to make sure it works before automating it
./deploy/backup.sh
tail -5 ~/backups/backup.log
ls -lh ~/backups/hourly ~/backups/daily
```

You should see one `.sql.gz` file in each folder and a log line ending in
`Backup run complete.` If `pg_dump` fails, check the log — the most
common cause is `docker compose` not finding the `db` service, so make
sure you run this from inside `~/isp-platform` (the script `cd`s there
itself, but double-check the `PROJECT_DIR` path at the top of the script
matches where you actually cloned the repo).

Once that works, install it as an hourly cron job:

```bash
crontab -l 2>/dev/null > /tmp/current-cron || true
echo "0 * * * * /home/ubuntu/isp-platform/deploy/backup.sh" >> /tmp/current-cron
crontab /tmp/current-cron
crontab -l   # confirm it's there
```

That's it — backups now run automatically at the top of every hour. Check
back in a couple of hours and confirm with `ls -lh ~/backups/hourly` that
new files are appearing, and `tail -f ~/backups/backup.log` to watch it
happen live at the next hour boundary.

## Phase 2 — off-site copies to Google Cloud Storage

This is optional but recommended: it protects you if the VPS itself is
ever lost, not just if the database gets corrupted. Google Cloud's free
tier and Storage pricing make this cheap for a database this size (your
backups will likely be a few MB each — this will cost cents per month,
not dollars), but double-check current pricing/region availability in the
console yourself since pricing pages change and I couldn't verify it live
just now.

### 1. Create the project and bucket

1. Go to console.cloud.google.com and sign in (or create an account —
   new accounts get a free trial credit).
2. Create a new project, e.g. `skybre-backups`.
3. In the left menu, go to **Cloud Storage → Buckets → Create**.
4. Give it a globally-unique name, e.g. `skybre-isp-backups-<something>`.
5. Choose a region — if `africa-south1` (Johannesburg) is offered when
   you get there, that's the natural pick for a South African business
   (lowest latency, data stays in-country). Otherwise pick whatever's
   closest/cheapest for you.
6. Storage class: **Standard** is fine — your data volume is small enough
   that the cheaper "infrequent access" classes (Nearline/Coldline) won't
   meaningfully change your bill, and Standard avoids their early-deletion
   fees, which matters since we're deleting backups after 30 days.
7. Leave the rest as defaults and create the bucket.

### 2. Create a service account (so the VPS can upload without your personal login)

1. **IAM & Admin → Service Accounts → Create Service Account.**
   Name it something like `skybre-backup-uploader`.
2. Grant it the **Storage Object Admin** role — ideally scoped to just
   this bucket (via the bucket's own **Permissions** tab → Grant Access →
   add the service account email with that role) rather than project-wide.
3. Open the service account → **Keys** tab → **Add Key → Create new key
   → JSON**. This downloads a `.json` file — treat it like a password.

### 3. Set the bucket to auto-delete old backups too

So off-site storage doesn't grow forever either, mirroring the local
retention:

1. Bucket → **Lifecycle** tab → **Add a rule**.
2. Rule 1: if object name matches prefix `hourly/`, delete after 3 days.
3. Rule 2: if object name matches prefix `daily/`, delete after 35 days.

### 4. Wire it into the VPS

Upload the service account JSON key (from your local machine, via
PowerShell):

```powershell
scp -i "C:\Users\Intel i7\OneDrive\Desktop\Xneelo\Skybre ISP.pem" "C:\path\to\your-downloaded-key.json" ubuntu@154.65.111.61:~/backups/gcs-service-account.json
```

On the VPS, install the Google Cloud CLI:

```bash
curl -sSL https://sdk.cloud.google.com | bash
exec -l $SHELL   # reload your shell so the `gcloud` command is available
gcloud --version
```

Then edit `deploy/backup.sh` and fill in your bucket name:

```bash
nano deploy/backup.sh
# set: GCS_BUCKET="skybre-isp-backups-<whatever-you-named-it>"
```

Run it once by hand again to confirm the off-site copy works:

```bash
./deploy/backup.sh
tail -5 ~/backups/backup.log
```

You should see `OK: uploaded to gs://...` in the log instead of the
"off-site backup not configured yet" line. From here it just runs
automatically every hour via the same cron job from Phase 1 — no further
changes needed.

## Restoring from a backup

**From a local backup:**

```bash
cd ~/isp-platform
gunzip -c ~/backups/hourly/skybre_2026-08-13_14-00.sql.gz | docker compose exec -T db psql -U <DB_USER> <DB_NAME>
```

(Use the `DB_USER`/`DB_NAME` values from `backend/.env`.)

**From an off-site (GCS) backup:**

```bash
gcloud storage cp gs://<your-bucket>/daily/skybre_2026-08-13.sql.gz ~/restore.sql.gz
gunzip -c ~/restore.sql.gz | docker compose exec -T db psql -U <DB_USER> <DB_NAME>
```

This restores on top of whatever's currently in the database — for a full
clean restore (e.g. after a disaster recovery onto a brand-new VPS), drop
and recreate the database first, or restore into a fresh `docker compose
up -d db` before running the app containers.

**Always test a restore occasionally** — an untested backup is a guess,
not a safety net. A quick way: spin up a throwaway Postgres container
locally and restore into that, rather than your real production database,
just to confirm the file isn't corrupted and actually contains your data.
