"""Report the extraction backlog and the work left after retiring legacy failures."""

from psycopg import IsolationLevel

from .. import db
from ..pipeline import count_pending


def report(data_source: str) -> None:
    with db.connect() as conn:
        conn.read_only = True
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        total = count_pending(conn)
        rows = conn.execute("""
            SELECT source, extract_error IS NOT NULL AS failed,
                   CASE WHEN fetched_at >= now() - interval '1 day' THEN '0-1d'
                        WHEN fetched_at >= now() - interval '7 days' THEN '1-7d'
                        ELSE '7d+' END AS age,
                   CASE WHEN source = 'gmail' THEN coalesce(payload->>'v', 'missing')
                        ELSE '-' END AS version,
                   count(*) AS n
            FROM raw_items
            WHERE extracted_at IS NULL
            GROUP BY source, failed, age, version
            ORDER BY source, failed, age, version
        """).fetchall()

    print(f"Data source: {data_source}")
    print(f"Pending rows: {total}")
    print("Age buckets: 0-1d = [now-1d, present and later], 1-7d = [now-7d, now-1d), 7d+ = before now-7d")
    print("source\textract_error\tage\tgmail_v\trows")
    for row in rows:
        error = "present" if row["failed"] else "absent"
        print(f"{row['source']}\t{error}\t{row['age']}\t{row['version']}\t{row['n']}")
    print(f"Expected first-run pending rows after phase 2 backfill: {sum(row['n'] for row in rows if not row['failed'])}")


if __name__ == "__main__":
    report("developer DB (configured DATABASE_URL)")
