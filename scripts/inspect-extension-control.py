#!/usr/bin/env python3
"""Inspect a public Chrome Web Store package as a possible capture input.

Downloads public extension code without installing or executing it. A package
match does not identify the extension version used by a reference capture.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import urllib.parse
import urllib.request
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extension_id")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[a-p]{32}", args.extension_id):
        parser.error("expected a Chrome extension ID")
    args.out.mkdir(parents=True, exist_ok=True)
    package = args.out / (args.extension_id + ".crx")
    receipt = args.out / "source.json"
    if package.exists():
        data = package.read_bytes()
        provenance = json.loads(receipt.read_text())
        if hashlib.sha256(data).hexdigest() != provenance["sha256"]:
            parser.error("cached package checksum mismatch")
    else:
        version = (Path(__file__).resolve().parents[1] / "build/CHROMIUM_VERSION").read_text().strip()
        query = urllib.parse.urlencode({
            "response": "redirect", "prodversion": version, "acceptformat": "crx3",
            "x": f"id={args.extension_id}&uc"})
        url = "https://clients2.google.com/service/update2/crx?" + query
        with urllib.request.urlopen(url, timeout=60) as response:
            final_url = response.url
            data = response.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            parser.error("package exceeds inspection size limit")
        provenance = {"request_url": url, "resolved_url": final_url,
                      "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        package.write_bytes(data)
        receipt.write_text(json.dumps(provenance, indent=2) + "\n")
    if len(data) < 12 or data[:4] != b"Cr24":
        parser.error("not a CRX package")
    version, header_size = struct.unpack_from("<II", data, 4)
    if version != 3 or header_size > 4 * 1024 * 1024:
        parser.error("unsupported CRX header")
    with zipfile.ZipFile(io.BytesIO(data[12 + header_size:])) as archive:
        if sum(item.file_size for item in archive.infolist()) > 256 * 1024 * 1024:
            parser.error("uncompressed package exceeds inspection limit")
        manifest = json.loads(archive.read("manifest.json"))
        fonts = [name for name in archive.namelist()
                 if name.lower().endswith((".woff", ".woff2", ".ttf", ".otf"))]
        markers = ["isPhantomInstalled", "_phantomHideProvidersArray", "SENTRY_RELEASE",
                   "_sentryDebugIds", "@font-face", "Noto Sans", "Roboto"]
        matches = {marker: [] for marker in markers}
        for item in archive.infolist():
            if item.filename.endswith((".js", ".css", ".html")):
                body = archive.read(item).decode("utf-8", errors="replace")
                for marker in markers:
                    if marker in body:
                        matches[marker].append(item.filename)
    report = {"extension_id": args.extension_id, "package_version": manifest.get("version"),
              "manifest": manifest, "font_files": fonts, "marker_files": matches,
              "package_sha256": provenance["sha256"],
              "installed": False, "reference_version_established": False}
    (args.out / "inspection.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("extension_id", "package_version", "font_files", "marker_files")}, indent=2))


if __name__ == "__main__":
    main()
