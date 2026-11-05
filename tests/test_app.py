from fastapi.testclient import TestClient

from scraper.app import app

local = TestClient(app, base_url="http://localhost")


def test_gui_page_served_on_localhost():
    r = local.get("/")
    assert r.status_code == 200 and "Workspace Scraper" in r.text


def test_other_hosts_rejected():
    assert TestClient(app, base_url="http://evil.test").get("/").status_code == 400


def test_post_without_gui_header_rejected():
    assert local.post("/api/extract").status_code == 403
