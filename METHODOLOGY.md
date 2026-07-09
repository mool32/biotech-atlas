# Methodology

This file is the objectivity contract. If a categorization involves judgment,
it is defined here, in the open — that transparency *is* the objectivity.

## Provenance policy

Every edge and every source-derived attribute records:

- `source` — the dataset it came from (e.g. `ClinicalTrials.gov`)
- `source_ref` — a deep link / record id to the exact record
- `retrieved_date` — when we pulled it
- `confidence` — one of:
  - `fact` — stated directly by the source
  - `estimate` — a quantified guess (e.g. market size), with a method note
  - `inferred` — derived by the pipeline/model from other facts

Raw API responses are archived under `data/raw/<source>/<date>/` before any
parsing, so every loaded row can be traced back and re-derived.

## Source registry (open, product-safe)

| source              | provides                        | status   |
|---------------------|---------------------------------|----------|
| ClinicalTrials.gov  | trials, sponsors, interventions | **live** |
| Open Targets        | drug → target, drug type        | **live** |
| MONDO (via EBI OLS4)| disease ontology + hierarchy    | **live** |
| SEC EDGAR           | public companies, financials    | planned  |
| ChEMBL / DrugBank   | drugs / compounds               | planned  |
| GLEIF (LEI)         | legal entity ids                | planned  |
| Lens.org            | patents                         | planned  |

Commercial sources (Cortellis, Evaluate, PitchBook) are intentionally excluded:
their licenses forbid republication, which would block the product path.

## Definitions (judgment calls, fixed here)

- **Company** — the legal/operating entity that sponsors or develops an asset.
  Parent/subsidiary merging is an explicit resolution step, not silent.
- **Asset** — a distinct drug/program (candidate or approved), keyed by
  normalized name until a canonical ChEMBL/INN id is attached.
- **"develops"** — currently *inferred* from trial sponsorship. Upgraded to
  `fact` only when confirmed by a company filing/label.
- **Modality** — coarse `inferred` tag from intervention type in this phase;
  refined against ChEMBL/label data later.
- **Oncology scope** — a trial is in-scope if `query.cond=cancer` matches; the
  indication is stored verbatim and mapped to MONDO in Phase 2.

## Stage-2 resolution (heuristics, interim until canonical ids)

All applied in `src/resolve.py`, non-destructive (nothing deleted — only tagged):

- **Asset role** — `proprietary` | `standard_of_care` | `placebo`. A curated
  list of generic chemo backbones, supportive care, and off-patent biologics is
  matched by token; everything else defaults to `proprietary`. This is a
  judgment heuristic (`role_source='heuristic'`), superseded by ChEMBL/ATC
  mapping. It is what lets the "proprietary programs" view exclude comparators.
- **Indication canonical** — qualifiers (metastatic, advanced, stage, recurrent,
  refractory, …) are stripped and plurals/umbrella synonyms folded, so
  `Metastatic Breast Cancer` → `Breast Cancer`. Interim until MONDO mapping;
  umbrella labels (`Cancer`, `Solid Tumor`) remain as-is by design.
- **Company parent** — a seed-declared `parent` links a subsidiary to its owner
  (Genentech→Roche, Janssen→J&J, Seagen→Pfizer, …). Roll-ups sum the
  authoritative per-entity `totalCount`.

Base normalization (`src/db.py:normalize`) stays conservative: lowercase, strip
punctuation and corporate suffixes. Fuzzy alias/ticker matching is still Phase 2.

## Changelog of methodology decisions

- 2026-07-07 — initial scope: oncology vertical, ClinicalTrials.gov only;
  fact/inferred split established.
- 2026-07-07 — (A) full pagination; store authoritative `company.trials_total`
  (source `totalCount`) separate from the materialized trial sample.
- 2026-07-07 — (B) stage-2 heuristics added: asset role, indication canonical,
  company parent roll-up (all logged, non-destructive).
- 2026-07-07 — (Phase 2) targets layer via Open Targets: `asset --targets-->
  target` edges (fact, source Open Targets), real modality from `drugType`, and
  ChEMBL ids attached — for the top proprietary assets (bounded by --limit).
- 2026-07-07 — (Phase 3) MONDO mapping via EBI OLS4: canonical labels resolved
  to MONDO ids (precision-first — exact label/synonym match only, else left
  unmapped), with `disease --subtype_of--> disease` hierarchy for rollup.
- 2026-07-07 — MONDO recall lifted to ~68% of trials: order- and
  spelling-independent token matching; canonical now strips parenthetical
  abbreviations, "or"/"and", and normalizes British spelling. Precision preserved
  (umbrellas like "solid tumor" still left unmapped).
- 2026-07-07 — broadened Open Targets to proprietary assets in ≥2 trials
  (630 resolved, 476 targets, real modality). Ingest is incremental (`ot_checked`
  skips resolved/tried) and de-parenthesizes asset names. Tubulin isoforms (TUB*)
  are excluded from the target leaderboard only — family-inflated and
  non-actionable; the `asset --targets--> target` edges stay in the graph.
- 2026-07-09 — (Phase 4) inverted the seed: `ingest_all_oncology.py` harvests
  every industry-led oncology trial into a separate census db (via `ATLAS_DB`) —
  ~27.3k trials, ~4.3k company names, ~18k assets. Scope = `leadSponsor.class ==
  INDUSTRY` (the `funderType:industry` aggFilter also admits academic-led trials
  with an industry collaborator, which we drop). Company entity resolution across
  name variants (Hoffmann-La Roche / Genentech; Merck Sharp & Dohme / Merck) is
  the known open problem — the ~4.3k count is name-level, not entity-level.
