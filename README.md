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

1. Pick sources and mailboxes (blank = every user), then **Run scrape**. Extraction runs after it.
2. Review each pending record: fix fields, check evidence (**Open source** shows the original email or row),
   then **Approve**, **Reject**, or **Merge** into another record's ID.
3. After editing `rules.yaml`, press **Re-run extraction**. No refetch from Google is needed.

Reruns are incremental: Gmail continues from the last sync point. Tick **Full rescan** to ignore it.
Approved, rejected and merged records are never overwritten by a rerun.

## Tests

```bash
uv run pytest
```

## Tables

| Table | Purpose |
|---|---|
| `raw_items` | What was fetched from Google, unchanged. |
| `candidates` | Extracted records waiting for review. |
| `clients` | Approved directory. The directory app reads only this. |
| `sync_state` | Where each mailbox's last scrape stopped. |
# Scraper
