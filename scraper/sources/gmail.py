"""Fetch Gmail messages into raw_items. Incremental via Gmail historyId."""

import base64
import html
import re
from email.utils import parseaddr

from googleapiclient.errors import HttpError

from .. import db
from ..config import env, env_list
from ..google_client import execute, pages, service

KEEP_HEADERS = {"from", "to", "cc", "reply-to", "subject", "date", "delivered-to",
                "list-unsubscribe", "list-id", "auto-submitted", "precedence"}
MAX_BODY = 20_000  # chars kept per message; enough for signatures and form fields


def list_mailboxes() -> list[str]:
    """MAILBOXES from .env, else every active user in the domain."""
    explicit = env_list("MAILBOXES")
    if explicit:
        return explicit
    admin = service("admin", env("GOOGLE_ADMIN_EMAIL"))
    users = pages(lambda t: admin.users().list(
        customer="my_customer", maxResults=500, pageToken=t, query="isSuspended=false"), "users")
    return [u["primaryEmail"].lower() for u in users]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(s)).strip()


def body_text(part: dict) -> str:
    """Plain-text body of a Gmail message payload; falls back to stripped HTML."""
    plain, rich, stack = [], [], [part]
    while stack:
        p = stack.pop()
        data = p.get("body", {}).get("data")
        if data and p.get("mimeType") == "text/plain":
            plain.append(_decode(data))
        elif data and p.get("mimeType") == "text/html":
            rich.append(_decode(data))
        stack.extend(reversed(p.get("parts", [])))
    return "\n".join(plain) if plain else html_to_text("\n".join(rich))


def message_to_payload(msg: dict, mailbox: str, label_names: dict[str, str]) -> dict:
    headers = {h["name"].lower(): h["value"]
               for h in msg["payload"].get("headers", []) if h["name"].lower() in KEEP_HEADERS}
    label_ids = msg.get("labelIds", [])
    sender = parseaddr(headers.get("from", ""))[1].lower()
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "labels": [label_names.get(i, i) for i in label_ids],
        "internal_date": int(msg.get("internalDate", 0)),
        "headers": headers,
        "body": body_text(msg["payload"])[:MAX_BODY],
        "direction": "out" if "SENT" in label_ids or sender == mailbox.lower() else "in",
        "mailbox": mailbox,
    }


def _history_ids(gm, start: str) -> list[str]:
    ids = []
    for h in pages(lambda t: gm.users().history().list(
            userId="me", startHistoryId=start, historyTypes=["messageAdded"], pageToken=t), "history"):
        ids += [m["message"]["id"] for m in h.get("messagesAdded", [])]
    return list(dict.fromkeys(ids))


def _all_ids(gm, since: str) -> list[str]:
    q = f"after:{since.replace('-', '/')}"
    return [m["id"] for m in pages(lambda t: gm.users().messages().list(
        userId="me", q=q, maxResults=500, pageToken=t), "messages")]


def fetch_mailbox(conn, mailbox: str, since: str, full: bool, log) -> int:
    """Store new messages of one mailbox. Returns count stored."""
    gm = service("gmail", mailbox)
    start_history = execute(gm.users().getProfile(userId="me"))["historyId"]
    label_names = {l["id"]: l["name"] for l in execute(gm.users().labels().list(userId="me"))["labels"]}

    ids = None
    cursor = None if full else db.get_cursor(conn, "gmail", mailbox)
    if cursor:
        try:
            ids = _history_ids(gm, cursor)
        except HttpError as e:
            if e.resp.status != 404:
                raise
            log(f"{mailbox}: history expired, doing full scan")
    if ids is None:
        ids = _all_ids(gm, since)

    new = db.missing_ids(conn, "gmail", mailbox, ids)
    log(f"{mailbox}: {len(ids)} messages found, {len(new)} new")
    stored = 0
    for mid in new:
        try:
            msg = execute(gm.users().messages().get(userId="me", id=mid, format="full"))
        except HttpError as e:
            if e.resp.status == 404:  # deleted since listing
                continue
            raise
        db.upsert_raw(conn, "gmail", mailbox, mid, message_to_payload(msg, mailbox, label_names))
        stored += 1
        if stored % 100 == 0:
            conn.commit()
            log(f"{mailbox}: {stored}/{len(new)}")
    # Cursor saved only after every message is stored, so a crash just resumes.
    db.set_cursor(conn, "gmail", mailbox, str(start_history))
    conn.commit()
    return stored
