"""Person-level payment verification and visible uncertainty."""

from datetime import datetime, timezone

import pytest

from scraper.config import load_rules
from scraper.extract import extract_contact, extract_item, signal
from scraper.merge import build_candidates, from_json, to_json

RULES = load_rules()
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def sig(stage, **kw):
    return signal("bob@client.test", "gmail", 1, stage=stage, date=NOW, **kw)


def fields(signals):
    return build_candidates(signals, RULES, NOW)["bob@client.test"]["fields"]


@pytest.mark.parametrize("signals", [
    [sig("payment_confirmed")],
    [sig("payment_claim", has_proof=True)],
    [sig("quoted", thread="mb:t"), sig("payment_screenshot", thread="mb:t")],
    [sig("invoiced", thread="mb:t"), sig("payment_screenshot", thread="mb:t")],
])
def test_paid_needs_confirmation_claim_proof_or_deal_thread(signals):
    f = fields(signals)
    assert f["stage"]["value"] == "paid"
    assert f["engagement_paid"]["value"] is True


def test_claim_without_proof_warns_and_stays_inquiry():
    f = fields([sig("payment_claim")])
    assert f["stage"]["value"] == "inquiry"
    assert "no proof" in f["needs_check"]["value"][0]


def test_screenshot_only_extract_to_merge_visible_with_warning():
    row = {"id": 1, "source": "gmail", "payload": {
        "headers": {"from": "bob@client.test", "to": "sales@acme.test", "subject": "Hello"},
        "body": "", "direction": "in", "internal_date": int(NOW.timestamp() * 1000),
        "mailbox": "sales@acme.test", "thread_id": "image",
        "attachments": [{"filename": "proof.png", "mime": "image/png", "size": 40000}]}}
    f = fields(extract_item(row, RULES, {"acme.test"}))
    assert f["stage"]["value"] == "inquiry" and not f["engagement_paid"]["value"]
    assert "outside a quote/invoice thread" in f["needs_check"]["value"][0]


def test_other_person_deal_does_not_validate_screenshot():
    other = {**sig("quoted", thread="mb:t"), "key": "other@client.test", "email": "other@client.test"}
    f = fields([other, sig("payment_screenshot", thread="mb:t")])
    assert not f["engagement_paid"]["value"]


def test_invoice_without_amount_warns():
    f = fields([sig("invoiced")])
    assert f["stage"]["value"] == "invoiced"
    assert "amount was not found" in f["needs_check"]["value"][0]


def test_invoice_keeps_total_and_evidence():
    s = sig("invoiced", amounts=[{"amount": 900, "currency": "PHP", "total": False},
                                 {"amount": 1000, "currency": "PHP", "total": True}])
    c = build_candidates([s], RULES, NOW)[s["key"]]
    assert c["fields"]["amounts"]["value"] == [{"stage": "invoiced", "amount": 1000,
        "currency": "PHP", "date": NOW.isoformat(), "raw_item_id": 1}]
    assert c["evidence"][0]["stage"] == "invoiced"


def test_contacts_only_add_details():
    contacts = extract_contact(2, {"kind": "contact", "name": "Bob", "emails": ["bob@client.test"],
                                  "phones": ["09175551234"], "company": "Bob Co"}, set())
    assert build_candidates(contacts, RULES, NOW) == {}
    assert fields(contacts + [sig("inquiry")])["name"]["value"] == "Bob"


@pytest.mark.parametrize("date", [NOW, None])
def test_signal_json_roundtrip(date):
    s = sig("invoiced", amounts=[{"amount": 1000, "currency": "PHP", "total": True}])
    s["date"] = date
    assert from_json(to_json(s)) == s
