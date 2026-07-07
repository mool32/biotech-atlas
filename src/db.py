"""Thin DB layer for biotech-atlas.

Dev engine: SQLite (stdlib, zero-install) so the pipeline runs anywhere.
Product target: Postgres / Supabase — see MIGRATION.md. Every DB access goes
through this module, so swapping engines touches one file.
"""
import os
import re
import sqlite3

# Corporate/entity suffixes stripped during normalization. Conservative on
# purpose — real resolution (aliases, tickers, M&A lineage) is a later stage.
_SUFFIXES = {
    "inc", "llc", "ltd", "plc", "corp", "co", "company", "gmbh", "sa", "ag",
    "nv", "the", "pharmaceuticals", "pharma", "therapeutics", "biosciences",
    "biotherapeutics", "biotech", "biopharma", "holdings", "group", "labs",
    "laboratories",
}


def normalize(name: str) -> str:
    """Cheap normalization key for entity resolution."""
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[.,/&()'’\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split(" ") if t not in _SUFFIXES]
    return " ".join(tokens) if tokens else s


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


# Additive columns introduced after a table's original CREATE. Applied on every
# init so an existing db picks up new columns without a rebuild (CREATE TABLE
# IF NOT EXISTS won't alter an existing table).
_MIGRATIONS = [
    ("company", "parent_name", "TEXT"),
    ("company", "parent_id", "INTEGER"),
    ("asset", "role", "TEXT"),
    ("asset", "role_source", "TEXT"),
    ("indication", "canonical", "TEXT"),
    ("indication", "mondo_id", "TEXT"),
    ("indication", "mondo_label", "TEXT"),
]


def init_schema(conn: sqlite3.Connection, schema_path: str) -> None:
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    for table, col, typ in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ};")
    conn.commit()


def upsert_node(conn, table: str, name: str, extra: dict | None = None) -> int:
    """Insert a node keyed by name_norm; return its id (idempotent)."""
    extra = extra or {}
    cols = ["name", "name_norm", *extra.keys()]
    vals = [name, normalize(name), *extra.values()]
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(name_norm) DO NOTHING;",
        vals,
    )
    row = conn.execute(
        f"SELECT id FROM {table} WHERE name_norm = ?;", (normalize(name),)
    ).fetchone()
    return row[0]


def upsert_trial(conn, nct_id, title, status, phase, start_date,
                 source, retrieved_date) -> int:
    conn.execute(
        "INSERT INTO trial (nct_id,title,status,phase,start_date,source,retrieved_date) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(nct_id) DO UPDATE SET "
        "title=excluded.title, status=excluded.status, phase=excluded.phase, "
        "start_date=excluded.start_date, retrieved_date=excluded.retrieved_date;",
        (nct_id, title, status, phase, start_date, source, retrieved_date),
    )
    return conn.execute(
        "SELECT id FROM trial WHERE nct_id = ?;", (nct_id,)
    ).fetchone()[0]


def upsert_edge(conn, src_type, src_id, rel, dst_type, dst_id,
                source, source_ref, retrieved_date, confidence="fact") -> None:
    conn.execute(
        "INSERT INTO edge (src_type,src_id,rel,dst_type,dst_id,source,source_ref,"
        "retrieved_date,confidence) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(src_type,src_id,rel,dst_type,dst_id) DO NOTHING;",
        (src_type, src_id, rel, dst_type, dst_id, source, source_ref,
         retrieved_date, confidence),
    )
