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
python3 src/ingest_opentargets.py --limit 80                 # 3. attach targets + modality
python3 src/build_landscape.py                               # 4. -> landscape.html
python3 src/query_examples.py                                # (optional) console views
```

Output lands in `data/biotech.sqlite`; raw API responses in `data/raw/` (the
audit trail); the map in `landscape.html`. Use `--limit N` on ingest to pull
only the first N seed companies.

## Stack

- **Dev:** SQLite (stdlib, zero-install).
- **Product:** Postgres / Supabase — all DB access is behind `src/db.py`, so the
  move is a connection swap. See [MIGRATION.md](MIGRATION.md).
- **Heavy analytics later:** DuckDB (Postgres-compatible SQL) for landscape rollups.

## Layout

```
schema.sql                     portable DDL (entities + edges + provenance)
src/db.py                      thin DB layer + entity normalization
src/ingest_clinicaltrials.py   stage 1 — ingest the oncology vertical
src/resolve.py                 stage 2 — asset role, indication canon, parent roll-up
src/ingest_opentargets.py      stage 2b — targets + real modality (Open Targets)
src/build_landscape.py         stage 3 — generate landscape.html from the graph
src/query_examples.py          read-only analytical queries
data/seeds/                    curated seed lists (committed)
data/raw/                      dated API snapshots (gitignored)
METHODOLOGY.md                 definitions, source registry, provenance policy
```

## Roadmap

- **Phase 1 (now):** oncology end-to-end from ClinicalTrials.gov.
- **Phase 2:** add sources (SEC EDGAR pipelines, Open Targets targets, financings),
  real entity resolution, 1–2 visualizations (landscape + focused graph).
- **Phase 3:** scheduled delta refresh; expand to the next therapeutic area.

## Known limitations (today)

- Company parent roll-up is **seed-declared**, not discovered — only the
  subsidiaries listed in the seed (Genentech, Janssen, Seagen, Mirati, …) merge.
  Automatic M&A/alias resolution is Phase 2.
- Asset role and indication canonical are **heuristics** (curated list / rule),
  not ontology-backed — good enough to separate comparators and fold obvious
  indication variants, pending ChEMBL and MONDO mapping.
- Modality is a coarse `inferred` guess (mostly unresolved for small molecules).
- Materialized graph samples up to 300 trials/company; org-level counts use the
  authoritative `totalCount`, so rankings are exact even where the sample caps.
