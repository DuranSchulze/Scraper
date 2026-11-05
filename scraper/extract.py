"""Turn raw items into signals: one per (person, item), with fields found by rules.yaml.

No AI involved. Pure functions, so they are unit-tested on fake fixtures.
"""

import re
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
FORM_LINE_RE = re.compile(r"^\s*\**([A-Za-z][\w /-]{0,40}?)\**\s*[:：]\s*(.+?)\s*$")


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
    return re.sub(r"[^\d+]", "", p)


def valid_phone(p: str) -> bool:
    return 9 <= len(re.sub(r"\D", "", p)) <= 15


def signal(key: str, source: str, raw_item_id: int, **kw) -> dict:
    s = {"key": key, "email": key if "@" in key else "", "source": source, "raw_item_id": raw_item_id,
         "date": None, "name": "", "phones": [], "company": "", "services": [], "department": "",
         "paid": False, "status": "", "direction": "in", "snippet": "", "enrich_only": False}
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


def signature_phones(body: str) -> list[str]:
    """Phones in the last lines of the sender's own text (quoted replies skipped)."""
    own = []
    for line in body.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:$", line.strip()):
            break
        own.append(line)
    tail = "\n".join(own)[-1500:]
    return list(dict.fromkeys(m.strip() for m in PHONE_RE.findall(tail) if valid_phone(m)))


def extract_gmail(item_id: int, payload: dict, rules: dict, internal: set[str]) -> list[dict]:
    h = payload["headers"]
    subject, body = h.get("subject", ""), payload.get("body", "")
    sender_name, sender = parseaddr(h.get("from", ""))
    sender = sender.lower()
    text = subject + "\n" + body
    date = datetime.fromtimestamp(payload["internal_date"] / 1000, tz=timezone.utc)
    common = {"date": date, "department": department_of(payload, rules), "paid": has_word(text, rules["paid_keywords"]),
              "services": services_in(text, rules), "snippet": (subject + " — " + body[:300]).strip()}

    # Website form notifications come from noise/internal senders but carry a real inquirer.
    if contains(sender, rules["form_senders"]) or is_noise_sender(sender, rules) or domain(sender) in internal:
        form = parse_form(body, rules)
        if form and domain(form["email"]) not in internal and not is_noise_sender(form["email"], rules):
            phone = form.get("phone", "")
            return [signal(form["email"], "gmail", item_id, name=form.get("name", ""),
                           phones=[phone] if valid_phone(phone) else [], company=form.get("company", ""),
                           direction="in", **common)]

    if is_noise_sender(sender, rules) or is_noise_message(payload, rules):
        return []

    if payload["direction"] == "in":
        if not sender or domain(sender) in internal:
            return []
        return [signal(sender, "gmail", item_id, name=sender_name.strip('" '),
                       phones=signature_phones(body), direction="in", **common)]

    out = []
    for name, addr in addresses(h.get("to", ""), h.get("cc", "")):
        addr = addr.lower()
        if addr and domain(addr) not in internal and not is_noise_sender(addr, rules):
            out.append(signal(addr, "gmail", item_id, name=name.strip('" '), direction="out", **common))
    return out


# --- Contacts ----------------------------------------------------------------

def extract_contact(item_id: int, payload: dict, internal: set[str]) -> list[dict]:
    # "Other contacts" are auto-saved from any email, so they only enrich existing people.
    return [signal(e, "contacts", item_id, name=payload["name"],
                   phones=[p for p in payload["phones"] if valid_phone(p)], company=payload["company"],
                   enrich_only=payload["kind"] == "other", snippet=f"Contact: {payload['name']} <{e}>")
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
    common = {
        "name": name, "company": get("company"), "department": get("department"),
        "phones": [p for p in re.split(r"[,;/]", get("phone")) if valid_phone(p)],
        "services": [s.strip() for s in re.split(r"[,;]", get("services")) if s.strip()],
        "status": sheet_status(get("status"), rules),
        "paid": get("paid").lower() in {"yes", "y", "true", "paid", "1", "✓"},
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
