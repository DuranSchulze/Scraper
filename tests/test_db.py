from scraper.db import pending_migrations


def test_pending_migrations_skips_applied_and_sorts():
    names = [p.name for p in pending_migrations(set())]
    assert names == sorted(names) and "001_init.sql" in names
    assert "001_init.sql" not in [p.name for p in pending_migrations({"001_init.sql"})]


def test_upgrade_001_backfills_legacy_and_rerun_is_noop(pg_url):
    from scraper import db

    with db.connect() as conn:
        conn.execute((db.MIGRATIONS_DIR / "001_init.sql").read_text())
        conn.execute("CREATE TABLE schema_migrations (name text PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES ('001_init.sql')")
        client = conn.execute("INSERT INTO clients (name) VALUES ('Legacy') RETURNING id").fetchone()["id"]
        conn.execute("INSERT INTO candidates (email_key, review_state, client_id) VALUES ('old@x.test', 'approved', %s)", (client,))
        conn.execute("INSERT INTO candidates (email_key) VALUES ('new@x.test')")
    assert db.migrate() == ["002_stages_publish.sql", "003_client_details.sql"]
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
    assert db.migrate() == ["003_client_details.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM clients").fetchone()
        assert row["name"] == "Legacy" and row["emails"] == ["old@test.com"] and row["stage"] == "paid"
        assert row["amounts"] == [{"amount": 500}]
        assert row["payments"] == [] and row["client_since"] is None
        assert row["services_availed"] == [] and row["departments"] == []
