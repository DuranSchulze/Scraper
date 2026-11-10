# Workspace Scraper

Local tool that reads Gmail, Google Contacts and Google Sheets (read-only), extracts client
records with rules from `backend/rules.yaml`, and lets a person review each record in a local web page.
Approved records go to the `clients` table in Neon Postgres. No AI or third-party service sees the data.

## 1. Google Workspace setup (admin, once)

1. In Google Cloud Console, create a project and enable: **Gmail API**, **People API**,
   **Admin SDK API**, **Google Sheets API**.
2. Create a **service account** and download its JSON key. Store it **outside this repo**.
3. In Google Admin console → Security → Access and data control → API controls →
   **Domain-wide delegation** → Add new. Use the service account's client ID and these scopes:

   ```
   https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/contacts.readonly,https://www.googleapis.com/auth/contacts.other.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/spreadsheets.readonly
   ```
4. Share each client-tracker Sheet with the account set as `GOOGLE_ADMIN_EMAIL`.

## 2. Local setup

## Folders

| Folder | What it is |
|---|---|
| `backend/` | Python: scraper, extraction rules (`rules.yaml`), API, migrations, tests |
| `frontend/` | Next.js review GUI; talks only to the backend API |
| `.env` (root) | Your settings and secrets, shared by both. Never committed. |

Run every command below from the repository root; the npm scripts in `package.json` handle the folders, so no `cd` is needed. Needs [uv](https://docs.astral.sh/uv/) and Node.js.

```bash
cp backend/.env.example .env        # fill in every value
npm run setup                       # installs backend and frontend dependencies
npm run db push                     # creates or updates the database tables
```

Use an absolute path for `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`.

Set `JWT_SECRET` to a random secret of at least 32 characters (for example, generate one with
`uv run --directory backend python -c "import secrets; print(secrets.token_urlsafe(32))"`).
Set `ADMIN_EMAIL` and a non-empty `ADMIN_PASSWORD` (up to 1024 characters) to create the first admin.
The API seeds it at startup only when the `users` table is empty; missing admin values log
a warning. Keep these values private. Set `APP_URL` to your HTTPS application URL when
using HTTPS behind a proxy so session cookies are always Secure.

After updating, run migrations first (`npm run db push`, including migration 007), then
start the API and GUI (`npm run dev`), then sign in with the admin account. Sessions last
8 hours. Login is limited to 5 attempts per email and socket peer IP per 15 minutes, and
30 per peer IP. Behind the Next.js rewrite, clients share the loopback IP; forwarded IP
headers are ignored. Limits are kept in each API process's memory and reset on restart.
Admins can list and create accounts with `/api/users`; members cannot manage accounts.

Edit `backend/rules.yaml`: department aliases, service names and keywords.

## 3. Run

```bash
npm run dev     # starts the API (127.0.0.1:8765) and the GUI (127.0.0.1:3000); Ctrl+C stops both
```

Or one per terminal: `npm run api` and `npm run web`.

Open http://127.0.0.1:3000. Both bind to this computer only.

Nothing is lost on a restart: records live in Neon, the job log and status are stored in the
`jobs` tables, filters and the open record are in the page URL, and unsaved form edits are kept in
the browser until you approve or reject. If the API stops during a scrape, the GUI shows
**Interrupted** about 2 minutes later with a **Resume** button; Resume skips mail already fetched.

1. Pick sources and mailboxes (blank = every user), then **Run scrape**. Records appear while it runs:
   extraction runs after each mailbox.
2. Review each pending record. Its **Stage** follows the sales trigger: inquiry, quoted (staff sent
   pricing or a quote), invoiced (staff sent an invoice, amount shown), paid (client sent payment
   proof, or staff confirmed payment). Read any **Check before approving** warnings, check the
   evidence (**Open source** shows the original email, attachments and invoice PDF text), then
   **Approve (staging)**, **Reject**, or **Merge** into another record's ID.
3. **Publish** copies approved records from staging (`candidates`) to the official `clients` table.
   Publishing again only sends records approved or edited since the last publish.
4. After editing `backend/rules.yaml`, press **Re-run extraction**. No refetch from Google is needed.
5. **Client details** (in each record) shows what the client paid, when they became a client and for how
   long, the services they bought, which department and staff mailbox handled them, and a timeline of
   deal emails. Payment rows marked **From invoice — confirm** or **Amount unknown** need a check:
   edit the amount, remove a duplicate row, or add a missing payment before approving.
   Everything except the timeline is copied to `clients` on Publish.

After upgrading: run `npm run db push`, then **Re-run extraction**. Records already
approved keep their old details until you move them **Back to pending** and approve them again.

`SCRAPE_PARALLEL_MAILBOXES` controls concurrent mailboxes (default 4, range 1–8);
`SCRAPE_MESSAGE_WORKERS` controls downloads per mailbox (default 16, range 1–25).
Gmail limits apply per mailbox and vary by project; the worker count is not a throughput promise.
If progress lines show frequent rate-limit retries, lower `SCRAPE_MESSAGE_WORKERS` to 12 and restart.

Reruns are incremental: Gmail continues from the last sync point. Tick **Full rescan** to ignore it.
Approved, rejected and merged records are never overwritten by a rerun.

## Deploy the backend on Render

Create a Render Blueprint from the root `render.yaml` (Python 3.12, paid Starter,
one instance). The build uses `uv sync --locked --no-dev`; each start runs migrations
(`python -m scraper.db push`) before starting the API. Supply the prompted secrets,
including the Neon `DATABASE_URL`, `JWT_SECRET`, and initial admin credentials. Upload
the Google service-account JSON as a Render Secret File named `sa-key.json`; it is read
from `/etc/secrets/sa-key.json`. Set the Google Workspace variables as for local setup
and add `DRIVE_SHEET_IDS` if needed.

Set `ALLOWED_HOSTS` to a comma list of API hostnames, including your service's
`<name>.onrender.com` hostname and any custom domain (no scheme or port). It defaults
to `127.0.0.1,localhost`; other hosts receive 400. `HOST` defaults to `127.0.0.1`;
the Blueprint sets `0.0.0.0`. Render supplies `PORT`, which overrides `GUI_PORT`
(default 8765). Set `APP_URL` to the HTTPS frontend URL to force Secure cookies.

The Next.js rewrite via `SCRAPER_API_URL` needs no CORS: set it to the Render API URL
on Vercel. Only direct browser calls need `CORS_ORIGINS`: an exact comma list of
HTTPS frontend origins (no trailing slash), never `*`; empty disables CORS. Direct
calls must send credentials and `X-Local-GUI: 1` on writes. For cross-site cookies,
set `COOKIE_SAMESITE=none`, which always sets Secure; browsers may still block
third-party cookies, so prefer the Next.js rewrite. `lax` is the default and invalid
values fall back to it; `strict` is also supported. Restart after changing settings.

Keep **one instance and one API process**: jobs run in threads and rate limits are
in memory. The free tier sleeps and would kill active scrapes; use an always-on paid
service. Deploys/restarts also interrupt jobs; resume them after startup. No dedicated
health-check path is configured; `/` is an unauthenticated GUI pointer.

## Start over (empty the database)

**This permanently deletes every scraped email, every staging record, the job history and the
official `clients` table.** There is no undo. The next scrape downloads every mailbox again.

```bash
uv run --directory backend python -m scraper.db reset          # shows the database and row counts; deletes nothing
```

```bash
uv run --directory backend python -m scraper.db reset --yes    # deletes everything listed above
```

It refuses while a job is running. Tables and migrations stay in place.

## Tests

Database tests run against a throwaway local Postgres on `127.0.0.1` with a random TCP port
(never the configured `DATABASE_URL` or Neon). Install PostgreSQL 17 once:

```bash
brew install postgresql@17
```

On Windows, run `choco install postgresql17` in an administrator shell. On Linux, install
the `postgresql-17` package. Set `PG_BIN` to the PostgreSQL `bin` folder if it differs from
the default (`C:\Program Files\PostgreSQL\17\bin`, `/opt/homebrew/opt/postgresql@17/bin`,
or `/usr/lib/postgresql/17/bin`). For example, in PowerShell:

```powershell
$env:PG_BIN = 'C:\Program Files\PostgreSQL\17\bin'
```

```bash
npm test        # backend tests, then frontend typecheck and lint
```

## Tables

| Table | Purpose |
|---|---|
| `raw_items` | What was fetched from Google, unchanged. |
| `signals` | One row per person per email or row; rebuilt into candidates. |
| `candidates` | Staging: extracted records and the reviewer's approved edits. |
| `clients` | Official directory, filled only by Publish. The directory app reads only this. Includes `payments`, `client_since`, `services_availed` and `departments`. |
| `sync_state` | Where each mailbox's last scrape stopped. |
| `jobs`, `job_log` | Scrape/extract runs, their status and log lines (kept across restarts). |
