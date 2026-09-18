"""Prove the symbol closure check discriminates, and cannot pass on nothing.

scripts/series-symbol-closure.py answers one question per library: does
anything the series added to a GN list reference a symbol nothing can supply.
Patch 0109 is why it exists -- its arm64 leg adds nine objects for ffmpeg's
HEVC SIMD path, three of them under libavcodec/aarch64/h26x with no "hevc" in
their names, and dropping those three leaves 355 undefined ff_hevc_put_hevc_*
symbols while every translation unit still compiles clean.

Three properties decide whether the check is worth running, and all three fail
silently if they regress:

  * It FAILS when a required object is missing. Demonstrated against the real
    macos-arm64 libbase.a by omitting one member at a time; omitting
    host_capability.o reports base::apostate::ProbeHostCapability and two
    others and exits 1.
  * It does NOT fail on a clean tree. The same real library references
    BoringSSL's SHA256_Update, libc's fputs and __stderrp, and libc++'s
    operator+, none of which is base's to define. An earlier version reported
    all of them.
  * A closure that ran on nothing is a FAILURE, not a pass. A missing archive,
    an archive with no members, or a library none of whose members are the
    objects we meant to check are all reported. This is the
    probe-trusting-its-own-exit-code defect, which this project has hit
    repeatedly.

The symbol readers are substituted here so the logic can be exercised without
a build; they are covered end to end against the real archive separately.
"""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "series-symbol-closure.py"
SPEC = importlib.util.spec_from_file_location("series_symbol_closure", SCRIPT)
CLOSURE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["series_symbol_closure"] = CLOSURE
SPEC.loader.exec_module(CLOSURE)

ARCHIVE = "obj/third_party/ffmpeg/libffmpeg_internal.a"
PREFIX = "obj/third_party/ffmpeg/"
ADDED = "ffmpeg_internal/hevcdsp_init_aarch64.o"
NEON = "ffmpeg_internal/hevcdsp_idct_neon.o"
OTHER = "ffmpeg_internal/allcodecs.o"


def library(objects=(ADDED,)):
    return {
        "third_party/ffmpeg:ffmpeg_internal": {
            "archive": ARCHIVE, "prefix": PREFIX,
            "directory": "third_party/ffmpeg",
            "objects": {PREFIX + o for o in objects},
            "sources": {"third_party/ffmpeg/libavcodec/aarch64/hevcdsp_init_aarch64.c"},
            "compiled": set(),
        }
    }


class ClosureLogicTests(unittest.TestCase):
    """closure() with the two nm readers substituted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        (self.out / PREFIX).mkdir(parents=True)
        (self.out / ARCHIVE).write_bytes(b"")          # existence is all closure() reads
        self._real_symbols = CLOSURE.read_symbols
        self._real_classify = CLOSURE.classify_candidates
        self.addCleanup(setattr, CLOSURE, "read_symbols", self._real_symbols)
        self.addCleanup(setattr, CLOSURE, "classify_candidates", self._real_classify)

    def install(self, defined, undefined, supplied=(), referenced=()):
        CLOSURE.read_symbols = lambda nm, archive, out: (
            {k: set(v) for k, v in defined.items()},
            {k: set(v) for k, v in undefined.items()},
        )
        CLOSURE.classify_candidates = lambda nm, out, candidates, own: (
            set(candidates) & set(supplied), set(candidates) & set(referenced),
        )

    def test_a_missing_neon_object_is_reported_and_fails(self):
        """The 0109 shape: the init object calls into assembly nothing defines."""
        self.install(
            defined={ADDED: {"ff_hevc_dsp_init_aarch64"}, OTHER: {"ff_hevc_profiles"}},
            undefined={ADDED: {"ff_hevc_put_hevc_qpel_h4_8_neon", "ff_hevc_profiles"},
                       OTHER: set()},
        )
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("SYMBOL CLOSURE INCOMPLETE", findings[0])
        self.assertIn("ff_hevc_put_hevc_qpel_h4_8_neon", findings[0])
        # ff_hevc_profiles is defined by another member, so it is not a hole.
        self.assertEqual(rows[0]["holes"], ["ff_hevc_put_hevc_qpel_h4_8_neon"])

    def test_a_platform_symbol_is_not_a_hole(self):
        """fputs and __stderrp are referenced all over the build and defined nowhere."""
        self.install(
            defined={ADDED: set(), OTHER: set()},
            undefined={ADDED: {"fputs", "__stderrp"}, OTHER: set()},
            referenced={"fputs", "__stderrp"},
        )
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["holes"], [])
        self.assertEqual(rows[0]["referenced"], ["__stderrp", "fputs"])

    def test_a_symbol_another_archive_defines_is_not_a_hole(self):
        self.install(
            defined={ADDED: set(), OTHER: set()},
            undefined={ADDED: {"SHA256_Update"}, OTHER: set()},
            supplied={"SHA256_Update"},
        )
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["supplied"], ["SHA256_Update"])

    def test_a_symbol_only_other_members_need_is_not_this_series_problem(self):
        """The third term: an undefined symbol the pre-existing members share."""
        self.install(
            defined={ADDED: set(), OTHER: set()},
            undefined={ADDED: {"av_log"}, OTHER: {"av_log"}},
        )
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["unresolved"], [])

    def test_omitting_a_member_drops_its_references_as_well_as_its_definitions(self):
        """A source never added to the GN list contributes neither side.

        Omitting the neon object must surface the init object's call into it,
        and must NOT surface the neon object's own undefined symbols -- those
        references would not exist either.
        """
        self.install(
            defined={ADDED: set(), NEON: {"ff_hevc_put_hevc_qpel_h4_8_neon"}, OTHER: set()},
            undefined={ADDED: {"ff_hevc_put_hevc_qpel_h4_8_neon"},
                       NEON: {"only_the_neon_object_calls_this"}, OTHER: set()},
        )
        libs = library(objects=(ADDED, NEON))
        findings, rows = CLOSURE.closure(libs, "nm", str(self.out), omit=("hevcdsp_idct_neon.o",))
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(rows[0]["holes"], ["ff_hevc_put_hevc_qpel_h4_8_neon"])
        self.assertNotIn("only_the_neon_object_calls_this", rows[0]["unresolved"])

    def test_a_clean_library_passes(self):
        self.install(
            defined={ADDED: {"ff_hevc_dsp_init_aarch64"},
                     NEON: {"ff_hevc_put_hevc_qpel_h4_8_neon"}, OTHER: set()},
            undefined={ADDED: {"ff_hevc_put_hevc_qpel_h4_8_neon"}, NEON: set(), OTHER: set()},
        )
        findings, rows = CLOSURE.closure(library(objects=(ADDED, NEON)), "nm", str(self.out))
        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["holes"], [])


class RanOnNothingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self._real = CLOSURE.read_symbols
        self.addCleanup(setattr, CLOSURE, "read_symbols", self._real)

    def test_a_missing_archive_fails(self):
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(rows, [])
        self.assertIn("CLOSURE RAN ON NOTHING", findings[0])
        self.assertIn("does not exist", findings[0])

    def test_an_archive_with_no_members_fails(self):
        (self.out / PREFIX).mkdir(parents=True)
        (self.out / ARCHIVE).write_bytes(b"")
        CLOSURE.read_symbols = lambda *a: ({}, {})
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(rows, [])
        self.assertIn("no members", findings[0])

    def test_a_library_without_the_added_objects_fails(self):
        """Closing over the wrong library is not a clean closure."""
        (self.out / PREFIX).mkdir(parents=True)
        (self.out / ARCHIVE).write_bytes(b"")
        CLOSURE.read_symbols = lambda *a: ({OTHER: {"x"}}, {OTHER: set()})
        findings, rows = CLOSURE.closure(library(), "nm", str(self.out))
        self.assertEqual(rows, [])
        self.assertIn("none of the 1 object(s) the patch added", findings[0])


class GnAdditionTests(unittest.TestCase):
    """What counts as a source the series added, and to which target."""

    def write_series(self, body):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        (root / "patches").mkdir()
        (root / "patches" / "series").write_text("0001-x.patch\n", encoding="utf-8")
        (root / "patches" / "0001-x.patch").write_text(body, encoding="utf-8")
        return CLOSURE.gn_source_additions(root)

    def test_a_source_resolves_against_the_build_files_directory(self):
        added = self.write_series(
            "Subject: x\n\n"
            "diff --git a/third_party/ffmpeg/ffmpeg_generated.gni b/third_party/ffmpeg/ffmpeg_generated.gni\n"
            "--- a/third_party/ffmpeg/ffmpeg_generated.gni\n"
            "+++ b/third_party/ffmpeg/ffmpeg_generated.gni\n"
            "@@ -1 +1,3 @@\n"
            "+  ffmpeg_c_sources += [\n"
            '+    "libavcodec/hevc/data.c",\n'
            "+  ]\n"
        )
        self.assertIn("third_party/ffmpeg/libavcodec/hevc/data.c", added)

    def test_a_generated_source_is_not_a_file_to_look_for(self):
        added = self.write_series(
            "Subject: x\n\n"
            "diff --git a/base/BUILD.gn b/base/BUILD.gn\n"
            "--- a/base/BUILD.gn\n"
            "+++ b/base/BUILD.gn\n"
            "@@ -1 +1,3 @@\n"
            "+  sources += [\n"
            '+    "$target_gen_dir/apostate/dispersion_tables.cc",\n'
            '+    "apostate/seed.cc",\n'
            "+  ]\n"
        )
        self.assertEqual(set(added), {"base/apostate/seed.cc"})

    def test_a_list_opener_does_not_leak_across_hunks(self):
        """0081's shape: sources in one hunk, an action's outputs in another."""
        added = self.write_series(
            "Subject: x\n\n"
            "diff --git a/base/BUILD.gn b/base/BUILD.gn\n"
            "--- a/base/BUILD.gn\n"
            "+++ b/base/BUILD.gn\n"
            "@@ -1 +1,2 @@\n"
            "+  sources += [\n"
            '+    "apostate/seed.cc",\n'
            "@@ -50 +51,3 @@\n"
            '+    "apostate/not_a_source.cc",\n'
        )
        self.assertEqual(set(added), {"base/apostate/seed.cc"})

    def test_a_target_the_patch_declares_is_recorded(self):
        added = self.write_series(
            "Subject: x\n\n"
            "diff --git a/third_party/angle/src/tests/BUILD.gn b/third_party/angle/src/tests/BUILD.gn\n"
            "--- a/third_party/angle/src/tests/BUILD.gn\n"
            "+++ b/third_party/angle/src/tests/BUILD.gn\n"
            "@@ -1 +1,5 @@\n"
            "+  angle_test(\"apostate_large_index_tests\") {\n"
            "+    sources = [\n"
            '+      "test_utils/ANGLETest.cpp",\n'
            "+    ]\n"
        )
        source = "third_party/angle/src/tests/test_utils/ANGLETest.cpp"
        self.assertEqual({t for *_, t in added[source]}, {"apostate_large_index_tests"})

    def test_a_declared_target_restricts_which_library_is_measured(self):
        """The real angle case: a shared source, added to a target this platform lacks.

        ANGLETest.cpp is compiled into three other angle test libraries. Closing
        over those would report their unrelated undefined symbols as this
        series' fault.
        """
        source = "third_party/angle/src/tests/test_utils/ANGLETest.cpp"
        added = {source: {("0063.patch", "third_party/angle/src/tests/BUILD.gn",
                           "sources", "apostate_large_index_tests")}}
        index = {source: ["obj/third_party/angle/src/tests/angle_end2end_tests/ANGLETest.o"]}
        libraries, skipped = CLOSURE.affected_libraries(added, index)
        self.assertEqual(libraries, {})
        self.assertEqual(len(skipped), 1)
        self.assertIn("did not declare", skipped[0][1])

    def test_an_append_to_a_pre_existing_list_is_not_restricted(self):
        """ffmpeg_c_sources belongs to a target no patch declares, so any object counts."""
        source = "third_party/ffmpeg/libavcodec/hevc/data.c"
        added = {source: {("0109.patch", "third_party/ffmpeg/ffmpeg_generated.gni",
                           "ffmpeg_c_sources", "")}}
        index = {source: ["obj/third_party/ffmpeg/ffmpeg_internal/data.o"]}
        libraries, skipped = CLOSURE.affected_libraries(added, index)
        self.assertEqual(list(libraries), ["third_party/ffmpeg:ffmpeg_internal"])
        self.assertEqual(
            libraries["third_party/ffmpeg:ffmpeg_internal"]["archive"],
            "obj/third_party/ffmpeg/libffmpeg_internal.a",
        )
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
