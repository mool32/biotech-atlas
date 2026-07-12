"""biotech-atlas explorer — a zero-dependency local web app over the graph.

Serves a single-page UI plus a small read-only JSON API on top of the SQLite
graph. No frameworks, no installs (Python stdlib only). Switch datasets with the
census/curated toggle in the UI.

  python3 src/serve.py            # http://localhost:8787
  PORT=9000 python3 src/serve.py
"""
import json
import os
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBS = {
    "census": os.path.join(ROOT, "data", "census.sqlite"),
    "curated": os.path.join(ROOT, "data", "biotech.sqlite"),
}
WEB = os.path.join(ROOT, "web", "index.html")
PORT = int(os.environ.get("PORT", "8787"))

# canonical group expression + trial-count value that works on both dbs
ORG = "COALESCE(c.canonical_name, c.name)"


def rows(db, sql, params=()):
    con = sqlite3.connect(DBS.get(db, DBS["census"]))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def one(db, sql, params=()):
    r = rows(db, sql, params)
    return r[0] if r else {}


# ---- endpoints -------------------------------------------------------------

def ep_overview(db, _):
    return {
        "counts": one(db, """SELECT
            (SELECT count(DISTINCT COALESCE(canonical_name,name)) FROM company) AS companies,
            (SELECT count(*) FROM trial) AS trials,
            (SELECT count(*) FROM asset WHERE role='proprietary') AS assets,
            (SELECT count(*) FROM target) AS targets,
            (SELECT count(*) FROM disease) AS diseases"""),
        "companies": rows(db, f"""SELECT {ORG} AS org,
            COALESCE(NULLIF(SUM(c.trials_total),0), count(DISTINCT e.dst_id)) AS n
            FROM company c
            LEFT JOIN edge e ON e.src_type='company' AND e.src_id=c.id AND e.rel='runs'
            GROUP BY {ORG} ORDER BY n DESC LIMIT 15"""),
        "targets": rows(db, """SELECT t.id, t.name, count(DISTINCT e.src_id) AS n
            FROM target t JOIN edge e ON e.dst_type='target' AND e.dst_id=t.id AND e.rel='targets'
            WHERE t.name NOT LIKE 'TUB%' GROUP BY t.id ORDER BY n DESC LIMIT 15"""),
        "diseases": rows(db, """SELECT d.id, d.label, count(DISTINCT e.src_id) AS n
            FROM disease d JOIN indication i ON i.mondo_id=d.mondo_id
            JOIN edge e ON e.dst_type='indication' AND e.dst_id=i.id AND e.rel='for'
            GROUP BY d.id ORDER BY n DESC LIMIT 15"""),
        "phases": rows(db, """SELECT COALESCE(phase,'(n/a)') AS phase, count(*) AS n
            FROM trial GROUP BY phase ORDER BY n DESC"""),
    }


def ep_search(db, p):
    q = f"%{(p.get('q') or '').strip()}%"
    if q == "%%":
        return {"companies": [], "assets": [], "targets": [], "diseases": []}
    return {
        "companies": rows(db, f"""SELECT DISTINCT {ORG} AS org FROM company c
            WHERE c.name LIKE ? OR c.canonical_name LIKE ? ORDER BY org LIMIT 8""", (q, q)),
        "assets": rows(db, """SELECT id, name, modality FROM asset
            WHERE role='proprietary' AND name LIKE ? ORDER BY length(name) LIMIT 8""", (q,)),
        "targets": rows(db, """SELECT id, name FROM target WHERE name LIKE ?
            ORDER BY length(name) LIMIT 8""", (q,)),
        "diseases": rows(db, """SELECT id, label FROM disease WHERE label LIKE ?
            ORDER BY length(label) LIMIT 8""", (q,)),
    }


def ep_company(db, p):
    name = p.get("name", "")
    # resolve the group's node ids once, then drive edge queries by src_id IN (…)
    # so the (src_type, src_id) index is used instead of a COALESCE full scan.
    ids = [r["id"] for r in rows(db, f"SELECT id FROM company c WHERE {ORG}=?", (name,))]
    if not ids:
        return {"name": name, "trials": 0, "phases": [], "assets": [], "targets": [], "diseases": []}
    ph = ",".join("?" * len(ids))
    return {
        "name": name,
        "trials": one(db, f"""SELECT count(DISTINCT dst_id) AS n FROM edge
            WHERE rel='runs' AND src_type='company' AND src_id IN ({ph})""", ids).get("n", 0),
        "phases": rows(db, f"""SELECT COALESCE(tr.phase,'(n/a)') AS phase, count(DISTINCT tr.id) AS n
            FROM edge re JOIN trial tr ON tr.id=re.dst_id
            WHERE re.rel='runs' AND re.src_type='company' AND re.src_id IN ({ph})
            GROUP BY phase ORDER BY n DESC""", ids),
        "assets": rows(db, f"""SELECT a.id, a.name, a.modality, count(DISTINCT te.dst_id) AS trials
            FROM edge de JOIN asset a ON a.id=de.dst_id
            LEFT JOIN edge te ON te.src_type='asset' AND te.src_id=a.id AND te.rel='tested_in'
            WHERE de.rel='develops' AND de.src_type='company' AND de.src_id IN ({ph})
              AND a.role='proprietary'
            GROUP BY a.id ORDER BY trials DESC LIMIT 25""", ids),
        "targets": rows(db, f"""SELECT t.id, t.name, count(DISTINCT a.id) AS assets
            FROM edge de JOIN asset a ON a.id=de.dst_id
            JOIN edge tge ON tge.src_type='asset' AND tge.src_id=a.id AND tge.rel='targets'
            JOIN target t ON t.id=tge.dst_id
            WHERE de.rel='develops' AND de.src_type='company' AND de.src_id IN ({ph})
              AND t.name NOT LIKE 'TUB%'
            GROUP BY t.id ORDER BY assets DESC LIMIT 12""", ids),
        "diseases": rows(db, f"""SELECT d.id, d.label, count(DISTINCT tr.id) AS trials
            FROM edge re JOIN trial tr ON tr.id=re.dst_id
            JOIN edge fe ON fe.src_type='trial' AND fe.src_id=tr.id AND fe.rel='for'
            JOIN indication i ON i.id=fe.dst_id JOIN disease d ON d.mondo_id=i.mondo_id
            WHERE re.rel='runs' AND re.src_type='company' AND re.src_id IN ({ph})
            GROUP BY d.id ORDER BY trials DESC LIMIT 12""", ids),
        "cik": (one(db, f"SELECT sec_cik AS cik FROM company WHERE id IN ({ph}) "
                    "AND sec_cik IS NOT NULL LIMIT 1", ids) or {}).get("cik"),
        "financials": rows(db, f"""SELECT DISTINCT f.metric, f.value, f.fiscal_year
            FROM company c JOIN financials f ON f.cik=c.sec_cik WHERE c.id IN ({ph})""", ids),
    }


def ep_target(db, p):
    tid = p.get("id")
    t = one(db, "SELECT id, name, ensembl_id FROM target WHERE id=?", (tid,))
    t["assets"] = rows(db, f"""SELECT a.id, a.name, a.modality,
        (SELECT {ORG} FROM company c JOIN edge de ON de.src_type='company' AND de.src_id=c.id
         AND de.rel='develops' WHERE de.dst_id=a.id LIMIT 1) AS company
        FROM edge e JOIN asset a ON a.id=e.src_id
        WHERE e.dst_type='target' AND e.dst_id=? AND e.rel='targets'
        ORDER BY a.name LIMIT 60""", (tid,))
    return t


def ep_disease(db, p):
    did = p.get("id")
    d = one(db, "SELECT id, label, mondo_id FROM disease WHERE id=?", (did,))
    d["companies"] = rows(db, f"""SELECT {ORG} AS org, count(DISTINCT tr.id) AS n
        FROM disease d JOIN indication i ON i.mondo_id=d.mondo_id
        JOIN edge fe ON fe.dst_type='indication' AND fe.dst_id=i.id AND fe.rel='for'
        JOIN trial tr ON tr.id=fe.src_id
        JOIN edge re ON re.dst_type='trial' AND re.dst_id=tr.id AND re.rel='runs'
        JOIN company c ON c.id=re.src_id WHERE d.id=? GROUP BY org ORDER BY n DESC LIMIT 12""", (did,))
    d["assets"] = rows(db, """SELECT a.id, a.name, count(DISTINCT tr.id) AS n
        FROM disease d JOIN indication i ON i.mondo_id=d.mondo_id
        JOIN edge fe ON fe.dst_type='indication' AND fe.dst_id=i.id AND fe.rel='for'
        JOIN trial tr ON tr.id=fe.src_id
        JOIN edge te ON te.dst_type='trial' AND te.dst_id=tr.id AND te.rel='tested_in'
        JOIN asset a ON a.id=te.src_id WHERE d.id=? AND a.role='proprietary'
        GROUP BY a.id ORDER BY n DESC LIMIT 20""", (did,))
    return d


def ep_asset(db, p):
    aid = p.get("id")
    a = one(db, """SELECT id, name, modality, modality_source, role, chembl_id
        FROM asset WHERE id=?""", (aid,))
    a["targets"] = rows(db, """SELECT t.id, t.name FROM edge e JOIN target t ON t.id=e.dst_id
        WHERE e.src_type='asset' AND e.src_id=? AND e.rel='targets'""", (aid,))
    a["companies"] = rows(db, f"""SELECT DISTINCT {ORG} AS org FROM edge de
        JOIN company c ON c.id=de.src_id
        WHERE de.rel='develops' AND de.dst_type='asset' AND de.dst_id=? LIMIT 8""", (aid,))
    a["indications"] = rows(db, """SELECT DISTINCT COALESCE(i.canonical, i.name) AS ind
        FROM edge e JOIN indication i ON i.id=e.dst_id
        WHERE e.src_type='asset' AND e.src_id=? AND e.rel='treats' LIMIT 12""", (aid,))
    a["trials"] = one(db, """SELECT count(DISTINCT dst_id) AS n FROM edge
        WHERE src_type='asset' AND src_id=? AND rel='tested_in'""", (aid,)).get("n", 0)
    return a


ROUTES = {
    "/api/overview": ep_overview, "/api/search": ep_search, "/api/company": ep_company,
    "/api/target": ep_target, "/api/disease": ep_disease, "/api/asset": ep_asset,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._file(WEB, "text/html; charset=utf-8")
        fn = ROUTES.get(u.path)
        if not fn:
            return self._send(404, b"not found", "text/plain")
        p = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            body = json.dumps(fn(p.get("db", "census"), p), ensure_ascii=False).encode()
            self._send(200, body, "application/json; charset=utf-8")
        except Exception as e:  # surface errors as JSON
            self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)
        except FileNotFoundError:
            self._send(404, b"missing web/index.html", "text/plain")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"biotech-atlas explorer -> http://localhost:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
