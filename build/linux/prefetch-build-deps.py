#!/usr/bin/env python3
"""Prefetch snapshot-selected packages concurrently; APT still installs them.

Runs only inside the build container after its signed snapshot indexes load.
The ordinary Ubuntu archive is a transport mirror: filenames, versions, sizes,
and SHA-256 checksums come exclusively from the snapshot's package metadata.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request


def package_metadata(text):
    result = {}
    for stanza in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in stanza.splitlines()
                      if ': ' in line and not line.startswith(' '))
        if not {'Filename', 'SHA256', 'Size'} <= fields.keys():
            continue
        key = fields['Filename']
        value = (fields['SHA256'], int(fields['Size']))
        if key in result and result[key] != value:
            raise ValueError('conflicting snapshot metadata: ' + key)
        result[key] = value
    return result


def selected_downloads(text, metadata):
    result = []
    for line in text.splitlines():
        if not line.startswith("'"):
            continue
        url, filename, size, _ = shlex.split(line)
        proxy = os.environ.get('APOSTATE_SNAPSHOT_PROXY')
        origin = os.environ.get('APOSTATE_SNAPSHOT_BASE')
        if proxy and origin and url.startswith(proxy + '/'):
            url = origin + url[len(proxy):]
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'snapshot.ubuntu.com':
            raise ValueError('package selected outside snapshot: ' + url)
        _, found, suffix = urllib.parse.unquote(parsed.path).partition('/pool/')
        key = 'pool/' + suffix
        if not found or key not in metadata:
            raise ValueError('missing signed metadata: ' + url)
        sha256, metadata_size = metadata[key]
        if (filename != pathlib.Path(filename).name or not filename.endswith('.deb')
                or not re.fullmatch('[0-9a-f]{64}', sha256) or int(size) != metadata_size):
            raise ValueError('invalid package metadata: ' + filename)
        mirror = 'https://archive.ubuntu.com/ubuntu/' + urllib.parse.quote(key, safe='/~+')
        result.append({'filename': filename, 'sha256': sha256, 'size': metadata_size,
                       'snapshot_url': url, 'mirror_url': mirror})
    return result


def fetch(entry, directory):
    target = directory / entry['filename']
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']:
        return dict(entry, transport='cache')
    errors = []
    for key in ('mirror_url', 'snapshot_url'):
        temporary = None
        try:
            hasher = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(entry[key], timeout=90) as response, tempfile.NamedTemporaryFile(
                    dir=directory, prefix='.prefetch-', delete=False) as output:
                temporary = pathlib.Path(output.name)
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > entry['size']:
                        raise ValueError('package exceeds signed size')
                    hasher.update(block)
                    output.write(block)
            if size != entry['size'] or hasher.hexdigest() != entry['sha256']:
                raise ValueError('package checksum mismatch')
            temporary.chmod(0o644)
            temporary.replace(target)
            return dict(entry, transport=key)
        except Exception as error:
            errors.append(str(error))
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    raise RuntimeError(entry['filename'] + ': ' + '; '.join(errors))


def main():
    installer = pathlib.Path(__file__).with_name('install-build-deps.py')
    spec = importlib.util.spec_from_file_location('chromium_builddeps', installer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = module.parse_args(sys.argv[1:])
    module.check_distro(options)
    module.apt_update(options)
    packages = module.package_list(options)
    metadata = package_metadata(subprocess.check_output(['apt-cache', 'dumpavail'], text=True))
    uris = subprocess.check_output(['apt-get', '--assume-yes', '--download-only',
                                    '--print-uris', 'install', *packages], text=True)
    selected = selected_downloads(uris, metadata)
    directory = pathlib.Path('/var/cache/apt/archives')
    directory.mkdir(parents=True, exist_ok=True)
    print('prefetching', len(selected), 'snapshot-selected packages', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda entry: fetch(entry, directory), selected))
    installer.with_name('prefetched.json').write_text(json.dumps(results, indent=2) + '\n')
    print('verified', len(results), 'package SHA-256 checksums', flush=True)


if __name__ == '__main__':
    main()
