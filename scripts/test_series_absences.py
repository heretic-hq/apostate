"""Prove the series gate tells a legitimate skip from an unchecked patch.

The gate classifies a patched file that produces no object on a platform, and
the only property that matters is that it discriminates: the same run has to
pass when the patched bytes demonstrably reached a compiler and fail when they
did not. A check that passes on both is worse than no check, because it ships
a passing badge with it.

So these fixtures drive the real scripts -- scripts/series_absences.py
discover over a real directory tree, then scripts/series-gate-report.py for the
verdict and the exit code -- rather than reimplementing either. No Chromium
build is involved: ninja's two answers, the compile database and the recorded
dependencies, are fixture text, which is exactly the seam where the gate's
judgement lives.

The include-only shape mirrors patch 0061: a fragment under a per-target
config directory, textually included by a source whose include operand is only
the tail of the fragment's path, and a sibling config directory holding a file
of the same name that a different target reads instead.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
DISCOVER = SCRIPTS / "series_absences.py"
REPORT = SCRIPTS / "series-gate-report.py"

UNIT = "vendor/config/linux/x64/libfoo/list_fragment.c"
DECOY = "vendor/config/win/x64/libfoo/list_fragment.c"
INCLUDER = "vendor/src/registry.c"
INCLUDER_OBJECT = "obj/vendor/vendor/registry.o"
PREFIX = "../../"


class GateFixture:
    """A throwaway repository, checkout and pair of ninja answers."""

    def __init__(self, stack):
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.checkout = self.root / "src"

        # Enough of a repository for the report script to stamp provenance.
        (self.root / "patches").mkdir()
        (self.root / "patches" / "series").write_text("0061-fixture.patch\n", encoding="utf-8")
        (self.root / "build").mkdir()
        (self.root / "build" / "CHROMIUM_VERSION").write_text("152.0.7977.83\n", encoding="utf-8")

        # A module with a BUILD.gn, which is the boundary discovery searches
        # within, two per-target config directories holding a same-named
        # fragment, and one real source that includes the fragment by its tail.
        for path, body in {
            "vendor/BUILD.gn": 'source_set("vendor") { sources = [ "src/registry.c" ] }\n',
            UNIT: "static const Codec *const list[] = {\n    &hevc,\n    NULL,\n};\n",
            DECOY: "static const Codec *const list[] = {\n    NULL,\n};\n",
            INCLUDER: '#include "libfoo/list_fragment.c"\n\nconst Codec *const *all(void) { return list; }\n',
        }.items():
            full = self.checkout / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(body, encoding="utf-8")

        # `ninja -t compdb`, reduced to what the gate keeps: every compiled
        # source and the objects it produces.
        self.source_index = self.root / "compdb-sources.tsv"
        self.source_index.write_text(f"{INCLUDER}\t{INCLUDER_OBJECT}\n", encoding="utf-8")

    def discover(self, units):
        """Run the real discovery pass over the fixture checkout."""
        out = subprocess.run(
            [sys.executable, str(DISCOVER), "discover",
             "--root", str(self.checkout),
             "--source-index", str(self.source_index), "--"] + units,
            capture_output=True, text=True, check=True,
        ).stdout
        path = self.root / "includers.tsv"
        path.write_text(out, encoding="utf-8")
        return path, out

    def run_gate(self, units, objects, includers, deps, absences, target="linux-x64", mode="compile"):
        """Run the real report script and return (exit code, stdout, report)."""
        units_file = self.root / "units.tsv"
        units_file.write_text(
            "".join(f"{unit}\t0061-fixture.patch\n" for unit in units), encoding="utf-8",
        )
        map_file = self.root / "map.tsv"
        map_file.write_text(
            f"#prefix\t{PREFIX}\n" + "".join(
                f"{unit}\t{' '.join(objects.get(unit, []))}\n" for unit in units
            ),
            encoding="utf-8",
        )
        deps_file = self.root / "deps.txt"
        deps_file.write_text(deps, encoding="utf-8")
        absences_file = self.root / "series-absences.tsv"
        absences_file.write_text(absences, encoding="utf-8")
        report_file = self.root / "report.json"

        result = subprocess.run(
            [sys.executable, str(REPORT),
             "--units", str(units_file), "--map", str(map_file),
             "--includers", str(includers) if includers else "",
             "--deps", str(deps_file), "--absences", str(absences_file),
             "--prefix", PREFIX, "--report", str(report_file),
             "--target", target, "--mode", mode,
             "--root", str(self.root)],
            capture_output=True, text=True,
        )
        report = json.loads(report_file.read_text(encoding="utf-8"))
        return result.returncode, result.stdout + result.stderr, report


def deps_naming(*sources):
    """`ninja -t deps` output for the includer object, naming these sources."""
    body = "".join(f"    {PREFIX}{source}\n" for source in sources)
    return f"{INCLUDER_OBJECT}: #deps {len(sources)}, deps mtime 1789510566658825994 (VALID)\n{body}"


def status_of(report, path):
    for unit in report["units"]:
        if unit["path"] == path:
            return unit["status"]
    raise AssertionError(f"{path} is not in the report")


class IncludeOnlyTests(unittest.TestCase):
    def setUp(self):
        self.stack = __import__("contextlib").ExitStack()
        self.addCleanup(self.stack.close)
        self.fixture = GateFixture(self.stack)

    def test_includer_compiles_and_read_it_so_the_unit_is_verified(self):
        """Fixture 1: the patched bytes reached a compiler. Pass."""
        includers, discovered = self.fixture.discover([UNIT])
        self.assertIn(INCLUDER, discovered, "discovery did not find the includer")

        code, output, report = self.fixture.run_gate(
            units=[UNIT], objects={}, includers=includers,
            deps=deps_naming(UNIT), absences="",
        )
        print("\n--- fixture 1: include-only, includer compiled and read it ---")
        print(output.rstrip())
        print(f"exit={code}")
        self.assertEqual(code, 0, "a verified include-only unit must not fail the gate")
        self.assertEqual(status_of(report, UNIT), "include-only")
        self.assertIn("verified", output)
        self.assertIn(INCLUDER, output)

    def test_includer_absent_from_the_compile_set_fails_naming_both(self):
        """Fixture 2: nothing compiled the includer, so nothing verified it. Fail."""
        # The includer produces no object on this target, so discovery finds it
        # but the compile set cannot contain it.
        self.fixture.source_index.write_text("", encoding="utf-8")
        includers, discovered = self.fixture.discover([UNIT])
        self.assertIn(INCLUDER, discovered)

        code, output, report = self.fixture.run_gate(
            units=[UNIT], objects={}, includers=includers, deps="", absences="",
        )
        print("\n--- fixture 2: includer not in the compile set ---")
        print(output.rstrip())
        print(f"exit={code}")
        self.assertEqual(code, 1, "an unverifiable include-only unit must fail the gate")
        self.assertEqual(status_of(report, UNIT), "unexplained")
        self.assertIn(UNIT, output)
        self.assertIn(INCLUDER, output)

    def test_includer_read_a_different_targets_copy_so_it_is_not_verified(self):
        """The discriminator: same includer, same name, different file.

        This is the real Windows case. allcodecs.c is compiled everywhere, and
        on Windows it reads win-msvc's codec_list.c, never the linux/x64 one a
        patch edits. Verification keyed on the includer existing would call
        this covered.
        """
        includers, _ = self.fixture.discover([UNIT])
        code, output, report = self.fixture.run_gate(
            units=[UNIT], objects={}, includers=includers,
            deps=deps_naming(DECOY), absences="",
        )
        print("\n--- discriminator: includer compiled, but read another config's copy ---")
        print(output.rstrip())
        print(f"exit={code}")
        self.assertEqual(code, 1)
        self.assertEqual(status_of(report, UNIT), "unexplained")

        # Declared as platform-scoped, the same run is a legitimate skip.
        code, output, report = self.fixture.run_gate(
            units=[UNIT], objects={}, includers=includers,
            deps=deps_naming(DECOY),
            absences=f"{UNIT}\tlinux-x64\tplatform\tonly the win config dir is on this include path\n",
        )
        self.assertEqual(code, 0)
        self.assertEqual(status_of(report, UNIT), "absent-platform")

    def test_membership_only_run_still_reports_a_definitive_negative(self):
        """A dependency record that names another file is an answer, not a gap.

        Regression: `--list` compiles nothing, so an includer with no record
        yet is legitimately `unchecked`. But when ninja *does* hold a record
        and this unit is not in it, the includer has already answered, and
        excusing that as `unchecked` let a real negative pass on the one mode
        whose name sounds harmless. Caught by running the real driver against
        the macOS graph, where allcodecs.o records mac/arm64's copy.
        """
        includers, _ = self.fixture.discover([UNIT])

        # A record exists and names the other config's file: definitive.
        code, output, report = self.fixture.run_gate(
            units=[UNIT], objects={}, includers=includers,
            deps=deps_naming(DECOY), absences="", mode="list",
        )
        self.assertEqual(code, 1, "a definitive negative must fail even in --list")
        self.assertEqual(status_of(report, UNIT), "unexplained")

        # No record at all: nothing has been asked yet, so nothing is claimed.
        code, output, report = self.fixture.run_gate(
            units=[UNIT], objects={}, includers=includers,
            deps="", absences="", mode="list",
        )
        self.assertEqual(code, 0, "--list must not invent a finding it did not measure")
        self.assertEqual(status_of(report, UNIT), "unchecked")


class UnexplainedTests(unittest.TestCase):
    def setUp(self):
        self.stack = __import__("contextlib").ExitStack()
        self.addCleanup(self.stack.close)
        self.fixture = GateFixture(self.stack)

    def test_a_file_in_no_category_fails_as_unexplained(self):
        """Fixture 3: no object, no includer, no declaration. Fail."""
        orphan = "vendor/src/orphan.c"
        (self.fixture.checkout / orphan).write_text("int orphan(void) { return 1; }\n", encoding="utf-8")
        includers, discovered = self.fixture.discover([orphan])
        self.assertEqual(discovered, "", "nothing includes the orphan")

        code, output, report = self.fixture.run_gate(
            units=[orphan], objects={}, includers=includers, deps="", absences="",
        )
        print("\n--- fixture 3: patched file in no category ---")
        print(output.rstrip())
        print(f"exit={code}")
        self.assertEqual(code, 1, "an unaccounted-for patched file must fail the gate")
        self.assertEqual(status_of(report, orphan), "unexplained")
        self.assertIn("UNEXPLAINED", output)
        self.assertIn(orphan, output)

    def test_a_declared_platform_absence_is_a_legitimate_skip(self):
        """Fixture 4: platform-scoped and declared. Pass."""
        windows_only = "vendor/src/registry_win.c"
        (self.fixture.checkout / windows_only).write_text("int win(void) { return 1; }\n", encoding="utf-8")
        includers, _ = self.fixture.discover([windows_only])

        absences = (
            "# declared absences\n"
            f"{windows_only}\tlinux-*,macos-*\tplatform\tvendor/BUILD.gn adds it under `if (is_win)`\n"
        )
        code, output, report = self.fixture.run_gate(
            units=[windows_only], objects={}, includers=includers, deps="", absences=absences,
        )
        print("\n--- fixture 4: genuinely platform-inapplicable, declared ---")
        print(output.rstrip())
        print(f"exit={code}")
        self.assertEqual(code, 0, "a declared platform absence is a legitimate skip")
        self.assertEqual(status_of(report, windows_only), "absent-platform")
        self.assertEqual(report["counts"]["absent-platform"], 1)

    def test_a_declaration_the_run_disproves_is_stale_and_fails(self):
        """The other direction: the list cannot rot into a blanket exemption."""
        compiled = "vendor/src/registry.c"
        absences = f"{compiled}\tlinux-*\tplatform\tclaimed Windows-only, but this target compiles it\n"
        code, output, report = self.fixture.run_gate(
            units=[compiled], objects={compiled: [INCLUDER_OBJECT]},
            includers=None, deps="", absences=absences,
        )
        print("\n--- stale declaration ---")
        print(output.rstrip())
        print(f"exit={code}")
        self.assertEqual(code, 1)
        self.assertIn("STALE", output)
        self.assertEqual(len(report["stale_declarations"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
