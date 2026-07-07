"""Ingest oncology trials from ClinicalTrials.gov (API v2) for the seed companies.

Stage 1 (ingest) + a thin stage 2/3 (resolve + load):
  seed company -> query CT.gov -> land raw JSON snapshot -> parse -> upsert graph.

Provenance rule: facts stated directly by the source are confidence='fact';
anything the pipeline guesses (modality, "company develops this asset") is
confidence='inferred'. Nothing is invented — every row traces to a raw snapshot
under data/raw/.

Usage:
  python3 src/ingest_clinicaltrials.py --limit 10     # first 10 seed companies
  python3 src/ingest_clinicaltrials.py                # all seeds
"""
import argparse
import csv
import datetime
import json
import os
import time
import urllib.parse
import urllib.request

import db as dbm

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
SOURCE = "ClinicalTrials.gov"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(ROOT, "data", "biotech.sqlite")
SCHEMA = os.path.join(ROOT, "schema.sql")
SEEDS = os.path.join(ROOT, "data", "seeds", "oncology_companies.csv")

# Coarse modality guesses from intervention type — deliberately marked 'inferred'.
MODALITY_HINT = {"BIOLOGICAL": "biologic", "GENETIC": "gene / nucleic acid"}
ASSET_TYPES = ("DRUG", "BIOLOGICAL", "GENETIC")


def slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def _get(params: dict) -> dict:
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "biotech-atlas/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_company_all(name: str, page_size: int, max_records: int = 0):
    """Fetch all oncology trials for a sponsor, following pagination."""
    studies, token, total = [], None, None
    while True:
        params = {"query.spons": name, "query.cond": "cancer", "pageSize": str(page_size)}
        if token is None:
            params["countTotal"] = "true"       # only needed on the first page
        else:
            params["pageToken"] = token
        data = _get(params)
        if total is None:
            total = data.get("totalCount")
        studies.extend(data.get("studies", []) or [])
        token = data.get("nextPageToken")
        if not token or (max_records and len(studies) >= max_records):
            break
        time.sleep(0.15)
    return studies, total


def phase_str(phases):
    if not phases:
        return None
    return "/".join(p.replace("PHASE", "Phase ").replace("NA", "N/A") for p in phases)


def load_study(conn, company_id: int, study: dict, retrieved: str) -> bool:
    ps = study.get("protocolSection", {})
    idm = ps.get("identificationModule", {})
    nct = idm.get("nctId")
    if not nct:
        return False
    sm = ps.get("statusModule", {})
    trial_id = dbm.upsert_trial(
        conn, nct, idm.get("briefTitle"), sm.get("overallStatus"),
        phase_str(ps.get("designModule", {}).get("phases")),
        sm.get("startDateStruct", {}).get("date"), SOURCE, retrieved,
    )
    ref = f"https://clinicaltrials.gov/study/{nct}"
    conditions = ps.get("conditionsModule", {}).get("conditions", []) or []
    interventions = ps.get("armsInterventionsModule", {}).get("interventions", []) or []

    # company runs trial (fact: registry lists it as the sponsor)
    dbm.upsert_edge(conn, "company", company_id, "runs", "trial", trial_id,
                    SOURCE, ref, retrieved, "fact")

    ind_ids = []
    for cond in conditions:
        ind_id = dbm.upsert_node(conn, "indication", cond)
        ind_ids.append(ind_id)
        dbm.upsert_edge(conn, "trial", trial_id, "for", "indication", ind_id,
                        SOURCE, ref, retrieved, "fact")

    for iv in interventions:
        if (iv.get("type") or "").upper() not in ASSET_TYPES or not iv.get("name"):
            continue
        modality = MODALITY_HINT.get((iv.get("type") or "").upper())
        extra = {"modality": modality, "modality_source": "inferred"} if modality else {}
        asset_id = dbm.upsert_node(conn, "asset", iv["name"], extra)
        dbm.upsert_edge(conn, "asset", asset_id, "tested_in", "trial", trial_id,
                        SOURCE, ref, retrieved, "fact")
        dbm.upsert_edge(conn, "company", company_id, "develops", "asset", asset_id,
                        SOURCE, ref, retrieved, "inferred")
        for ind_id in ind_ids:
            dbm.upsert_edge(conn, "asset", asset_id, "treats", "indication", ind_id,
                            SOURCE, ref, retrieved, "inferred")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max companies (0 = all)")
    ap.add_argument("--page-size", type=int, default=1000, help="records per API page (max 1000)")
    ap.add_argument("--max-per-company", type=int, default=0, help="cap trials/company (0 = all)")
    args = ap.parse_args()
    page_size = min(max(args.page_size, 1), 1000)
    if args.max_per_company:                 # don't over-fetch a page we'll truncate
        page_size = min(page_size, args.max_per_company)

    retrieved = datetime.date.today().isoformat()
    raw_dir = os.path.join(ROOT, "data", "raw", "clinicaltrials", retrieved)
    os.makedirs(raw_dir, exist_ok=True)

    with open(SEEDS, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("name")]
    if args.limit:
        rows = rows[: args.limit]

    conn = dbm.connect(DB_PATH)
    dbm.init_schema(conn, SCHEMA)

    total = 0
    for i, row in enumerate(rows, 1):
        name = row["name"].strip()
        parent = (row.get("parent") or "").strip() or None
        try:
            studies, tc = fetch_company_all(name, page_size, args.max_per_company)
        except Exception as e:  # keep the run going on a single bad request
            print(f"[{i}/{len(rows)}] {name}: ERROR {e}")
            continue
        with open(os.path.join(raw_dir, slug(name) + ".json"), "w", encoding="utf-8") as out:
            json.dump({"query": {"spons": name, "cond": "cancer"},
                       "totalCount": tc, "retrieved": retrieved, "studies": studies},
                      out, ensure_ascii=False)
        company_id = dbm.upsert_node(conn, "company", name,
                                     {"parent_name": parent} if parent else None)
        conn.execute("UPDATE company SET trials_total=? WHERE id=?", (tc, company_id))
        loaded = sum(load_study(conn, company_id, s, retrieved) for s in studies)
        conn.commit()
        tail = f" (of {tc} total)" if tc is not None else ""
        print(f"[{i}/{len(rows)}] {name}: {loaded} loaded{tail}")
        total += loaded
        time.sleep(0.2)

    print(f"\nDone. Loaded {total} studies from {len(rows)} companies -> {DB_PATH}")
    _summary(conn)


def _summary(conn):
    scalar = lambda q: conn.execute(q).fetchone()[0]
    print("\n--- graph size ---")
    for t in ("company", "asset", "indication", "trial", "edge"):
        print(f"  {t:11s}: {scalar(f'SELECT count(*) FROM {t}')}")
    print("\n--- edges by relation / confidence ---")
    for rel, conf, n in conn.execute(
        "SELECT rel, confidence, count(*) FROM edge "
        "GROUP BY rel, confidence ORDER BY 3 DESC"
    ):
        print(f"  {rel:10s} [{conf:8s}]: {n}")


if __name__ == "__main__":
    main()
