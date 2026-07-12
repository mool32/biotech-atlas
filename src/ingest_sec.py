"""Business layer — link companies to SEC EDGAR and pull key financials.

Public companies file with the SEC. This links each company group to its CIK
(via the ticker in the seed list, matched through the same canonical() resolver
so it works on curated AND census), then pulls the latest annual XBRL facts:
revenue, R&D expense, cash, net income. Private / foreign-unlisted companies
(most of the census tail) have no filings and are left blank — honest coverage.

  python3 src/ingest_sec.py                              # curated
  ATLAS_DB=data/census.sqlite python3 src/ingest_sec.py  # census
"""
import csv
import json
import os
import time
import urllib.request

import db as dbm
from resolve_companies import canonical

UA = "biotech-atlas/0.1 (research; admin@biotech-atlas.example)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ATLAS_DB", os.path.join(ROOT, "data", "biotech.sqlite"))
SCHEMA = os.path.join(ROOT, "schema.sql")
SEEDS = os.path.join(ROOT, "data", "seeds", "oncology_companies.csv")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json"

# our metric -> candidate us-gaap tags (tag names change over time; take the
# most recent annual value found across any of them)
METRICS = {  # us-gaap (US filers)
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "rd_expense": ["ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
                   "ResearchAndDevelopmentExpense"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "net_income": ["NetIncomeLoss"],
}
IFRS = {  # ifrs-full (foreign filers: Sanofi, GSK, Novartis, BioNTech, …)
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "cash": ["CashAndCashEquivalents"],
    "net_income": ["ProfitLoss"],
}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def latest_fy(ns, tags):
    """Latest annual value: newest fiscal year across the candidate tags; for the
    same year, the higher-priority tag wins (so the recurring-R&D tag beats the
    IPR&D-only tag). Within a tag, prefer 'frame'd (consolidated) facts."""
    best = None                                   # ((fy, -tag_index), val, fy)
    for i, tag in enumerate(tags):
        framed, plain = [], []
        for x in ns.get(tag, {}).get("units", {}).get("USD", []) or []:
            if x.get("fp") == "FY" and x.get("val") is not None and x.get("fy"):
                (framed if x.get("frame") else plain).append(x)
        pool = framed or plain
        if not pool:
            continue
        b = max(pool, key=lambda x: x["fy"])
        key = (b["fy"], -i)
        if best is None or key > best[0]:
            best = (key, b["val"], b["fy"])
    return {"fy": best[2], "val": best[1]} if best else None


def main():
    conn = dbm.connect(DB_PATH)
    dbm.init_schema(conn, SCHEMA)

    # seed canonical name -> ticker
    seed_ticker = {}
    with open(SEEDS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("ticker"):
                seed_ticker[canonical(r["name"])] = r["ticker"].strip().upper()

    tmap = {v["ticker"].upper(): (v["cik_str"], v["title"])
            for v in get_json(TICKERS_URL).values()}

    groups = [r[0] for r in conn.execute(
        "SELECT DISTINCT COALESCE(canonical_name, name) FROM company").fetchall()]

    linked = facts_n = 0
    for org in groups:
        tk = seed_ticker.get(org)
        if not tk or tk not in tmap:
            continue
        cik, title = tmap[tk]
        conn.execute("UPDATE company SET sec_cik=? WHERE COALESCE(canonical_name,name)=?",
                     (str(cik), org))
        linked += 1
        try:
            facts = get_json(FACTS_URL.format(cik))
            time.sleep(0.15)
        except Exception as e:
            print(f"  {org}: facts ERROR {e}")
            continue
        allf = facts.get("facts", {})
        gaap, ifrs = allf.get("us-gaap", {}), allf.get("ifrs-full", {})
        got = []
        for metric in METRICS:
            b = latest_fy(gaap, METRICS[metric]) or latest_fy(ifrs, IFRS[metric])
            if b:
                conn.execute(
                    "INSERT INTO financials (cik,metric,value,fiscal_year) VALUES (?,?,?,?) "
                    "ON CONFLICT(cik,metric) DO UPDATE SET value=excluded.value, "
                    "fiscal_year=excluded.fiscal_year",
                    (str(cik), metric, b["val"], b["fy"]))
                facts_n += 1
                got.append(f"{metric}={b['val']/1e9:.1f}B'{str(b['fy'])[2:]}")
        conn.commit()
        print(f"  {org} -> CIK {cik} ({tk})  " + " ".join(got))

    print(f"\nlinked {linked} company groups to SEC EDGAR; {facts_n} financial facts stored")


if __name__ == "__main__":
    main()
