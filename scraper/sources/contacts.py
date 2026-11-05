"""Fetch a user's saved contacts and "other contacts" (auto-saved people they emailed)."""

from .. import db
from ..google_client import pages, service

PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations"


def person_payload(p: dict, kind: str) -> dict:
    return {
        "kind": kind,
        "name": (p.get("names") or [{}])[0].get("displayName", ""),
        "emails": [e["value"].lower() for e in p.get("emailAddresses", []) if e.get("value")],
        "phones": [n["value"] for n in p.get("phoneNumbers", []) if n.get("value")],
        "company": (p.get("organizations") or [{}])[0].get("name", ""),
    }


def fetch_contacts(conn, mailbox: str, log) -> int:
    ppl = service("people", mailbox)
    saved = pages(lambda t: ppl.people().connections().list(
        resourceName="people/me", personFields=PERSON_FIELDS, pageSize=1000, pageToken=t), "connections")
    other = pages(lambda t: ppl.otherContacts().list(
        readMask="names,emailAddresses,phoneNumbers", pageSize=1000, pageToken=t), "otherContacts")
    n = 0
    for kind, people in (("contact", saved), ("other", other)):
        for p in people:
            db.upsert_raw(conn, "contacts", mailbox, p["resourceName"], person_payload(p, kind))
            n += 1
    conn.commit()
    log(f"{mailbox}: {n} contacts")
    return n
