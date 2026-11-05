"""Extraction and merge rules on fake data only. No Google or DB access."""

import base64
from datetime import datetime, timedelta, timezone

import pytest

from scraper.config import load_rules
from scraper.extract import extract_contact, extract_gmail, extract_sheet, parse_form
from scraper.merge import build_candidates, resolve
from scraper.sources.gmail import message_to_payload
from scraper.sources.sheets import rows_to_records

RULES = load_rules()
INTERNAL = {"acme.test"}
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def mail(frm, to, subject, body, direction="in", days_ago=10, labels=(), **headers):
    h = {"from": frm, "to": to, "subject": subject, **headers}
    return {"headers": h, "body": body, "direction": direction, "labels": list(labels),
            "mailbox": "jane@acme.test",
            "internal_date": int((NOW - timedelta(days=days_ago)).timestamp() * 1000)}


def test_gmail_message_to_payload_decodes_body_and_direction():
    enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    msg = {"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "Label_7"], "internalDate": "1700000000000",
           "payload": {"mimeType": "multipart/alternative", "headers": [
               {"name": "From", "value": "Bob <bob@client.test>"}, {"name": "Subject", "value": "Hi"},
               {"name": "X-Junk", "value": "dropped"}],
               "parts": [{"mimeType": "text/plain", "body": {"data": enc("Hello there")}},
                         {"mimeType": "text/html", "body": {"data": enc("<p>Hello there</p>")}}]}}
    p = message_to_payload(msg, "jane@acme.test", {"Label_7": "Clients"})
    assert p["body"] == "Hello there"
    assert p["direction"] == "in"
    assert p["labels"] == ["INBOX", "Clients"]
    assert "x-junk" not in p["headers"]


def test_inbound_inquiry_extracts_sender_service_department_phone():
    p = mail('"Bob Cruz" <Bob@Client.test>', "sales@acme.test", "Quote for a new website",
             "Hi, we need a WordPress site.\n\nBob Cruz\nMobile: +63 917 123 4567\n\n> old quoted 0999 999 9999")
    [s] = extract_gmail(1, p, RULES, INTERNAL)
    assert s["email"] == "bob@client.test" and s["name"] == "Bob Cruz"
    assert s["services"] == ["Web Development"]
    assert s["department"] == "Sales"
    assert s["phones"] == ["+63 917 123 4567"]
    assert not s["paid"]


def test_form_notification_yields_the_inquirer_not_the_form_sender():
    body = "New submission\nName: Ana Reyes\nEmail: ana@shop.test\nPhone: 0917 555 1234\nMessage: SEO help"
    p = mail("WordPress <wordpress@acme.test>", "info@acme.test", "New form entry", body)
    [s] = extract_gmail(2, p, RULES, INTERNAL)
    assert (s["email"], s["name"]) == ("ana@shop.test", "Ana Reyes")
    assert s["services"] == ["Digital Marketing"]
    assert s["phones"] == ["0917 555 1234"]


def test_outbound_invoice_marks_external_recipients_paid():
    p = mail("jane@acme.test", "Bob <bob@client.test>, boss@acme.test", "Invoice #42", "Please see attached.",
             direction="out")
    [s] = extract_gmail(3, p, RULES, INTERNAL)
    assert s["email"] == "bob@client.test" and s["paid"] and s["direction"] == "out"


@pytest.mark.parametrize("payload", [
    mail("news@brand.test", "jane@acme.test", "Weekly deals", "Buy now", **{"list-unsubscribe": "<mailto:x>"}),
    mail("noreply@service.test", "jane@acme.test", "Your account", "Hello"),
    mail("boss@acme.test", "jane@acme.test", "Lunch?", "Invoice talk later"),
    mail("bob@client.test", "jane@acme.test", "Automatic reply: away", "Out", **{"auto-submitted": "auto-replied"}),
    mail("bob@client.test", "jane@acme.test", "Sale", "x", labels=["CATEGORY_PROMOTIONS"]),
])
def test_noise_and_internal_mail_dropped(payload):
    assert extract_gmail(9, payload, RULES, INTERNAL) == []


def test_unpaid_is_not_matched_as_paid():
    p = mail("bob@client.test", "jane@acme.test", "Unpaid invoices?", "no")
    # "invoices" is not the keyword "invoice" as a whole word, and "unpaid" is not "paid".
    assert not extract_gmail(10, p, RULES, INTERNAL)[0]["paid"]


def test_parse_form_requires_email():
    assert parse_form("Name: X\nPhone: 123", RULES) is None


def test_sheet_row_maps_headers_status_and_paid():
    [(row_no, rec)] = rows_to_records([["Client Name", "Email", "Status", "Service", "Paid"],
                                       ["Cora Lim", "cora@firm.test", "Ongoing", "SEO, Website", "Yes"],
                                       ["", "", ""]])
    payload = {"sheet": "Tracker", "tab": "2025", "row": row_no, "record": rec}
    [s] = extract_sheet(5, payload, RULES, INTERNAL)
    assert (s["email"], s["name"], s["status"], s["paid"]) == ("cora@firm.test", "Cora Lim", "active", True)
    assert s["services"] == ["SEO", "Website"]


def test_sheet_row_without_email_keyed_by_name():
    payload = {"sheet": "T", "tab": "A", "row": 2, "record": {"Name": "Dan  Uy", "Email": ""}}
    assert extract_sheet(6, payload, RULES, INTERNAL)[0]["key"] == "name:dan uy"


def test_merge_derives_status_and_skips_other_contacts_only():
    sigs = (extract_gmail(1, mail("bob@client.test", "sales@acme.test", "website?", "hi", days_ago=400), RULES, INTERNAL)
            + extract_gmail(2, mail("jane@acme.test", "bob@client.test", "Official receipt", "thanks",
                                    direction="out", days_ago=20), RULES, INTERNAL)
            + extract_gmail(3, mail("old@lead.test", "sales@acme.test", "pricing", "hi", days_ago=200), RULES, INTERNAL)
            + extract_gmail(4, mail("new@lead.test", "sales@acme.test", "pricing", "hi", days_ago=5), RULES, INTERNAL)
            + extract_contact(5, {"kind": "other", "name": "Random", "emails": ["rand@x.test"], "phones": [],
                                  "company": ""}, INTERNAL))
    c = build_candidates(sigs, RULES, NOW)
    assert "rand@x.test" not in c
    status = {k: v["fields"]["status"]["value"] for k, v in c.items()}
    assert status == {"bob@client.test": "active", "old@lead.test": "lost", "new@lead.test": "inquiry"}
    bob = c["bob@client.test"]["fields"]
    assert bob["engagement_paid"]["value"] and bob["department"]["value"] == "Sales"
    assert bob["company"]["value"] == "Client"


def test_merged_keys_fold_into_target():
    sigs = extract_contact(1, {"kind": "contact", "name": "Bob", "emails": ["b1@x.test", "b2@x.test"],
                               "phones": [], "company": ""}, INTERNAL)
    c = build_candidates(sigs, RULES, NOW, merged={"b2@x.test": "b1@x.test"})
    assert list(c) == ["b1@x.test"]
    assert c["b1@x.test"]["fields"]["emails"]["value"] == ["b1@x.test", "b2@x.test"]
    assert resolve("a", {"a": "b", "b": "a"}) in {"a", "b"}  # cycles terminate
