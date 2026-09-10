import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("patch_times", Path(__file__).with_name("preserve-patch-mtimes.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PatchTimesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.patches = self.root / "patches"
        self.patches.mkdir()
        self.file = self.source / "file.cc"
        self.file.write_text("original\n")
        os.utime(self.file, ns=(1000000000, 2000000000))
        (self.patches / "one.patch").write_text("--- a/file.cc\n+++ b/file.cc\n")

    def test_identical_recreated_file_recovers_timestamp(self):
        saved = module.snapshot(self.source, self.patches)
        self.file.unlink()
        self.file.write_text("original\n")
        self.assertEqual(module.restore(self.source, saved), 1)
        self.assertEqual(self.file.stat().st_mtime_ns, 2000000000)

    def test_changed_bytes_are_never_backdated(self):
        saved = module.snapshot(self.source, self.patches)
        self.file.write_text("changed\n")
        modified = self.file.stat().st_mtime_ns
        self.assertEqual(module.restore(self.source, saved), 0)
        self.assertEqual(self.file.stat().st_mtime_ns, modified)

    def test_changed_modes_and_missing_files_are_not_restored(self):
        saved = module.snapshot(self.source, self.patches)
        self.file.chmod(0o700)
        self.assertEqual(module.restore(self.source, saved), 0)
        self.file.unlink()
        self.assertEqual(module.restore(self.source, saved), 0)

    def test_symlink_is_not_followed(self):
        saved = module.snapshot(self.source, self.patches)
        target = self.source / "other.cc"
        target.write_text("original\n")
        self.file.unlink()
        self.file.symlink_to(target)
        modified = target.stat().st_mtime_ns
        self.assertEqual(module.restore(self.source, saved), 0)
        self.assertEqual(target.stat().st_mtime_ns, modified)

    def test_escaping_patch_path_is_rejected(self):
        (self.patches / "bad.patch").write_text("+++ b/../outside\n")
        with self.assertRaises(ValueError):
            module.snapshot(self.source, self.patches)


if __name__ == "__main__":
    unittest.main()
