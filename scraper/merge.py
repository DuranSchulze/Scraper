"""Group signals by person key and derive one candidate record per person."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .extract import domain, norm_phone

SOURCE_WEIGHT = {"sheets": 3.0, "contacts": 2.0, "gmail": 1.0}
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
             "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "ymail.com"}
MAX_EVIDENCE = 25


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


def derive_status(sigs: list[dict], rules: dict, now: datetime) -> dict:
    explicit = best(sigs, "status")
    if explicit:
        return explicit
    cfg = rules["status"]
    paid_dates = [s["date"] for s in sigs if s["paid"] and s["date"]]
    if any(s["paid"] for s in sigs):
        recent = max(paid_dates) if paid_dates else None
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
    paid = any(s["paid"] for s in sigs)
    fields = {
        "name": best(sigs, "name"),
        "emails": field(emails),
        "phones": field(sorted({norm_phone(p) for s in sigs for p in s["phones"]})),
        "company": company,
        "status": derive_status(sigs, rules, now),
        "services": field(sorted({x for s in sigs for x in s["services"]})),
        "department": best(sigs, "department"),
        "engagement_paid": field(paid, confidence=0.7 if paid else 0.5),
        "first_contact": field(min(dates).isoformat()) if dates else None,
        "last_contact": field(max(dates).isoformat()) if dates else None,
        "sources": field(sorted({s["source"] for s in sigs})),
    }
    by_date = sorted(sigs, key=lambda s: s["date"] or datetime.min.replace(tzinfo=now.tzinfo), reverse=True)
    evidence = [{"source": s["source"], "raw_item_id": s["raw_item_id"], "direction": s["direction"],
                 "date": s["date"].isoformat() if s["date"] else None, "snippet": s["snippet"],
                 "paid": s["paid"], "services": s["services"]} for s in by_date[:MAX_EVIDENCE]]
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
            for k, sigs in groups.items() if not all(s["enrich_only"] for s in sigs)}
