# biotech-atlas

An objective, source-anchored map of the biotech industry, built as a knowledge
graph. Research-first, but product-ready: everything is built on **open sources**
so it can grow into a public/commercial resource without licensing walls.

Currently scoped to **vertical 1: oncology**, source **1: ClinicalTrials.gov**.

## The one rule (this is what "objective" means here)

> The pipeline **links and structures** data. It never invents facts.
> Every atomic fact traces to a source snapshot. Anything the pipeline *guesses*
> (modality, "company X develops asset Y") is stored as `confidence='inferred'`,
> never mixed with stated facts.

Objectivity comes from **provenance**, not from picking the "right" taxonomy.

## Data model

Typed entities + one typed-edge table. Each entity is anchored to an external
canonical id so we never reinvent naming:

| entity      | canonical id source        |
|-------------|----------------------------|
| company     | SEC EDGAR · GLEIF (LEI)     |
| asset       | ChEMBL · INN               |
| target      | UniProt · Open Targets     |
| indication  | MONDO · MeSH               |
| trial       | ClinicalTrials.gov (NCT)   |
| patent      | Lens.org · PatentsView     |

Relationships live in `edge` (`develops`, `runs`, `targets`, `treats`,
`tested_in`, `for`, …), each carrying `source`, `retrieved_date`, `confidence`.

## Pipeline

```
seed companies ─▶ ingest (API) ─▶ land raw snapshot ─▶ resolve ─▶ load graph
                                    data/raw/…            (dedup)    (with provenance)
```

## Run it

No installs needed (stdlib only):

```bash
python3 src/ingest_clinicaltrials.py --max-per-company 300   # 1. ingest trials (all seeds)
python3 src/resolve.py                                       # 2. resolve/clean
python3 src/ingest_opentargets.py --min-trials 2 --limit 900 # 3. targets + modality (incremental)
python3 src/ingest_mondo.py --reset --limit 150              # 4. map indications to MONDO
python3 src/build_landscape.py                               # 5. -> landscape.html
python3 src/query_examples.py                                # (optional) console views
```

Output lands in `data/biotech.sqlite`; raw API responses in `data/raw/` (the
audit trail); the map in `landscape.html`. Use `--limit N` on ingest to pull
only the first N seed companies.

### Two graphs: curated vs census

The seed pipeline above builds a **curated** graph (`data/biotech.sqlite`) — 45
hand-picked companies, deeply enriched (targets, MONDO, parent roll-ups). To
instead discover the **whole industry** bottom-up, invert it: walk every
industry-led oncology trial and let the company universe fall out of the data.

```bash
# full industry census -> a separate db (curated graph untouched)
ATLAS_DB=data/census.sqlite python3 src/ingest_all_oncology.py   # ~27k trials, ~4.3k companies
ATLAS_DB=data/census.sqlite python3 src/resolve.py               # roles + canonical labels
ATLAS_DB=data/census.sqlite python3 src/resolve_companies.py     # merge company name variants
ATLAS_DB=data/census.sqlite python3 src/build_landscape.py       # census landscape
```

Every script honors `ATLAS_DB`, so the same pipeline (resolve, Open Targets,
MONDO) can enrich the census incrementally.

## Stack

- **Dev:** SQLite (stdlib, zero-install).
- **Product:** Postgres / Supabase — all DB access is behind `src/db.py`, so the
  move is a connection swap. See [MIGRATION.md](MIGRATION.md).
- **Heavy analytics later:** DuckDB (Postgres-compatible SQL) for landscape rollups.

## Layout

```
schema.sql                     portable DDL (entities + edges + provenance)
src/db.py                      thin DB layer + entity normalization
src/ingest_clinicaltrials.py   stage 1 — ingest the oncology vertical (seed companies)
src/ingest_all_oncology.py     census — invert the seed: all industry-led onco trials
src/resolve.py                 stage 2 — asset role, indication canon, parent roll-up
src/resolve_companies.py       entity resolution — company name variants -> canonical group
src/ingest_opentargets.py      stage 2b — targets + real modality (Open Targets)
src/ingest_mondo.py            stage 2c — map indications to MONDO + hierarchy
src/build_landscape.py         stage 3 — generate landscape.html from the graph
src/query_examples.py          read-only analytical queries
data/seeds/                    curated seed lists (committed)
data/raw/                      dated API snapshots (gitignored)
METHODOLOGY.md                 definitions, source registry, provenance policy
```

## Roadmap

- **Done:** oncology end-to-end (curated 45); asset roles; MONDO diseases +
  hierarchy; Open Targets targets + modality; landscape + focused graph; full
  industry **census** (bottom-up, ~4.3k companies / ~27k trials).
- **Company entity resolution (head done):** a curated alias/subsidiary map
  collapses the major groups (Roche ← Genentech/Chugai; J&J ← 30 Janssen
  variants; Merck & Co kept distinct from Merck KGaA), fixing the leaderboard.
  The mid/long tail still merges only by legal-suffix stripping — full recall
  needs fuzzy clustering or GLEIF/LEI ids (with review).
- **Then:** enrich the census (targets/MONDO incrementally), add a business
  layer (SEC EDGAR financings/M&A), scheduled delta refresh, next therapeutic area.

## Known limitations (today)

- Company parent roll-up is **seed-declared**, not discovered — only the
  subsidiaries listed in the seed (Genentech, Janssen, Seagen, Mirati, …) merge.
  Automatic M&A/alias resolution is Phase 2.
- Indications are MONDO-mapped for ~60% of trials (precision-first: exact
  match only). Umbrella terms and stage-2 word-order/abbrev noise stay unmapped
  and fall back to the heuristic `canonical` — improving recall is a later lever.
- Asset role is still a curated **heuristic** (not ontology-backed) — good
  enough to separate comparators/placebo, pending full ChEMBL/ATC classing.
- Targets + modality are sourced from Open Targets for the 630 proprietary
  assets in ≥2 trials (incremental via `ot_checked`; rerun `--min-trials 1` to
  add the single-trial tail). The target leaderboard hides tubulin isoforms
  (family-inflated); their edges still live in the graph.
- Materialized graph samples up to 300 trials/company; org-level counts use the
  authoritative `totalCount`, so rankings are exact even where the sample caps.
