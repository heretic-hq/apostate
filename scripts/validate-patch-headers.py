#!/usr/bin/env python3
"""Validate (and optionally repair) unified-diff hunk headers in patches/.

A hunk header declares how many lines its body covers on each side. `git apply`
trusts that count: when a header says `+1,110` and the body carries 113 added
lines, the extra lines are silently dropped and the result is a truncated file
that still "applies cleanly". That happened to
`patches/0004-base-device-profile-loader.patch`, which produced a
`base/apostate/profile.cc` missing its closing brace, so every patch after it
failed against drifted context and the whole series stopped at patch 7 --
while the build directory kept a binary from the partial tree.

The count is recomputable from the body, so nothing has to be trusted. This
script recomputes it, reports every disagreement, and with --fix rewrites the
headers. It also recomputes the new-side start line of every hunk after the
first in a file, because an inserted or deleted body line shifts them.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class Hunk:
    __slots__ = ("index", "old_start", "old_count", "new_start", "new_count", "tail", "body")

    def __init__(self, index: int, old_start: int, old_count: int, new_start: int, new_count: int, tail: str):
        self.index = index
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.tail = tail
        self.body: list[str] = []

    def measured(self) -> tuple[int, int]:
        old = new = 0
        for line in self.body:
            if line.startswith("+"):
                new += 1
            elif line.startswith("-"):
                old += 1
            elif line.startswith("\\"):
                # "\ No newline at end of file" annotates the previous line.
                continue
            else:
                # A context line starts with a space. An empty line is a context
                # line whose single leading space was stripped in transit, which
                # is common enough that rejecting it would reject real patches.
                old += 1
                new += 1
        return old, new

    def header(self) -> str:
        old = f"{self.old_start}" if self.old_count == 1 else f"{self.old_start},{self.old_count}"
        new = f"{self.new_start}" if self.new_count == 1 else f"{self.new_start},{self.new_count}"
        return f"@@ -{old} +{new} @@{self.tail}"


def _is_file_header(lines: list[str], i: int) -> bool:
    """True when lines[i] opens a file section.

    Not every patch in the series carries `diff --git`; several are plain
    `--- a/x` / `+++ b/x` pairs. A body line could itself read `--- foo`, so the
    pair is what identifies a header, never the prefix alone.
    """
    return (
        lines[i].startswith("--- ")
        and i + 1 < len(lines)
        and lines[i + 1].startswith("+++ ")
    )


def _parse(lines: list[str]) -> list[tuple[int, Hunk, int]]:
    """Return (header line index, hunk, file index) for every hunk, in order."""
    hunks: list[tuple[int, Hunk, int]] = []
    current: Hunk | None = None
    file_index = -1
    # Empty lines after the last content line are the file's trailing newlines,
    # not context. git apply reads exactly the declared count and ignores them.
    last_content = max(
        (i for i, line in enumerate(lines) if line != ""), default=len(lines) - 1
    )
    for i, line in enumerate(lines):
        if _is_file_header(lines, i):
            file_index += 1
            current = None
            continue
        if line.startswith("+++ ") and current is None:
            continue
        match = _HUNK_RE.match(line)
        if match:
            current = Hunk(
                i,
                int(match.group(1)),
                int(match.group(2)) if match.group(2) is not None else 1,
                int(match.group(3)),
                int(match.group(4)) if match.group(4) is not None else 1,
                match.group(5),
            )
            hunks.append((i, current, file_index))
            continue
        if current is None:
            continue
        if line.startswith("diff --git ") or line.startswith("-- "):
            current = None
            continue
        if line == "" and i > last_content:
            # Trailing newline sentinel, not a context line.
            continue
        # A blank line before the next file section is ambiguous: it is either a
        # separator or a trailing context line whose single leading space was
        # stripped. Both appear in this series, so it is counted as context here
        # and the ambiguity is resolved in check().
        if line[:1] in {"+", "-", " ", "\\"} or line == "":
            current.body.append(line)
        else:
            # Anything else ends the hunk body: a new file header, a commit
            # trailer, or prose between diffs.
            current = None
    return hunks


def check(path: Path, fix: bool) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    problems: list[str] = []

    # new_start is determined: it is the old_start shifted by the net lines every
    # earlier hunk in the same file inserted. A new-file hunk starts at 1.
    delta: dict[int, int] = {}
    for header_index, hunk, owner in _parse(lines):
        measured_old, measured_new = hunk.measured()
        # Resolve the ambiguous trailing blank in favour of the declared header:
        # counting it as context and dropping it are both legitimate readings, so
        # a header that matches either one is correct and only a header matching
        # neither is a defect.
        if (
            hunk.body
            and hunk.body[-1] == ""
            and (hunk.old_count, hunk.new_count) == (measured_old - 1, measured_new - 1)
        ):
            hunk.body.pop()
            measured_old -= 1
            measured_new -= 1
        shift = delta.get(owner, 0)
        expected_start = 1 if hunk.old_start == 0 else hunk.old_start + shift
        delta[owner] = shift + measured_new - measured_old
        if (measured_old, measured_new, expected_start) == (
            hunk.old_count,
            hunk.new_count,
            hunk.new_start,
        ):
            continue
        problems.append(
            f"{path.name}:{header_index + 1}: declared -{hunk.old_count},+{hunk.new_count}"
            f" at +{hunk.new_start}; measured -{measured_old},+{measured_new}"
            f" at +{expected_start}"
        )
        if fix:
            hunk.old_count = measured_old
            hunk.new_count = measured_new
            hunk.new_start = expected_start
            lines[header_index] = hunk.header()

    if fix and problems:
        path.write_text("\n".join(lines), encoding="utf-8")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--fix", action="store_true", help="rewrite disagreeing headers")
    args = parser.parse_args(argv)

    series = args.root / "patches" / "series"
    names = [
        line.partition("#")[0].strip()
        for line in series.read_text(encoding="utf-8").splitlines()
        if line.partition("#")[0].strip()
    ]

    problems: list[str] = []
    for name in names:
        problems.extend(check(args.root / "patches" / name, args.fix))

    if not problems:
        print(f"{len(names)} patches: every hunk header matches its body")
        return 0
    for problem in problems:
        print(f"{'fixed' if args.fix else 'ERROR'}: {problem}")
    return 0 if args.fix else 1


if __name__ == "__main__":
    raise SystemExit(main())
