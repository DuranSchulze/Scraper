"""Group signals by person key and derive one candidate record per person.

Stage is verified across the person's messages:
  paid     <- staff confirmed payment, OR client payment message with a screenshot/PDF attached,
              OR client image sent inside a thread where we quoted/invoiced them.
  invoiced <- staff sent an invoice.     quoted <- staff sent pricing / a quote.
  inquiry  <- client asked, or a website form.
Anything uncertain is listed in needs_check for the reviewer instead of being guessed.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .extract import domain, norm_phone

SOURCE_WEIGHT = {"sheets": 3.0, "contacts": 2.0, "gmail": 1.0}
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
             "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "ymail.com"}
MAX_EVIDENCE = 25
# Only these create a person in staging; contacts and plain emails only add details.
CREATES_RECORD = {"inquiry", "quoted", "invoiced", "payment_claim", "payment_confirmed", "payment_screenshot"}
STAGE_ORDER = ["inquiry", "quoted", "invoiced", "paid"]


def field(value, source="derived", confidence=1.0) -> dict:
    return {"value": value, "source": source, "confidence": round(confidence, 2)}


def best(sigs: list[dict], name: str) -> dict | None:
    """Most supported non-empty value, weighted by source reliability."""
    scores, first_src = Counter(), {}
    for s in sigs:
        v = s.get(name)
        if v:
            scores[v] += SOURCE_WEIGHT[s["source"]]
            first_src.setdefault(v, s["source"])
    if not scores:
        return None
    v, score = scores.most_common(1)[0]
    return field(v, first_src[v], score / sum(scores.values()))


def _mailbox(s: dict) -> str:
    return s["thread"].partition(":")[0] if s["source"] == "gmail" else ""


def _payment_amount(s: dict) -> dict | None:
    return next((a for a in s["amounts"] if a["total"]), next(iter(s["amounts"]), None))


def _payments(paid: list[dict], sigs: list[dict]) -> tuple[list[dict], list[str]]:
    unique = []
    for s in sorted(paid, key=lambda s: s["raw_item_id"]):
        if any(s["key"] == other["key"] and s["snippet"] == other["snippet"]
               and _mailbox(s) and _mailbox(other) and _mailbox(s) != _mailbox(other)
               and s["date"] and other["date"] and abs(s["date"] - other["date"]) <= timedelta(minutes=5)
               for other in unique):
            continue
        unique.append(s)
    threads = defaultdict(list)
    for s in unique:
        threads[s["thread"] or ("item", s["raw_item_id"])].append(s)
    payments, checks = [], []
    for thread, thread_signals in threads.items():
        groups = defaultdict(list)
        for s in thread_signals:
            amount = _payment_amount(s)
            groups[(amount["amount"], amount["currency"]) if amount else None].append(s)
        if None in groups and len(groups) == 2:
            amount = next(key for key in groups if key is not None)
            groups[amount].extend(groups.pop(None))
        for amount, group in groups.items():
            earliest = min(group, key=lambda s: (s["date"] is None, _sort_date(s), s["raw_item_id"]))
            basis = "stated" if amount else "unknown"
            if amount is None:
                invoices = [s for s in sigs if s["stage"] == "invoiced" and s["thread"]
                            and s["thread"] == thread and s["amounts"]]
                if invoices:
                    invoice = max(invoices, key=lambda s: (_sort_date(s), s["raw_item_id"]))
                    found = _payment_amount(invoice)
                    amount, basis = (found["amount"], found["currency"]), "invoice"
                    checks.append(f"Payment on {_when(earliest)} uses the invoice amount from the same thread; confirm it")
                else:
                    checks.append(f"Payment on {_when(earliest)}: amount not found")
            stages = {s["stage"] for s in group}
            kind = "confirmed" if "payment_confirmed" in stages else (
                "claim" if "payment_claim" in stages else "screenshot")
            payment = {"date": earliest["date"].isoformat() if earliest["date"] else None,
                       "amount": amount[0] if amount else None, "currency": amount[1] if amount else None,
                       "basis": basis, "raw_item_id": earliest["raw_item_id"], "kind": kind}
            payments.append((payment, thread, earliest["date"]))
    payments.sort(key=lambda p: (p[2] is None, p[2].timestamp() if p[2] else 0, p[0]["raw_item_id"]))
    for i, (a, thread_a, date_a) in enumerate(payments):
        for b, thread_b, date_b in payments[i + 1:]:
            if (thread_a != thread_b and a["amount"] is not None and a["amount"] == b["amount"]
                    and a["currency"] == b["currency"] and date_a and date_b
                    and abs(date_a - date_b) <= timedelta(days=7)):
                checks.append(f"Possible duplicate payment: {a['amount']} on {date_a.date()} and {date_b.date()}; remove one if it is the same")
    return [p[0] for p in payments], checks


def _handlers(sigs: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for s in sigs:
        if s["source"] == "gmail":
            groups[(s["department"], _mailbox(s))].append(s)
    newest = sorted(groups, key=lambda key: max(
        (s["date"].timestamp() for s in groups[key] if s["date"]), default=float("-inf")), reverse=True)
    handlers = []
    for department, mailbox in newest:
        group = groups[(department, mailbox)]
        dates = [s["date"] for s in group if s["date"]]
        handlers.append({"department": department, "mailbox": mailbox, "messages": len(group),
                         "first": min(dates).isoformat() if dates else None,
                         "last": max(dates).isoformat() if dates else None})
    return handlers


def engagement(sigs: list[dict]) -> dict:
    """Stage, verified payments, amounts and reviewer warnings for one person."""
    deal_threads = {s["thread"] for s in sigs if s["stage"] in ("quoted", "invoiced") and s["thread"]}
    paid, checks = [], []
    for s in sigs:
        if s["stage"] == "payment_confirmed":
            paid.append((s, 0.95))
        elif s["stage"] == "payment_claim" and s["has_proof"]:
            paid.append((s, 0.85))
        elif s["stage"] == "payment_claim":
            checks.append(f"Client says they paid but attached no proof ({_when(s)}).")
        elif s["stage"] == "payment_screenshot" and s["thread"] in deal_threads:
            paid.append((s, 0.7))
        elif s["stage"] == "payment_screenshot":
            checks.append(f"Client sent an image outside a quote/invoice thread ({_when(s)}); may be payment proof.")

    by_stage = {st: [s for s in sigs if s["stage"] == st] for st in ("inquiry", "quoted", "invoiced")}
    if paid:
        stage, conf = "paid", max(c for _, c in paid)
        if not (by_stage["quoted"] or by_stage["invoiced"]) and not any(
                s["stage"] == "payment_confirmed" for s, _ in paid):
            checks.append("Payment proof found but no quote or invoice on record; confirm it is a client payment.")
    elif by_stage["invoiced"]:
        stage, conf = "invoiced", 0.9
    elif by_stage["quoted"]:
        stage, conf = "quoted", 0.85
    else:
        stage, conf = "inquiry", 0.7

    for s in by_stage["invoiced"]:
        if not s["amounts"]:
            checks.append(f"Invoice sent ({_when(s)}) but the amount was not found; open the source.")

    amounts, seen = [], set()
    for s in sorted(by_stage["quoted"] + by_stage["invoiced"], key=_sort_date):
        totals = [a for a in s["amounts"] if a["total"]]
        for a in (totals or s["amounts"])[:5]:
            k = (s["stage"], a["amount"], a["currency"])
            if k not in seen:
                seen.add(k)
                amounts.append({"stage": s["stage"], "amount": a["amount"], "currency": a["currency"],
                                "date": s["date"].isoformat() if s["date"] else None,
                                "raw_item_id": s["raw_item_id"]})

    payments, payment_checks = _payments([s for s, _ in paid], sigs)
    checks.extend(payment_checks)
    first = lambda group: min((s["date"] for s in group if s["date"]), default=None)
    stage_dates = {"inquiry": first(by_stage["inquiry"]), "quoted": first(by_stage["quoted"]),
                   "invoiced": first(by_stage["invoiced"]), "paid": first([s for s, _ in paid])}
    paid_dates = [s["date"] for s, _ in paid if s["date"]]
    return {"stage": stage, "confidence": conf, "paid": bool(paid), "paid_dates": paid_dates,
            "undated_paid": any(not s["date"] for s, _ in paid), "amounts": amounts, "payments": payments,
            "paid_threads": {s["thread"] for s, _ in paid if s["thread"]},
            "checks": list(dict.fromkeys(checks)),
            "stage_dates": {k: v.isoformat() for k, v in stage_dates.items() if v}}


def _when(s: dict) -> str:
    return s["date"].date().isoformat() if s["date"] else "undated"


def _sort_date(s: dict):
    return s["date"].timestamp() if s["date"] else 0


def derive_status(sigs: list[dict], eng: dict, rules: dict, now: datetime) -> dict:
    explicit = best(sigs, "status")
    if explicit:
        return explicit
    cfg = rules["status"]
    if eng["paid"]:
        recent = max(eng["paid_dates"]) if eng["paid_dates"] else None
        if recent and recent >= now - timedelta(days=30 * cfg["active_months"]):
            return field("active", confidence=0.7)
        return field("past", confidence=0.6 if recent else 0.3)
    dates = [s["date"] for s in sigs if s["date"]]
    if dates and max(dates) < now - timedelta(days=cfg["lost_after_days"]):
        return field("lost", confidence=0.5)
    return field("inquiry", confidence=0.6)


def build_candidate(key: str, sigs: list[dict], rules: dict, now: datetime) -> dict:
    dates = [s["date"] for s in sigs if s["date"]]
    emails = sorted({s["email"] for s in sigs if s["email"]})
    company = best(sigs, "company")
    if not company and emails and domain(emails[0]) not in FREE_MAIL:
        company = field(domain(emails[0]).split(".")[0].title(), "domain", 0.3)
    eng = engagement(sigs)
    handlers = _handlers(sigs)
    department = next((h["department"] for h in handlers if h["department"]), None)
    availed = sorted({service for s in sigs if s["thread"] in eng["paid_threads"] for service in s["services"]})
    availed_confidence = 1.0
    if eng["paid"] and not availed:
        availed = sorted({service for s in sigs if s["stage"] in ("quoted", "invoiced") for service in s["services"]})
        availed_confidence = 0.5
    timeline_signals = sorted((s for s in sigs if s["stage"]), key=lambda s: (_sort_date(s), s["raw_item_id"]))
    timeline = {"items": [{"date": s["date"].isoformat() if s["date"] else None, "stage": s["stage"],
                           "department": s["department"], "mailbox": _mailbox(s), "amounts": s["amounts"],
                           "raw_item_id": s["raw_item_id"], "snippet": s["snippet"][:200]}
                          for s in timeline_signals[-200:]], "truncated": len(timeline_signals) > 200}
    client_since = eng["payments"][0]["date"] if eng["payments"] else None
    fields = {
        "name": best(sigs, "name"),
        "emails": field(emails),
        "phones": field(sorted({norm_phone(p) for s in sigs for p in s["phones"]})),
        "company": company,
        "stage": field(eng["stage"], confidence=eng["confidence"]),
        "status": derive_status(sigs, eng, rules, now),
        "services": field(sorted({x for s in sigs for x in s["services"]})),
        "department": field(department, "gmail") if department else best(sigs, "department"),
        "payments": field(eng["payments"]),
        "client_since": field(client_since) if client_since else None,
        "services_availed": field(availed, confidence=availed_confidence),
        "handlers": field(handlers),
        "timeline": field(timeline),
        "engagement_paid": field(eng["paid"], confidence=eng["confidence"] if eng["paid"] else 0.6),
        "amounts": field(eng["amounts"]),
        "stage_dates": field(eng["stage_dates"]),
        "needs_check": field(eng["checks"]),
        "first_contact": field(min(dates).isoformat()) if dates else None,
        "last_contact": field(max(dates).isoformat()) if dates else None,
        "sources": field(sorted({s["source"] for s in sigs})),
    }
    # Evidence: stage-bearing items first, newest first.
    ranked = sorted(sigs, key=lambda s: (s["stage"] is not None, _sort_date(s)), reverse=True)
    evidence = [{"source": s["source"], "raw_item_id": s["raw_item_id"], "direction": s["direction"],
                 "date": s["date"].isoformat() if s["date"] else None, "snippet": s["snippet"],
                 "stage": s["stage"], "amounts": s["amounts"], "reasons": s["reasons"],
                 "services": s["services"]} for s in ranked[:MAX_EVIDENCE]]
    return {"key": key, "fields": {k: v for k, v in fields.items() if v is not None}, "evidence": evidence}


def resolve(key: str, merged: dict[str, str]) -> str:
    """Follow merge chains (a -> b -> c) to the surviving key."""
    seen = set()
    while key in merged and key not in seen:
        seen.add(key)
        key = merged[key]
    return key


def build_candidates(signals: list[dict], rules: dict, now: datetime,
                     merged: dict[str, str] | None = None) -> dict[str, dict]:
    """merged maps a merged-away key to the key it was merged into."""
    groups = defaultdict(list)
    for s in signals:
        groups[resolve(s["key"], merged or {})].append(s)
    return {k: build_candidate(k, sigs, rules, now)
            for k, sigs in groups.items() if any(s["stage"] in CREATES_RECORD for s in sigs)}


# --- signal <-> JSON (signals table) ---

def to_json(s: dict) -> dict:
    return {**s, "date": s["date"].isoformat() if s["date"] else None}


def from_json(d: dict) -> dict:
    return {**d, "date": datetime.fromisoformat(d["date"]) if d["date"] else None}
