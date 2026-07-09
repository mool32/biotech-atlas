"""Company entity resolution — collapse name variants and subsidiaries into
canonical corporate groups.

Non-destructive: sets `company.canonical_name`; nodes and edges are untouched,
so rollups just GROUP BY canonical_name. Precision-first — a curated alias /
subsidiary map handles the head of the distribution (the companies that matter);
everything else falls back to legal-suffix-stripped normalization. The alias map
is deterministic and reviewable (see METHODOLOGY.md); it is not exhaustive.

  ATLAS_DB=data/census.sqlite python3 src/resolve_companies.py
"""
import os
import re
import sqlite3

from db import init_schema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("ATLAS_DB", os.path.join(ROOT, "data", "biotech.sqlite"))
SCHEMA = os.path.join(ROOT, "schema.sql")

# Ordered (phrase-in-name -> canonical group). SPECIFIC / disambiguating rules
# first — e.g. Merck KGaA before the generic "merck" (which is Merck & Co / MSD).
ALIASES = [
    ("emd serono", "Merck KGaA"), ("merck serono", "Merck KGaA"),
    ("merck kgaa", "Merck KGaA"), ("merck patent", "Merck KGaA"),
    ("merck healthcare", "Merck KGaA"),
    ("genentech", "Roche"), ("hoffmann", "Roche"), ("chugai", "Roche"),
    ("roche", "Roche"),
    ("msd", "Merck & Co"), ("merck sharp", "Merck & Co"), ("merck & co", "Merck & Co"),
    ("merck and co", "Merck & Co"), ("merck co", "Merck & Co"),
    ("schering plough", "Merck & Co"), ("merck", "Merck & Co"),
    ("janssen", "Johnson & Johnson"), ("johnson", "Johnson & Johnson"),
    ("celgene", "Bristol Myers Squibb"), ("juno therapeutics", "Bristol Myers Squibb"),
    ("bristol", "Bristol Myers Squibb"),
    ("pharmacyclics", "AbbVie"), ("stemcentrx", "AbbVie"), ("allergan", "AbbVie"),
    ("abbvie", "AbbVie"),
    ("loxo", "Eli Lilly"), ("eli lilly", "Eli Lilly"), ("lilly", "Eli Lilly"),
    ("seagen", "Pfizer"), ("seattle genetics", "Pfizer"), ("array biopharma", "Pfizer"),
    ("medivation", "Pfizer"), ("wyeth", "Pfizer"), ("pfizer", "Pfizer"),
    ("medimmune", "AstraZeneca"), ("alexion", "AstraZeneca"),
    ("astrazeneca", "AstraZeneca"), ("astra zeneca", "AstraZeneca"),
    ("genzyme", "Sanofi"), ("aventis", "Sanofi"), ("sanofi", "Sanofi"),
    ("millennium", "Takeda"), ("takeda", "Takeda"),
    ("tesaro", "GSK"), ("glaxosmithkline", "GSK"), ("glaxo", "GSK"), ("gsk", "GSK"),
    ("novartis", "Novartis"),
    ("kite pharma", "Gilead"), ("immunomedics", "Gilead"), ("forty seven", "Gilead"),
    ("gilead", "Gilead"),
    ("onyx", "Amgen"), ("amgen", "Amgen"),
    ("boehringer", "Boehringer Ingelheim"), ("bayer", "Bayer"), ("eisai", "Eisai"),
    ("hengrui", "Jiangsu Hengrui"), ("beigene", "BeiGene"),
    ("daiichi", "Daiichi Sankyo"), ("regeneron", "Regeneron"), ("incyte", "Incyte"),
    ("biontech", "BioNTech"), ("moderna", "Moderna"), ("astellas", "Astellas"),
    ("otsuka", "Otsuka"), ("servier", "Servier"), ("ipsen", "Ipsen"),
    ("exelixis", "Exelixis"), ("jazz", "Jazz Pharmaceuticals"),
    ("chia tai", "Chia Tai Tianqing"), ("tianqing", "Chia Tai Tianqing"),
    ("innovent", "Innovent"), ("junshi", "Junshi Biosciences"),
    ("hutchmed", "HutchMed"), ("hutchison", "HutchMed"),
]

# legal forms + generic descriptors stripped in the fallback (non-alias) canonical
STRIP = {
    "inc", "llc", "ltd", "plc", "corp", "corporation", "co", "company", "gmbh",
    "ag", "sa", "spa", "nv", "oy", "ab", "as", "bv", "kk", "kg", "kgaa", "limited",
    "holdings", "group", "international", "usa", "the", "and", "pharmaceuticals",
    "pharmaceutical", "pharma", "therapeutics", "biosciences", "bioscience",
    "biopharmaceuticals", "biopharmaceutical", "biopharma", "biotech",
    "biotechnology", "sciences", "oncology", "medicines", "medical", "laboratories",
    "labs", "r", "d", "research", "development", "technologies", "technology",
}


def light(name):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip()


def canonical(name):
    n = light(name)
    padded = f" {n} "
    for key, canon in ALIASES:
        if f" {key} " in padded:
            return canon
    toks = [t for t in n.split(" ") if t not in STRIP]
    return " ".join(w.capitalize() for w in toks) or (name or "").strip()


def main():
    conn = sqlite3.connect(DB)
    init_schema(conn, SCHEMA)                 # ensures canonical_name column exists

    rows = conn.execute("SELECT id, name FROM company").fetchall()
    for cid, name in rows:
        conn.execute("UPDATE company SET canonical_name=? WHERE id=?",
                     (canonical(name), cid))
    conn.commit()

    n_names = len(rows)
    n_canon = conn.execute("SELECT count(DISTINCT canonical_name) FROM company").fetchone()[0]
    print(f"{n_names} company names -> {n_canon} canonical groups "
          f"({n_names - n_canon} merged)")

    print("\ntop canonical companies by # oncology trials led:")
    for name, t, v in conn.execute("""
        SELECT co.canonical_name, count(DISTINCT e.dst_id) AS trials,
               count(DISTINCT co.id) AS variants
        FROM company co
        JOIN edge e ON e.src_type='company' AND e.src_id=co.id AND e.rel='runs'
        GROUP BY co.canonical_name ORDER BY trials DESC LIMIT 15;"""):
        print(f"  {t:5d}  {name}  ({v} name variant{'s' if v != 1 else ''})")

    print("\nexample merges (canonical <- variants):")
    for canon in ("Roche", "Merck & Co", "Bristol Myers Squibb", "Johnson & Johnson"):
        vs = [r[0] for r in conn.execute(
            "SELECT DISTINCT name FROM company WHERE canonical_name=? ORDER BY name LIMIT 6;",
            (canon,))]
        if vs:
            print(f"  {canon} <- " + " | ".join(vs))


if __name__ == "__main__":
    main()
