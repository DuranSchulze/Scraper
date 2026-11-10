"""Turn raw items into signals: one per (person, item), with fields found by rules.yaml.

Engagement trigger (from the sales process):
  inquiry  -> client asks (or a website form arrives)
  quoted   -> staff reply with pricing / a service quote
  invoiced -> staff send a service invoice (amount read from the body or the attached PDF)
  paid     -> client sends payment proof (wire transfer / PayPal screenshot), or staff confirm payment

No AI involved. Pure functions, unit-tested on fake fixtures.
"""

import re
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

import phonenumbers

EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\(?\d[\d\s().-]{7,}\d")
REPLY_RE = re.compile(r"^\s*((re|fw|fwd|aw|sv)\s*(\[\d+\])?\s*:\s*)+", re.I)
FORM_LINE_RE = re.compile(r"^\s*\**([A-Za-z][\w /-]{0,40}?)\**\s*[:：]\s*(.+?)\s*$")

# Where quoted earlier messages start in a reply. Text after this is not the sender's own.
QUOTE_START_RE = re.compile(
    r"(?m)^(?:>|-{2,}\s*Original Message|-{5,}\s*Forwarded message|_{10,}|From:\s.+\n(?:Sent|Date):)"
    r"|^On [^\n]{0,200}(?:\n[^\n]{0,200})?wrote:\s*$", re.I
)

# Amounts must carry a currency marker, so phone numbers and dates are never read as money.
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRE = r"₱|PHP|Php|php|P(?=\s?\d{1,3}(?:,\d{3})+)|US\$|USD|A\$|AUD|S\$|SGD|C\$|CAD|NZ\$|NZD|HK\$|HKD|EUR|€|GBP|£|JPY|¥|\$"
_POST = r"PHP|USD|AUD|SGD|CAD|NZD|HKD|EUR|GBP|JPY|pesos?"
AMOUNT_RE = re.compile(
    rf"(?<![A-Za-z])(?P<cur>{_PRE})\s?(?P<num>{_NUM})(?!\d)"
    rf"|(?<![\d.,])(?P<num2>{_NUM})\s?(?P<cur2>{_POST})\b"
)
CURRENCY = {"₱": "PHP", "php": "PHP", "p": "PHP", "peso": "PHP", "pesos": "PHP",
            "$": "USD", "us$": "USD", "usd": "USD", "a$": "AUD", "aud": "AUD", "s$": "SGD", "sgd": "SGD",
            "c$": "CAD", "cad": "CAD", "nz$": "NZD", "nzd": "NZD", "hk$": "HKD", "hkd": "HKD",
            "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "¥": "JPY", "jpy": "JPY"}


def domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def addresses(*header_values: str) -> list[tuple[str, str]]:
    """(name, address) pairs; empty headers skipped (strict getaddresses rejects them)."""
    return [(n, a) for n, a in getaddresses([v for v in header_values if v]) if a]


def has_word(text: str, keywords) -> bool:
    """Any keyword present as a whole word/phrase, case-insensitive."""
    return any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", text, re.I) for k in keywords)


def contains(text: str, patterns) -> bool:
    t = text.lower()
    return any(p.lower() in t for p in patterns)


def address_matches(address: str, key: str) -> bool:
    """'sales@' matches sales@ on any domain; a full address matches exactly."""
    address, key = address.lower(), key.lower()
    return address.startswith(key) if key.endswith("@") else address == key


def norm_phone(p: str) -> str:
    try:
        number = phonenumbers.parse(p, "PH")
        if phonenumbers.is_valid_number(number):
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    if not p.strip().startswith("+"):
        try:
            number = phonenumbers.parse("+" + re.sub(r"\D", "", p), "PH")
            if phonenumbers.is_valid_number(number):
                return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
    return re.sub(r"[^\d+]", "", p)


def strict_phone(p: str) -> bool:
    try:
        return phonenumbers.is_valid_number(phonenumbers.parse(p, "PH"))
    except phonenumbers.NumberParseException:
        return False


def valid_phone(p: str) -> bool:
    return strict_phone(p) or (
        not p.strip().startswith("+") and strict_phone("+" + re.sub(r"\D", "", p)))


def own_text(body: str) -> str:
    """The sender's own words: everything before the quoted earlier message."""
    m = QUOTE_START_RE.search(body)
    return body[:m.start()] if m else body


def amounts_in(text: str, context_words) -> list[dict]:
    """Money amounts with currency. 'total' marks amounts on a line with a context word (Total, Amount due)."""
    out = []
    for line in text.splitlines():
        is_total = has_word(line, context_words)
        for m in AMOUNT_RE.finditer(line):
            cur = (m.group("cur") or m.group("cur2")).lower()
            value = float((m.group("num") or m.group("num2")).replace(",", ""))
            if value > 0:
                out.append({"amount": value, "currency": CURRENCY.get(cur, cur.upper()), "total": is_total})
    return out


def signal(key: str, source: str, raw_item_id: int, **kw) -> dict:
    s = {"key": key, "email": key if "@" in key else "", "source": source, "raw_item_id": raw_item_id,
         "date": None, "name": "", "phones": [], "company": "", "services": [], "department": "",
         "status": "", "direction": "in", "snippet": "", "stage": None, "amounts": [],
         "has_proof": False, "thread": "", "reasons": []}
    s.update(kw)
    return s


# --- Gmail -----------------------------------------------------------------

def services_in(text: str, rules: dict) -> list[str]:
    return [svc for svc, kws in rules["services"].items() if has_word(text, kws)]


def department_of(payload: dict, rules: dict) -> str:
    h = payload["headers"]
    if payload["direction"] == "in":
        addrs = [a for _, a in addresses(h.get("to", ""), h.get("cc", ""), h.get("delivered-to", ""))]
    else:
        addrs = [parseaddr(h.get("from", ""))[1]]
    for addr in addrs + [payload.get("mailbox", "")]:
        for key, dept in rules["departments"].items():
            if addr and address_matches(addr, key):
                return dept
    for label in payload.get("labels", []):
        if label in (rules.get("label_departments") or {}):
            return rules["label_departments"][label]
    return ""


def is_noise_sender(address: str, rules: dict) -> bool:
    return contains(address, rules["noise"]["sender_patterns"])


def is_noise_message(payload: dict, rules: dict) -> bool:
    h = payload["headers"]
    return bool(
        h.get("list-unsubscribe") or h.get("list-id")
        or h.get("precedence", "").lower() in {"bulk", "list", "junk"}
        or h.get("auto-submitted", "no").lower() != "no"
        or contains(h.get("subject", ""), rules["noise"]["subject_patterns"])
        or set(payload.get("labels", [])) & set(rules["noise"]["labels"])
    )


def parse_form(body: str, rules: dict) -> dict | None:
    """'Label: value' lines -> fields. Returns None unless a valid email is found."""
    found = {}
    for line in body.splitlines():
        m = FORM_LINE_RE.match(line)
        if not m:
            continue
        label, value = m.group(1).strip().lower(), m.group(2)
        for field, aliases in rules["form_fields"].items():
            if label in aliases and field not in found:
                found[field] = value
    m = EMAIL_RE.search(found.get("email", ""))
    if not m:
        return None
    found["email"] = m.group(0).lower()
    return found


def signature_phones(body: str, rules: dict) -> list[str]:
    """Phones in the last lines of the sender's own text."""
    tail = own_text(body)[-1500:]
    return list(dict.fromkeys(
        m.strip() for line in tail.splitlines()
        if not has_word(line, rules["phone_exclude_context"])
        for m in PHONE_RE.findall(line) if strict_phone(m)))


def wants_pdf_text(subject: str, filename: str, rules: dict) -> bool:
    """Download a PDF attachment's text only for likely invoices/quotes."""
    st = rules["stages"]
    return (contains(filename, st["invoice_attachment_patterns"] + st["quote_attachment_patterns"])
            or has_word(subject, st["invoice_keywords"] + st["pricing_keywords"]))


def proof_attachments(payload: dict, rules: dict) -> list[str]:
    """Screenshots / PDFs big enough to be payment proof (small inline images are signature logos)."""
    st = rules["stages"]
    doc_patterns = st["invoice_attachment_patterns"] + st["quote_attachment_patterns"]
    out = []
    for a in payload.get("attachments", []):
        if a["mime"].startswith("image/") and a["size"] >= st["min_screenshot_bytes"]:
            out.append(a["filename"])
        # A supplier's own invoice/quote PDF is not proof that anyone paid.
        elif a["mime"] == "application/pdf" and (
                not contains(a["filename"], doc_patterns) or contains(a["filename"], st["proof_attachment_patterns"])):
            out.append(a["filename"])
    return out


def classify_stage(payload: dict, rules: dict) -> tuple[str | None, list[dict], bool, list[str]]:
    """(stage, amounts, has_proof, reasons) for one message, judged on the sender's own text only."""
    st = rules["stages"]
    h = payload["headers"]
    subject = h.get("subject", "")
    # A reply/forward subject was written by whoever started the thread (maybe a vendor), so it
    # only counts as this sender's words on a fresh message.
    own_subject = "" if REPLY_RE.match(subject) else subject
    mine = own_text(payload.get("body", ""))
    text = own_subject + "\n" + mine
    files = [a["filename"] for a in payload.get("attachments", [])]
    pdf_text = "\n".join(payload.get("attachment_text", {}).values())
    amounts = amounts_in(mine, st["amount_context"]) + amounts_in(pdf_text, st["amount_context"])
    negative = has_word(text, st["payment_negative_keywords"])

    if payload["direction"] == "out":
        if not negative and (has_word(text, st["payment_confirm_keywords"]) or any(
                contains(f, st["payment_confirm_attachment_patterns"]) for f in files)):
            return "payment_confirmed", amounts, False, ["staff confirmed payment"]
        invoice_file = [f for f in files if contains(f, st["invoice_attachment_patterns"])]
        strong_invoice = bool(invoice_file) or has_word(own_subject, st["invoice_keywords"])
        # Staff describing their own payment are paying a supplier, unless this is our own
        # invoice or the payment words are instructions ("once payment has been made").
        if (not strong_invoice and has_word(text, st["outbound_payment_keywords"])
                and not has_word(text, st["payment_instruction_keywords"])):
            return None, [], False, []
        if strong_invoice or (has_word(mine, st["invoice_keywords"]) and amounts):
            why = f"invoice attachment {invoice_file[0]}" if invoice_file else "invoice wording"
            return "invoiced", amounts, False, [why + ("" if amounts else "; amount not found")]
        quote_file = [f for f in files if contains(f, st["quote_attachment_patterns"])]
        if quote_file or (has_word(text, st["pricing_keywords"]) and amounts):
            why = f"quote attachment {quote_file[0]}" if quote_file else "pricing with amount"
            return "quoted", amounts, False, [why]
        return None, [], False, []

    # Supplier documents can contain payment instructions and thank us for an inquiry.
    inquiry_text = text.lower()
    for phrase in st["inquiry_courtesy_phrases"]:
        inquiry_text = inquiry_text.replace(phrase.lower(), "")
    inquiry = has_word(inquiry_text, st["inquiry_keywords"]) or (
        has_word(inquiry_text, st["quote_request_topics"])
        and has_word(inquiry_text, st["quote_request_phrases"]))
    instructions = has_word(text, st["payment_instruction_keywords"])
    completed_payment = has_word(text, st["payment_completed_keywords"]) and not negative and not instructions
    vendor_document = (has_word(own_subject, st["invoice_keywords"] + st["pricing_keywords"])
                       or any(contains(f, st["invoice_attachment_patterns"] + st["quote_attachment_patterns"])
                              for f in files))
    if vendor_document and not inquiry and not completed_payment:
        return None, [], False, []
    proof = proof_attachments(payload, rules)
    # Generic words ("paypal", "bank transfer") only claim a payment when proof is attached:
    # "Do you accept PayPal?" is not a payment.
    generic_claim = has_word(text, st["payment_claim_keywords"]) and bool(proof) and not instructions
    if (generic_claim or completed_payment) and not negative:
        return "payment_claim", amounts, bool(proof), (
            [f"client payment message with proof {proof[0]}"] if proof else ["client says paid, no proof attached"])
    if inquiry:
        return "inquiry", [], False, ["client inquiry wording"]
    if proof and not negative and any(
            a["mime"].startswith("image/") and a["filename"] in proof
            for a in payload.get("attachments", [])):
        return "payment_screenshot", amounts, True, [f"client sent image {proof[0]}"]
    return None, [], False, []


def extract_gmail(item_id: int, payload: dict, rules: dict, internal: set[str]) -> list[dict]:
    h = payload["headers"]
    subject, body = h.get("subject", ""), payload.get("body", "")
    sender_name, sender = parseaddr(h.get("from", ""))
    sender = sender.lower()
    mine = own_text(body)
    date = datetime.fromtimestamp(payload["internal_date"] / 1000, tz=timezone.utc)
    stage, amounts, has_proof, reasons = classify_stage(payload, rules)
    common = {"date": date, "department": department_of(payload, rules),
              "services": services_in(subject + "\n" + mine, rules),
              "snippet": (subject + " — " + mine[:300]).strip(),
              "thread": f"{payload.get('mailbox', '')}:{payload.get('thread_id', '')}",
              "stage": stage, "amounts": amounts, "has_proof": has_proof, "reasons": reasons}

    # Website form notifications come from noise/internal senders but carry a real inquirer.
    if contains(sender, rules["form_senders"]) or is_noise_sender(sender, rules) or domain(sender) in internal:
        form = parse_form(body, rules)
        if form and domain(form["email"]) not in internal and not is_noise_sender(form["email"], rules):
            phone = form.get("phone", "")
            return [signal(form["email"], "gmail", item_id, name=form.get("name", ""),
                           phones=[phone] if valid_phone(phone) else [], company=form.get("company", ""),
                           direction="in", **{**common, "stage": "inquiry", "amounts": [], "has_proof": False,
                                              "reasons": ["website form"],
                                              "services": services_in(form.get("message", "") + "\n" + subject, rules)})]

    if is_noise_sender(sender, rules) or is_noise_message(payload, rules):
        return []

    if payload["direction"] == "in":
        if not sender or domain(sender) in internal:
            return []
        return [signal(sender, "gmail", item_id, name=sender_name.strip('" '),
                       phones=signature_phones(body, rules), direction="in", **common)]

    out = []
    for name, addr in addresses(h.get("to", ""), h.get("cc", "")):
        addr = addr.lower()
        if addr and domain(addr) not in internal and not is_noise_sender(addr, rules):
            out.append(signal(addr, "gmail", item_id, name=name.strip('" '), direction="out", **common))
    return out


# --- Contacts ----------------------------------------------------------------

def extract_contact(item_id: int, payload: dict, internal: set[str]) -> list[dict]:
    # Contacts never create a client on their own (no stage); they only add names and phones.
    return [signal(e, "contacts", item_id, name=payload["name"],
                   phones=[p for p in payload["phones"] if valid_phone(p)], company=payload["company"],
                   snippet=f"Contact: {payload['name']} <{e}>")
            for e in payload["emails"] if domain(e) not in internal]


# --- Sheets ------------------------------------------------------------------

def sheet_status(value: str, rules: dict) -> str:
    v = value.strip().lower()
    for status, words in rules["sheet_status_values"].items():
        if v in words:
            return status
    return ""


def extract_sheet(item_id: int, payload: dict, rules: dict, internal: set[str]) -> list[dict]:
    rec = {k.strip().lower(): str(v).strip() for k, v in payload["record"].items()}
    get = lambda field: next((rec[a] for a in rules["sheet_headers"][field] if rec.get(a)), "")
    name = get("name")
    paid = get("paid").lower() in {"yes", "y", "true", "paid", "1", "✓"}
    common = {
        "name": name, "company": get("company"), "department": get("department"),
        "phones": [p for p in re.split(r"[,;/]", get("phone")) if valid_phone(p)],
        "services": [s.strip() for s in re.split(r"[,;]", get("services")) if s.strip()],
        "status": sheet_status(get("status"), rules),
        # A tracker row is a deliberate record, so it always counts; paid column = confirmed payment.
        "stage": "payment_confirmed" if paid else "inquiry",
        "reasons": ["tracker sheet paid column"] if paid else ["tracker sheet row"],
        "snippet": f"Sheet {payload['sheet']} / {payload['tab']} row {payload['row']}: "
                   + "; ".join(f"{k}={v}" for k, v in payload["record"].items() if v)[:300],
    }
    emails = [e.lower() for e in EMAIL_RE.findall(get("email")) if domain(e) not in internal]
    # Rows with no email are kept under their name so the reviewer can merge them.
    keys = emails or ([f"name:{' '.join(name.lower().split())}"] if name else [])
    return [signal(k, "sheets", item_id, **common) for k in keys]


def extract_item(row: dict, rules: dict, internal: set[str]) -> list[dict]:
    src, payload = row["source"], row["payload"]
    if src == "gmail":
        return extract_gmail(row["id"], payload, rules, internal)
    if src == "contacts":
        return extract_contact(row["id"], payload, internal)
    return extract_sheet(row["id"], payload, rules, internal)
