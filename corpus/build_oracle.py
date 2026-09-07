#!/usr/bin/env python3
"""Build the queryable ground-truth oracle from the raw capture set.

The oracle is the reference every patch is measured against (docs/METHODOLOGY.md
§1). It is generated, never committed: provenance belongs to the raw captures,
and a checked-in database would let a stale copy silently become the reference.

    python3 corpus/build_oracle.py --src ~/fingerprints/fingerprints
    python3 corpus/build_oracle.py --coverage
"""

import argparse
import collections
import json
import pathlib
import re
import sqlite3
import sys

DEFAULT_SRC = pathlib.Path.home() / "fingerprints" / "fingerprints"
DEFAULT_DB = pathlib.Path(__file__).parent / "oracle.sqlite"

# Fields encoding measured device output: a 64-byte digest followed by a block
# of sampled bytes. A degenerate sample block means the row is a template, not a
# capture — see resources/fingerprints/PROVENANCE.md.
MEASURED_FIELDS = ("canvas", "webgl", "audio", "rectangles")

SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id              TEXT PRIMARY KEY,   -- source filename stem
    source          TEXT NOT NULL,      -- absolute path of the capture
    platform        TEXT NOT NULL,      -- android | ios | windows | macos | linux | unknown
    browser         TEXT,
    browser_major   INTEGER,
    ua              TEXT,
    width           INTEGER,
    height          INTEGER,
    gpu_vendor      TEXT,
    gpu_renderer    TEXT,
    is_capture      INTEGER NOT NULL,   -- 0 = template/synthetic, never valid as T0
    synthetic_note  TEXT,
    raw             TEXT NOT NULL       -- full original JSON
);
CREATE INDEX IF NOT EXISTS device_platform ON device(platform);
CREATE INDEX IF NOT EXISTS device_browser  ON device(browser, browser_major);
CREATE INDEX IF NOT EXISTS device_gpu      ON device(gpu_renderer);
CREATE INDEX IF NOT EXISTS device_capture  ON device(is_capture);

-- One row per (device, field) so conformance diffs can join directly.
CREATE TABLE IF NOT EXISTS field (
    device_id  TEXT NOT NULL REFERENCES device(id),
    key        TEXT NOT NULL,
    value      TEXT,
    PRIMARY KEY (device_id, key)
);
CREATE INDEX IF NOT EXISTS field_key ON field(key);
"""


def classify_platform(ua: str) -> str:
    if "Android" in ua:
        return "android"
    if "iPhone" in ua or "iPad" in ua:
        return "ios"
    if "Windows" in ua:
        return "windows"
    if "Mac OS X" in ua:
        return "macos"
    if "X11" in ua or "Linux" in ua:
        return "linux"
    return "unknown"


def classify_browser(ua: str):
    m = re.search(r"(HeadlessChrome|CriOS|EdgA?|OPR|Chrome|Firefox|Version)/(\d+)", ua)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def synthetic_reasons(doc: dict) -> list:
    """Detect template rows. Conservative: only degenerate sample blocks count."""
    reasons = []
    for key in MEASURED_FIELDS:
        val = doc.get(key)
        if not isinstance(val, str) or len(val) <= 128:
            continue
        tail = val[128:]
        octets = {tail[i:i + 2] for i in range(0, len(tail), 2)}
        if len(octets) <= 2:
            reasons.append(f"{key} sample block is constant ({len(octets)} distinct byte(s))")
    if "HeadlessChrome" in (doc.get("ua") or ""):
        reasons.append("captured under HeadlessChrome")
    return reasons


def build(src: pathlib.Path, db_path: pathlib.Path) -> None:
    files = sorted(src.glob("*.json"))
    if not files:
        sys.exit(f"no captures found in {src}")

    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    dupes: dict = collections.defaultdict(list)
    skipped = 0

    for path in files:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError:
            skipped += 1
            print(f"  skip (bad json): {path.name}", file=sys.stderr)
            continue

        ua = doc.get("ua") or ""
        gpu = doc.get("webgl_properties") or {}
        reasons = synthetic_reasons(doc)
        browser, major = classify_browser(ua)

        con.execute(
            "INSERT OR REPLACE INTO device VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                path.stem, str(path.resolve()), classify_platform(ua),
                browser, major, ua,
                doc.get("width"), doc.get("height"),
                gpu.get("unmaskedVendor"), gpu.get("unmaskedRenderer"),
                0 if reasons else 1,
                "; ".join(reasons) or None,
                json.dumps(doc, separators=(",", ":")),
            ),
        )
        con.executemany(
            "INSERT OR REPLACE INTO field VALUES (?,?,?)",
            [
                (path.stem, k, v if isinstance(v, str) else json.dumps(v, separators=(",", ":")))
                for k, v in doc.items()
            ],
        )
        # Identical measured blocks across devices indicate a shared template.
        for key in MEASURED_FIELDS:
            val = doc.get(key)
            if isinstance(val, str) and len(val) > 128:
                dupes[(key, val[128:])].append(path.stem)

    con.commit()
    shared = {k: v for k, v in dupes.items() if len(v) > 1}
    report(con, skipped, shared)
    con.close()
    print(f"\nwrote {db_path}")


def report(con: sqlite3.Connection, skipped: int = 0, shared: dict | None = None) -> None:
    total, captures = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(is_capture),0) FROM device"
    ).fetchone()
    print(f"devices: {total}   usable captures (T0): {captures}   templates: {total - captures}")
    if skipped:
        print(f"skipped (unparseable): {skipped}")

    print("\nT0 coverage by platform — a class with 0 has no ground truth and cannot reach V3:")
    rows = con.execute(
        "SELECT platform, SUM(is_capture), COUNT(*) FROM device GROUP BY platform ORDER BY 2 DESC"
    ).fetchall()
    for platform, ok, tot in rows:
        print(f"  {platform:9s} {ok or 0:5d} capture(s)" + (f"   [{tot - (ok or 0)} template(s)]" if tot != (ok or 0) else ""))

    for target in ("windows", "macos", "ios", "linux"):
        if not any(r[0] == target and (r[1] or 0) for r in rows):
            print(f"  GAP: no {target} ground truth — profiles for this class cannot be verified")

    print("\nBrowser majors present (T0 only):")
    for browser, major, n in con.execute(
        "SELECT browser, browser_major, COUNT(*) FROM device WHERE is_capture=1 "
        "GROUP BY browser, browser_major ORDER BY 3 DESC LIMIT 8"
    ):
        print(f"  {browser}/{major}: {n}")

    distinct_gpu = con.execute(
        "SELECT COUNT(DISTINCT gpu_renderer) FROM device WHERE is_capture=1"
    ).fetchone()[0]
    print(f"\ndistinct GPU renderers (T0): {distinct_gpu}")

    if shared:
        print(f"\nWARNING: {len(shared)} measured block(s) shared across devices — "
              "templates leaked into the source set:")
        for (key, _), ids in list(shared.items())[:5]:
            print(f"  {key}: {', '.join(ids[:4])}{' …' if len(ids) > 4 else ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=pathlib.Path, default=DEFAULT_SRC)
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--coverage", action="store_true", help="report on an existing oracle without rebuilding")
    args = ap.parse_args()

    if args.coverage:
        if not args.db.exists():
            sys.exit(f"{args.db} does not exist — build it first")
        con = sqlite3.connect(args.db)
        report(con)
        con.close()
        return
    build(args.src, args.db)


if __name__ == "__main__":
    main()
