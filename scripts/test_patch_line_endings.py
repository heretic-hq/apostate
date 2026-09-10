"""Check the real Git whitespace gate with pinned-style CRLF sources."""
import difflib
from pathlib import Path
import subprocess
import tempfile
import unittest


class PatchLineEndingTests(unittest.TestCase):
    def check_patch(self, added):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = "first\r\nlast\r\n"
            after = "first\r\n" + added + "last\r\n"
            (root / "source.rs").write_bytes(before.encode())
            patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                 fromfile="a/source.rs", tofile="b/source.rs"))
            return subprocess.run([
                "git", "-c", "core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol",
                "apply", "--check", "--whitespace=error", "-"
            ], input=patch.encode(), cwd=root, capture_output=True)

    def test_crlf_is_a_valid_line_ending(self):
        result = self.check_patch("new code\r\n")
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_real_trailing_spaces_and_tabs_still_fail(self):
        for added in ["new code \r\n", "new code\t\r\n", "new code \n"]:
            with self.subTest(added=added):
                result = self.check_patch(added)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"whitespace", result.stderr)


if __name__ == "__main__":
    unittest.main()
