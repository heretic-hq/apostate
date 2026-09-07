#!/usr/bin/env python3
"""Read one TLS ClientHello per connection and dump its structure + JA3.

Never completes the handshake: we only need the first flight. The client will
report a connection error, which is expected and harmless.
"""
import socket, struct, hashlib, sys, json, threading

GREASE = {0x0a0a,0x1a1a,0x2a2a,0x3a3a,0x4a4a,0x5a5a,0x6a6a,0x7a7a,
          0x8a8a,0x9a9a,0xaaaa,0xbaba,0xcaca,0xdada,0xeaea,0xfafa}

EXT_NAMES = {0:"server_name",5:"status_request",10:"supported_groups",
    11:"ec_point_formats",13:"signature_algorithms",16:"alpn",
    17:"status_request_v2",18:"signed_certificate_timestamp",
    21:"padding",22:"encrypt_then_mac",23:"extended_master_secret",
    27:"compress_certificate",28:"record_size_limit",35:"session_ticket",
    41:"pre_shared_key",42:"early_data",43:"supported_versions",
    44:"cookie",45:"psk_key_exchange_modes",47:"certificate_authorities",
    49:"post_handshake_auth",50:"signature_algorithms_cert",
    51:"key_share",17513:"application_settings",17613:"application_settings_new",
    65037:"encrypted_client_hello",65281:"renegotiation_info"}

def rd(b, o, n):
    return b[o:o+n], o+n

def parse(ch):
    out = {}
    o = 0
    o += 2                                   # legacy_version
    o += 32                                  # random
    sidlen = ch[o]; o += 1 + sidlen
    cslen = struct.unpack(">H", ch[o:o+2])[0]; o += 2
    ciphers = list(struct.unpack(">%dH" % (cslen//2), ch[o:o+cslen])); o += cslen
    out["ciphers"] = ciphers
    out["ciphers_nogrease"] = [c for c in ciphers if c not in GREASE]
    out["grease_in_ciphers"] = [i for i,c in enumerate(ciphers) if c in GREASE]
    cmlen = ch[o]; o += 1 + cmlen
    extlen = struct.unpack(">H", ch[o:o+2])[0]; o += 2
    end = o + extlen
    exts, detail = [], {}
    while o < end:
        et, el = struct.unpack(">HH", ch[o:o+4]); o += 4
        body = ch[o:o+el]; o += el
        exts.append(et)
        nm = EXT_NAMES.get(et, "grease" if et in GREASE else "unknown_%d" % et)
        if et == 10:
            n = struct.unpack(">H", body[:2])[0]
            detail["supported_groups"] = list(struct.unpack(">%dH" % (n//2), body[2:2+n]))
        elif et == 13:
            n = struct.unpack(">H", body[:2])[0]
            detail["sig_algs"] = ["0x%04x" % x for x in struct.unpack(">%dH" % (n//2), body[2:2+n])]
        elif et == 16:
            n = struct.unpack(">H", body[:2])[0]; p = 2; alpn = []
            while p < 2+n:
                ln = body[p]; p += 1; alpn.append(body[p:p+ln].decode()); p += ln
            detail["alpn"] = alpn
        elif et == 43:
            n = body[0]
            detail["supported_versions"] = ["0x%04x" % x for x in struct.unpack(">%dH" % (n//2), body[1:1+n])]
        elif et == 51:
            n = struct.unpack(">H", body[:2])[0]; p = 2; ks = []
            while p < 2+n:
                g, kl = struct.unpack(">HH", body[p:p+4]); p += 4 + kl
                ks.append(g)
            detail["key_share_groups"] = ks
        elif et == 21:
            detail["padding_len"] = el
        elif et == 45:
            detail["psk_modes"] = list(body[1:1+body[0]])
        elif et == 27:
            detail["cert_compression"] = list(struct.unpack(">%dH" % (body[0]//2), body[1:1+body[0]]))
    out["ext_order"] = [(e, EXT_NAMES.get(e, "grease" if e in GREASE else "unknown_%d" % e)) for e in exts]
    out["ext_nogrease"] = [e for e in exts if e not in GREASE]
    out["detail"] = detail
    # JA3 (GREASE stripped, per spec)
    grp = [g for g in detail.get("supported_groups", []) if g not in GREASE]
    ja3 = "771,%s,%s,%s,%s" % (
        "-".join(map(str, out["ciphers_nogrease"])),
        "-".join(map(str, out["ext_nogrease"])),
        "-".join(map(str, grp)),
        "0")
    out["ja3"] = ja3
    out["ja3_md5"] = hashlib.md5(ja3.encode()).hexdigest()
    return out

def handle(c, label, results):
    try:
        c.settimeout(5)
        hdr = c.recv(5)
        if len(hdr) < 5 or hdr[0] != 0x16:
            return
        ln = struct.unpack(">H", hdr[3:5])[0]
        body = b""
        while len(body) < ln:
            chunk = c.recv(ln - len(body))
            if not chunk: break
            body += chunk
        # handshake header: type(1) len(3)
        if body[0] != 0x01:
            return
        results.append(parse(body[4:]))
    except Exception as e:
        print("err:", e, file=sys.stderr)
    finally:
        c.close()

def main():
    port = int(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port)); s.listen(8); s.settimeout(120)
    results = []
    while len(results) < n:
        try:
            c, _ = s.accept()
        except socket.timeout:
            break
        handle(c, "", results)
    print(json.dumps(results[0] if results else {}, indent=1))

main()
