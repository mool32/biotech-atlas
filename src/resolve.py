"""Stage 2 — resolution & cleanup over the loaded graph.

Post-processing that turns raw ingest into a cleaner map WITHOUT deleting
anything (transparency): assets get a role, indications a canonical label,
companies a resolved parent. All heuristic and documented in METHODOLOGY.md —
interim until canonical ids (ChEMBL, MONDO, LEI) are attached.

  python3 src/resolve.py
"""
import os
import re
import sqlite3

from db import normalize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "biotech.sqlite")

# Generic chemo backbones / supportive care / established off-patent biologics.
# These dominate combo-trial arms as *background* therapy, not as the sponsor's
# proprietary program. Interim list — superseded by ChEMBL/ATC mapping later.
SOC = {
    "carboplatin", "cisplatin", "oxaliplatin", "paclitaxel", "docetaxel",
    "gemcitabine", "pemetrexed", "fluorouracil", "capecitabine", "irinotecan",
    "doxorubicin", "epirubicin", "cyclophosphamide", "ifosfamide", "etoposide",
    "vincristine", "vinblastine", "vinorelbine", "methotrexate", "cytarabine",
    "fludarabine", "bendamustine", "temozolomide", "mitomycin", "topotecan",
    "leucovorin", "folinic", "bleomycin", "dacarbazine", "melphalan", "busulfan",
    "azacitidine", "decitabine", "hydroxyurea", "dexamethasone", "prednisone",
    "prednisolone", "methylprednisolone", "filgrastim", "pegfilgrastim",
    "ondansetron", "tamoxifen", "letrozole", "anastrozole", "exemestane",
    "fulvestrant", "leuprolide", "goserelin", "bicalutamide", "abiraterone",
    "rituximab", "trastuzumab", "bevacizumab", "cetuximab", "pertuzumab",
}
PLACEBO = {"placebo", "saline", "vehicle", "sham", "bsc"}

# Qualifiers stripped when canonicalizing an indication label.
QUALIFIERS = {
    "metastatic", "advanced", "locally", "recurrent", "refractory", "relapsed",
    "unresectable", "newly", "diagnosed", "early", "high", "low", "risk",
    "adult", "pediatric", "childhood", "primary", "secondary", "resistant",
    "progressive", "stage", "i", "ii", "iii", "iv", "or", "and",
}

# British -> American + possessive normalization, so labels match MONDO terms.
SPELLING = {
    "tumour": "tumor", "tumours": "tumors", "oesophageal": "esophageal",
    "oesophagus": "esophagus", "leukaemia": "leukemia", "leukaemias": "leukemias",
    "paediatric": "pediatric", "haematologic": "hematologic",
    "haematological": "hematological", "haematopoietic": "hematopoietic",
    "coeliac": "celiac", "oedema": "edema", "hodgkins": "hodgkin",
    "metastases": "metastasis",
}


def tokens(s):
    return [SPELLING.get(t, t) for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def disease_key(s):
    """Order-independent, spelling-normalized token key for disease matching."""
    return tuple(sorted(tokens(s)))


def classify_asset(name):
    tks = set(tokens(name))
    if tks & PLACEBO or "best supportive care" in (name or "").lower():
        return "placebo"
    if tks & SOC:
        return "standard_of_care"
    return "proprietary"


def canonical_indication(name):
    name = re.sub(r"\([^)]*\)", " ", name or "")   # drop "(RCC)"-style abbreviations
    out = []
    for t in tokens(name):
        if t in QUALIFIERS:
            continue
        if t in ("tumors", "tumor"):
            t = "tumor"
        elif t in ("cancers", "cancer", "neoplasm", "neoplasms"):
            t = "cancer"
        out.append(t)
    s = " ".join(out).strip()
    return " ".join(w.capitalize() for w in s.split()) or (name or "").strip().title()


def main():
    conn = sqlite3.connect(DB)

    roles = {"proprietary": 0, "standard_of_care": 0, "placebo": 0}
    for aid, name in conn.execute("SELECT id, name FROM asset").fetchall():
        role = classify_asset(name)
        roles[role] += 1
        conn.execute("UPDATE asset SET role=?, role_source='heuristic' WHERE id=?",
                     (role, aid))

    raw_inds = conn.execute("SELECT count(*) FROM indication").fetchone()[0]
    canon = set()
    for iid, name in conn.execute("SELECT id, name FROM indication").fetchall():
        c = canonical_indication(name)
        canon.add(c)
        conn.execute("UPDATE indication SET canonical=? WHERE id=?", (c, iid))

    linked = 0
    for cid, pname in conn.execute(
        "SELECT id, parent_name FROM company WHERE parent_name IS NOT NULL"
    ).fetchall():
        row = conn.execute(
            "SELECT id FROM company WHERE name_norm=?", (normalize(pname),)
        ).fetchone()
        if row and row[0] != cid:
            conn.execute("UPDATE company SET parent_id=? WHERE id=?", (row[0], cid))
            linked += 1

    conn.commit()

    print("assets classified:")
    for r, n in sorted(roles.items(), key=lambda x: -x[1]):
        print(f"  {r:18s}: {n}")
    print(f"indications: {raw_inds} raw labels -> {len(canon)} canonical "
          f"({raw_inds - len(canon)} collapsed)")
    print(f"companies: {linked} subsidiaries linked to a parent")


if __name__ == "__main__":
    main()
