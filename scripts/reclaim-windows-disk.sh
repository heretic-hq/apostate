#!/usr/bin/env bash
# Reclaim disk on the Windows runner image before a build.
#
# The image advertises 130GB of storage, but that is the volume size: 76GB is
# the image itself and only 55GB is free. A complete build measured 66GB
# locally (50GB checkout, 16GB output), so the build does not fit as shipped.
#
# Every path here was measured by the "Census Windows disk usage" step in
# .github/workflows/probe-runners.yml rather than guessed, and each is software
# a Chromium build never touches. Deliberately NOT removed:
#
#   C:\Windows\Installer         2.7GB, but MSI repair and uninstall need it,
#                                and this build runs winsdksetup.exe.
#   C:\ProgramData\chocolatey    2.6GB, but it owns shims for tools the image
#                                installed through it, including 7z.
#   C:\Program Files (x86)\Windows Kits   3.5GB, that is the SDK we build with.
#   The active Python in hostedtoolcache  python3 resolves into it, so the
#                                whole tree cannot go; the other toolchains can.
#
# Idempotent and never fatal: a path that has already gone, or that the runner
# will not let us delete, is reported and skipped. Reclaiming less than hoped
# must not fail a build that would otherwise have fitted.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

say() { printf '==> %s\n' "$*"; }

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) printf 'error: reclaim-windows-disk.sh only runs on Windows\n' >&2; exit 1 ;;
esac

free_mb() { df -m /c 2>/dev/null | awk 'NR==2 {print $4}'; }

before="$(free_mb)"
say "free before: ${before} MB"

drop() {
  local path="$1" size
  if [ ! -e "$path" ]; then
    printf '    skip (absent)  %s\n' "$path"
    return 0
  fi
  size="$(du -sm "$path" 2>/dev/null | cut -f1)"
  if rm -rf "$path" 2>/dev/null; then
    printf '    freed %6s MB  %s\n' "${size:-?}" "$path"
  else
    printf '    could not remove (in use or protected)  %s\n' "$path"
  fi
}

say "removing database servers and runtimes a Chromium build never uses"
drop "/c/Program Files/MongoDB"
drop "/c/Program Files/MySQL"
drop "/c/Program Files/PostgreSQL"
drop "/c/Program Files/dotnet"
drop "/c/Program Files/Microsoft SDKs/Azure"

# python3 lives under hostedtoolcache, so keep the tree that provides it and
# drop the language runtimes nothing in this build calls. Resolved rather than
# assumed: the census showed python3 at
# /c/hostedtoolcache/windows/Python/3.9.13/x64/python3.
say "removing unused language toolchains from hostedtoolcache"
keep=""
if command -v python3 >/dev/null 2>&1; then
  keep="$(cd "$(dirname "$(command -v python3)")" && pwd -P)"
fi
for tool in /c/hostedtoolcache/windows/*; do
  [ -d "$tool" ] || continue
  if [ -n "$keep" ] && [[ "$keep" == "$tool"/* || "$keep" == "$tool" ]]; then
    printf '    keep           %s (provides python3)\n' "$tool"
    continue
  fi
  drop "$tool"
done

after="$(free_mb)"
say "free after: ${after} MB (reclaimed $(( ${after:-0} - ${before:-0} )) MB)"

# Report only. scripts/bootstrap.sh already warns when the workspace has less
# than a complete build needs, and that warning is the one place this decision
# belongs.
if command -v python3 >/dev/null 2>&1; then
  say "python3 still present at $(command -v python3)"
else
  printf 'error: python3 is gone after reclaiming; that is a bug in this script\n' >&2
  exit 1
fi
