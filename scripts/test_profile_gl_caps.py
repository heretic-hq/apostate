#!/usr/bin/env python3
"""Check lossless viewport derivation and shared-context conflicts."""

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("to_profile", ROOT / "capture/derive/to_profile.py")
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)


def gl(**parameters):
    return {"parameters": parameters}


class GlProfileCapsTests(unittest.TestCase):
    def test_square_viewport_keeps_legacy_bound(self):
        measured = gl(MAX_VIEWPORT_DIMS=[16384, 16384])
        self.assertEqual(PROFILE.derive_gl_limits(measured, measured), {
            "MAX_VIEWPORT_DIMS": 16384,
            "MAX_VIEWPORT_DIMS_WIDTH": 16384,
            "MAX_VIEWPORT_DIMS_HEIGHT": 16384,
        })

    def test_rectangular_viewport_preserves_components(self):
        self.assertEqual(PROFILE.derive_gl_limits(gl(MAX_VIEWPORT_DIMS=[32767, 16384]), {}), {
            "MAX_VIEWPORT_DIMS_WIDTH": 32767,
            "MAX_VIEWPORT_DIMS_HEIGHT": 16384,
        })

    def test_malformed_viewport_is_rejected(self):
        for value in ([], [1], [1, 2, 3], [True, 2], [0, 2], [-1, 2],
                      [1.5, 2], [1, 2**31], 16384):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "MAX_VIEWPORT_DIMS"):
                PROFILE.derive_gl_limits(gl(MAX_VIEWPORT_DIMS=value), {})

    def test_context_conflict_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "MAX_VERTEX_UNIFORM_COMPONENTS"):
            PROFILE.derive_gl_limits(gl(MAX_VERTEX_UNIFORM_COMPONENTS=4096),
                                     gl(MAX_VERTEX_UNIFORM_COMPONENTS=16384))
        with self.assertRaisesRegex(ValueError, "MAX_VIEWPORT_DIMS"):
            PROFILE.derive_gl_limits(gl(MAX_VIEWPORT_DIMS=[16384, 32767]),
                                     gl(MAX_VIEWPORT_DIMS=[32767, 16384]))

    def test_scalar_limits_remain_exact(self):
        self.assertEqual(PROFILE.derive_gl_limits(gl(MAX_VERTEX_UNIFORM_COMPONENTS=4096),
                                                 gl(MAX_UNIFORM_BLOCK_SIZE=16384,
                                                    MAX_ELEMENT_INDEX=4294967294)), {
            "MAX_VERTEX_UNIFORM_COMPONENTS": 4096,
            "MAX_UNIFORM_BLOCK_SIZE": 16384,
            "MAX_ELEMENT_INDEX": 4294967294,
        })

    def test_absent_or_boolean_limit_does_not_create_claim(self):
        self.assertEqual(PROFILE.derive_gl_limits({}, {}), {})
        self.assertEqual(PROFILE.derive_gl_limits(gl(MAX_TEXTURE_SIZE=True), {}), {})


if __name__ == "__main__":
    unittest.main()
