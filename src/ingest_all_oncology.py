"""Phase 4 — invert the seed: harvest the FULL industry oncology corpus.

Instead of "seed companies -> their trials", this walks every industry-LED
oncology trial on ClinicalTrials.gov and builds the graph bottom-up, so the
company universe is *discovered from the data* rather than a curated list.

Writes to a separate census db by default (keeps the curated graph intact):
  ATLAS_DB=data/census.sqlite python3 src/ingest_all_oncology.py

Scope: query.cond=cancer, pre-filtered to industry funding, then kept only where
leadSponsor.class == INDUSTRY (aggFilters also lets academic-led trials with an
industry collaborator through, which we drop). Fields are trimmed to what we
parse, so the raw corpus stays ~tens of MB, not ~1 GB.
"""
import datetime
import json
import os
import time
import urllib.parse
import urllib.request

import db as dbm

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
SOURCE = "ClinicalTrials.gov"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ATLAS_DB", os.path.join(ROOT, "data", "census.sqlite"))
SCHEMA = os.path.join(ROOT, "schema.sql")

FIELDS = ",".join([
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.designModule.phases",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor",
    "protocolSection.conditionsModule.conditions",
    "protocolSection.armsInterventionsModule.interventions",
])
MODALITY_HINT = {"BIOLOGICAL": "biologic", "GENETIC": "gene / nucleic acid"}
ASSET_TYPES = ("DRUG", "BIOLOGICAL", "GENETIC")


def fetch_page(token):
    params = {"query.cond": "cancer", "aggFilters": "funderType:industry",
              "pageSize": "1000", "fields": FIELDS}
    if token:
        params["pageToken"] = token
    else:
        params["countTotal"] = "true"
    # keep ':' , '.' and ',' literal — encoding the colon breaks aggFilters
    url = BASE_URL + "?" + urllib.parse.urlencode(params, safe=":,.")
    req = urllib.request.Request(url, headers={"User-Agent": "biotech-atlas/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def phase_str(phases):
    if not phases:
        return None
    return "/".join(p.replace("PHASE", "Phase ").replace("NA", "N/A") for p in phases)


def load_study(conn, s, retrieved):
    ps = s.get("protocolSection", {})
    nct = ps.get("identificationModule", {}).get("nctId")
    sp = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    if not nct or sp.get("class") != "INDUSTRY" or not sp.get("name"):
        return False
    company_id = dbm.upsert_node(conn, "company", sp["name"])
    trial_id = dbm.upsert_trial(
        conn, nct, ps.get("identificationModule", {}).get("briefTitle"),
        ps.get("statusModule", {}).get("overallStatus"),
        phase_str(ps.get("designModule", {}).get("phases")), None, SOURCE, retrieved)
    ref = f"https://clinicaltrials.gov/study/{nct}"
    dbm.upsert_edge(conn, "company", company_id, "runs", "trial", trial_id,
                    SOURCE, ref, retrieved, "fact")

    ind_ids = []
    for cond in ps.get("conditionsModule", {}).get("conditions", []) or []:
        iid = dbm.upsert_node(conn, "indication", cond)
        ind_ids.append(iid)
        dbm.upsert_edge(conn, "trial", trial_id, "for", "indication", iid,
                        SOURCE, ref, retrieved, "fact")

    for iv in ps.get("armsInterventionsModule", {}).get("interventions", []) or []:
        itype = (iv.get("type") or "").upper()
        if itype not in ASSET_TYPES or not iv.get("name"):
            continue
        modality = MODALITY_HINT.get(itype)
        extra = {"modality": modality, "modality_source": "inferred"} if modality else {}
        aid = dbm.upsert_node(conn, "asset", iv["name"], extra)
        dbm.upsert_edge(conn, "asset", aid, "tested_in", "trial", trial_id,
                        SOURCE, ref, retrieved, "fact")
        dbm.upsert_edge(conn, "company", company_id, "develops", "asset", aid,
                        SOURCE, ref, retrieved, "inferred")
        for iid in ind_ids:
            dbm.upsert_edge(conn, "asset", aid, "treats", "indication", iid,
                            SOURCE, ref, retrieved, "inferred")
    return True


def main():
    retrieved = datetime.date.today().isoformat()
    raw_dir = os.path.join(ROOT, "data", "raw", "census", retrieved)
    os.makedirs(raw_dir, exist_ok=True)

    conn = dbm.connect(DB_PATH)
    dbm.init_schema(conn, SCHEMA)

    token, page, fetched, kept, total = None, 0, 0, 0, None
    while True:
        data = fetch_page(token)
        if total is None:
            total = data.get("totalCount")
        studies = data.get("studies", []) or []
        with open(os.path.join(raw_dir, f"page_{page:03d}.json"), "w", encoding="utf-8") as f:
            json.dump(studies, f, ensure_ascii=False)
        kept += sum(load_study(conn, s, retrieved) for s in studies)
        fetched += len(studies)
        conn.commit()
        page += 1
        print(f"page {page}: fetched {fetched}"
              + (f"/{total}" if total else "") + f", kept industry-led {kept}")
        token = data.get("nextPageToken")
        if not token or not studies:
            break
        time.sleep(0.1)

    print(f"\nDone. {kept} industry-led trials kept (of {fetched} industry-funded fetched).")
    for t in ("company", "trial", "asset", "indication", "edge"):
        n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t:11s}: {n}")


if __name__ == "__main__":
    main()
