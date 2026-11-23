"""Seed a disposable review database for browser tests."""

import argparse
from datetime import date, timedelta
import os

from psycopg.types.json import Jsonb

from scraper import auth, db, master


EMAIL = "review-fixture@seed.test"
PASSWORD = "review-fixture-password"
EXACT = "exact@review-directory.test"
DOMAIN = "domain@review-directory.test"
UNKNOWN = "unevaluated@review-fixture.test"
RECORDS = [EXACT, DOMAIN, "unmatched@review-fixture.test", UNKNOWN] + [
    f"record-{i:03}@review-fixture.test" for i in range(128)
]
REVIEWED = {"approved@review-fixture.test": "approved", "rejected@review-fixture.test": "rejected"}
DIRECTORY_MARKER = "tests.seed_review fixture directory"


def seed_directory(conn, enabled):
    for table in ("companies", "contacts"):
        row = conn.execute("SELECT to_regclass(%s) AS relation, obj_description(to_regclass(%s), 'pg_class') AS marker",
                           (table, table)).fetchone()
        if row["relation"] and row["marker"] != DIRECTORY_MARKER:
            raise RuntimeError("Use a disposable database without existing directory tables")
    if enabled:
        conn.execute("CREATE TABLE IF NOT EXISTS companies (id bigint PRIMARY KEY, name text NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS contacts (id bigint PRIMARY KEY, company_id bigint REFERENCES companies(id), emails text[])")
        conn.execute(f"COMMENT ON TABLE companies IS '{DIRECTORY_MARKER}'")
        conn.execute(f"COMMENT ON TABLE contacts IS '{DIRECTORY_MARKER}'")
        conn.execute("INSERT INTO companies VALUES (1,'Fixture Company') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO contacts VALUES (1,1,ARRAY[%s]) ON CONFLICT DO NOTHING", (EXACT,))
    else:
        conn.execute("DROP TABLE IF EXISTS contacts, companies")
    master.rematch(conn)
    if enabled:
        conn.execute("UPDATE candidates SET master_evaluated_at=NULL WHERE email_key=%s", (UNKNOWN,))


def complete(conn, label, emails):
    if conn.execute("SELECT 1 FROM jobs WHERE params->>'seed_review'=%s AND completed", (label,)).fetchone():
        return
    job = conn.execute(
        "INSERT INTO jobs (name,status,params,completed,finished_at) VALUES ('scrape','done',%s,true,clock_timestamp())"
        " RETURNING id,finished_at", (Jsonb({"seed_review": label}),)).fetchone()
    conn.execute("UPDATE candidates SET pending_scrape_job=%s,pending_scraped_at=%s WHERE email_key=ANY(%s)",
                 (job["id"], job["finished_at"], emails))
    db.publish_scrape_completion(conn, job["id"], [], job["finished_at"])


def seed(conn, directory, second):
    if (conn.execute("SELECT 1 FROM users WHERE email<>%s LIMIT 1", (EMAIL,)).fetchone()
            or conn.execute("SELECT 1 FROM candidates WHERE NOT (email_key=ANY(%s)) LIMIT 1",
                            (RECORDS + list(REVIEWED),)).fetchone()):
        raise RuntimeError("Use a disposable database for the review fixture")
    role = conn.execute("INSERT INTO roles (name,routes) VALUES ('Review fixture',ARRAY['review','scraping'])"
                        " ON CONFLICT (lower(name)) DO UPDATE SET routes=EXCLUDED.routes RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO users (email,name,password_hash,role_id) VALUES (%s,'Review Fixture',%s,%s)"
                 " ON CONFLICT (lower(email)) DO NOTHING", (EMAIL, auth.hash_password(PASSWORD), role))
    conn.execute("INSERT INTO schedule (id,enabled,interval_minutes) VALUES (1,false,15)"
                 " ON CONFLICT (id) DO UPDATE SET enabled=false")

    rows = []
    for i, email in enumerate(RECORDS + list(REVIEWED)):
        last = (date(2026, 11, 15) - timedelta(days=i % 18)).isoformat()
        department = "Sales" if i % 2 else "Finance"
        fields = {"name": email.split("@")[0].replace("-", " ").title(), "emails": [email], "department": department,
                  "status": "inquiry" if i % 2 else "active", "stage": "inquiry" if i % 2 else "quoted",
                  "first_contact": "2026-10-01", "last_contact": last, "services": ["Web Design" if i % 2 else "SEO"],
                  "handlers": [{"department": department, "mailbox": "sales@seed.test" if i % 2 else "billing@seed.test",
                                "first": "2026-10-01", "last": last, "messages": 2}],
                  "phones": ["09175551234"] if i % 2 else [],
                  "amounts": [] if i % 2 else [{"amount": 2500, "currency": "PHP", "stage": "quoted", "date": last}]}
        rows.append((email, Jsonb({key: {"value": value} for key, value in fields.items()}), REVIEWED.get(email, "pending")))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO candidates (email_key,fields,review_state) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", rows)
    for source in ("gmail", "sheets"):
        db.upsert_raw(conn, source, "", "review-fixture-failure", {})
    conn.execute("UPDATE raw_items SET extracted_at=now(),extract_error='Fixture parse failure'"
                 " WHERE source_id='review-fixture-failure'")
    seed_directory(conn, directory)
    complete(conn, "first", RECORDS[:125])
    if second:
        complete(conn, "second", [EXACT, RECORDS[-1]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True, help="database URL for a disposable database")
    parser.add_argument("--directory", choices=("on", "off"), default="on")
    parser.add_argument("--complete-second-scrape", action="store_true")
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = args.db_url
    db.migrate()
    with db.connect() as conn:
        seed(conn, args.directory == "on", args.complete_second_scrape)
    print(f"Review fixture sign-in\nEmail: {EMAIL}\nPassword: {PASSWORD}")


if __name__ == "__main__":
    main()
