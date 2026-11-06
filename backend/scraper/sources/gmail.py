"""Fetch Gmail messages into raw_items. Incremental via Gmail historyId, parallel downloads."""

import base64
import html
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from email.utils import parseaddr

from googleapiclient.errors import HttpError

from .. import db
from ..db import JobStopped
from ..config import env, env_int, env_list, load_rules
from ..extract import wants_pdf_text
from ..google_client import execute, pages, service

KEEP_HEADERS = {"from", "to", "cc", "reply-to", "subject", "date", "delivered-to",
                "list-unsubscribe", "list-id", "auto-submitted", "precedence"}
MAX_BODY = 20_000          # chars kept per message; enough for signatures, quotes and form fields
MAX_PDF_BYTES = 5_000_000  # invoice/quote PDFs larger than this are not downloaded
MAX_PDF_TEXT = 8_000
PAYLOAD_VERSION = 2        # v2 adds attachments + PDF text; older rows are refetched
WORKERS = 16               # default; SCRAPE_MESSAGE_WORKERS bounds each mailbox pool to 1-25
CHUNK = 200                # messages saved to Neon per batch

_local = threading.local()


def list_mailboxes() -> list[str]:
    """MAILBOXES from .env, else every active user in the domain."""
    explicit = env_list("MAILBOXES")
    if explicit:
        return explicit
    admin = service("admin", env("GOOGLE_ADMIN_EMAIL"))
    users = pages(lambda t: admin.users().list(
        customer="my_customer", maxResults=500, pageToken=t, query="isSuspended=false"), "users")
    return [u["primaryEmail"].lower() for u in users]


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(s)).strip()


def walk(part: dict):
    """Every MIME part, depth-first, in document order."""
    stack = [part]
    while stack:
        p = stack.pop()
        yield p
        stack.extend(reversed(p.get("parts", [])))


def body_text(part: dict) -> str:
    """Plain-text body of a Gmail message payload; falls back to stripped HTML."""
    plain, rich = [], []
    for p in walk(part):
        data = p.get("body", {}).get("data")
        if not data or p.get("filename"):
            continue
        if p.get("mimeType") == "text/plain":
            plain.append(_decode(data).decode("utf-8", "replace"))
        elif p.get("mimeType") == "text/html":
            rich.append(_decode(data).decode("utf-8", "replace"))
    return "\n".join(plain) if plain else html_to_text("\n".join(rich))


def attachments_of(part: dict) -> list[dict]:
    return [{"filename": p["filename"], "mime": p.get("mimeType", ""),
             "size": p.get("body", {}).get("size", 0), "att_id": p.get("body", {}).get("attachmentId")}
            for p in walk(part) if p.get("filename")]


def message_to_payload(msg: dict, mailbox: str, label_names: dict[str, str]) -> dict:
    headers = {h["name"].lower(): h["value"]
               for h in msg["payload"].get("headers", []) if h["name"].lower() in KEEP_HEADERS}
    label_ids = msg.get("labelIds", [])
    sender = parseaddr(headers.get("from", ""))[1].lower()
    return {
        "v": PAYLOAD_VERSION,
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "labels": [label_names.get(i, i) for i in label_ids],
        "internal_date": int(msg.get("internalDate", 0)),
        "headers": headers,
        "body": body_text(msg["payload"])[:MAX_BODY],
        "attachments": attachments_of(msg["payload"]),
        "attachment_text": {},
        "direction": "out" if "SENT" in label_ids or sender == mailbox.lower() else "in",
        "mailbox": mailbox,
    }


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:3])[:MAX_PDF_TEXT]
    except JobStopped:
        raise
    except Exception:  # encrypted or malformed PDF: leave it for the reviewer
        return ""


def _gmail(mailbox: str):
    """One API client per worker thread (the client library is not thread-safe)."""
    if getattr(_local, "mailbox", None) != mailbox:
        _local.gm, _local.mailbox = service("gmail", mailbox), mailbox
    return _local.gm


def fetch_message(mailbox: str, mid: str, label_names: dict, rules: dict, *, on_rate_limit=None) -> dict | None:
    gm = _gmail(mailbox)
    try:
        msg = execute(gm.users().messages().get(userId="me", id=mid, format="full"), on_rate_limit=on_rate_limit)
    except HttpError as e:
        if e.resp.status == 404:  # deleted since listing
            return None
        raise
    payload = message_to_payload(msg, mailbox, label_names)
    subject = payload["headers"].get("subject", "")
    for att in payload["attachments"]:
        # Invoice/quote amounts are often only inside the attached PDF.
        if (att["mime"] == "application/pdf" and att["att_id"] and att["size"] <= MAX_PDF_BYTES
                and wants_pdf_text(subject, att["filename"], rules)):
            data = execute(gm.users().messages().attachments().get(
                userId="me", messageId=mid, id=att["att_id"]), on_rate_limit=on_rate_limit)["data"]
            payload["attachment_text"][att["filename"]] = pdf_text(_decode(data))
    return payload


def _history_ids(gm, start: str, *, on_rate_limit=None) -> list[str]:
    ids = []
    for h in pages(lambda t: gm.users().history().list(
            userId="me", startHistoryId=start, historyTypes=["messageAdded"], pageToken=t), "history", on_rate_limit=on_rate_limit):
        ids += [m["message"]["id"] for m in h.get("messagesAdded", [])]
    return list(dict.fromkeys(ids))


def _all_ids(gm, since: str, *, on_rate_limit=None) -> list[str]:
    q = f"after:{since.replace('-', '/')}"
    return [m["id"] for m in pages(lambda t: gm.users().messages().list(
        userId="me", q=q, maxResults=500, pageToken=t), "messages", on_rate_limit=on_rate_limit)]


def fetch_mailbox(conn, mailbox: str, since: str, full: bool, log, checkpoint=lambda: None) -> int:
    """Store new messages of one mailbox. Returns count stored."""
    workers = env_int("SCRAPE_MESSAGE_WORKERS", WORKERS, 1, 25)
    retries = 0
    retry_lock = threading.Lock()
    def rate_limit_retry():
        nonlocal retries
        with retry_lock:
            retries += 1
    rules = load_rules()
    gm = service("gmail", mailbox)
    start_history = execute(gm.users().getProfile(userId="me"), on_rate_limit=rate_limit_retry)["historyId"]
    label_names = {l["id"]: l["name"] for l in execute(gm.users().labels().list(userId="me"), on_rate_limit=rate_limit_retry)["labels"]}

    ids = None
    cursor = None if full else db.get_cursor(conn, "gmail", mailbox)
    if cursor:
        try:
            ids = _history_ids(gm, cursor, on_rate_limit=rate_limit_retry)
        except HttpError as e:
            if e.resp.status != 404:
                raise
            log(f"{mailbox}: history expired, doing full scan")
    if ids is None:
        ids = _all_ids(gm, since, on_rate_limit=rate_limit_retry)

    ids = list(dict.fromkeys(ids + db.outdated_gmail_ids(conn, mailbox, PAYLOAD_VERSION)))
    new = db.missing_ids(conn, "gmail", mailbox, ids, PAYLOAD_VERSION)
    log(f"{mailbox}: {len(ids)} messages found, {len(new)} to download"
        + (f", {retries} rate-limit retries" if retries else ""))
    retries = 0
    stored = 0
    with ThreadPoolExecutor(workers) as pool:
        for i in range(0, len(new), CHUNK):
            chunk = new[i:i + CHUNK]
            payloads = list(pool.map(lambda mid: fetch_message(mailbox, mid, label_names, rules, on_rate_limit=rate_limit_retry), chunk))
            items = [(p["id"], p) for p in payloads if p]
            db.upsert_raw_many(conn, "gmail", mailbox, items)
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO gone_items (source, mailbox, source_id) VALUES ('gmail',%s,%s) ON CONFLICT DO NOTHING",
                    [(mailbox, mid) for mid, payload in zip(chunk, payloads) if payload is None])
            conn.commit()
            stored += len(items)
            log(f"{mailbox}: {stored}/{len(new)}" + (f", {retries} rate-limit retries" if retries else ""))
            retries = 0
            checkpoint()
    # Cursor saved only after every message is stored, so a crash just resumes.
    db.set_cursor(conn, "gmail", mailbox, str(start_history))
    conn.commit()
    checkpoint()
    return stored
