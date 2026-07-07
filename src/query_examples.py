"""Analytical 'map' queries over the graph — the kind of question the
filterable-table and landscape views will answer. Read-only.

Run after ingest + resolve:
  python3 src/query_examples.py
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "biotech.sqlite")


def show(conn, title, sql):
    print(f"\n## {title}")
    rows = conn.execute(sql).fetchall()
    for r in rows:
        print("  " + " | ".join("·" if v is None else str(v) for v in r))
    if not rows:
        print("  (no rows yet — run ingest + resolve first)")


def main():
    conn = sqlite3.connect(DB)

    show(conn, "top orgs by # oncology trials (parent-rolled-up, authoritative count)", """
        SELECT COALESCE(p.name, c.name) AS org, SUM(c.trials_total) AS total_onco_trials
        FROM company c LEFT JOIN company p ON c.parent_id = p.id
        GROUP BY COALESCE(p.name, c.name)
        ORDER BY total_onco_trials DESC LIMIT 12;""")

    show(conn, "subsidiary roll-ups applied", """
        SELECT c.name AS subsidiary, p.name AS rolled_into, c.trials_total
        FROM company c JOIN company p ON c.parent_id = p.id
        ORDER BY c.trials_total DESC;""")

    show(conn, "trial landscape by phase", """
        SELECT COALESCE(phase,'(unspecified)') AS phase, count(*) AS trials
        FROM trial GROUP BY phase ORDER BY trials DESC;""")

    show(conn, "most-studied indications (canonicalized)", """
        SELECT i.canonical, count(DISTINCT e.src_id) AS trials
        FROM indication i
        JOIN edge e ON e.dst_type='indication' AND e.dst_id=i.id AND e.rel='for'
        GROUP BY i.canonical ORDER BY trials DESC LIMIT 10;""")

    show(conn, "asset roles (comparators/placebo separated out)", """
        SELECT COALESCE(role,'(unresolved)') AS role, count(*) AS assets
        FROM asset GROUP BY role ORDER BY assets DESC;""")

    show(conn, "top PROPRIETARY multi-trial programs (SOC + placebo excluded)", """
        SELECT a.name, COALESCE(a.modality,'?') AS modality,
               count(DISTINCT e.dst_id) AS trials
        FROM asset a
        JOIN edge e ON e.src_type='asset' AND e.src_id=a.id AND e.rel='tested_in'
        WHERE a.role='proprietary'
        GROUP BY a.id ORDER BY trials DESC LIMIT 15;""")

    show(conn, "what got filtered: top non-proprietary agents by trial count", """
        SELECT a.name, a.role, count(DISTINCT e.dst_id) AS trials
        FROM asset a
        JOIN edge e ON e.src_type='asset' AND e.src_id=a.id AND e.rel='tested_in'
        WHERE a.role IN ('standard_of_care','placebo')
        GROUP BY a.id ORDER BY trials DESC LIMIT 10;""")

    show(conn, "provenance audit: fact vs inferred edges", """
        SELECT confidence, count(*) FROM edge GROUP BY confidence ORDER BY 2 DESC;""")

    conn.close()


if __name__ == "__main__":
    main()
