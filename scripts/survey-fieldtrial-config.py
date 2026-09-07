"""Report which field-trial testing-config features differ from their code default.

A non-branded Chromium build applies testing/variations/fieldtrial_testing_config.json
by default; Google Chrome does not. Every feature the config forces to a state
other than its compiled-in default is a behavioural difference from the Chrome
we claim to be. See docs/METHODOLOGY.md and build/args/common.gni.

Usage: scripts/survey-fieldtrial-config.py [path-to-chromium-src]
"""
import json, re, os, sys, collections

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.workspace', 'src')
cfg = json.load(open(os.path.join(SRC, 'testing/variations/fieldtrial_testing_config.json')))

forced = {}   # feature name -> set of states forced ('on'/'off')
for study, entries in cfg.items():
    for e in entries:
        for x in e.get('experiments', []):
            for f in x.get('enable_features', []):
                forced.setdefault(f.split('<')[0], set()).add('on')
            for f in x.get('disable_features', []):
                forced.setdefault(f.split('<')[0], set()).add('off')

# scrape BASE_FEATURE declarations for code defaults
two = re.compile(r'BASE_FEATURE\(\s*k(\w+)\s*,\s*base::FEATURE_(ENABLED|DISABLED)_BY_DEFAULT')
three = re.compile(r'BASE_FEATURE\(\s*k\w+\s*,\s*"([^"]+)"\s*,\s*base::FEATURE_(ENABLED|DISABLED)_BY_DEFAULT')
defaults = {}
locs = {}
for root, dirs, files in os.walk(SRC):
    dirs[:] = [d for d in dirs if d not in ('.git','out','third_party') or root.endswith('src')]
    if '/test' in root or root.endswith('/tests'):
        continue
    for fn in files:
        if not fn.endswith(('.cc','.mm')):
            continue
        p = os.path.join(root, fn)
        try:
            t = open(p, errors='ignore').read()
        except Exception:
            continue
        if 'BASE_FEATURE(' not in t:
            continue
        rel = os.path.relpath(p, SRC)
        for m in three.finditer(t):
            defaults[m.group(1)] = m.group(2); locs[m.group(1)] = rel
        for m in two.finditer(t):
            defaults[m.group(1)] = m.group(2); locs[m.group(1)] = rel

known = {f: s for f, s in forced.items() if f in defaults}
divergent = {}
for f, states in known.items():
    if len(states) != 1:
        continue
    want = 'ENABLED' if 'on' in states else 'DISABLED'
    if defaults[f] != want:
        divergent[f] = (defaults[f], want, locs[f])

def bucket(rel):
    if rel.startswith('third_party/blink'): return 'blink (renderer, web-exposed)'
    if rel.startswith('services/network'): return 'network service'
    if rel.startswith('content/'): return 'content'
    if rel.startswith('media/'): return 'media'
    if rel.startswith('gpu/') or rel.startswith('ui/gl'): return 'gpu/gl'
    if rel.startswith('net/'): return 'net'
    if rel.startswith('device/'): return 'device'
    return 'other (browser/UI/infra)'

print('features named by the testing config : %d' % len(forced))
print('  of those, default found in source  : %d' % len(known))
print('  DIVERGENT from the code default    : %d' % len(divergent))
print()
b = collections.Counter(bucket(v[2]) for v in divergent.values())
for k, n in b.most_common():
    print('  %-32s %d' % (k, n))
print()
WEB = ('third_party/blink', 'services/network', 'content/', 'media/', 'gpu/', 'ui/gl', 'net/', 'device/')
web = {f: v for f, v in divergent.items() if v[2].startswith(WEB)}
print('--- web-exposed subsystems, divergent (%d) ---' % len(web))
for f, (d, w, rel) in sorted(web.items(), key=lambda kv: kv[1][2]):
    print('  %-52s %-8s -> %-8s  %s' % (f, d, w, rel))
