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
