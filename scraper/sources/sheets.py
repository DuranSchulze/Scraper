"""Fetch client tracker sheets. Each non-empty row becomes one raw item.

Sheets are read as GOOGLE_ADMIN_EMAIL, so each sheet must be shared with that account.
"""

from .. import db
from ..config import env
from ..google_client import execute, service


def rows_to_records(rows: list[list[str]]) -> list[tuple[int, dict]]:
    """First row is the header. Returns (sheet row number, {header: cell}) for non-empty rows."""
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out = []
    for n, row in enumerate(rows[1:], start=2):
        if any(str(c).strip() for c in row):
            out.append((n, {h: (row[i] if i < len(row) else "") for i, h in enumerate(header) if h}))
    return out


def fetch_sheets(conn, sheet_ids: list[str], log) -> int:
    from .. import pipeline

    sh = service("sheets", env("GOOGLE_ADMIN_EMAIL"))
    n = 0
    for sid in sheet_ids:
        meta = execute(sh.spreadsheets().get(
            spreadsheetId=sid, fields="properties.title,sheets.properties.title"))
        # Row numbers shift when rows are inserted, so replace the whole sheet each run.
        keys = {r["key"] for r in conn.execute(
            "SELECT s.key FROM signals s JOIN raw_items r ON r.id=s.raw_item_id"
            " WHERE r.source='sheets' AND starts_with(r.source_id, %s)", (f"{sid}:",))}
        conn.execute("DELETE FROM raw_items WHERE source = 'sheets' AND starts_with(source_id, %s)", (f"{sid}:",))
        for tab in (s["properties"]["title"] for s in meta["sheets"]):
            rng = "'" + tab.replace("'", "''") + "'"
            rows = execute(sh.spreadsheets().values().get(spreadsheetId=sid, range=rng)).get("values", [])
            for row_no, record in rows_to_records(rows):
                db.upsert_raw(conn, "sheets", "", f"{sid}:{tab}:{row_no}", {
                    "sheet": meta["properties"]["title"], "tab": tab, "row": row_no, "record": record})
                n += 1
        conn.commit()
        pipeline.run_extract(log, keys=keys)
        log(f"sheet {meta['properties']['title']}: done")
    log(f"sheets: {n} rows")
    return n
