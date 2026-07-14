"""Stage the static explorer's data: VACUUM-copy the graph databases into
`web/db/` so `web/` becomes a self-contained static site — index.html +
vendor/ (sql.js) + db/ — ready to deploy to any static host (GitHub Pages,
Cloudflare Pages, Netlify). No server, no build step on the host.

  python3 src/build_web.py
"""
import os
import shutil
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "db")
DBS = [("data/biotech.sqlite", "curated.sqlite"),
       ("data/census.sqlite", "census.sqlite")]


def main():
    os.makedirs(OUT, exist_ok=True)
    for src, name in DBS:
        srcp = os.path.join(ROOT, src)
        if not os.path.exists(srcp):
            print(f"  skip {name}: {src} not found")
            continue
        dst = os.path.join(OUT, name)
        shutil.copy(srcp, dst)
        con = sqlite3.connect(dst)
        con.execute("VACUUM")            # compact: drop free pages
        con.close()
        mb = os.path.getsize(dst) / 1e6
        note = "  (>25 MB: GitHub Pages / R2, not Cloudflare Pages)" if mb > 25 else ""
        print(f"  web/db/{name}: {mb:.1f} MB{note}")
    print("\nweb/ is ready to deploy. Preview locally:  python3 -m http.server -d web 8080")


if __name__ == "__main__":
    main()
