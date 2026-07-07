-- biotech-atlas — knowledge-graph schema
-- Dev dialect: SQLite. Portable to Postgres/Supabase (see MIGRATION.md).
--
-- Model: typed entities + one typed-edge table. Every edge (and source-derived
-- node attribute) carries provenance: where it came from, when, and whether it
-- is a stated fact or a pipeline inference.

-- ── entities ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS company (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  name_norm    TEXT NOT NULL UNIQUE,   -- normalized key for entity resolution
  trials_total INTEGER,                -- authoritative onco trial count (source totalCount)
  parent_name  TEXT,                   -- declared corporate parent (from seed)
  parent_id    INTEGER,                -- resolved parent company id (stage 2)
  lei          TEXT,                   -- canonical id: GLEIF        (to be filled)
  sec_cik      TEXT                    -- canonical id: SEC EDGAR    (to be filled)
);

CREATE TABLE IF NOT EXISTS asset (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  name_norm       TEXT NOT NULL UNIQUE,
  modality        TEXT,             -- e.g. biologic, small molecule, RNA
  modality_source TEXT,             -- 'extracted' | 'inferred'
  role            TEXT,             -- proprietary | standard_of_care | placebo (stage 2)
  role_source     TEXT,             -- 'heuristic' | 'curated' | 'chembl'
  chembl_id       TEXT              -- canonical id: ChEMBL       (to be filled)
);

CREATE TABLE IF NOT EXISTS indication (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  name_norm TEXT NOT NULL UNIQUE,
  canonical TEXT,                   -- rolled-up label (stage 2, pre-MONDO)
  mondo_id  TEXT                    -- canonical id: MONDO/MeSH   (to be filled)
);

CREATE TABLE IF NOT EXISTS trial (
  id             INTEGER PRIMARY KEY,
  nct_id         TEXT NOT NULL UNIQUE,   -- canonical id: ClinicalTrials.gov
  title          TEXT,
  status         TEXT,
  phase          TEXT,
  start_date     TEXT,
  source         TEXT NOT NULL,
  retrieved_date TEXT NOT NULL
);

-- ── the graph: one typed-edge table with cross-cutting provenance ──────────
CREATE TABLE IF NOT EXISTS edge (
  id             INTEGER PRIMARY KEY,
  src_type       TEXT NOT NULL,
  src_id         INTEGER NOT NULL,
  rel            TEXT NOT NULL,     -- develops | runs | targets | treats | tested_in | for ...
  dst_type       TEXT NOT NULL,
  dst_id         INTEGER NOT NULL,
  -- provenance (applies to every edge)
  source         TEXT NOT NULL,     -- e.g. ClinicalTrials.gov
  source_ref     TEXT,              -- deep link / record id
  retrieved_date TEXT NOT NULL,
  confidence     TEXT NOT NULL DEFAULT 'fact',  -- fact | estimate | inferred
  UNIQUE (src_type, src_id, rel, dst_type, dst_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_src ON edge (src_type, src_id);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge (dst_type, dst_id);
CREATE INDEX IF NOT EXISTS idx_edge_rel ON edge (rel);
