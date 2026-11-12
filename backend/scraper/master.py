"""Accounting master-list import and pending-candidate matching."""

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

from fastapi import HTTPException
from openpyxl import load_workbook

from . import db
from .config import env_list
from .merge import FREE_MAIL

MAX_BYTES = 10 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_ROWS = 20_000
LOCK = "SELECT pg_advisory_xact_lock(783214902)"
FIELDS = ("registered_name", "alias", "trade_name", "tin", "intercompany", "engagement_status", "contact_person")
ALIASES = {
    "registeredname": "registered_name", "companyname": "registered_name", "alias": "alias",
    "tradename": "trade_name", "tin": "tin", "tinnumber": "tin", "intercompany": "intercompany",
    "engagementstatus": "engagement_status", "contactpersondesignation": "contact_person",
    "contactperson": "contact_person", "emailaddress": "emails", "emailaddresses": "emails", "emailforsi": "emails",
}
EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}", re.I)


def _normalize(value):
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def _text(value):
    value = str(value).strip() if value is not None else ""
    return "" if value.upper() in ("N/A", "-") else value


def parse(data: bytes):
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Workbook exceeds 10 MB")
    workbook = None
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED:
                raise HTTPException(413, "Expanded workbook exceeds 100 MB")
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
        names = [name for name in workbook.sheetnames if _normalize(name) == "allclientsdatabase"]
        if len(names) != 1:
            raise HTTPException(400, "Expected exactly one All Clients Database sheet; found: " + ", ".join(workbook.sheetnames))
        sheet = workbook[names[0]]
        # Ignore worksheet dimension hints; a stale or forged hint must not truncate the import.
        sheet.reset_dimensions()
        rows = enumerate(sheet.iter_rows(max_col=100, values_only=True), 1)
        columns = []
        for header_no, cells in rows:
            columns = [(i, ALIASES[_normalize(value)]) for i, value in enumerate(cells) if _normalize(value) in ALIASES]
            if {"registered_name", "emails"} <= {field for _, field in columns}:
                break
            if header_no == 10:
                columns = []
                break
        if not columns or not {"registered_name", "emails"} <= {field for _, field in columns}:
            raise HTTPException(400, "Missing REGISTERED NAME and EMAIL ADDRESS headers in first 10 rows")
        records, skipped, flagged = [], [], []
        for row_no, cells in rows:
            if row_no - header_no > MAX_ROWS:
                raise HTTPException(400, "Workbook exceeds 20,000 data rows")
            record = dict.fromkeys(FIELDS, "")
            emails = set()
            invalid = False
            for i, field in columns:
                value = _text(cells[i])
                if field != "emails":
                    record[field] = record[field] or value
                    continue
                for token in re.split(r"[\s,;]+", value):
                    if not token or not _text(token):
                        continue
                    token = token.lower()
                    local = token.split("@", 1)[0]
                    if EMAIL.fullmatch(token) and len(local) <= 64 and not local.startswith(".") and not local.endswith(".") and ".." not in local:
                        emails.add(token)
                    else:
                        invalid = True
            if invalid:
                flagged.append({"row": row_no, "reason": "Invalid email text"})
            if not record["registered_name"] and not emails:
                skipped.append({"row": row_no, "reason": "No registered name or valid email"})
                continue
            if record["registered_name"] and not emails:
                flagged.append({"row": row_no, "reason": "Registered name has no email"})
            records.append({"row_no": row_no, **record, "emails": sorted(emails),
                            "domains": sorted({email.rsplit("@", 1)[1] for email in emails})})
        return names[0], records, skipped, flagged
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "Not a valid .xlsx file") from exc
    finally:
        if workbook is not None:
            workbook.close()


def rematch(conn, keys=None):
    conn.execute(LOCK)
    excluded = sorted(FREE_MAIL | set(env_list("INTERNAL_DOMAINS")))
    conn.execute(
        "WITH matches AS (SELECT c.id,"
        " ARRAY(SELECT m.id FROM master_clients m WHERE m.emails @> ARRAY[lower(c.email_key)] ORDER BY m.id) AS email_ids,"
        " ARRAY(SELECT m.id FROM master_clients m WHERE m.domains @> ARRAY[split_part(lower(c.email_key),'@',2)]"
        " AND NOT (split_part(lower(c.email_key),'@',2)=ANY(%s)) ORDER BY m.id) AS domain_ids"
        " FROM candidates c WHERE %s::text[] IS NULL OR c.email_key=ANY(%s))"
        " UPDATE candidates c SET master_match=CASE WHEN c.review_state<>'pending' THEN NULL"
        " WHEN cardinality(m.email_ids)>0 THEN 'email' WHEN cardinality(m.domain_ids)>0 THEN 'domain' END,"
        " master_ids=CASE WHEN c.review_state<>'pending' THEN NULL"
        " WHEN cardinality(m.email_ids)>0 THEN m.email_ids WHEN cardinality(m.domain_ids)>0 THEN m.domain_ids END"
        " FROM matches m WHERE c.id=m.id", (excluded, keys, keys))


def counts(conn):
    return conn.execute(
        "SELECT count(*) FILTER (WHERE master_match='email') AS email,"
        " count(*) FILTER (WHERE master_match='domain') AS domain,"
        " count(*) FILTER (WHERE master_match IS NULL) AS none FROM candidates WHERE review_state='pending'").fetchone()


def import_workbook(data: bytes, filename: str, imported_by: str, check=False):
    sheet, records, skipped, flagged = parse(data)
    emails = len({email for record in records for email in record["emails"]})
    with db.connect() as conn:
        conn.execute(LOCK)
        conn.execute("DELETE FROM master_clients")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO master_clients(row_no,registered_name,alias,trade_name,tin,intercompany,engagement_status,contact_person,emails,domains)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(record["row_no"], *(record[field] for field in FIELDS), record["emails"], record["domains"]) for record in records])
        conn.execute("INSERT INTO master_imports(filename,sheet,rows,emails,imported_by) VALUES (%s,%s,%s,%s,%s)",
                     (filename, sheet, len(records), emails, imported_by))
        rematch(conn)
        matched = counts(conn)
        result = {"rows": len(records), "emails": emails, "skipped": skipped, "flagged": flagged,
                  "matched": {key: matched[key] for key in ("email", "domain")}, "dry_run": check}
        if check:
            conn.rollback()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        with args.path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        print(json.dumps(import_workbook(data, args.path.name, "cli", args.check)))
    except HTTPException as exc:
        print(exc.detail, file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
