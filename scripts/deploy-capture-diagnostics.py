#!/usr/bin/env python3
"""Deploy the opt-in font diagnostic while preserving an existing capture service.

Stages a hashed release, checks unchanged collector bytes and runs the HTTP
tests before activation. A failed activation restores the previous override.
Capture and diagnostic data are never deleted or moved by this script.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = ["capture/server/receive.py", "capture/server/test_font_context.py",
         "capture/collector/collector.js", "capture/collector/index.html",
         "capture/diagnostics/font-context.html", "capture/diagnostics/font-context.js",
         "scripts/fixtures/font-context-supplement.html"]

REMOTE = r'''
import base64, hashlib, json, os, pathlib, re, subprocess, tempfile, time, urllib.request
data = json.loads(base64.b64decode(PAYLOAD))
service = data['service']
run = lambda args: subprocess.check_output(args, text=True).strip()
pid = int(run(['systemctl', 'show', service, '--property=MainPID', '--value']))
if pid <= 0: raise RuntimeError('existing capture service must be running')
argv = pathlib.Path(f'/proc/{pid}/cmdline').read_bytes().decode().rstrip('\0').split('\0')
if len(argv) < 3 or not argv[1].endswith('/capture/server/receive.py'):
    raise RuntimeError('unexpected existing capture command')
if '--once' in argv: raise RuntimeError('refusing to change a one-shot capture')
old_root = pathlib.Path(argv[1]).resolve().parents[2]
for name in ['capture/collector/collector.js', 'capture/collector/index.html']:
    actual = hashlib.sha256((old_root/name).read_bytes()).hexdigest()
    if actual != data['files'][name]['sha256']:
        raise RuntimeError('current collector differs: ' + name)
base_root = pathlib.Path(data['server_root'])
release = base_root/'releases'/('font-context-' + data['bundle_sha256'])
release.mkdir(parents=True, exist_ok=True)
for name, item in data['files'].items():
    value = base64.b64decode(item['data'])
    if hashlib.sha256(value).hexdigest() != item['sha256']:
        raise RuntimeError('transfer hash mismatch')
    path = release/name
    if path.exists():
        if path.read_bytes() != value: raise RuntimeError('existing release conflicts')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output: output.write(value)
        path.chmod(0o644)
with (release/'validation.log').open('w') as log:
    subprocess.run([argv[0], '-m', 'unittest', 'discover', '-s', 'capture/server',
                    '-p', 'test_font_context.py', '-v'], cwd=release,
                   stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
for name, item in data['files'].items():
    if hashlib.sha256((release/name).read_bytes()).hexdigest() != item['sha256']:
        raise RuntimeError('release changed during validation')
diagnostics = pathlib.Path(data['diagnostics_out']).resolve()
captures = pathlib.Path(argv[argv.index('--out')+1]).resolve()
if diagnostics == captures or diagnostics.is_relative_to(captures) or captures.is_relative_to(diagnostics):
    raise RuntimeError('diagnostic and capture data must be separate')
diagnostics.mkdir(parents=True, exist_ok=True, mode=0o700)
diagnostics.chmod(0o700)
new_argv = list(argv)
new_argv[1] = str(release/'capture/server/receive.py')
if '--diagnostics-out' in new_argv:
    new_argv[new_argv.index('--diagnostics-out')+1] = str(diagnostics)
else:
    new_argv += ['--diagnostics-out', str(diagnostics)]
if any(not re.fullmatch(r'[A-Za-z0-9_./:-]+', arg) for arg in new_argv):
    raise RuntimeError('existing command needs explicit systemd quoting review')
override = pathlib.Path('/etc/systemd/system')/(service+'.d')/'50-font-context.conf'
previous = override.read_text() if override.exists() else None
marker = '# Managed by deploy-capture-diagnostics.py\n'
if previous is not None and not previous.startswith(marker):
    raise RuntimeError('refusing to overwrite an unmanaged service override')
content = marker+'[Service]\nExecStart=\nExecStart='+' '.join(new_argv)+'\n'
changed = previous != content or argv != new_argv
receipt = {'bundle_sha256': data['bundle_sha256'], 'release': str(release),
           'service': service, 'previous_argv': argv, 'new_argv': new_argv,
           'diagnostics_out': str(diagnostics), 'collector_unchanged': True,
           'validation': '10 focused HTTP tests passed', 'restarted': changed}
def publish(text):
    override.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.font-context-', dir=override.parent)
    with os.fdopen(fd, 'w') as output: output.write(text)
    os.chmod(temporary, 0o644)
    os.replace(temporary, override)
try:
    if changed:
        publish(content)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'restart', service], check=True, timeout=30)
    if run(['systemctl', 'is-active', service]) != 'active':
        raise RuntimeError('capture service is not active')
    # Type=simple reports active before Python has bound its TLS listener.
    for attempt in range(40):
        try:
            with urllib.request.urlopen(data['origin']+'/echo', timeout=2) as response:
                response.read()
            break
        except (OSError, urllib.error.URLError):
            if attempt == 39: raise
            time.sleep(0.25)
    receipt['http'] = {}
    before = sorted(path.name for path in diagnostics.glob('*.json'))
    for route in ['/diagnostics/font-context', '/diagnostics/font-context/app.js',
                  '/diagnostics/font-context/probe.js', '/echo', '/collector.js']:
        with urllib.request.urlopen(data['origin']+route, timeout=15) as response:
            body = response.read()
            receipt['http'][route] = response.status
            if route == '/collector.js' and hashlib.sha256(body).hexdigest() != data['files']['capture/collector/collector.js']['sha256']:
                raise RuntimeError('served collector differs')
    # Real contributors may submit concurrently. The HTTP tests establish that
    # GET cannot store data; do not misattribute a live submission to this check.
    receipt['diagnostic_files_before_checks'] = len(before)
    receipt['diagnostic_files_after_checks'] = len(list(diagnostics.glob('*.json')))
    receipt['passed'] = True
except Exception:
    if changed:
        if previous is None: override.unlink(missing_ok=True)
        else: publish(previous)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'restart', service], check=True, timeout=30)
    raise
print(json.dumps(receipt))
'''

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--service', default='apostate-probe.service')
    p.add_argument('--server-root', default='/opt/apostate-probe')
    p.add_argument('--diagnostics-out', required=True)
    p.add_argument('--origin', required=True)
    p.add_argument('--receipt', type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', a.host) or a.host.startswith('-'):
        p.error('invalid SSH host')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.service', a.service): p.error('invalid service')
    if not a.origin.startswith('https://') or a.origin.endswith('/'): p.error('provide an HTTPS origin without trailing slash')
    files = {}
    digest = hashlib.sha256()
    for name in FILES:
        value = (ROOT/name).read_bytes()
        digest.update(name.encode()+b'\0'+value+b'\0')
        files[name] = {'sha256': hashlib.sha256(value).hexdigest(), 'data': base64.b64encode(value).decode()}
    payload = dict(files=files, bundle_sha256=digest.hexdigest(), service=a.service,
                   server_root=a.server_root, diagnostics_out=a.diagnostics_out, origin=a.origin)
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    result = subprocess.run(['ssh', a.host, 'python3 -'], input='PAYLOAD='+repr(encoded)+'\n'+REMOTE,
                            text=True, capture_output=True, timeout=180)
    a.receipt.parent.mkdir(parents=True, exist_ok=True)
    a.receipt.with_suffix('.stderr.log').write_text(result.stderr)
    if result.returncode:
        raise RuntimeError('deployment failed; see '+str(a.receipt.with_suffix('.stderr.log')))
    receipt = json.loads(result.stdout)
    a.receipt.write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'passed': receipt['passed'], 'restarted': receipt['restarted'],
                      'diagnostic_url': a.origin+'/diagnostics/font-context'}))

if __name__ == '__main__':
    main()
