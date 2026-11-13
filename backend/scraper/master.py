"""Read-only Client Directory matching for pending scraper candidates."""

import logging
from datetime import timezone

from .config import env_list
from .merge import FREE_MAIL

FREE_MAIL_PROVIDERS = ("yahoo", "ymail", "rocketmail", "hotmail", "outlook", "live", "msn", "gmail", "googlemail",
                       "aol", "icloud", "me", "mac", "proton", "protonmail", "gmx", "mail", "zoho", "yandex")
LOCK = "SELECT pg_advisory_xact_lock(783214902)"
_missing_logged = False


def available(conn):
    return conn.execute(
        "SELECT to_regclass('contacts') IS NOT NULL AND to_regclass('companies') IS NOT NULL AS available"
    ).fetchone()["available"]


def rematch(conn, keys=None):
    global _missing_logged
    conn.execute(LOCK)
    if not available(conn):
        if not _missing_logged:
            logging.getLogger(__name__).warning("Client Directory tables unavailable; clearing scraper match tags")
            _missing_logged = True
        conn.execute("UPDATE candidates SET master_match=NULL,master_ids=NULL"
                     " WHERE master_match IS NOT NULL OR master_ids IS NOT NULL")
    else:
        excluded = sorted(FREE_MAIL | set(env_list("INTERNAL_DOMAINS")))
        conn.execute(
            "WITH contact_emails AS MATERIALIZED ("
            " SELECT DISTINCT c.company_id,lower(e.email) AS email FROM contacts c"
            " CROSS JOIN LATERAL unnest(c.emails) AS e(email) WHERE e.email IS NOT NULL AND e.email<>''),"
            " email_ids AS MATERIALIZED (SELECT email,array_agg(DISTINCT company_id ORDER BY company_id) AS ids"
            " FROM contact_emails GROUP BY email),"
            " domain_ids AS MATERIALIZED (SELECT split_part(email,'@',2) AS domain,"
            " array_agg(DISTINCT company_id ORDER BY company_id) AS ids FROM contact_emails"
            " WHERE split_part(email,'@',2)<>'' AND NOT (split_part(email,'@',2)=ANY(%s))"
            " AND NOT (split_part(split_part(email,'@',2),'.',1)=ANY(%s)) GROUP BY 1),"
            " matches AS (SELECT c.id,e.ids AS email_ids,d.ids AS domain_ids FROM candidates c"
            " LEFT JOIN email_ids e ON e.email=lower(c.email_key)"
            " LEFT JOIN domain_ids d ON d.domain=split_part(lower(c.email_key),'@',2)"
            " WHERE %s::text[] IS NULL OR c.email_key=ANY(%s))"
            " UPDATE candidates c SET master_match=CASE WHEN c.review_state<>'pending' THEN NULL"
            " WHEN m.email_ids IS NOT NULL THEN 'email' WHEN m.domain_ids IS NOT NULL THEN 'domain' END,"
            " master_ids=CASE WHEN c.review_state<>'pending' THEN NULL"
            " WHEN m.email_ids IS NOT NULL THEN m.email_ids ELSE m.domain_ids END"
            " FROM matches m WHERE c.id=m.id", (excluded, list(FREE_MAIL_PROVIDERS), keys, keys))
    if keys is None:
        conn.execute("UPDATE directory_tag_state SET tagged_at=clock_timestamp()")


def counts(conn):
    return conn.execute(
        "SELECT count(*) FILTER (WHERE master_match='email') AS email,"
        " count(*) FILTER (WHERE master_match='domain') AS domain,"
        " count(*) FILTER (WHERE master_match IS NULL) AS none FROM candidates WHERE review_state='pending'").fetchone()


def summary(conn):
    ready = available(conn)
    totals = conn.execute("SELECT (SELECT count(*) FROM companies) AS companies,"
                          " (SELECT count(*) FROM contacts) AS contacts").fetchone() if ready else {"companies": 0, "contacts": 0}
    tagged_at = conn.execute("SELECT tagged_at FROM directory_tag_state").fetchone()["tagged_at"]
    if tagged_at is not None:
        tagged_at = tagged_at.astimezone(timezone.utc)
    return {"available": ready, **totals, **counts(conn), "tagged_at": tagged_at}
