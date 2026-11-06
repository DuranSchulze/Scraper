# Workspace Scraper

Local tool that reads Gmail, Google Contacts and Google Sheets (read-only), extracts client
records with rules from `rules.yaml`, and lets a person review each record in a local web page.
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

```bash
cp .env.example .env        # fill in every value
uv sync
uv run python -m scraper.db migrate
```

Edit `rules.yaml`: department aliases, service names and keywords.

## 3. Run

```bash
uv run python -m scraper.app
```

Open http://127.0.0.1:8765.

1. Pick sources and mailboxes (blank = every user), then **Run scrape**. Records appear while it runs:
   extraction runs after each mailbox.
2. Review each pending record. Its **Stage** follows the sales trigger: inquiry, quoted (staff sent
   pricing or a quote), invoiced (staff sent an invoice, amount shown), paid (client sent payment
   proof, or staff confirmed payment). Read any **Check before approving** warnings, check the
   evidence (**Open source** shows the original email, attachments and invoice PDF text), then
   **Approve (staging)**, **Reject**, or **Merge** into another record's ID.
3. **Publish** copies approved records from staging (`candidates`) to the official `clients` table.
   Publishing again only sends records approved or edited since the last publish.
4. After editing `rules.yaml`, press **Re-run extraction**. No refetch from Google is needed.
5. **Client details** (in each record) shows what the client paid, when they became a client and for how
   long, the services they bought, which department and staff mailbox handled them, and a timeline of
   deal emails. Payment rows marked **From invoice — confirm** or **Amount unknown** need a check:
   edit the amount, remove a duplicate row, or add a missing payment before approving.
   Everything except the timeline is copied to `clients` on Publish.

After upgrading: run `uv run python -m scraper.db migrate`, then **Re-run extraction**. Records already
approved keep their old details until you move them **Back to pending** and approve them again.

Reruns are incremental: Gmail continues from the last sync point. Tick **Full rescan** to ignore it.
Approved, rejected and merged records are never overwritten by a rerun.

## Tests

Database tests run against a throwaway local Postgres (never Neon). Install it once:

```bash
brew install postgresql@17
```

```bash
uv run pytest
```

## Tables

| Table | Purpose |
|---|---|
| `raw_items` | What was fetched from Google, unchanged. |
| `signals` | One row per person per email or row; rebuilt into candidates. |
| `candidates` | Staging: extracted records and the reviewer's approved edits. |
| `clients` | Official directory, filled only by Publish. The directory app reads only this. Includes `payments`, `client_since`, `services_availed` and `departments`. |
| `sync_state` | Where each mailbox's last scrape stopped. |
