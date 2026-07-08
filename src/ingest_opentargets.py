"""Phase 2 — attach targets (and real modality + ChEMBL id) via Open Targets.

For the top proprietary assets by trial count, resolve the drug in Open Targets
(GraphQL), then load:
  asset  --targets-->  target        (rel='targets', source='Open Targets', fact)
  asset.chembl_id, asset.modality (= drugType, from a real source)

This upgrades the coarse `inferred` modality to a sourced value and adds the
scientific spine (asset -> target). Bounded to --limit assets to keep API calls
reasonable.

  python3 src/ingest_opentargets.py --limit 80
"""
import argparse
import datetime
import json
import os
import re
import time
import urllib.request

import db as dbm

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"
SOURCE = "Open Targets"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "biotech.sqlite")
SCHEMA = os.path.join(ROOT, "schema.sql")

SEARCH_Q = 'query($q:String!){ search(queryString:$q, entityNames:["drug"]){ hits { id name entity } } }'
DRUG_Q = ('query($id:String!){ drug(chemblId:$id){ drugType '
          'mechanismsOfAction { rows { targets { id approvedSymbol } } } } }')


def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        OT_URL, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "biotech-atlas/0.1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def resolve_chembl(name):
    clean = re.sub(r"\([^)]*\)", " ", name).strip()   # drop "(Nexavar, BAY43-9006)" noise
    hits = (gql(SEARCH_Q, {"q": clean}).get("data", {}).get("search", {}) or {}).get("hits", []) or []
    drug_hits = [h for h in hits if h.get("entity") == "drug"]
    for h in drug_hits:                       # prefer an exact (normalized) name match
        if dbm.normalize(h.get("name", "")) == dbm.normalize(clean):
            return h["id"]
    return drug_hits[0]["id"] if len(drug_hits) == 1 else None


def drug_targets(chembl):
    drug = (gql(DRUG_Q, {"id": chembl}).get("data", {}) or {}).get("drug") or {}
    targets = {}
    for row in (drug.get("mechanismsOfAction", {}) or {}).get("rows", []) or []:
        for t in row.get("targets", []) or []:
            if t.get("approvedSymbol"):
                targets[t["approvedSymbol"]] = t.get("id")
    return drug.get("drugType"), targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80, help="max assets to process this run")
    ap.add_argument("--min-trials", type=int, default=1, help="only assets in >= N trials")
    ap.add_argument("--sleep", type=float, default=0.12)
    args = ap.parse_args()
    retrieved = datetime.date.today().isoformat()

    conn = dbm.connect(DB_PATH)
    dbm.init_schema(conn, SCHEMA)

    # incremental: only assets not yet resolved and not yet tried
    assets = conn.execute(
        """SELECT a.id, a.name
           FROM asset a
           JOIN edge e ON e.src_type='asset' AND e.src_id=a.id AND e.rel='tested_in'
           WHERE a.role='proprietary' AND a.chembl_id IS NULL AND a.ot_checked IS NULL
           GROUP BY a.id
           HAVING count(DISTINCT e.dst_id) >= ?
           ORDER BY count(DISTINCT e.dst_id) DESC LIMIT ?;""",
        (args.min_trials, args.limit),
    ).fetchall()

    matched = n_edges = 0
    for i, (aid, name) in enumerate(assets, 1):
        try:
            chembl = resolve_chembl(name)
            time.sleep(args.sleep)
            drug_type, targets = drug_targets(chembl) if chembl else (None, {})
            if chembl:
                time.sleep(args.sleep)
        except Exception as e:  # leave ot_checked NULL -> retried on a later run
            print(f"[{i}/{len(assets)}] {name}: ERROR {e}")
            continue

        conn.execute("UPDATE asset SET ot_checked=? WHERE id=?", (retrieved, aid))
        if not chembl:
            conn.commit()
            print(f"[{i}/{len(assets)}] {name}: no drug match")
            continue

        conn.execute(
            "UPDATE asset SET chembl_id=?, modality=COALESCE(?,modality), "
            "modality_source=CASE WHEN ? IS NOT NULL THEN 'Open Targets' ELSE modality_source END "
            "WHERE id=?",
            (chembl, drug_type, drug_type, aid),
        )
        for symbol, ens in targets.items():
            tid = dbm.upsert_node(conn, "target", symbol, {"ensembl_id": ens})
            dbm.upsert_edge(conn, "asset", aid, "targets", "target", tid,
                            SOURCE, chembl, retrieved, "fact")
            n_edges += 1
        matched += 1
        conn.commit()
        tgt = ", ".join(list(targets)[:4]) or "(no MoA targets)"
        print(f"[{i}/{len(assets)}] {name} -> {chembl} [{drug_type}] : {tgt}")

    tot_res = conn.execute("SELECT count(*) FROM asset WHERE chembl_id IS NOT NULL").fetchone()[0]
    n_t = conn.execute("SELECT count(*) FROM target").fetchone()[0]
    print(f"\nResolved {matched}/{len(assets)} new assets; {n_edges} new asset->target edges.")
    print(f"cumulative: {tot_res} assets resolved, {n_t} targets in graph")


if __name__ == "__main__":
    main()
