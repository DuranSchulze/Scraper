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

    first = lambda group: min((s["date"] for s in group if s["date"]), default=None)
    stage_dates = {"inquiry": first(by_stage["inquiry"]), "quoted": first(by_stage["quoted"]),
                   "invoiced": first(by_stage["invoiced"]), "paid": first([s for s, _ in paid])}
    paid_dates = [s["date"] for s, _ in paid if s["date"]]
    return {"stage": stage, "confidence": conf, "paid": bool(paid), "paid_dates": paid_dates,
            "undated_paid": any(not s["date"] for s, _ in paid), "amounts": amounts,
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
    fields = {
        "name": best(sigs, "name"),
        "emails": field(emails),
        "phones": field(sorted({norm_phone(p) for s in sigs for p in s["phones"]})),
        "company": company,
        "stage": field(eng["stage"], confidence=eng["confidence"]),
        "status": derive_status(sigs, eng, rules, now),
        "services": field(sorted({x for s in sigs for x in s["services"]})),
        "department": best(sigs, "department"),
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
