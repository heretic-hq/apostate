#!/usr/bin/env bash
# V1 series gate: compile every translation unit the patch series touches on
# this platform, and classify each one honestly.
#
#   scripts/checkseries.sh                      # this host's default target
#   scripts/checkseries.sh linux-x64            # a configured target
#   scripts/checkseries.sh --list               # membership only, no compiling
#   scripts/checkseries.sh --merge a.json b.json
#
# scripts/checkfile.sh gates one file at a time, which is the right tool while
# writing a patch and the wrong one for answering "is the series green on this
# platform". Driven by hand on the one configured target, it reported green for
# a series whose Linux-only and Windows-only files had never been compiled at
# all: at 152.0.7977.83, 20 of the 130 translation units the series touches are
# absent from the macOS build graph, so no per-file run on this host could ever
# have covered them.
#
# Three outcomes per translation unit, and the third is why this script exists:
#
#   compiles   ninja produced every object the graph derives from the file
#   fails      an object was not produced; the error is reported and the gate
#              exits non-zero
#   absent     this platform's build graph produces no object from the file
#
# "absent" is legitimate — font_cache_linux.cc is Linux-only, sys_info_win.cc
# is Windows-only — but it is never folded into a pass. It is named, counted,
# and written to the report, so that merging the per-platform reports can tell
# an honestly platform-specific file from one that nothing compiles anywhere.
source "$(dirname "$0")/lib.sh"

GATE_ARGV=("$@")

TARGET=""
REPORT=""
MODE="compile"
VERIFY_ABSENT=1
JOBS="${APOSTATE_JOBS:-}"
MERGE_REPORTS=()

while (($#)); do
  case "$1" in
    --list) MODE="list" ;;
    # "absent" is the classification that can hide a defect, so confirming it
    # against the graph node itself is on by default. Each confirmation costs
    # one ninja manifest load -- about 10s on this warm macOS checkout, less on
    # a fresh CI one -- so --fast exists for a local edit-compile loop.
    --fast) VERIFY_ABSENT=0 ;;
    --report) REPORT="${2:?--report needs a path}"; shift ;;
    --report=*) REPORT="${1#*=}" ;;
    -j) JOBS="${2:?-j needs a job count}"; shift ;;
    -j*) JOBS="${1#-j}" ;;
    --merge) MODE="merge"; shift; MERGE_REPORTS=("$@"); break ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$TARGET" ] || die "target given twice: $TARGET and $1"; TARGET="$1" ;;
  esac
  shift
done

TU_TOOL="$REPO_ROOT/scripts/series-translation-units.py"
[ -f "$TU_TOOL" ] || die "missing $TU_TOOL"

# Linux compiles inside the pinned container, exactly as build.sh and
# checkfile.sh do. Compiling against the host's libraries would answer a
# question about this machine rather than about the build.
#
# After argument parsing, not before it: --merge reads finished reports on a
# plain hosted runner with no checkout, no Docker and no image, and re-execing
# it into a build container would make the cross-platform summary the most
# expensive step in the workflow.
if [ "$MODE" != "merge" ] && [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/checkseries.sh "${GATE_ARGV[@]}"
fi

# ----------------------------------------------------------------- merge mode
#
# One run sees one platform. "Not in this platform's build graph" is a fact
# about a platform; "in no platform's build graph" is a defect in the series,
# and it is only visible with the per-platform reports side by side.
if [ "$MODE" = "merge" ]; then
  [ "${#MERGE_REPORTS[@]}" -ge 2 ] || die "--merge needs at least two report files"
  python3 - "${MERGE_REPORTS[@]}" <<'PY'
import json, pathlib, sys

reports = []
for arg in sys.argv[1:]:
    path = pathlib.Path(arg)
    if not path.is_file():
        raise SystemExit(f"error: no report at {path}")
    reports.append(json.loads(path.read_text(encoding="utf-8")))

targets = [report["target"] for report in reports]
if len(set(targets)) != len(targets):
    raise SystemExit(f"error: two reports name the same target: {targets}")

# Families rather than target names: linux-x64 and linux-arm64 answer almost
# the same question, and merging those two alone must not be allowed to
# conclude anything about macOS or Windows.
families = {target.split("-")[0] for target in targets}
uncovered = sorted({"linux", "macos", "windows"} - families)

status = {}
for report in reports:
    for unit in report["units"]:
        status.setdefault(unit["path"], {})[report["target"]] = unit["status"]

dead = [
    path
    for path, per_target in status.items()
    if len(per_target) == len(targets) and set(per_target.values()) == {"absent"}
]
failures = sorted(
    (target, path)
    for path, per_target in status.items()
    for target, state in per_target.items()
    if state == "fails"
)

print(f"merged {len(reports)} reports: {', '.join(targets)}")
print(f"translation units: {len(status)}")
for path, per_target in status.items():
    states = "  ".join(f"{t}={per_target.get(t, 'no-report')}" for t in targets)
    print(f"  {path}\n      {states}")

if failures:
    print("\nfails to compile:")
    for target, path in failures:
        print(f"  {target}  {path}")

exit_code = 1 if failures else 0
if not dead:
    print("\nevery translation unit is compiled on at least one merged platform")
elif uncovered:
    print(f"\nabsent from every merged platform ({len(dead)}):")
    for path in dead:
        print(f"  {path}")
    print(
        "not conclusive: no report for " + ", ".join(uncovered) +
        ". Add one and merge again before calling these dead."
    )
else:
    print(f"\nIN NO PLATFORM'S BUILD GRAPH ({len(dead)}):")
    for path in dead:
        print(f"  {path}")
    print("A patch edits each of these and nothing compiles it.")
    exit_code = 1

raise SystemExit(exit_code)
PY
  exit $?
fi

# ------------------------------------------------------------ gate the series
TARGET="${TARGET:-${APOSTATE_TARGET:-$(target_default)}}"
OUT="$SRC/out/$TARGET"
REPORT="${REPORT:-$OUT/apostate-v1-$TARGET.json}"

[ -f "$OUT/build.ninja" ] || die "no build graph for $TARGET; run scripts/configure.sh $TARGET"
[ -x "$NINJA" ] || die "no ninja at $NINJA; the checkout is incomplete"
JOBS="${JOBS:-$(python3 -c 'import os;print(max(1,int(os.cpu_count()*0.75)))')}"

# nice is not guaranteed off Linux and macOS, and a missing nice must not be
# the reason the gate cannot run on a Windows runner.
NINJA_CMD=("$NINJA")
command -v nice >/dev/null 2>&1 && NINJA_CMD=(nice -n 10 "$NINJA")

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
UNITS="$WORK/units.tsv"
MAP="$WORK/map.tsv"
BUILD_LOG="$WORK/build.log"
DRY_LOG="$WORK/dry.log"
ABSENT_LOG="$WORK/absent.tsv"
: > "$ABSENT_LOG"

python3 "$TU_TOOL" --root "$REPO_ROOT" --with-patches > "$UNITS"
total="$(grep -c . "$UNITS" || true)"
[ "${total:-0}" -gt 0 ] || die "patches/series names no translation units; that cannot be right"

say "V1 series gate  target=$TARGET  $total translation units from patches/series"

# Phase 1 -- which of them does this platform compile?
#
# Asked of the build graph through `ninja -t compdb`, which lists every edge
# with its first input and its output. Two alternatives were measured and
# rejected:
#
#   * compile_commands.json, which checkfile.sh uses. It is a side artifact of
#     `gn gen`, 573MB here, and matched there by path suffix, so a short path
#     can bind to an entry for a different file. It is not what ninja builds
#     from.
#   * Asking ninja to build the object and reading the failure. Measured:
#     `ninja obj/...` for a target outside the graph prints
#     "ninja: error: unknown target '...'" and exits 1, and a translation unit
#     that fails to compile also exits 1. Keying the classification on that
#     text makes one ninja message change turn a missing file into a pass.
#
# So the gate never asks ninja to build an object the graph has not already
# named, and a file that produces no object is absent by construction rather
# than by interpretation. One invocation covers every unit in the series.
#
# Passed with -c rather than on stdin, because stdin is the compdb stream.
PHASE1_PY="$(cat <<'PY'
import collections, re, sys

units = []
for line in open(sys.argv[1], encoding="utf-8"):
    path = line.partition("\t")[0].strip()
    if path:
        units.append(path)
objects = {path: [] for path in units}

field = re.compile(rb'^\s*"(file|output)": "(.*?)",?\s*$')
depths = collections.Counter()
records = 0
record = {}
for raw in sys.stdin.buffer:
    match = field.match(raw)
    if not match:
        continue
    record[match.group(1)] = match.group(2).decode("utf-8", "replace")
    if len(record) < 2:
        continue
    source, output = record[b"file"], record[b"output"]
    record = {}
    records += 1
    # ninja writes source paths relative to the build directory. The depth is
    # counted rather than assumed, so a build directory at another depth --
    # or a file inside a nested DEPS repository -- still maps.
    depth = 0
    while source.startswith("../"):
        source = source[3:]
        depth += 1
    if depth:
        depths[depth] += 1
    # An object output is the whole question: not "does ninja know this path"
    # but "does this platform compile it".
    if source in objects and output.endswith((".o", ".obj")):
        objects[source].append(output)

if records == 0:
    raise SystemExit("error: ninja -t compdb listed no edges; the build graph is unreadable")
mapped = sum(1 for path in units if objects[path])
if mapped == 0:
    raise SystemExit(
        f"error: none of the {len(units)} series translation units mapped to an object.\n"
        "Every platform compiles some of them, so this is a broken gate rather than a\n"
        "clean series. Check the path spelling ninja uses in -t compdb."
    )

print(f"#prefix\t{'../' * (depths.most_common(1)[0][0] if depths else 0)}")
for path in units:
    print(f"{path}\t{' '.join(objects[path])}")
print(f"  {records} graph edges read, {mapped}/{len(units)} units produce objects", file=sys.stderr)
PY
)"
say "phase 1: build-graph membership (ninja -t compdb)"
set +e
( cd "$OUT" && "$NINJA" -t compdb ) 2>/dev/null | python3 -c "$PHASE1_PY" "$UNITS" > "$MAP"
phase1=("${PIPESTATUS[@]}")
set -e
[ "${phase1[0]}" -eq 0 ] || die "ninja -t compdb failed in $OUT"
[ "${phase1[1]}" -eq 0 ] || exit "${phase1[1]}"

# Out-dir-relative spelling of a source path, as ninja itself writes it. Taken
# from the compdb output rather than computed from the directory depth, so the
# gate cannot disagree with ninja about the prefix.
PREFIX="$(awk -F'\t' '$1=="#prefix"{print $2; exit}' "$MAP")"

objects=()
in_graph=()
absent=()
while IFS=$'\t' read -r path objs; do
  case "$path" in '#'*) continue ;; esac
  if [ -n "$objs" ]; then
    in_graph+=("$path")
    for obj in $objs; do objects+=("$obj"); done
  else
    absent+=("$path")
  fi
done < "$MAP"

# Phase 1b -- confirm every absent file against the graph node itself.
#
# compdb answers "no edge produces an object from this file", which is the
# question the gate asks. `ninja -t query` answers the narrower "ninja has
# never heard of this path", and the difference matters: a file ninja knows but
# never compiles is a louder finding than a file that is simply not part of
# this platform, and both are reported with the evidence that produced them.
if [ "${#absent[@]}" -gt 0 ] && [ "$VERIFY_ABSENT" -eq 1 ]; then
  say "phase 1b: confirming ${#absent[@]} absent unit(s) with ninja -t query"
  for path in "${absent[@]}"; do
    query_out="$( ( cd "$OUT" && "$NINJA" -t query "$PREFIX$path" ) 2>&1 || true )"
    if printf '%s' "$query_out" | grep -q "unknown target"; then
      reason="not in this platform's build graph (ninja -t query: unknown target)"
    else
      reason="IN THE GRAPH BUT NO OBJECT IS PRODUCED FROM IT (ninja -t query: $(printf '%s' "$query_out" | tr '\n' ' ' | cut -c1-160))"
    fi
    printf '%s\t%s\n' "$path" "$reason" >> "$ABSENT_LOG"
  done
else
  for path in "${absent[@]-}"; do
    [ -n "$path" ] || continue
    printf '%s\t%s\n' "$path" "not in this platform's build graph (no object edge in ninja -t compdb)" >> "$ABSENT_LOG"
  done
fi

while IFS=$'\t' read -r path reason; do
  printf '  absent    %s\n            %s\n' "$path" "$reason"
done < "$ABSENT_LOG"

ninja_exit=0
if [ "$MODE" = "list" ]; then
  say "list mode: ${#in_graph[@]} unit(s) in the graph, ${#absent[@]} absent; nothing compiled"
  : > "$BUILD_LOG"
  : > "$DRY_LOG"
else
  [ "${#objects[@]}" -gt 0 ] || die "no objects to compile; the mapping is broken, not the fork"

  # Phase 1c -- plan before spending. Two things for one manifest load:
  #
  #   * Every object name is proved to be a real target now rather than after
  #     forty minutes of compiling. A name that does not exist makes ninja
  #     print "unknown target" and stop, which is a broken gate, not a clean
  #     series, and it must not be reachable from the compile phase.
  #   * The edge count is the gate's real cost, and it is not the 130 files.
  #     Measured on macos-arm64 at 152.0.7977.83: the static prerequisite
  #     closure of these objects is 53,670 generated files, or 112,086 once
  #     obj/chrome/app/test_support/chrome_main_delegate.o is included --
  #     that one object's GN hard deps include phony/chrome/chrome_framework,
  #     the macOS framework bundle, so compiling it first links the browser.
  #     Printing the number keeps that visible instead of surprising someone
  #     with a build-sized bill.
  if ! ( set +x; cd "$OUT" && "$NINJA" -n -k 0 "${objects[@]}" ) > "$DRY_LOG" 2>&1; then
    cat "$DRY_LOG" >&2
    die "ninja cannot plan the objects the graph named; the gate is broken, not the series"
  fi
  if grep -q '^ninja: no work to do\.$' "$DRY_LOG"; then
    say "phase 1c: every object is already up to date"
  else
    say "phase 1c: ninja plans $(grep -c . "$DRY_LOG") edge(s) for ${#objects[@]} object(s)"
  fi

  say "phase 2: compiling ${#in_graph[@]} unit(s), ${#objects[@]} object(s), -j$JOBS"
  # One invocation, not one per file. Every translation unit here shares the
  # generated-header prerequisites, which dominate a fresh build directory; a
  # per-file loop rebuilds nothing twice but pays ninja's manifest load each
  # time (about 10s warm here, so 130 files is 20 idle minutes). -k 0 keeps
  # going after a failure so one broken patch does not hide the rest.
  set +e
  ( cd "$OUT" && "${NINJA_CMD[@]}" -k 0 -j "$JOBS" "${objects[@]}" ) 2>&1 | tee "$BUILD_LOG"
  ninja_exit="${PIPESTATUS[0]}"
  # Phase 3 -- state, not text. After a keep-going build every requested
  # object must be up to date; whatever ninja would still rebuild was not
  # produced, whether or not its own edge printed "FAILED:". This is what
  # catches a translation unit blocked by a failed prerequisite action, where
  # the failure names a code generator and never mentions the object.
  #
  # set +x inside the subshell because the redirect captures its stderr, and
  # under `bash -x` the trace of this very command -- which lists every object
  # on one line -- would land in the file being scanned for object names.
  # Debugging the gate would then make it report every unit as failing.
  ( set +x; cd "$OUT" && "$NINJA" -n -k 0 "${objects[@]}" ) > "$DRY_LOG" 2>&1
  set -e
fi

python3 - "$UNITS" "$MAP" "$ABSENT_LOG" "$BUILD_LOG" "$DRY_LOG" "$REPORT" \
  "$TARGET" "$MODE" "$ninja_exit" "$JOBS" "$REPO_ROOT" <<'PY'
import hashlib, json, os, pathlib, platform, sys, time

units_file, map_file, absent_file, build_log, dry_log, report_path, target, mode, ninja_exit, jobs, repo_root = sys.argv[1:12]

patches = {}
order = []
for line in pathlib.Path(units_file).read_text(encoding="utf-8").splitlines():
    path, _, names = line.partition("\t")
    if path:
        order.append(path)
        patches[path] = [n for n in names.split(",") if n]

objects = {}
for line in pathlib.Path(map_file).read_text(encoding="utf-8").splitlines():
    path, _, objs = line.partition("\t")
    if path.startswith("#"):
        continue
    objects[path] = objs.split()

reasons = {}
for line in pathlib.Path(absent_file).read_text(encoding="utf-8").splitlines():
    path, _, reason = line.partition("\t")
    if path:
        reasons[path] = reason

log_text = pathlib.Path(build_log).read_text(encoding="utf-8", errors="replace")
log_lines = log_text.splitlines()

# ninja prints "FAILED: <output> [<output>...]" and then the command and the
# compiler's diagnostics. The outputs are ninja's own spelling of the edge, so
# they map back to a translation unit exactly.
failed_objects = set()
excerpts = {}
for index, line in enumerate(log_lines):
    if not line.startswith("FAILED: "):
        continue
    outs = line[len("FAILED: "):].split()
    failed_objects.update(outs)
    body = []
    for follow in log_lines[index + 1:]:
        if follow.startswith("FAILED: ") or follow.startswith("ninja: "):
            break
        if follow.startswith("[") and "] " in follow[:12]:
            break
        body.append(follow)
        if len(body) >= 40:
            break
    for out in outs:
        excerpts[out] = "\n".join(body).strip()

# Anything ninja would still build is not up to date, so it was not produced.
dry_tokens = set()
for line in pathlib.Path(dry_log).read_text(encoding="utf-8", errors="replace").split():
    dry_tokens.add(line)

units = []
counts = {"compiles": 0, "fails": 0, "absent": 0, "unchecked": 0}
for path in order:
    objs = objects.get(path, [])
    unit = {"path": path, "patches": patches[path], "objects": objs}
    if not objs:
        unit["status"] = "absent"
        unit["reason"] = reasons.get(path, "not in this platform's build graph")
    elif mode == "list":
        unit["status"] = "unchecked"
        unit["reason"] = "membership only; --list did not compile"
    else:
        failed = [o for o in objs if o in failed_objects]
        residual = [o for o in objs if o in dry_tokens]
        if failed or residual:
            unit["status"] = "fails"
            unit["failed_objects"] = sorted(set(failed) | set(residual))
            if failed and not residual:
                unit["reason"] = "ninja reported FAILED for an object that is now up to date; re-run the gate"
            elif residual and not failed:
                unit["reason"] = "object not produced and no FAILED line names it; a prerequisite failed"
            else:
                unit["reason"] = "compile failed"
            unit["error"] = "\n".join(excerpts.get(o, "") for o in failed).strip()
        else:
            unit["status"] = "compiles"
    counts[unit["status"]] += 1
    units.append(unit)

series = pathlib.Path(repo_root, "patches", "series").read_bytes()
report = {
    "schema": "apostate-v1-series-gate/1",
    "target": target,
    "mode": mode,
    "host": f"{platform.system()}-{platform.machine()}",
    "chromium_version": pathlib.Path(repo_root, "build", "CHROMIUM_VERSION").read_text(encoding="utf-8").strip(),
    "series_sha256": hashlib.sha256(series).hexdigest(),
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "jobs": int(jobs),
    "ninja_exit": int(ninja_exit),
    "counts": counts,
    "units": units,
}
pathlib.Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

for unit in units:
    if unit["status"] == "fails":
        print(f"  FAIL      {unit['path']}")
        print(f"            {unit['reason']}")
        # The first line after "FAILED:" is the compile command, which for
        # Chromium is a few thousand characters. It stays whole in the report
        # so a failure can be reproduced by hand, and is cut here so the
        # diagnostics after it are the part a reader sees.
        for line in unit.get("error", "").splitlines()[:12]:
            print(f"            {line[:200]}")

print()
print(f"==> V1 series gate  {target}  ({report['generated_at']})")
print(f"  compiles              {counts['compiles']:4d}")
print(f"  fails                 {counts['fails']:4d}")
print(f"  not in build graph    {counts['absent']:4d}")
if counts["unchecked"]:
    print(f"  unchecked (--list)    {counts['unchecked']:4d}")
print(f"  total                 {len(units):4d}")
print(f"  report                {report_path}")

# A non-zero ninja exit with nothing attributed means the gate cannot say what
# broke. That is a gate failure, not a pass.
if int(ninja_exit) != 0 and counts["fails"] == 0:
    print("\nninja exited non-zero but no translation unit was attributed a failure;")
    print("treat this run as inconclusive and read the log above.")
    raise SystemExit(1)
raise SystemExit(1 if counts["fails"] else 0)
PY
