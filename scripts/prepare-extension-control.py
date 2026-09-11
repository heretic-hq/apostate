#!/usr/bin/env python3
"""Verify a Chrome Web Store CRX3 and unpack it for an explicit private control.

Does not install, launch, modify, register, or redistribute an extension.
CRX3 proof framing and the store signer pin follow Chromium152's crx_verifier.cc.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import urllib.parse
import urllib.request
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

STORE_KEY = "61f7f2a6bfcf74cd0bc1fe2497cc9b04254c658f79f2145392867ea8366367cf"

def fields(data):
    offset = 0
    def varint():
        nonlocal offset
        result = 0
        for shift in range(0, 70, 7):
            if offset == len(data): raise ValueError("truncated protobuf")
            byte = data[offset]; offset += 1
            result |= (byte & 127) << shift
            if byte < 128: return result
        raise ValueError("oversized protobuf integer")
    result = {}
    while offset < len(data):
        key = varint(); number, wire = key >> 3, key & 7
        if not number: raise ValueError("invalid protobuf field")
        if wire == 2:
            size = varint()
            if size > len(data) - offset: raise ValueError("truncated protobuf field")
            value = data[offset:offset + size]; offset += size
        elif wire == 0:
            value = varint()
        else: raise ValueError("unsupported CRX protobuf wire type")
        result.setdefault(number, []).append(value)
    return result

def one(data, field):
    values = data.get(field, [])
    if len(values) != 1 or not isinstance(values[0], bytes):
        raise ValueError("missing or duplicate CRX field")
    return values[0]

def verify(blob, extension_id):
    if len(blob) < 12 or blob[:4] != b"Cr24": raise ValueError("not CRX")
    version, size = struct.unpack_from("<II", blob, 4)
    if version != 3 or not 0 < size <= min(1024 * 1024, len(blob) - 12):
        raise ValueError("invalid CRX3 header")
    header = fields(blob[12:12 + size]); archive = blob[12 + size:]
    signed = one(header, 10000); declared = one(fields(signed), 1)
    encoded = ''.join(chr(ord('a') + int(c, 16)) for c in declared.hex())
    if len(declared) != 16 or encoded != extension_id: raise ValueError("extension ID mismatch")
    message = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed)) + signed + archive
    proofs = []
    for kind in (2, 3):
        for raw in header.get(kind, []):
            proof = fields(raw); public = one(proof, 1); signature = one(proof, 2)
            key = serialization.load_der_public_key(public)
            if kind == 2 and isinstance(key, rsa.RSAPublicKey):
                key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
            elif kind == 3 and isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
            else: raise ValueError("CRX proof key type mismatch")
            digest = hashlib.sha256(public).hexdigest()
            proofs.append({"sha256": digest, "developer": bytes.fromhex(digest)[:16] == declared,
                           "store": digest == STORE_KEY})
    if not any(p['developer'] for p in proofs) or not any(p['store'] for p in proofs):
        raise ValueError("developer and Chrome Web Store proofs are required")
    return archive, proofs

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch(r"[a-p]{32}", a.id): p.error("invalid extension ID")
    a.out.mkdir(parents=True, exist_ok=False)
    query = urllib.parse.urlencode({"response": "redirect", "prodversion": "152.0.7977.83",
        "acceptformat": "crx3", "x": "id=" + a.id + "&uc"})
    url = "https://clients2.google.com/service/update2/crx?" + query
    with urllib.request.urlopen(url, timeout=60) as response:
        blob = response.read(64 * 1024 * 1024 + 1)
    if len(blob) > 64 * 1024 * 1024: raise ValueError("CRX size limit")
    archive, proofs = verify(blob, a.id)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > 256 * 1024 * 1024:
            raise ValueError("extension extraction limit")
        names = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or
                    entry.filename in names or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("unsafe extension archive entry")
            names.add(entry.filename)
        manifest = json.loads(zipped.read("manifest.json"))
        if manifest.get("version") != a.version: raise ValueError("extension version differs from expected")
        destination = a.out / "extension"; destination.mkdir()
        zipped.extractall(destination)
    (a.out / "original.crx").write_bytes(blob)
    receipt = {"diagnostic_only": True, "corpus_admission": False, "source_url": url,
        "extension_id": a.id, "version": a.version, "crx_sha256": hashlib.sha256(blob).hexdigest(),
        "proofs": proofs, "files": {str(f.relative_to(destination)): hashlib.sha256(f.read_bytes()).hexdigest()
                                  for f in destination.rglob('*') if f.is_file()}}
    (a.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"extension_id": a.id, "version": a.version, "signature_verified": True,
                      "files": len(receipt['files'])}))

if __name__ == "__main__":
    main()
