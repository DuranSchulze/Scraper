from scraper.db import pending_migrations


def test_pending_migrations_skips_applied_and_sorts():
    names = [p.name for p in pending_migrations(set())]
    assert names == sorted(names) and "001_init.sql" in names
    assert "001_init.sql" not in [p.name for p in pending_migrations({"001_init.sql"})]


def test_directory_sync_upgrade_rewrites_roles_clears_ids_and_preserves_directory(pg_url):
    from psycopg.errors import CheckViolation
    from scraper import db

    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations(name text PRIMARY KEY)")
        for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if path.name == "012_directory_sync.sql":
                break
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))
        conn.execute("CREATE TABLE companies(id bigint PRIMARY KEY,name text)")
        conn.execute("CREATE TABLE contacts(company_id bigint REFERENCES companies(id),emails text[])")
        conn.execute("INSERT INTO companies VALUES (1,'Kept')")
        conn.execute("INSERT INTO contacts VALUES (1,ARRAY['kept@company.test'])")
        conn.execute("INSERT INTO master_clients(row_no,registered_name) VALUES (2,'Old')")
        conn.execute("INSERT INTO master_imports(filename,sheet,rows,emails,imported_by) VALUES ('old.xlsx','old',1,1,'test')")
        conn.execute("INSERT INTO roles(name,routes) VALUES ('Only import',ARRAY['masterlist']),('Mixed',ARRAY['masterlist','accounts'])")
        conn.execute("INSERT INTO candidates(email_key,review_state,master_match,master_ids) VALUES"
                     " ('pending@test','pending','domain',ARRAY[1]),('approved@test','approved','email',ARRAY[1])")
        conn.execute("INSERT INTO jobs(name,status) VALUES ('extract','done')")
    with db.connect() as conn:
        conn.execute((db.MIGRATIONS_DIR / "012_directory_sync.sql").read_text())
        conn.rollback()
        assert conn.execute("SELECT count(*) AS n FROM master_clients").fetchone()["n"] == 1
        assert conn.execute("SELECT master_ids FROM candidates LIMIT 1").fetchone()["master_ids"] == [1]
    assert db.migrate() == ["012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT to_regclass('master_clients') AS clients,to_regclass('master_imports') AS imports").fetchone() == {"clients": None, "imports": None}
        assert all(r["master_match"] is None and r["master_ids"] is None for r in conn.execute("SELECT * FROM candidates"))
        roles = {r["name"]: r for r in conn.execute("SELECT * FROM roles")}
        assert roles["Only import"]["routes"] == ['review']
        assert roles["Mixed"]["routes"] == ['accounts']
        assert roles["Owner"]["routes"] == ['review', 'scraping', 'accounts'] and roles["Owner"]["locked"]
        with pytest.raises(CheckViolation):
            with conn.transaction():
                conn.execute("UPDATE roles SET routes=ARRAY['masterlist']")
        assert conn.execute("SELECT progress FROM jobs").fetchone()["progress"] is None
        assert conn.execute("SELECT tagged_at FROM directory_tag_state").fetchone()["tagged_at"] is None
        assert conn.execute("SELECT * FROM companies").fetchall() == [{"id": 1, "name": "Kept"}]
        assert conn.execute("SELECT * FROM contacts").fetchall() == [{"company_id": 1, "emails": ['kept@company.test']}]


def test_roles_upgrade_preserves_accounts_and_grants(pg_url):
    from psycopg.errors import CheckViolation, ForeignKeyViolation, NotNullViolation, UniqueViolation
    from scraper import db

    cases = [
        ("admin", ["review"], "Owner"), ("admin", ["scraping"], "Owner"),
        ("member", ["review", "scraping"], "Developer"),
        ("member", ["scraping", "review", "scraping"], "Developer"),
        ("member", ["review"], "Review only"), ("member", ["scraping"], "Scraping only"),
    ]
    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations(name text PRIMARY KEY)")
        for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if path.name == "010_roles.sql":
                break
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))
        for i, (role, routes, _) in enumerate(cases):
            conn.execute("INSERT INTO users(email,name,password_hash,role,routes,active) VALUES (%s,%s,'kept hash',%s,%s,%s)",
                         (f"user{i}@test", f"User {i}", role, routes, i != 1))
        before = conn.execute("SELECT id,email,name,password_hash,active,created_at FROM users ORDER BY id").fetchall()
    assert db.migrate() == ["010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT id,email,name,password_hash,active,created_at FROM users ORDER BY id").fetchall() == before
        rows = conn.execute("SELECT r.name,r.routes,r.locked FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.id").fetchall()
        assert [row["name"] for row in rows] == [case[2] for case in cases]
        assert rows[0]["routes"] == rows[1]["routes"] == ["review", "scraping", "accounts"]
        assert rows[0]["locked"] is True and rows[2]["locked"] is False
        assert rows[2]["routes"] == rows[3]["routes"] == ["review", "scraping"]
        assert rows[4]["routes"] == ["review"] and rows[5]["routes"] == ["scraping"]
        columns = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='users'")}
        assert "role_id" in columns and not {"role", "routes"} & columns
        for routes in ([], ["unknown"], ["review", None], None):
            with pytest.raises((CheckViolation, NotNullViolation)):
                with conn.transaction():
                    conn.execute("INSERT INTO roles(name,routes) VALUES ('Invalid',%s)", (routes,))
        with pytest.raises(UniqueViolation):
            with conn.transaction():
                conn.execute("INSERT INTO roles(name,routes) VALUES ('OWNER',ARRAY['accounts'])")
        for rid, error in ((None, NotNullViolation), (999999, ForeignKeyViolation)):
            with pytest.raises(error):
                with conn.transaction():
                    conn.execute("UPDATE users SET role_id=%s WHERE id=1", (rid,))


def test_user_routes_upgrade_defaults_constraints_and_noop(pg_url):
    from psycopg.errors import CheckViolation, NotNullViolation
    from scraper import db

    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if path.name == "008_user_routes.sql":
                break
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES ('old@test','Old','hash','member')")
    assert db.migrate() == ["008_user_routes.sql", "009_schedule.sql", "010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    with db.connect() as conn:
        assert conn.execute("SELECT r.routes FROM users u JOIN roles r ON r.id=u.role_id").fetchone()["routes"] == ["review", "scraping"]
        new = conn.execute(
            "INSERT INTO users(email,name,password_hash,role_id) SELECT 'new@test','New','hash',id FROM roles WHERE name='Developer' RETURNING *"
        ).fetchone()
        assert conn.execute("SELECT routes FROM roles WHERE id=%s", (new["role_id"],)).fetchone()["routes"] == ["review", "scraping"]
        for routes in (["unknown"], [], ["review", None], None):
            with pytest.raises((CheckViolation, NotNullViolation)):
                with conn.transaction():
                    conn.execute("UPDATE roles SET routes=%s WHERE id=%s", (routes, new["role_id"]))
        before = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT * FROM users ORDER BY id").fetchall() == before


def test_users_migration_preserves_populated_006_and_reruns_as_noop(pg_url):
    from psycopg import sql
    from scraper import db

    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if path.name == "007_users.sql":
                break
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))
        db.upsert_raw(conn, "gmail", "a", "kept", {"body": "kept"})
        conn.execute("INSERT INTO clients (name) VALUES ('Kept')")
        jid = conn.execute("INSERT INTO jobs(name,status) VALUES ('scrape','done') RETURNING id").fetchone()["id"]
        conn.execute("INSERT INTO job_log(job_id,line) VALUES (%s,'Kept')", (jid,))
        conn.execute("INSERT INTO job_mailboxes(job_id,mailbox,status) VALUES (%s,'a','done')", (jid,))
        tables = [row["tablename"] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'")]
        before = {table: conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall()
                  for table in tables}
    assert db.migrate() == ["007_users.sql", "008_user_routes.sql", "009_schedule.sql", "010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        for table, rows in before.items():
            after = conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall()
            if table == "jobs":
                for row in after:
                    assert row.pop("progress") is None
            assert after == rows
        assert conn.execute("SELECT * FROM users").fetchall() == []


def test_upgrade_001_backfills_legacy_and_rerun_is_noop(pg_url):
    from scraper import db

    with db.connect() as conn:
        conn.execute((db.MIGRATIONS_DIR / "001_init.sql").read_text())
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES ('001_init.sql')")
        client = conn.execute("INSERT INTO clients (name) VALUES ('Legacy') RETURNING id").fetchone()["id"]
        conn.execute("INSERT INTO candidates (email_key, review_state, client_id) VALUES ('old@x.test', 'approved', %s)", (client,))
        conn.execute("INSERT INTO candidates (email_key) VALUES ('new@x.test')")
    assert db.migrate() == ["002_stages_publish.sql", "003_client_details.sql", "004_jobs.sql", "005_job_controls.sql", "006_job_mailboxes.sql", "007_users.sql", "008_user_routes.sql", "009_schedule.sql", "010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        old = conn.execute("SELECT * FROM candidates WHERE email_key='old@x.test'").fetchone()
        assert old["reviewed_at"] == old["published_at"] == old["updated_at"]
        assert conn.execute("SELECT published_at FROM candidates WHERE email_key='new@x.test'").fetchone()["published_at"] is None
        db.upsert_raw(conn, "gmail", "a", "m", {"v": 1})
        row = conn.execute("SELECT extracted_at, extract_error FROM raw_items").fetchone()
        assert row == {"extracted_at": None, "extract_error": None}
        idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE tablename='raw_items'").fetchall()
        assert any("WHERE (extracted_at IS NULL)" in r["indexdef"] for r in idx)
        assert conn.execute("SELECT count(*) AS n FROM signals").fetchone()["n"] == 0
        assert conn.execute("SELECT stage, amounts FROM clients").fetchone() == {"stage": "inquiry", "amounts": []}


def test_raw_helpers_replace_and_invalidate_extraction(pg_url):
    from scraper import db

    db.migrate()
    with db.connect() as conn:
        db.upsert_raw_many(conn, "gmail", "a", [("m", {"v": 1}), ("n", {"v": 2})])
        conn.execute("UPDATE raw_items SET extracted_at=now()")
        assert db.missing_ids(conn, "gmail", "a", ["m", "n", "new"], 2) == ["m", "new"]
        db.upsert_raw(conn, "gmail", "a", "m", {"v": 2})
        assert db.missing_ids(conn, "gmail", "a", ["m", "n"], 2) == []
        assert conn.execute("SELECT extracted_at FROM raw_items WHERE source_id='m'").fetchone()["extracted_at"] is None
        db.upsert_raw_many(conn, "gmail", "a", [("n", {"v": 2, "body": "changed"})])
        assert conn.execute("SELECT extracted_at FROM raw_items WHERE source_id='n'").fetchone()["extracted_at"] is None


def test_upgrade_002_preserves_populated_clients_and_adds_details(pg_url):
    from scraper import db

    with db.connect() as conn:
        for name in ("001_init.sql", "002_stages_publish.sql"):
            conn.execute((db.MIGRATIONS_DIR / name).read_text())
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES ('001_init.sql'), ('002_stages_publish.sql')")
        conn.execute("INSERT INTO clients (name, emails, stage, amounts) VALUES ('Legacy', ARRAY['old@test.com'], 'paid', '[{\"amount\": 500}]')")
    assert db.migrate() == ["003_client_details.sql", "004_jobs.sql", "005_job_controls.sql", "006_job_mailboxes.sql", "007_users.sql", "008_user_routes.sql", "009_schedule.sql", "010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM clients").fetchone()
        assert row["name"] == "Legacy" and row["emails"] == ["old@test.com"] and row["stage"] == "paid"
        assert row["amounts"] == [{"amount": 500}]
        assert row["payments"] == [] and row["client_since"] is None
        assert row["services_availed"] == [] and row["departments"] == []


def test_upgrade_003_adds_job_tables_and_keeps_clients(pg_url):
    from scraper import db

    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        for name in ("001_init.sql", "002_stages_publish.sql", "003_client_details.sql"):
            conn.execute((db.MIGRATIONS_DIR / name).read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (%s)", (name,))
        conn.execute("INSERT INTO clients (name) VALUES ('Kept')")
    assert db.migrate() == ["004_jobs.sql", "005_job_controls.sql", "006_job_mailboxes.sql", "007_users.sql", "008_user_routes.sql", "009_schedule.sql", "010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM clients").fetchone()["name"] == "Kept"
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0
        jid = conn.execute("INSERT INTO jobs (name, params, status) VALUES ('extract', '{}', 'done') RETURNING id").fetchone()["id"]
        conn.execute("INSERT INTO job_log (job_id, line) VALUES (%s, 'test')", (jid,))
        conn.execute("DELETE FROM jobs WHERE id=%s", (jid,))
        assert conn.execute("SELECT count(*) AS n FROM job_log").fetchone()["n"] == 0


def reset_cli(pg_url, *args):
    import os
    import subprocess
    import sys

    return subprocess.run([sys.executable, "-m", "scraper.db", "reset", *args],
                          env={**os.environ, "DATABASE_URL": pg_url}, capture_output=True, text=True, timeout=10)


def test_reset_dry_run_and_yes_clear_all_data_and_restart_ids(pg_url):
    from scraper import db

    db.migrate()
    tables = ("job_log", "jobs", "signals", "candidates", "clients", "raw_items", "sync_state", "gone_items")
    with db.connect() as conn:
        jid = conn.execute("INSERT INTO jobs (name, status) VALUES ('extract', 'done') RETURNING id").fetchone()["id"]
        conn.execute("INSERT INTO job_log (job_id, line) VALUES (%s, 'kept')", (jid,))
        db.upsert_raw(conn, "gmail", "a", "m", {})
        conn.execute("INSERT INTO signals (raw_item_id, key, data) SELECT id, 'a', '{}' FROM raw_items")
        conn.execute("INSERT INTO clients (name) VALUES ('Official')")
        conn.execute("INSERT INTO candidates (email_key, client_id) SELECT 'a', id FROM clients")
        db.set_cursor(conn, "gmail", "a", "123")
        conn.execute("INSERT INTO gone_items(source,mailbox,source_id) VALUES ('gmail','a','gone')")
    dry = reset_cli(pg_url)
    assert dry.returncode == 1
    with db.connect() as conn:
        assert conn.info.host in dry.stdout and conn.info.dbname in dry.stdout
        for table in tables:
            assert f"{table}: 1" in dry.stdout
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 1
    reset = reset_cli(pg_url, "--yes")
    assert reset.returncode == 0, reset.stderr
    with db.connect() as conn:
        for table in tables:
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == len(pending_migrations(set()))
        assert conn.execute("INSERT INTO jobs (name, status) VALUES ('extract','done') RETURNING id").fetchone()["id"] == 1
        assert conn.execute("INSERT INTO job_log (job_id, line) VALUES (1,'new') RETURNING id").fetchone()["id"] == 1
        db.upsert_raw(conn, "gmail", "a", "new", {})
        assert conn.execute("SELECT id FROM raw_items").fetchone()["id"] == 1
        assert conn.execute("INSERT INTO signals (raw_item_id, key, data) VALUES (1,'a','{}') RETURNING id").fetchone()["id"] == 1
        assert conn.execute("INSERT INTO clients DEFAULT VALUES RETURNING id").fetchone()["id"] == 1
        assert conn.execute("INSERT INTO candidates (email_key) VALUES ('a') RETURNING id").fetchone()["id"] == 1


def test_reset_refuses_fresh_running_job_but_allows_stale_one(pg_url):
    from scraper import db

    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs (name, status) VALUES ('scrape', 'running')")
        conn.execute("INSERT INTO clients (name) VALUES ('Keep')")
    result = reset_cli(pg_url, "--yes")
    assert result.returncode == 1
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM clients").fetchone()["name"] == "Keep"
        assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "running"
        conn.execute("UPDATE jobs SET heartbeat_at=now()-interval '3 minutes'")
    assert reset_cli(pg_url, "--yes").returncode == 0
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 0


import pytest


@pytest.mark.parametrize("source", ["gmail", "contacts", "sheets"])
@pytest.mark.parametrize("bulk", [False, True])
def test_raw_upserts_remove_nested_nuls_and_store_normal_neighbor(pg_url, source, bulk):
    from scraper import db

    db.migrate()
    payload = {
        "attachment_text": {"inv\x00oice.pdf": "Total PHP 1,\x00000"},
        "body": "H\x00i\nCafé 😀\t終",
        "headers": {"sub\x00ject": "In\x00voice"},
        "nested": ["a\x00b", {"k\x00": [None, False, True, 0, 42, 12.5, "終\x00"]}, [], {}],
        "literal": r"\u0000 \x00",
        "v": 2,
    }
    expected = {
        "attachment_text": {"invoice.pdf": "Total PHP 1,000"},
        "body": "Hi\nCafé 😀\t終",
        "headers": {"subject": "Invoice"},
        "nested": ["ab", {"k": [None, False, True, 0, 42, 12.5, "終"]}, [], {}],
        "literal": r"\u0000 \x00",
        "v": 2,
    }
    normal = {"body": "Normal ✓", "values": [7, False, None], "v": 2}
    with db.connect() as conn:
        if bulk:
            db.upsert_raw_many(conn, source, "mailbox", [("bad", payload), ("normal", normal)])
        else:
            db.upsert_raw(conn, source, "mailbox", "bad", payload)
            db.upsert_raw(conn, source, "mailbox", "normal", normal)
        conn.commit()
    with db.connect() as conn:
        rows = {r["source_id"]: r["payload"] for r in conn.execute("SELECT source_id, payload FROM raw_items")}
    assert rows == {"bad": expected, "normal": normal}
    assert rows["bad"]["nested"][1]["k"][1] is False
    assert rows["bad"]["nested"][1]["k"][2] is True
    assert payload["body"] == "H\x00i\nCafé 😀\t終"
    assert "inv\x00oice.pdf" in payload["attachment_text"]


def test_upgrade_004_preserves_jobs_logs_and_data_and_reserves_paused_slot(pg_url):
    from scraper import db
    from psycopg.errors import UniqueViolation

    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        for name in ("001_init.sql", "002_stages_publish.sql", "003_client_details.sql", "004_jobs.sql"):
            conn.execute((db.MIGRATIONS_DIR / name).read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (%s)", (name,))
        conn.execute("INSERT INTO jobs (name,status,params) VALUES ('scrape','running','{\"done_mailboxes\":[\"a\"]}')")
        conn.execute("INSERT INTO job_log (job_id,line) VALUES (1,'kept')")
        conn.execute("INSERT INTO clients (name) VALUES ('Kept')")
    assert db.migrate() == ["005_job_controls.sql", "006_job_mailboxes.sql", "007_users.sql", "008_user_routes.sql", "009_schedule.sql", "010_roles.sql", "011_master_list.sql", "012_directory_sync.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT params, control FROM jobs").fetchone() == {"params": {"done_mailboxes": ["a"]}, "control": None}
        assert conn.execute("SELECT line FROM job_log").fetchone()["line"] == "kept"
        assert conn.execute("SELECT name FROM clients").fetchone()["name"] == "Kept"
        conn.execute("UPDATE jobs SET status='paused', control='pause'")
        with pytest.raises(UniqueViolation), conn.transaction():
            conn.execute("INSERT INTO jobs (name,status) VALUES ('extract','running')")
        conn.execute("INSERT INTO jobs (name,status) VALUES ('extract','stopped')")
        assert conn.execute("SELECT count(*) AS n FROM gone_items").fetchone()["n"] == 0


@pytest.mark.parametrize("bulk", [False, True])
def test_unchanged_raw_payload_preserves_extraction_and_fetch_timestamps(pg_url, bulk):
    from scraper import db

    db.migrate()
    with db.connect() as conn:
        db.upsert_raw(conn, "gmail", "a", "m", {"body": "same"})
        conn.execute("UPDATE raw_items SET fetched_at='2020-01-01', extracted_at='2020-01-02'")
        before = conn.execute("SELECT * FROM raw_items").fetchone()
        if bulk:
            db.upsert_raw_many(conn, "gmail", "a", [("m", {"body": "same"})])
        else:
            db.upsert_raw(conn, "gmail", "a", "m", {"body": "same"})
        assert conn.execute("SELECT * FROM raw_items").fetchone() == before
        if bulk:
            db.upsert_raw_many(conn, "gmail", "a", [("m", {"body": "changed"})])
        else:
            db.upsert_raw(conn, "gmail", "a", "m", {"body": "changed"})
        after = conn.execute("SELECT * FROM raw_items").fetchone()
        assert after["id"] == before["id"] and after["extracted_at"] is None
        assert after["fetched_at"] > before["fetched_at"]


def test_reset_refuses_paused_job(pg_url):
    from scraper import db

    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs (name,status,control) VALUES ('scrape','paused','pause')")
    assert reset_cli(pg_url, "--yes").returncode == 1
    with db.connect() as conn:
        assert conn.execute("SELECT status, control FROM jobs").fetchone() == {"status": "paused", "control": "pause"}


def test_mailbox_migration_defaults_validation_and_cascade(pg_url):
    import pytest
    from psycopg.errors import CheckViolation
    from scraper import db
    db.migrate()
    with db.connect() as conn:
        jid = conn.execute("INSERT INTO jobs(name,status) VALUES ('scrape','done') RETURNING id").fetchone()['id']
        conn.execute("INSERT INTO job_mailboxes(job_id,mailbox,status) VALUES (%s,'a','pending')", (jid,))
        row = conn.execute('SELECT * FROM job_mailboxes').fetchone()
        assert row['fetched'] == row['total'] == row['rate_limited'] == 0
        with pytest.raises(CheckViolation), conn.transaction():
            conn.execute("UPDATE job_mailboxes SET status='invalid'")
        conn.execute('DELETE FROM jobs WHERE id=%s', (jid,))
        assert conn.execute('SELECT * FROM job_mailboxes').fetchall() == []
