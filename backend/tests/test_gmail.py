"""Gmail API doubles with real batch writes to disposable Postgres."""

import base64
import io
import threading
import time
from types import SimpleNamespace

from googleapiclient.errors import HttpError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from test_auth import authenticated_client

from scraper import db
from scraper.sources import contacts, gmail, sheets

MAILBOX = "sales@acme.test"


def encoded(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def message(mid, parts=()):
    return {"id": mid, "threadId": "t" + mid, "labelIds": ["SENT"], "internalDate": "1700000000000",
            "payload": {"mimeType": "multipart/mixed", "headers": [
                {"name": "From", "value": MAILBOX}, {"name": "Subject", "value": "Invoice"}],
                "parts": [{"mimeType": "text/plain", "body": {"data": encoded(b"See attached")}}, *parts]}}


def part(filename, mime, size, att_id):
    return {"filename": filename, "mimeType": mime, "body": {"size": size, "attachmentId": att_id}}


def invoice_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 20 350 Td (Total PHP 25,000) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class Request:
    def __init__(self, run):
        self.run = run

    def execute(self):
        return self.run()


class FakeGmail:
    def __init__(self, messages, latency=0):
        self.messages_data = {m["id"]: m for m in messages}
        self.history_ids = list(self.messages_data)
        self.latency = latency
        self.expired = False
        self.fail_id = None
        self.missing = set()
        self.created = []
        self.downloads = []
        self.attachment_downloads = []
        self.attachment_data = {}
        self.pages = 0

    def service(self, kind, mailbox):
        assert (kind, mailbox) == ("gmail", MAILBOX)
        self.created.append(threading.get_ident())
        return Client(self)


class Client:
    def __init__(self, fake):
        self.fake = fake
        self.thread = threading.get_ident()

    def users(self):
        assert threading.get_ident() == self.thread, "Gmail client shared across threads"
        return self

    def messages(self):
        return Endpoint(self, "messages")

    def history(self):
        return Endpoint(self, "history")

    def labels(self):
        return Endpoint(self, "labels")

    def getProfile(self, **kw):
        assert kw == {"userId": "me"}
        return Request(lambda: {"historyId": "new-cursor"})


class Endpoint:
    def __init__(self, client, kind):
        self.client, self.kind = client, kind
        self.fake = client.fake

    def attachments(self):
        return Endpoint(self.client, "attachments")

    def list(self, **kw):
        def run():
            if self.kind == "labels":
                return {"labels": [{"id": "SENT", "name": "Sent"}]}
            if self.kind == "history":
                assert kw["startHistoryId"] == "old-cursor"
                if self.fake.expired:
                    raise HttpError(SimpleNamespace(status=404, reason="Gone"), b'{}')
                return {"history": [{"messagesAdded": [{"message": {"id": i}} for i in self.fake.history_ids]}]}
            assert self.kind == "messages" and kw["q"] == "after:2025/01/01"
            self.fake.pages += 1
            ids = list(self.fake.messages_data)
            start = int(kw.get("pageToken") or 0)
            result = {"messages": [{"id": i} for i in ids[start:start + 70]]}
            if start + 70 < len(ids):
                result["nextPageToken"] = str(start + 70)
            return result
        return Request(run)

    def get(self, **kw):
        def run():
            assert threading.get_ident() == self.client.thread
            if self.kind == "attachments":
                self.fake.attachment_downloads.append(kw["id"])
                return {"data": encoded(self.fake.attachment_data[kw["id"]])}
            assert self.kind == "messages" and kw["format"] == "full"
            mid = kw["id"]
            self.fake.downloads.append(mid)
            time.sleep(self.fake.latency)
            if mid == self.fake.fail_id:
                raise RuntimeError("injected download failure")
            if mid in self.fake.missing:
                raise HttpError(SimpleNamespace(status=404, reason="Gone"), b'{}')
            return self.fake.messages_data[mid]
        return Request(run)


@pytest.fixture
def conn(pg_url):
    db.migrate()
    with db.connect() as conn:
        yield conn


def fetch(conn, full=False, log=lambda _: None):
    return gmail.fetch_mailbox(conn, MAILBOX, "2025-01-01", full, log)


def test_payload_v2_attachments_and_selective_pdf_text(conn, monkeypatch):
    parts = [part("logo.png", "image/png", 100, "logo"),
             part("screenshot.png", "image/png", 40000, "screen"),
             part("invoice.pdf", "application/pdf", 1000, "invoice"),
             part("large-invoice.pdf", "application/pdf", gmail.MAX_PDF_BYTES + 1, "large")]
    fake = FakeGmail([message("m", parts)])
    fake.attachment_data["invoice"] = invoice_pdf()
    monkeypatch.setattr(gmail, "service", fake.service)
    assert fetch(conn) == 1
    payload = conn.execute("SELECT payload FROM raw_items").fetchone()["payload"]
    assert payload["v"] == 2
    assert payload["attachments"][0] == {"filename": "logo.png", "mime": "image/png", "size": 100, "att_id": "logo"}
    assert len(payload["attachments"]) == 4
    assert "Total PHP 25,000" in payload["attachment_text"]["invoice.pdf"]
    assert fake.attachment_downloads == ["invoice"]
    # A generic PDF on an unrelated message is not fetched.
    generic = message("generic", [part("brochure.pdf", "application/pdf", 1000, "brochure")])
    generic["payload"]["headers"][-1]["value"] = "Hello"
    fake.messages_data["generic"] = generic
    fake.history_ids = ["generic"]
    # get_cursor will now hold the cursor stored above.
    db.set_cursor(conn, "gmail", MAILBOX, "old-cursor")
    conn.commit()
    assert fetch(conn) == 1
    assert fake.attachment_downloads == ["invoice"]


def test_parallel_clients_batches_and_404(conn, pg_url, monkeypatch):
    fake = FakeGmail([message(str(i)) for i in range(17)], latency=.005)
    fake.missing.add("3")
    monkeypatch.setattr(gmail, "service", fake.service)
    monkeypatch.setattr(gmail, "CHUNK", 8)
    committed = []
    def log(_):
        with db.connect() as observer:
            committed.append(observer.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"])
    assert fetch(conn, log=log) == 16
    assert committed == [0, 7, 15, 16]
    assert len(set(fake.created)) > 2
    assert len(fake.created) == len(set(fake.created))
    assert db.get_cursor(conn, "gmail", MAILBOX) == "new-cursor"


@pytest.mark.parametrize("expired", [False, True])
def test_history_fallback_and_payload_upgrade(conn, monkeypatch, expired):
    db.upsert_raw(conn, "gmail", MAILBOX, "old", {"v": 1})
    db.upsert_raw(conn, "gmail", "other@acme.test", "other", {"v": 1})
    db.set_cursor(conn, "gmail", MAILBOX, "old-cursor")
    conn.commit()
    fake = FakeGmail([message("old")])
    fake.expired = expired
    fake.history_ids = []
    monkeypatch.setattr(gmail, "service", fake.service)
    assert fetch(conn) == 1
    assert fake.downloads == ["old"]
    assert fake.pages == int(expired)
    assert conn.execute("SELECT payload->>'v' AS v FROM raw_items WHERE mailbox=%s", (MAILBOX,)).fetchone()["v"] == "2"


def test_failure_keeps_committed_batch_and_old_cursor_then_resumes(conn, monkeypatch):
    db.set_cursor(conn, "gmail", MAILBOX, "old-cursor")
    conn.commit()
    fake = FakeGmail([message(str(i)) for i in range(6)])
    fake.fail_id = "4"
    monkeypatch.setattr(gmail, "service", fake.service)
    monkeypatch.setattr(gmail, "CHUNK", 3)
    with pytest.raises(RuntimeError, match="injected"):
        fetch(conn)
    conn.rollback()
    assert db.get_cursor(conn, "gmail", MAILBOX) == "old-cursor"
    assert conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"] == 3
    fake.fail_id = None
    fake.downloads.clear()
    assert fetch(conn) == 3
    assert set(fake.downloads) == {"3", "4", "5"}
    assert db.get_cursor(conn, "gmail", MAILBOX) == "new-cursor"


def test_fetch_120_messages_at_least_five_times_faster(conn, monkeypatch):
    fake = FakeGmail([message(str(i)) for i in range(120)], latency=.05)
    monkeypatch.setattr(gmail, "service", fake.service)
    start = time.perf_counter()
    assert fetch(conn, full=True) == 120
    parallel = time.perf_counter() - start
    fake.downloads.clear()
    rules = gmail.load_rules()
    start = time.perf_counter()
    # Same downloads, payload conversion, DB batch writes and cursor commit, sequentially.
    payloads = [gmail.fetch_message(MAILBOX, str(i), {"SENT": "Sent"}, rules) for i in range(120)]
    db.upsert_raw_many(conn, "gmail", MAILBOX, [(p["id"], p) for p in payloads])
    conn.commit()
    db.set_cursor(conn, "gmail", MAILBOX, "new-cursor")
    conn.commit()
    sequential = time.perf_counter() - start
    assert sequential / parallel >= 5, (sequential, parallel)
    assert fake.pages == 2


def test_contacts_and_sheets_still_store_through_upsert(conn, monkeypatch):
    class People:
        def people(self): return self
        def connections(self): return self
        def otherContacts(self): return self
        def list(self, **kw):
            key = "connections" if "resourceName" in kw else "otherContacts"
            return Request(lambda: {key: [{"resourceName": key + "/1", "names": [{"displayName": "Bob"}],
                "emailAddresses": [{"value": "bob@client.test"}], "phoneNumbers": [{"value": "09175551234"}]}]})
    class Sheets:
        def spreadsheets(self): return self
        def values(self): return self
        def get(self, **kw):
            return Request(lambda: {"values": [["Name", "Email"], ["Bob", "bob@client.test"]]} if "range" in kw else {
                "properties": {"title": "Tracker"}, "sheets": [{"properties": {"title": "Clients"}}]})
    monkeypatch.setattr(contacts, "service", lambda *_: People())
    monkeypatch.setattr(sheets, "service", lambda *_: Sheets())
    assert contacts.fetch_contacts(conn, MAILBOX, lambda _: None) == 2
    assert sheets.fetch_sheets(conn, ["sheet1"], lambda _: None) == 1
    assert sheets.fetch_sheets(conn, ["sheet1"], lambda _: None) == 1
    rows = conn.execute("SELECT source, payload FROM raw_items ORDER BY source, source_id").fetchall()
    assert [r["source"] for r in rows] == ["contacts", "contacts", "sheets"]
    assert rows[0]["payload"]["emails"] == ["bob@client.test"]
    assert rows[-1]["payload"]["record"] == {"Name": "Bob", "Email": "bob@client.test"}


@pytest.mark.parametrize("existing", [False, True])
def test_deleted_message_tombstone_prevents_refetch_and_keeps_existing_payload(conn, monkeypatch, existing):
    if existing:
        payload = gmail.message_to_payload(message("gone"), MAILBOX, {})
        payload["v"] = 1
        db.upsert_raw(conn, "gmail", MAILBOX, "gone", payload)
        conn.execute("UPDATE raw_items SET extracted_at=now()")
        conn.execute("INSERT INTO signals (raw_item_id,key,data) SELECT id,'kept','{}' FROM raw_items")
        conn.commit()
    before = conn.execute("SELECT * FROM raw_items").fetchall()
    signals = conn.execute("SELECT * FROM signals").fetchall()
    fake = FakeGmail([message("gone")])
    fake.missing.add("gone")
    monkeypatch.setattr(gmail, "service", fake.service)
    assert fetch(conn) == 0
    db.set_cursor(conn, "gmail", MAILBOX, "old-cursor")
    conn.commit()
    assert fetch(conn) == 0
    assert fake.downloads == ["gone"]
    assert conn.execute("SELECT * FROM raw_items").fetchall() == before
    assert conn.execute("SELECT * FROM signals").fetchall() == signals
    assert conn.execute("SELECT count(*) AS n FROM gone_items").fetchone()["n"] == 1
    assert db.missing_ids(conn, "gmail", MAILBOX, ["gone"], 2) == []
    assert db.outdated_gmail_ids(conn, MAILBOX, 2) == []
    assert db.missing_ids(conn, "gmail", "different", ["gone"], 2) == ["gone"]


@pytest.fixture
def people_service(monkeypatch):
    state = {"calls": [], "saved": [], "other": [], "fail_other": False}
    class People:
        def people(self): return self
        def connections(self): return self
        def otherContacts(self): return self
        def list(self, **kw):
            key = "saved" if "resourceName" in kw else "other"
            def run():
                state["calls"].append(key)
                if key == "other" and state["fail_other"]:
                    raise RuntimeError("other contacts failed")
                return {"connections" if key == "saved" else "otherContacts": state[key]}
            return Request(run)
    monkeypatch.setattr(contacts, "service", lambda *_: People())
    return state


@pytest.mark.parametrize("hours,full,called", [(23.99, False, False), (24, False, True), (25, False, True), (1, True, True)])
def test_contacts_freshness_24_hour_boundary_and_full_rescan(conn, monkeypatch, people_service, hours, full, called):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now
    monkeypatch.setattr(contacts, "datetime", Clock, raising=False)
    db.set_cursor(conn, "contacts", MAILBOX, (now - timedelta(hours=hours)).isoformat())
    conn.commit()
    assert contacts.fetch_contacts(conn, MAILBOX, lambda _: None, full=full) == 0
    assert people_service["calls"] == (["saved", "other"] if called else [])
    assert db.get_cursor(conn, "contacts", MAILBOX) == (now.isoformat() if called else (now - timedelta(hours=hours)).isoformat())


def test_empty_contacts_mark_fresh_and_partial_failure_does_not(conn, people_service):
    assert contacts.fetch_contacts(conn, MAILBOX, lambda _: None) == 0
    marker = db.get_cursor(conn, "contacts", MAILBOX)
    assert marker
    people_service["saved"] = [{"resourceName": "p/1", "names": [{"displayName": "Bob"}]}]
    people_service["fail_other"] = True
    with pytest.raises(RuntimeError, match="other contacts"):
        contacts.fetch_contacts(conn, MAILBOX, lambda _: None, full=True)
    conn.rollback()
    assert db.get_cursor(conn, "contacts", MAILBOX) == marker
    assert conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"] == 0


@pytest.mark.parametrize("action", ["pause", "stop"])
def test_gmail_chunk_control_keeps_committed_rows_and_blocks_later_sources(conn, monkeypatch, action, authenticated_client):
    from scraper import app as api
    from psycopg.types.json import Jsonb

    fake = FakeGmail([message(str(i)) for i in range(5)])
    monkeypatch.setattr(gmail, "service", fake.service)
    monkeypatch.setattr(gmail, "CHUNK", 2)
    monkeypatch.setattr(api, "PAUSE_POLL_SECONDS", .01, raising=False)
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    params = {"sources": ["gmail", "contacts"], "mailboxes": [MAILBOX], "since": "2025-01-01", "full": False}
    jid = conn.execute("INSERT INTO jobs(name,status,params) VALUES ('scrape','running',%s) RETURNING id", (Jsonb(params),)).fetchone()["id"]
    conn.commit()
    later = []
    monkeypatch.setattr(contacts, "fetch_contacts", lambda *args, **kwargs: later.append("contacts"))
    original = api.JobLog.checkpoint
    requested = threading.Event()
    def checkpoint(logger):
        with db.connect() as observer:
            n = observer.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"]
            if n == 2 and not requested.is_set():
                observer.execute("UPDATE jobs SET control=%s WHERE id=%s", (action, jid))
                requested.set()
        return original(logger)
    monkeypatch.setattr(api.JobLog, "checkpoint", checkpoint)
    worker = threading.Thread(target=api._run_job, args=(jid, "scrape", params))
    worker.start()
    client = authenticated_client
    try:
        assert requested.wait(timeout=5)
        if action == "pause":
            deadline = time.monotonic() + 5
            while conn.execute("SELECT status FROM jobs").fetchone()["status"] != "paused":
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"] == 2
            assert later == []
            assert client.post(f"/api/jobs/{jid}/continue", headers={"X-Local-GUI": "1"}).status_code == 200
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        if worker.is_alive():
            client.post(f"/api/jobs/{jid}/stop", headers={"X-Local-GUI": "1"})
            worker.join(timeout=5)
    row = conn.execute("SELECT status, control FROM jobs").fetchone()
    assert row == {"status": "done" if action == "pause" else "stopped", "control": None}
    assert conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"] == (5 if action == "pause" else 2)
    lines = [r["line"] for r in conn.execute("SELECT line FROM job_log")]
    if action == "stop":
        assert later == [] and not any("finished" in line for line in lines)
    else:
        assert later == ["contacts"]
        assert any(line.endswith("paused") for line in lines) and any(line.endswith("continued") for line in lines)


def test_message_pool_uses_env_worker_count_and_rate_retries_reset_per_chunk(conn, monkeypatch):
    from scraper import google_client

    fake = FakeGmail([message(str(i)) for i in range(4)])
    monkeypatch.setattr(gmail, "service", fake.service)
    monkeypatch.setattr(gmail, "CHUNK", 2)
    monkeypatch.setenv("SCRAPE_MESSAGE_WORKERS", "2")
    monkeypatch.setattr(google_client.time, "sleep", lambda _: None)
    original_pool, pool_sizes = gmail.ThreadPoolExecutor, []
    def pool(workers):
        pool_sizes.append(workers)
        return original_pool(workers)
    monkeypatch.setattr(gmail, "ThreadPoolExecutor", pool)
    original = Endpoint.get
    failed, barrier = [], threading.Barrier(2)
    def get(endpoint, **kw):
        request = original(endpoint, **kw)
        if endpoint.kind != "messages":
            return request
        def execute():
            if kw["id"] == "0" and not failed:
                failed.append(True)
                raise HttpError(SimpleNamespace(status=429, reason="rate limited"), b'{}')
            if kw["id"] in ("0", "1"):
                barrier.wait(timeout=5)
            return request.execute()
        return Request(execute)
    monkeypatch.setattr(Endpoint, "get", get)
    log = []
    assert fetch(conn, log=log.append) == 4
    assert pool_sizes == [2]
    assert f"{MAILBOX}: 2/4, 1 rate-limit retries" in log
    assert f"{MAILBOX}: 4/4" in log
    assert len(fake.created) == 3 and len(set(fake.created)) == 3


@pytest.mark.parametrize('empty', [False, True])
def test_progress_counts_chunks_deleted_messages_and_cumulative_retries(conn, monkeypatch, empty):
    fake = FakeGmail([] if empty else [message(str(i)) for i in range(3)])
    fake.missing.add('1')
    monkeypatch.setattr(gmail, 'service', fake.service)
    monkeypatch.setattr(gmail, 'CHUNK', 2)
    original = gmail.execute
    def execute(request, *, on_rate_limit=None):
        if on_rate_limit:
            on_rate_limit()
        return original(request, on_rate_limit=on_rate_limit)
    monkeypatch.setattr(gmail, 'execute', execute)
    # Listing pages go through google_client.execute; count those calls the same way.
    import scraper.google_client as google_client
    listing = google_client.execute
    def list_execute(request, *, on_rate_limit=None, **kwargs):
        if on_rate_limit:
            on_rate_limit()
        return listing(request, on_rate_limit=on_rate_limit, **kwargs)
    monkeypatch.setattr(google_client, 'execute', list_execute)
    reports = []
    class Log:
        def __call__(self, line): pass
        def mailbox_progress(self, mailbox, fetched, total, rate_limited):
            assert mailbox == MAILBOX
            reports.append((fetched, total, rate_limited))
    assert fetch(conn, log=Log()) == (0 if empty else 2)
    assert reports[0][0:2] == (0, 0 if empty else 3)
    assert reports[-1] == ((0, 0, 3) if empty else (3, 3, 6))
    if not empty:
        assert reports[1] == (2, 3, 5)
