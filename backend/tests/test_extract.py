"""Extraction and merge rules on fake data only. No Google or DB access."""

import base64
from datetime import datetime, timedelta, timezone

import pytest

from scraper.config import load_rules
from scraper.extract import amounts_in, classify_stage, extract_contact, extract_gmail, extract_sheet, parse_form, services_in
from scraper.extract import norm_phone, signature_phones, strict_phone
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
    p = mail('"Bob Cruz" <Bob@Client.test>', "sales@acme.test", "Quote for SEC registration",
             "Hi, we need to register our business.\n\nBob Cruz\nMobile: +63 917 123 4567\n\n> old quoted 0999 999 9999")
    [s] = extract_gmail(1, p, RULES, INTERNAL)
    assert s["email"] == "bob@client.test" and s["name"] == "Bob Cruz"
    assert s["services"] == ["Business Permits & Registration"]
    assert s["department"] == "Sales"
    assert s["phones"] == ["+63 917 123 4567"]
    assert s["stage"] == "inquiry"


def test_form_notification_yields_the_inquirer_not_the_form_sender():
    body = "New submission\nName: Ana Reyes\nEmail: ana@shop.test\nPhone: 0917 555 1234\nMessage: 9G visa help"
    p = mail("WordPress <wordpress@acme.test>", "info@acme.test", "New form entry", body)
    [s] = extract_gmail(2, p, RULES, INTERNAL)
    assert (s["email"], s["name"]) == ("ana@shop.test", "Ana Reyes")
    assert s["services"] == ["Work Visa Services"]
    assert s["phones"] == ["0917 555 1234"]


@pytest.mark.parametrize("phone,expected", [
    ("262-828-336-0000", None), ("2025 2025-0991", None), ("1600-4743-9", None),
    ("786-389781863", None), ("0010100500", None), ("1000195079", None),
    ("+63 917 123 4567", "+639171234567"), ("0917 555 1234", "+639175551234"),
    ("(02) 8478 5826", "+63284785826"), ("+1 470 705 3865", "+14707053865"),
    ("+61 3 8808 8130", "+61388088130"),
])
def test_signature_phone_validation_and_normalization(phone, expected):
    [s] = extract_gmail(1, mail("bob@client.test", "sales@acme.test", "Hello",
                              f"Regards,\nBob\nPhone: {phone}"), RULES, INTERNAL)
    assert s["phones"] == ([phone] if expected else [])
    assert strict_phone(phone) == bool(expected)
    if expected:
        assert norm_phone(phone) == expected


@pytest.mark.parametrize("word", RULES["phone_exclude_context"])
def test_signature_identifier_lines_are_excluded(word):
    assert signature_phones(f"{word.upper()}: +63 917 123 4567\nMobile: 0917 555 1234", RULES) == [
        "0917 555 1234"]


def test_signature_phones_stay_on_their_line_and_ignore_quotes():
    body = "Reporting contact:\n0917 555 1234\n+1 470 705 3865\n0917 555 1234\n> +61 3 8808 8130"
    assert signature_phones(body, RULES) == ["0917 555 1234", "+1 470 705 3865"]


@pytest.mark.parametrize("path", ["contacts", "sheets", "form"])
def test_explicit_phone_fields_keep_lenient_validation_through_merge(path):
    phone = "4155550123"
    assert not strict_phone(phone)
    if path == "contacts":
        signals = extract_contact(1, {"name": "Bob", "emails": ["bob@client.test"],
                                      "phones": [phone], "company": ""}, INTERNAL)
        signals += extract_gmail(2, mail("bob@client.test", "sales@acme.test", "Inquiry", "Hello"),
                                 RULES, INTERNAL)
    elif path == "sheets":
        signals = extract_sheet(1, {"sheet": "T", "tab": "A", "row": 2,
                                   "record": {"Email": "bob@client.test", "Phone": phone}}, RULES, INTERNAL)
    else:
        signals = extract_gmail(1, mail("wordpress@acme.test", "sales@acme.test", "New form",
                                       f"Email: bob@client.test\nPhone: {phone}"), RULES, INTERNAL)
    assert signals[0]["phones"] == [phone]
    assert build_candidates(signals, RULES, NOW)["bob@client.test"]["fields"]["phones"]["value"] == [phone]


@pytest.mark.parametrize("phone,expected", [("", ""), ("abc", ""), ("++12", "++12")])
def test_malformed_phone_helpers_do_not_raise(phone, expected):
    assert not strict_phone(phone)
    assert norm_phone(phone) == expected


@pytest.mark.parametrize("text,expected", [
    ("one sec please", []),
    ("Dole bananas for the office", []),
    ("spa weekend", []),
    ("our website", []),
    ("AEP renewal and 9G visa", ["Work Visa Services", "Alien Employment Permit (AEP)"]),
])
def test_services_need_catalog_phrases(text, expected):
    assert services_in(text, RULES) == expected


def test_outbound_invoice_marks_external_recipients_invoiced():
    p = mail("jane@acme.test", "Bob <bob@client.test>, boss@acme.test", "Invoice #42", "Please see attached.",
             direction="out")
    [s] = extract_gmail(3, p, RULES, INTERNAL)
    assert s["email"] == "bob@client.test" and s["stage"] == "invoiced" and s["direction"] == "out"


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
    assert extract_gmail(10, p, RULES, INTERNAL)[0]["stage"] is None


def test_parse_form_requires_email():
    assert parse_form("Name: X\nPhone: 123", RULES) is None


def test_sheet_row_maps_headers_status_and_paid():
    [(row_no, rec)] = rows_to_records([["Client Name", "Email", "Status", "Service", "Paid"],
                                       ["Cora Lim", "cora@firm.test", "Ongoing", "SEO, Website", "Yes"],
                                       ["", "", ""]])
    payload = {"sheet": "Tracker", "tab": "2025", "row": row_no, "record": rec}
    [s] = extract_sheet(5, payload, RULES, INTERNAL)
    assert (s["email"], s["name"], s["status"], s["stage"]) == ("cora@firm.test", "Cora Lim", "active", "payment_confirmed")
    assert s["services"] == ["SEO", "Website"]


def test_sheet_row_without_email_keyed_by_name():
    payload = {"sheet": "T", "tab": "A", "row": 2, "record": {"Name": "Dan  Uy", "Email": ""}}
    assert extract_sheet(6, payload, RULES, INTERNAL)[0]["key"] == "name:dan uy"


def test_merge_derives_status_and_skips_other_contacts_only():
    sigs = (extract_gmail(1, mail("bob@client.test", "sales@acme.test", "website?", "interested in a website", days_ago=400), RULES, INTERNAL)
            + extract_gmail(2, mail("jane@acme.test", "bob@client.test", "Official receipt", "thanks",
                                    direction="out", days_ago=20), RULES, INTERNAL)
            + extract_gmail(3, mail("old@lead.test", "sales@acme.test", "pricing for consulting", "hi", days_ago=200), RULES, INTERNAL)
            + extract_gmail(4, mail("new@lead.test", "sales@acme.test", "pricing for consulting", "hi", days_ago=5), RULES, INTERNAL)
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
    sigs += extract_gmail(2, mail("b1@x.test", "sales@acme.test", "Inquiry", "hi"), RULES, INTERNAL)
    c = build_candidates(sigs, RULES, NOW, merged={"b2@x.test": "b1@x.test"})
    assert list(c) == ["b1@x.test"]
    assert c["b1@x.test"]["fields"]["emails"]["value"] == ["b1@x.test", "b2@x.test"]
    assert resolve("a", {"a": "b", "b": "a"}) in {"a", "b"}  # cycles terminate


def stage(subject="", body="", direction="out", attachments=(), **kw):
    return classify_stage({**mail("jane@acme.test", "bob@client.test", subject, body, direction),
                           "attachments": list(attachments), **kw}, RULES)


def att(filename="proof.png", mime="image/png", size=40000):
    return {"filename": filename, "mime": mime, "size": size, "att_id": "a"}


@pytest.mark.parametrize("text,value,currency", [
    ("₱25,000.00", 25000, "PHP"), ("PHP 25,000", 25000, "PHP"),
    ("Php25,000", 25000, "PHP"), ("P25,000", 25000, "PHP"),
    ("$1,200", 1200, "USD"), ("USD 1,200.50", 1200.50, "USD"),
    ("1,200 USD", 1200, "USD"), ("AUD 300", 300, "AUD"),
    ("5,000 pesos", 5000, "PHP"),
])
def test_currency_amount_formats(text, value, currency):
    assert amounts_in(text, RULES["stages"]["amount_context"]) == [
        {"amount": value, "currency": currency, "total": False}]


def test_phone_and_dates_are_not_amounts():
    assert amounts_in("+63 917 123 4567\n2026-11-05\n11/05/2026", ["total"]) == []


def test_invoice_total_not_subtotal():
    amounts = stage("Invoice", "Subtotal PHP 1,000\nTax PHP 120\nAmount due PHP 1,120")[1]
    assert [a for a in amounts if a["total"]] == [{"amount": 1120, "currency": "PHP", "total": True}]


@pytest.mark.parametrize("subject,body,files,want", [
    ("Pricing", "Our fee is PHP 25,000", [], "quoted"),
    ("Pricing", "Let's discuss", [], None),
    ("Hello", "Attached", [att("service-quote.pdf", "application/pdf")], "quoted"),
    ("Invoice #42", "Please see attached", [], "invoiced"),
    ("Hello", "Attached", [att("invoice-42.pdf", "application/pdf")], "invoiced"),
    ("Hello", "Your invoice total is $120", [], "invoiced"),
    ("Hello", "We will send an invoice later", [], None),
    ("Payment received", "Thanks", [], "payment_confirmed"),
    ("Payment received", "We transferred the domain to you.", [], "payment_confirmed"),
    ("Official receipt", "Thanks", [], "payment_confirmed"),
    ("Hello", "Attached", [att("official receipt.pdf", "application/pdf")], "payment_confirmed"),
    ("Re: Invoice #42", "Thanks", [], None),
    ("Fwd: Payment received", "Can you clarify?", [], None),
    ("RE: Payment received", "Thanks", [], None),
    ("Re: Invoice #42", "We paid the invoice total PHP 25,000", [], None),
    ("Hello", "Thanks\nOn Tue, Bob wrote:\nPayment received\nTotal PHP 25,000", [], None),
    ("Hello", "Thanks\n> Invoice PHP 25,000", [], None),
    ("Hello", "Thanks\nfrom: Vendor <v@x.test>\nsent: Tuesday\nInvoice PHP 25,000", [], None),
])
def test_outbound_triggers_and_reply_traps(subject, body, files, want):
    assert stage(subject, body, attachments=files)[0] == want


@pytest.mark.parametrize("subject,body,files,want,proof", [
    ("Inquiry", "Your services", [], "inquiry", False),
    ("Request for quotation", "Please send your rates", [], "inquiry", False),
    ("Quote request", "How much for a website? We are interested in your services.", [], "inquiry", False),
    ("Payment for Invoice #42", "Paid via bank transfer", [att()], "payment_claim", True),
    ("Hello", "Payment sent", [att("invoice-42 proof.png")], "payment_claim", True),
    ("Invoice #42", "Payment sent", [att("invoice-42 proof.pdf", "application/pdf")], "payment_claim", True),
    ("Re: Invoice #42", "Paid the invoice via bank transfer", [att()], "payment_claim", True),
    ("Hello", "We have paid by PayPal", [att()], "payment_claim", True),
    ("Hello", "Wire transfer attached", [att("proof.pdf", "application/pdf")], "payment_claim", True),
    ("Hello", "Payment sent", [], "payment_claim", False),
    ("Hello", "", [att()], "payment_screenshot", True),
    ("Hello", "", [att(size=100)], None, False),
    ("Invoice for our services", "Please pay PHP 1,000", [], None, False),
    ("Quotation", "Our fee is PHP 1,000", [], None, False),
    ("Invoice", "Attached", [att("invoice.pdf", "application/pdf")], None, False),
    ("Quotation", "Attached", [att()], None, False),
    ("Invoice #88", "Payment via PayPal to billing@supplier.test", [att("invoice-88.pdf", "application/pdf")], None, False),
    ("Quotation", "Thank you for your inquiry. Attached is our quotation.", [att("quotation.pdf", "application/pdf")], None, False),
])
def test_inbound_triggers_and_vendor_traps(subject, body, files, want, proof):
    result = stage(subject, body, "in", files)
    assert (result[0], result[2]) == (want, proof)


@pytest.mark.parametrize("negative", ["unpaid", "payment reminder", "overdue", "not yet paid", "not received"])
@pytest.mark.parametrize("direction", ["in", "out"])
def test_negatives_never_become_payment_even_with_proof(negative, direction):
    assert stage("Payment received", f"Payment sent but {negative}", direction, [att()])[0] not in {
        "payment_confirmed", "payment_claim", "payment_screenshot"}


def test_invoice_amount_from_pdf():
    result = stage("Hello", "Attached", attachments=[att("invoice.pdf", "application/pdf")],
                   attachment_text={"invoice.pdf": "Subtotal PHP 900\nTotal PHP 1,000"})
    assert result[0] == "invoiced"
    assert result[1][-1] == {"amount": 1000, "currency": "PHP", "total": True}


# Review findings: supplier payment instructions, staff paying suppliers, bare verbs, quote requests.
@pytest.mark.parametrize("subject,body,files", [
    ("Invoice #88", "Total amount to be paid: PHP 1,000", [att("invoice-88.pdf", "application/pdf")]),
    ("Invoice #88", "Please have the amount deposited to our BDO account", [att("invoice-88.pdf", "application/pdf")]),
    ("Invoice #88", "Please send proof of payment to billing@v.test", [att("invoice-88.pdf", "application/pdf")]),
    ("Invoice #88", "Payment via bank transfer to BDO 1234", [att("invoice-88.pdf", "application/pdf")]),
    ("Quotation", "Amount to be transferred upon approval: PHP 5,000", [att("quotation.pdf", "application/pdf")]),
])
def test_supplier_payment_instructions_never_tag_the_supplier(subject, body, files):
    assert stage(subject, body, "in", files)[0] is None


@pytest.mark.parametrize("subject,body", [
    ("Re: Invoice #88", "Payment has been made for invoice #88, PHP 25,000."),
    ("Re: Invoice 88", "Payment sent for invoice 88 total PHP 25,000"),
    ("Re: Invoice 88", "We have settled your invoice, PHP 25,000"),
])
def test_staff_paying_a_supplier_is_not_an_invoice(subject, body):
    assert stage(subject, body, "out")[0] is None


@pytest.mark.parametrize("body", ["I transferred the files, see attached", "We paid attention to your notes",
                                  "Photos deposited in the shared folder"])
def test_bare_payment_verbs_are_not_payments(body):
    assert stage("Hello", body, "in", [att()])[0] not in {"payment_claim", "payment_confirmed"}


def test_real_wire_transfer_screenshot_is_a_payment_with_proof():
    result = stage("Re: Invoice #42", "Hi, we transferred the payment via wire transfer today.", "in", [att()])
    assert (result[0], result[2]) == ("payment_claim", True)


@pytest.mark.parametrize("subject,body", [
    ("Quotation request", "Hi, need a logo."),
    ("Hello", "Can you send a quotation for a logo?"),
    ("Quote", "Could you send your pricing for SEO?"),
])
def test_client_quote_requests_are_inquiries(subject, body):
    assert stage(subject, body, "in")[0] == "inquiry"


def test_supplier_quotation_as_per_request_is_not_an_inquiry():
    assert stage("Quotation", "Please find attached our quotation as per your request.", "in",
                 [att("quotation.pdf", "application/pdf")])[0] is None


@pytest.mark.parametrize("subject,body,files", [
    ("Invoice #88", "To be paid by Nov 30 via BDO", [att("invoice-88.pdf", "application/pdf"), att("bank-qr.png")]),
    ("Invoice #88", "Please send proof once payment has been made.", [att("invoice-88.pdf", "application/pdf")]),
    ("Invoice #88", "We need a PO number before processing.", [att("invoice-88.pdf", "application/pdf")]),
])
def test_more_supplier_instruction_traps(subject, body, files):
    assert stage(subject, body, "in", files)[0] is None


def test_staff_invoice_mentioning_future_payment_is_invoiced():
    result = stage("Invoice #42", "Please let us know once payment has been made. Total PHP 25,000", "out",
                   [att("invoice-42.pdf", "application/pdf")])
    assert result[0] == "invoiced" and result[1][-1]["amount"] == 25000


def test_asking_about_paypal_is_not_a_payment():
    assert stage("Hello", "Do you accept PayPal or bank transfer?", "in")[0] is None


@pytest.mark.parametrize("subject,body", [
    ("Pricing", "What is the price of a website?"),
    ("Rates", "May I know the price for SEO?"),
])
def test_price_questions_under_pricing_subject_are_inquiries(subject, body):
    assert stage(subject, body, "in")[0] == "inquiry"


def test_payment_screenshot_keeps_stated_amounts():
    result = stage("Hello", "Total PHP 1,000", "in", [att()])
    assert result[0] == "payment_screenshot"
    assert result[1] == [{"amount": 1000, "currency": "PHP", "total": True}]
