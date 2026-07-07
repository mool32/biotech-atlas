# Migrating SQLite → Postgres / Supabase

The dev engine is SQLite; the product engine is Postgres (Supabase/Neon). All DB
access is isolated in `src/db.py`, so the move is small and mechanical.

## Steps

1. **Provision** a Postgres (Supabase free tier gives you Postgres + REST API +
   auth + hosting out of the box).

2. **Port the schema.** `schema.sql` is already close. Changes:
   - `INTEGER PRIMARY KEY` → `BIGINT GENERATED ALWAYS AS IDENTITY`
   - `start_date` / `retrieved_date` `TEXT` → `DATE`
   - `confidence` → add `CHECK (confidence IN ('fact','estimate','inferred'))`
   - optional: promote `edge.src_id/dst_id` to real FKs per `src_type`, or move
     to a per-relation edge table set once the model stabilizes.

3. **Swap the driver in `src/db.py`.** Replace the `sqlite3` connection with
   `psycopg` (`pip install "psycopg[binary]"`), read the DSN from an env var:
   ```python
   import os, psycopg
   def connect(_=None):
       return psycopg.connect(os.environ["DATABASE_URL"])
   ```
   The upsert helpers already use `INSERT … ON CONFLICT … DO NOTHING/UPDATE`,
   which is native Postgres syntax — they carry over unchanged. Swap the SQLite
   `?` placeholders for `%s`.

4. **Expose it.** With Supabase the graph is instantly a REST/GraphQL API —
   that's the product surface for the map's front end.

## Why not start on Postgres?

No local Postgres and Docker was not running at scaffold time; SQLite let the
live-data proof run immediately with zero setup. The schema was written portable
from day one specifically so this migration stays a config change, not a rewrite.
