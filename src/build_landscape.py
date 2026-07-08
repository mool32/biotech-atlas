"""Stage 3 (viz) — generate a self-contained landscape.html from the graph.

No external libraries, no build step: open the file in any browser. Regenerate
whenever the data changes:
  python3 src/build_landscape.py
"""
import html
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "biotech.sqlite")
OUT = os.path.join(ROOT, "landscape.html")

ACCENTS = {"blue": "#4f7cc9", "teal": "#3fa39a", "amber": "#d69a3c",
           "purple": "#7a6cc9", "green": "#5b9c5b"}


def q(conn, sql):
    return conn.execute(sql).fetchall()


def bars(items, accent):
    mx = max((v for _, v in items), default=1) or 1
    out = []
    for label, v in items:
        pct = 100 * v / mx
        lab = html.escape(str(label))
        out.append(
            f'<div class="row"><div class="lab" title="{lab}">{lab}</div>'
            f'<div class="track"><div class="fill" style="width:{pct:.1f}%;background:{accent}"></div></div>'
            f'<div class="val">{v}</div></div>'
        )
    return "\n".join(out)


def panel(title, subtitle, items, accent):
    return (
        f'<section class="panel"><h2>{html.escape(title)}</h2>'
        f'<p class="sub">{html.escape(subtitle)}</p>'
        f'<div class="bars">{bars(items, accent)}</div></section>'
    )


def main():
    conn = sqlite3.connect(DB)
    stamp = q(conn, "SELECT COALESCE(MAX(retrieved_date),'') FROM trial")[0][0]
    n_comp = q(conn, "SELECT count(*) FROM company")[0][0]
    n_trial = q(conn, "SELECT count(*) FROM trial")[0][0]
    n_prop = q(conn, "SELECT count(*) FROM asset WHERE role='proprietary'")[0][0]
    n_ind = q(conn, "SELECT count(DISTINCT canonical) FROM indication")[0][0]
    n_tgt = q(conn, "SELECT count(*) FROM target")[0][0]
    n_dis = q(conn, "SELECT count(DISTINCT mondo_id) FROM indication WHERE mondo_id IS NOT NULL")[0][0]

    orgs = q(conn, """SELECT COALESCE(p.name,c.name), SUM(c.trials_total)
        FROM company c LEFT JOIN company p ON c.parent_id=p.id
        GROUP BY COALESCE(p.name,c.name) ORDER BY 2 DESC LIMIT 12;""")
    inds = q(conn, """SELECT d.label, count(DISTINCT e.src_id)
        FROM indication i JOIN disease d ON d.mondo_id=i.mondo_id
        JOIN edge e ON e.dst_type='indication' AND e.dst_id=i.id AND e.rel='for'
        GROUP BY d.mondo_id ORDER BY 2 DESC LIMIT 12;""")
    progs = q(conn, """SELECT a.name, count(DISTINCT e.dst_id)
        FROM asset a JOIN edge e ON e.src_type='asset' AND e.src_id=a.id AND e.rel='tested_in'
        WHERE a.role='proprietary' GROUP BY a.id ORDER BY 2 DESC LIMIT 12;""")
    phases = q(conn, """SELECT COALESCE(phase,'(unspecified)'), count(*)
        FROM trial GROUP BY phase ORDER BY 2 DESC;""")
    tgts = q(conn, """SELECT t.name, count(DISTINCT e.src_id)
        FROM target t JOIN edge e ON e.dst_type='target' AND e.dst_id=t.id AND e.rel='targets'
        WHERE t.name NOT LIKE 'TUB%'          -- family-inflated, non-actionable cytoskeleton
        GROUP BY t.id ORDER BY 2 DESC LIMIT 12;""")
    rollup = q(conn, """SELECT anc.label, count(DISTINCT e.src_id)
        FROM disease anc
        JOIN edge se ON se.dst_type='disease' AND se.dst_id=anc.id AND se.rel='subtype_of'
        JOIN disease d ON d.id=se.src_id
        JOIN indication i ON i.mondo_id=d.mondo_id
        JOIN edge e ON e.dst_type='indication' AND e.dst_id=i.id AND e.rel='for'
        GROUP BY anc.id ORDER BY 2 DESC LIMIT 10;""")

    tiles = "".join(
        f'<div class="tile"><div class="num">{n}</div><div class="cap">{c}</div></div>'
        for n, c in [(n_comp, "companies"), (n_trial, "trials"),
                     (n_prop, "proprietary assets"), (n_dis, "MONDO diseases"),
                     (n_tgt, "targets")]
    )
    body = (
        f'<header><h1>biotech-atlas — oncology landscape</h1>'
        f'<p class="meta">source: ClinicalTrials.gov · retrieved {html.escape(stamp)} · '
        f'sample capped at 300 trials/company; org totals are authoritative</p>'
        f'<div class="tiles">{tiles}</div></header>'
        f'<div class="grid">'
        + panel("Companies by oncology trials", "parent-rolled-up, authoritative total", orgs, ACCENTS["blue"])
        + panel("Most-studied diseases", "MONDO-mapped", inds, ACCENTS["teal"])
        + panel("Disease groups", "MONDO rollup via hierarchy", rollup, ACCENTS["teal"])
        + panel("Top proprietary programs", "comparators & placebo excluded", progs, ACCENTS["amber"])
        + panel("Top targets", "by proprietary assets · Open Targets", tgts, ACCENTS["green"])
        + panel("Trial landscape by phase", "all sampled trials", phases, ACCENTS["purple"])
        + "</div>"
    )

    doc = "<!doctype html><html lang='en'><head><meta charset='utf-8'>" \
          "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
          "<title>biotech-atlas — oncology landscape</title><style>" + CSS + \
          "</style></head><body>" + body + "</body></html>"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"wrote {OUT}")
    print(f"  {n_comp} companies · {n_trial} trials · {n_prop} proprietary assets · {n_ind} indications")


CSS = """
:root{--bg:#ffffff;--card:#faf9f7;--text:#1b1b1a;--muted:#6b6b68;--line:#e7e4df}
@media (prefers-color-scheme:dark){:root{--bg:#171716;--card:#232321;--text:#ededec;--muted:#9a9a96;--line:#34332f}}
*{box-sizing:border-box}
body{margin:0;padding:36px;background:var(--bg);color:var(--text);
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.5}
h1{font-size:24px;font-weight:600;margin:0 0 6px}
.meta{color:var(--muted);font-size:13px;margin:0 0 20px}
.tiles{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:28px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;min-width:130px}
.tile .num{font-size:26px;font-weight:600}
.tile .cap{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.panel h2{font-size:16px;font-weight:600;margin:0 0 2px}
.panel .sub{color:var(--muted);font-size:12px;margin:0 0 16px}
.row{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.lab{width:150px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track{flex:1;background:rgba(128,128,128,.14);border-radius:5px;height:14px;overflow:hidden}
.fill{height:100%;border-radius:5px}
.val{width:44px;flex:none;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
"""


if __name__ == "__main__":
    main()
