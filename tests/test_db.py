from scraper.db import pending_migrations


def test_pending_migrations_skips_applied_and_sorts():
    names = [p.name for p in pending_migrations(set())]
    assert names == sorted(names) and "001_init.sql" in names
    assert "001_init.sql" not in [p.name for p in pending_migrations({"001_init.sql"})]
