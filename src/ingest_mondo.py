"""Phase 3 — map indications to the MONDO disease ontology (EBI OLS4).

Maps each distinct stage-2 `canonical` label (qualifiers already stripped) to a
MONDO term using OLS exact matching, then pulls MONDO ancestors to build a rollup
hierarchy (`disease --subtype_of--> disease`).

Precision over recall: exact label/synonym match only. A label with no confident
MONDO term is left unmapped (it keeps its heuristic `canonical`) rather than
mapped wrongly — objectivity over coverage.

  python3 src/ingest_mondo.py --reset --limit 150
"""
import argparse
import datetime
import json
import os
import time
import urllib.parse
import urllib.request

import db as dbm
from resolve import disease_key

OLS = "https://www.ebi.ac.uk/ols4/api"
SOURCE = "MONDO (OLS)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ATLAS_DB", os.path.join(ROOT, "data", "biotech.sqlite"))
SCHEMA = os.path.join(ROOT, "schema.sql")

# ultra-generic MONDO nodes we do not roll up into (too broad to be useful)
DENY = {
    "MONDO:0000001",  # disease
    "MONDO:0700096",  # human disease
    "MONDO:0023370",  # neoplastic disease or syndrome
    "MONDO:0045024",  # cancer or benign tumor
    "MONDO:7770006",  # disease by body system or component
    "MONDO:7770008",  # disease by etiologic mechanism
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "biotech-atlas/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_mondo(term):
    """Precision-first, order- and spelling-independent: accept a MONDO term only
    if its label or a synonym has the same token multiset as the query. OLS
    ranking alone is unreliable, so we verify rather than trust the top hit."""
    url = f"{OLS}/search?" + urllib.parse.urlencode(
        {"q": term, "ontology": "mondo", "rows": 25,
         "fieldList": "iri,label,obo_id,synonym"})
    docs = [d for d in get(url).get("response", {}).get("docs", [])
            if (d.get("obo_id") or "").startswith("MONDO:")]
    key = disease_key(term)
    for d in docs:                                   # 1. label token-set match
        if disease_key(d.get("label", "")) == key:
            return d
    for d in docs:                                   # 2. synonym token-set match
        if any(disease_key(s) == key for s in (d.get("synonym") or [])):
            return d
    return None


def ancestors(iri):
    enc = urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")
    url = f"{OLS}/ontologies/mondo/terms/{enc}/hierarchicalAncestors?size=100"
    try:
        terms = get(url).get("_embedded", {}).get("terms", [])
    except Exception:
        return []
    return [(t["obo_id"], t.get("label")) for t in terms
            if (t.get("obo_id") or "").startswith("MONDO:") and t["obo_id"] not in DENY]


def upsert_disease(conn, mondo_id, label):
    conn.execute(
        "INSERT INTO disease (mondo_id,label,name_norm) VALUES (?,?,?) "
        "ON CONFLICT(mondo_id) DO UPDATE SET label=excluded.label;",
        (mondo_id, label, dbm.normalize(label or "")))
    return conn.execute("SELECT id FROM disease WHERE mondo_id=?", (mondo_id,)).fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150, help="top-N canonical indications by trials")
    ap.add_argument("--reset", action="store_true", help="clear prior MONDO mapping first")
    ap.add_argument("--sleep", type=float, default=0.1)
    args = ap.parse_args()
    retrieved = datetime.date.today().isoformat()

    conn = dbm.connect(DB_PATH)
    dbm.init_schema(conn, SCHEMA)

    if args.reset:
        conn.execute("DELETE FROM edge WHERE rel='subtype_of';")
        conn.execute("DELETE FROM disease;")
        conn.execute("UPDATE indication SET mondo_id=NULL, mondo_label=NULL;")
        conn.commit()

    cans = conn.execute(
        """SELECT i.canonical, count(DISTINCT e.src_id) AS trials
           FROM indication i
           JOIN edge e ON e.dst_type='indication' AND e.dst_id=i.id AND e.rel='for'
           WHERE i.canonical IS NOT NULL AND i.canonical <> ''
             AND i.canonical NOT IN (SELECT canonical FROM indication
                                     WHERE mondo_id IS NOT NULL AND canonical IS NOT NULL)
           GROUP BY i.canonical ORDER BY trials DESC LIMIT ?;""", (args.limit,)).fetchall()

    mapped = edges = 0
    for i, (can, tr) in enumerate(cans, 1):
        try:
            hit = search_mondo(can)
            time.sleep(args.sleep)
        except Exception as e:
            print(f"[{i}/{len(cans)}] {can}: ERROR {e}")
            continue
        if not hit:
            print(f"[{i}/{len(cans)}] {can}: no exact MONDO term")
            continue
        mid, label, iri = hit["obo_id"], hit.get("label"), hit.get("iri")
        did = upsert_disease(conn, mid, label)
        conn.execute("UPDATE indication SET mondo_id=?, mondo_label=? WHERE canonical=?",
                     (mid, label, can))
        for aid, albl in ancestors(iri):
            adid = upsert_disease(conn, aid, albl)
            if adid != did:
                dbm.upsert_edge(conn, "disease", did, "subtype_of", "disease", adid,
                                SOURCE, mid, retrieved, "fact")
                edges += 1
        time.sleep(args.sleep)
        mapped += 1
        conn.commit()
        print(f"[{i}/{len(cans)}] {can} -> {mid} {label}")

    tot = conn.execute("SELECT count(DISTINCT src_id) FROM edge WHERE rel='for'").fetchone()[0]
    cov = conn.execute(
        "SELECT count(DISTINCT e.src_id) FROM edge e JOIN indication i ON i.id=e.dst_id "
        "WHERE e.rel='for' AND i.mondo_id IS NOT NULL").fetchone()[0]
    n_dis = conn.execute("SELECT count(*) FROM disease").fetchone()[0]
    print(f"\nmapped {mapped}/{len(cans)} canonical labels; {edges} subtype_of edges; "
          f"{n_dis} disease nodes")
    print(f"trial coverage: {cov}/{tot} trials now have a MONDO-mapped indication")


if __name__ == "__main__":
    main()
