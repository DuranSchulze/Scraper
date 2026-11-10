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


def test_phone_formats_merge_into_one_e164_number():
    f = fields([sig("inquiry", phones=["0917 123 4567"]),
                sig("inquiry", phones=["+63 917 123 4567"])])
    assert f["phones"]["value"] == ["+639171234567"]


def test_foreign_phone_with_and_without_plus_merge_into_one_e164_number():
    f = fields([sig("inquiry", phones=["14155550123"]),
                sig("inquiry", phones=["+1 415 555 0123"])])
    assert f["phones"]["value"] == ["+14155550123"]


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


from datetime import timedelta


def detail_signal(stage="payment_confirmed", rid=1, day=0, amount=None, currency="PHP", thread="sales@acme.test:t", **kw):
    return signal("bob@client.test", "gmail", rid, stage=stage,
                  date=NOW + timedelta(days=day) if day is not None else None,
                  thread=thread, amounts=[] if amount is None else [
                      {"amount": amount, "currency": currency, "total": True}],
                  snippet=f"Message {rid}", **kw)


def payment_rows(signals):
    return fields(signals)["payments"]["value"]


def test_claim_confirmation_and_installments_form_two_payments():
    signals = [detail_signal("payment_claim", rid=2, day=1, amount=500, has_proof=True),
               detail_signal(rid=3, day=2, amount=500),
               detail_signal(rid=4, day=3, amount=1000)]
    rows = payment_rows(signals)
    assert rows == [
        {"date": (NOW + timedelta(days=1)).isoformat(), "amount": 500, "currency": "PHP", "basis": "stated", "raw_item_id": 2, "kind": "confirmed"},
        {"date": (NOW + timedelta(days=3)).isoformat(), "amount": 1000, "currency": "PHP", "basis": "stated", "raw_item_id": 4, "kind": "confirmed"}]
    assert fields(signals)["client_since"]["value"] == (NOW + timedelta(days=1)).isoformat()


def test_payment_uses_first_total_else_first_amount():
    s = detail_signal()
    s["amounts"] = [{"amount": 300, "currency": "USD", "total": False},
                    {"amount": 1000, "currency": "PHP", "total": True},
                    {"amount": 2000, "currency": "PHP", "total": True}]
    assert payment_rows([s])[0]["amount"] == 1000
    s["amounts"] = s["amounts"][:1]
    assert payment_rows([s])[0]["currency"] == "USD"
    assert payment_rows([s])[0]["amount"] == 300


def test_mailbox_copies_keep_lowest_raw_id_with_five_minute_window():
    a = detail_signal(rid=9, amount=1000)
    b = {**a, "raw_item_id": 2, "thread": "billing@acme.test:copy", "date": NOW + timedelta(minutes=5)}
    assert len(payment_rows([a, b])) == 1
    assert payment_rows([a, b])[0]["raw_item_id"] == 2
    b["date"] = NOW + timedelta(minutes=5, seconds=1)
    assert len(payment_rows([a, b])) == 2
    b["date"] = NOW
    b["key"] = "alias@client.test"
    merged = build_candidates([a, b], RULES, NOW, merged={"alias@client.test": "bob@client.test"})
    assert len(merged["bob@client.test"]["fields"]["payments"]["value"]) == 2


def test_no_amount_confirmation_joins_single_stated_amount():
    rows = payment_rows([detail_signal("payment_claim", rid=8, day=1, amount=1000, has_proof=True),
                         detail_signal(rid=3, day=2)])
    assert len(rows) == 1 and rows[0]["amount"] == 1000
    assert rows[0]["kind"] == "confirmed" and rows[0]["raw_item_id"] == 8


def test_multiple_stated_amounts_keep_no_amount_group_separate():
    rows = payment_rows([detail_signal(rid=1, amount=500), detail_signal(rid=2, day=1, amount=700),
                         detail_signal(rid=3, day=2), detail_signal(rid=4, day=3)])
    assert [p["amount"] for p in rows] == [500, 700, None]
    assert rows[-1]["date"] == (NOW + timedelta(days=2)).isoformat()


def test_invoice_fallback_latest_same_thread_and_unknown_warning():
    signals = [detail_signal("invoiced", rid=1, day=-3, amount=500),
               detail_signal("invoiced", rid=2, day=-1, amount=1000),
               detail_signal("invoiced", rid=3, day=1, amount=9999, thread="other:t"),
               detail_signal("payment_screenshot", rid=4)]
    f = fields(signals)
    assert f["payments"]["value"] == [{"date": NOW.isoformat(), "amount": 1000, "currency": "PHP",
        "basis": "invoice", "raw_item_id": 4, "kind": "screenshot"}]
    assert f"Payment on {NOW.date()} uses the invoice amount from the same thread; confirm it" in f["needs_check"]["value"]
    f = fields([detail_signal(rid=5)])
    assert f["payments"]["value"][0]["basis"] == "unknown"
    assert f["payments"]["value"][0]["currency"] is None
    assert f"Payment on {NOW.date()}: amount not found" in f["needs_check"]["value"]


def test_different_threads_equal_amounts_retained_and_warned_within_seven_days():
    signals = [detail_signal(rid=1, amount=1000), detail_signal(rid=2, day=7, amount=1000, thread="sales@acme.test:other")]
    f = fields(signals)
    assert len(f["payments"]["value"]) == 2
    assert any("Possible duplicate payment: 1000" in w and "remove one if it is the same" in w for w in f["needs_check"]["value"])
    signals[1]["date"] += timedelta(seconds=1)
    assert not any("Possible duplicate payment" in w for w in fields(signals)["needs_check"]["value"])
    signals[1]["date"] = NOW
    signals[1]["amounts"][0]["currency"] = "USD"
    assert not any("Possible duplicate payment" in w for w in fields(signals)["needs_check"]["value"])


def test_undated_payments_last_and_client_since_absent_unless_dated():
    undated = detail_signal(day=None, thread="mb:u")
    assert "client_since" not in fields([undated])
    rows = payment_rows([undated, detail_signal(rid=2, day=2, thread="mb:d")])
    assert rows[-1]["date"] is None
    assert "client_since" not in fields([detail_signal("inquiry")])
    assert payment_rows([detail_signal("payment_claim")]) == []
    assert payment_rows([detail_signal("payment_screenshot")]) == []


def test_services_availed_only_payment_threads_with_deal_fallback():
    signals = [detail_signal("inquiry", rid=1, thread="mb:lead", services=["SEO"]),
               detail_signal("quoted", rid=2, services=["Website"]), detail_signal(rid=3)]
    f = fields(signals)
    assert f["services"]["value"] == ["SEO", "Website"]
    assert f["services_availed"]["value"] == ["Website"]
    signals[-1]["thread"] = "mb:paid_elsewhere"
    fallback = fields(signals)["services_availed"]
    assert fallback["value"] == ["Website"] and fallback["confidence"] == .5
    assert fields(signals[:-1])["services_availed"]["value"] == []


def test_handlers_newest_department_counts_and_contact_fallback():
    signals = [detail_signal("inquiry", rid=1, day=-5, department="Sales"),
               detail_signal("quoted", rid=2, day=-2, department="Sales"),
               detail_signal(rid=3, department="Finance", thread="billing@acme.test:t"),
               detail_signal("inquiry", rid=4, day=2, department="", thread="info@acme.test:u")]
    f = fields(signals)
    assert f["department"]["value"] == "Finance"
    assert f["handlers"]["value"] == [
        {"department": "", "mailbox": "info@acme.test", "first": (NOW + timedelta(days=2)).isoformat(), "last": (NOW + timedelta(days=2)).isoformat(), "messages": 1},
        {"department": "Finance", "mailbox": "billing@acme.test", "first": NOW.isoformat(), "last": NOW.isoformat(), "messages": 1},
        {"department": "Sales", "mailbox": "sales@acme.test", "first": (NOW - timedelta(days=5)).isoformat(), "last": (NOW - timedelta(days=2)).isoformat(), "messages": 2}]
    s = detail_signal("inquiry", day=None, department="Support")
    assert fields([s])["handlers"]["value"][0]["first"] is None
    s["source"] = "sheets"
    assert fields([s])["handlers"]["value"] == []
    assert fields([s])["department"]["value"] == "Support"


def test_timeline_has_more_than_evidence_limit_and_keeps_newest_two_hundred():
    signals = [detail_signal("inquiry", rid=i, day=i, department="Sales") for i in range(205)]
    signals[-1]["snippet"] = "x" * 300
    c = build_candidates(signals, RULES, NOW)["bob@client.test"]
    timeline = c["fields"]["timeline"]["value"]
    assert timeline["truncated"] is True
    assert [x["raw_item_id"] for x in timeline["items"]] == list(range(5, 205))
    assert len(timeline["items"][-1]["snippet"]) == 200
    assert timeline["items"][-1]["mailbox"] == "sales@acme.test"
    assert timeline["items"][-1]["department"] == "Sales"
    assert timeline["items"][-1]["amounts"] == []
    assert len(c["evidence"]) == 25
    assert fields(signals[:30])["timeline"]["value"]["truncated"] is False
    assert len(fields(signals[:30])["timeline"]["value"]["items"]) == 30


def test_duplicate_warning_keeps_the_full_amount():
    f = fields([detail_signal(rid=1, amount=1000001.25),
                detail_signal(rid=2, day=1, amount=1000001.25, thread="sales@acme.test:other")])
    assert any("Possible duplicate payment: 1000001.25 on" in w for w in f["needs_check"]["value"])


@pytest.mark.parametrize("screenshot_day,confirmation_day,same_thread,warn", [
    (0, 7, False, True), (0, 8, False, False), (0, 1, True, False),
    (None, 1, False, False), (0, None, False, False),
])
def test_unknown_confirmation_near_screenshot_keeps_payments_and_warns_once(
        screenshot_day, confirmation_day, same_thread, warn):
    deal_thread = "sales@acme.test:deal"
    signals = [detail_signal("invoiced", rid=1, day=-1, amount=1000, thread=deal_thread),
               detail_signal("payment_screenshot", rid=2, day=screenshot_day, thread=deal_thread),
               detail_signal(rid=3, day=confirmation_day,
                             thread=deal_thread if same_thread else "billing@acme.test:confirmation")]
    f = fields(signals)
    assert len(f["payments"]["value"]) == (1 if same_thread else 2)
    duplicate_checks = [w for w in f["needs_check"]["value"] if w.startswith("Possible duplicate payment:")]
    assert duplicate_checks == ([
        "Possible duplicate payment: amount unknown on 2026-11-08 is within 7 days of 1000 PHP on 2026-11-01; remove one if it is the same"
    ] if warn else [])


def test_unknown_payment_before_known_payment_uses_unknown_date_in_warning():
    f = fields([detail_signal(rid=1, day=0),
                detail_signal(rid=2, day=1, amount=500, thread="billing@acme.test:other")])
    assert len(f["payments"]["value"]) == 2
    assert [w for w in f["needs_check"]["value"] if w.startswith("Possible duplicate payment:")] == [
        "Possible duplicate payment: amount unknown on 2026-11-01 is within 7 days of 500 PHP on 2026-11-02; remove one if it is the same"]


def test_two_unknown_payments_in_different_threads_warn_once():
    f = fields([detail_signal(rid=1), detail_signal(rid=2, day=1, thread="billing@acme.test:other")])
    assert len(f["payments"]["value"]) == 2
    assert [w for w in f["needs_check"]["value"] if w.startswith("Possible duplicate payment:")] == [
        "Possible duplicate payment: amount unknown on 2026-11-01 is within 7 days of amount unknown on 2026-11-02; remove one if it is the same"]


def test_unknown_and_installments_in_same_thread_do_not_warn_about_duplicates():
    f = fields([detail_signal(rid=1, amount=500), detail_signal(rid=2, day=1, amount=1000),
                detail_signal(rid=3, day=2)])
    assert len(f["payments"]["value"]) == 3
    assert not any(w.startswith("Possible duplicate payment:") for w in f["needs_check"]["value"])


def test_different_known_amounts_in_different_threads_do_not_warn_about_duplicates():
    f = fields([detail_signal(rid=1, amount=500),
                detail_signal(rid=2, day=1, amount=1000, thread="billing@acme.test:other")])
    assert len(f["payments"]["value"]) == 2
    assert not any(w.startswith("Possible duplicate payment:") for w in f["needs_check"]["value"])
