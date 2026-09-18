"""Guard the two boundaries that decide whether check 7 is trustworthy or noise.

Check 7 resolves every Chromium source path this repository cites. It used to
read ledger/*.jsonl only, which left the great majority of citations written
here unprotected — every path in a patch header, every GN reference in
scripts/series-absences.tsv, every path in docs. Two wrong citations on one day
were caught by a person reading carefully, which is not a control, so the check
was widened to those three sources.

Widening a path check is only safe if it keeps saying nothing about the two
kinds of text that look like citations and are not:

  * A patch's HUNK BODY. It carries `#include "third_party/..."`, context lines
    naming real files, and added source mentioning paths it does not cite. That
    is the code being changed, not evidence being offered. Extracting from it
    would produce hundreds of findings against a clean tree, and a check that
    cries wolf gets switched off inside a day.
  * MARKDOWN PROSE. Docs legitimately name a path illustratively. Only fenced
    blocks and inline code spans are read, which is where this repository puts
    a path it expects a reader to open.

Both rules fail open — they make the check say less, never more — so a
regression in either is invisible in a passing run. That is exactly the kind of
thing that needs a test rather than a comment.
"""

import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPT = Path(__file__).resolve().parent / "check-schema-wiring.py"
SPEC = importlib.util.spec_from_file_location("check_schema_wiring", SCRIPT)
WIRING = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["check_schema_wiring"] = WIRING
SPEC.loader.exec_module(WIRING)

# A first segment that names an entry at the checkout root is what separates a
# Chromium citation from a fragment relative to something the prose said
# earlier. These stand in for `{entry.name for entry in WORKSPACE.iterdir()}`.
ROOTS = {"base", "media", "third_party", "ui"}


def extract(text, rel="patches/0001-x.patch"):
    """Paths cited_paths() pulls out of one blob, as the check would see it."""
    lines = [(rel, n, line, None) for n, line in enumerate(text.splitlines(), 1)]
    return set(WIRING.cited_paths(lines, ROOTS))


class PatchHeaderBoundaryTests(unittest.TestCase):
    def test_header_prose_is_read_and_the_hunk_body_is_not(self):
        patch = (
            "Subject: fonts: something\n"
            "\n"
            "The table in base/header_citation.cc is the evidence.\n"
            "\n"
            "diff --git a/base/changed.cc b/base/changed.cc\n"
            "--- a/base/changed.cc\n"
            "+++ b/base/changed.cc\n"
            "@@ -1,3 +1,4 @@\n"
            ' #include "base/in_hunk_context.cc"\n'
            '+#include "base/in_hunk_added.cc"\n'
            "+// see media/in_hunk_comment.cc for the table\n"
        )
        prose = WIRING.patch_header_prose(patch)
        self.assertEqual([line for _, line in prose][-1], "")
        self.assertEqual(len(prose), 4, "prose must stop at the first diff line")

        cited = extract("".join(f"{line}\n" for _, line in prose))
        self.assertIn("base/header_citation.cc", cited)
        for path in ("base/in_hunk_context.cc", "base/in_hunk_added.cc",
                     "media/in_hunk_comment.cc"):
            self.assertNotIn(path, cited, f"{path} is patch content, not a citation")

    def test_a_patch_that_opens_on_a_diff_line_has_no_prose(self):
        """0061 opens on `diff --git`. Empty is the right answer, not a reason to look harder."""
        patch = ("diff --git a/base/x.cc b/base/x.cc\n"
                 "--- a/base/x.cc\n"
                 "+++ b/base/x.cc\n"
                 "@@ -1 +1 @@\n"
                 '-#include "base/old_path.cc"\n'
                 '+#include "base/new_path.cc"\n')
        self.assertEqual(WIRING.patch_header_prose(patch), [])

    def test_a_bare_triple_dash_does_not_end_the_prose(self):
        """git format-patch writes `---` before a diffstat; `--- ` opens a diff."""
        patch = ("Subject: x\n"
                 "\n"
                 "---\n"
                 " base/diffstat_citation.cc | 2 +-\n"
                 "\n"
                 "diff --git a/base/diffstat_citation.cc b/base/diffstat_citation.cc\n"
                 "--- a/base/diffstat_citation.cc\n")
        prose = WIRING.patch_header_prose(patch)
        text = "".join(f"{line}\n" for _, line in prose)
        self.assertIn("base/diffstat_citation.cc", extract(text),
                      "a diffstat is prose and its paths are citations")


class MarkdownBoundaryTests(unittest.TestCase):
    def test_code_is_read_and_bare_prose_is_not(self):
        doc = (
            "# Title\n"
            "\n"
            "Illustratively, a file such as base/bare_prose.cc would be scanned.\n"
            "\n"
            "The real one is `base/inline_span.cc` today.\n"
            "\n"
            "```text\n"
            "base/fenced_block.cc\n"
            "```\n"
            "\n"
            "After the fence, media/after_fence.cc is prose again.\n"
        )
        pairs = WIRING.markdown_citations(doc)
        cited = extract("".join(f"{text}\n" for _, text in pairs), rel="docs/X.md")
        self.assertIn("base/inline_span.cc", cited)
        self.assertIn("base/fenced_block.cc", cited)
        self.assertNotIn("base/bare_prose.cc", cited, "prose must not be scanned")
        self.assertNotIn("media/after_fence.cc", cited, "the fence must close")

    def test_two_spans_on_one_line_stay_two_paths(self):
        doc = "Compare `base/first.cc` with `media/second.cc` here.\n"
        cited = extract("".join(f"{t}\n" for _, t in WIRING.markdown_citations(doc)),
                        rel="docs/X.md")
        self.assertEqual(cited, {"base/first.cc", "media/second.cc"})


class ExtractorGuardTests(unittest.TestCase):
    """The three guards the widening inherits rather than reimplements."""

    def test_a_fragment_relative_to_earlier_prose_is_not_resolved(self):
        cited = extract("both in win/font_cache_skia_win.cc and reached from there\n")
        self.assertEqual(cited, set(), "`win` is not an entry at the checkout root")

    def test_the_real_0099_case_one_rooted_and_one_fragment_on_one_line(self):
        """Why the widening caught 0099 and said nothing about the line above it.

        `skia` IS an entry at a Chromium checkout root, so `skia/font_cache_skia.cc`
        resolves as a Chromium path and was wrong — the symbol it names,
        WindowsStretchSuffix, is added by patches/0048 to
        third_party/blink/renderer/platform/fonts/skia/font_cache_skia.cc.
        `win/font_cache_skia_win.cc` on the line above is a fragment relative to
        what the prose already said, and this pass neither can nor should
        resolve it.
        """
        roots = ROOTS | {"skia"}
        line = "skia/font_cache_skia.cc versus win/font_cache_skia_win.cc\n"
        cited = set(WIRING.cited_paths([("patches/0099.patch", 1, line, None)], roots))
        self.assertEqual(cited, {"skia/font_cache_skia.cc"})

    def test_a_googlesource_permalink_is_not_a_citation(self):
        line = ("https://chromium.googlesource.com/chromium/src/+/abc123/"
                "base/system/sys_info_win.cc\n")
        self.assertEqual(extract(line), set())

    def test_a_gni_extension_is_not_truncated_to_gn(self):
        cited = extract("third_party/ffmpeg/ffmpeg_generated.gni:90 lists it\n")
        self.assertEqual(cited, {"third_party/ffmpeg/ffmpeg_generated.gni"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
