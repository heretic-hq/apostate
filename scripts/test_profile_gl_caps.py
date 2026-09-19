#!/usr/bin/env python3
"""Check lossless viewport derivation and shared-context conflicts."""

import contextlib
import importlib.util
import io
import json
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

    def test_the_five_dropped_parameters_are_derived(self):
        # Every one of these was measured by the capture and discarded, because
        # this pipeline accepted only MAX_-prefixed values above zero plus one
        # hardcoded range. They are the same five the anchor pipeline dropped.
        self.assertEqual(PROFILE.derive_gl_limits(gl(
            ALIASED_LINE_WIDTH_RANGE=[1, 1],
            MAX_SERVER_WAIT_TIMEOUT=0,
            MIN_PROGRAM_TEXEL_OFFSET=-8,
            UNIFORM_BUFFER_OFFSET_ALIGNMENT=256,
        ), {}), {
            "ALIASED_LINE_WIDTH_RANGE_MIN": 1,
            "ALIASED_LINE_WIDTH_RANGE_MAX": 1,
            "MAX_SERVER_WAIT_TIMEOUT": 0,
            "MIN_PROGRAM_TEXEL_OFFSET": -8,
            "UNIFORM_BUFFER_OFFSET_ALIGNMENT": 256,
        })

    def test_a_malformed_line_width_range_is_rejected(self):
        # The same validation the point size range already gets: a recognised
        # name with a malformed measurement is an error, not a silent skip.
        for value in ([], [1], [1, 2, 3], [2, 1], [0, 2], [-1, 2], 1):
            with self.subTest(value=value), \
                    self.assertRaisesRegex(ValueError, "ALIASED_LINE_WIDTH_RANGE"):
                PROFILE.derive_gl_limits(gl(ALIASED_LINE_WIDTH_RANGE=value), {})

    def test_an_unrecognised_parameter_is_skipped_not_rejected(self):
        # A capture carries more than the profile can serve. An unknown name is
        # not an error, and a WebGL-specification constant is not a GL limit.
        self.assertEqual(PROFILE.derive_gl_limits(
            gl(VIEWPORT=[0, 0, 300, 150], MAX_CLIENT_WAIT_TIMEOUT_WEBGL=0), {}), {})


RESOLVER_SPEC = importlib.util.spec_from_file_location(
    "profile_resolver", ROOT / "scripts/profile_resolver.py")
RESOLVER = importlib.util.module_from_spec(RESOLVER_SPEC)
RESOLVER_SPEC.loader.exec_module(RESOLVER)

ANCHOR_PATH = ROOT / "corpus/anchors/windows-d3d11-intel-79dfeb5b4f99.json"

#: Every parameter a live detector found the shipped build serving from the
#: host instead of from this anchor, and the value the anchor measured. A
#: two-element range is checked through the scalar keys it decomposes into,
#: because that is what the loader and the serving hooks read.
DETECTOR_FLAGGED = {
    "ALIASED_LINE_WIDTH_RANGE_MIN": 1,
    "ALIASED_LINE_WIDTH_RANGE_MAX": 1,
    "ALIASED_POINT_SIZE_RANGE_MIN": 1,
    "ALIASED_POINT_SIZE_RANGE_MAX": 1024,
    "MAX_VIEWPORT_DIMS_WIDTH": 32767,
    "MAX_VIEWPORT_DIMS_HEIGHT": 32767,
    "MAX_VERTEX_UNIFORM_VECTORS": 4096,
    "MAX_ELEMENTS_INDICES": 2147483647,
    "MAX_ELEMENTS_VERTICES": 2147483647,
    "MAX_PROGRAM_TEXEL_OFFSET": 7,
    "MAX_SAMPLES": 16,
    "MAX_SERVER_WAIT_TIMEOUT": 0,
    "MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS": 120,
    "MAX_VERTEX_UNIFORM_COMPONENTS": 16384,
    "MIN_PROGRAM_TEXEL_OFFSET": -8,
    "UNIFORM_BUFFER_OFFSET_ALIGNMENT": 256,
}


def anchor_limits(record):
    """The limit map one anchor record produces, through the shared rule."""
    limits = {}
    for context in ("webgl1", "webgl2"):
        section = record["capability_cluster"].get(context) or {}
        for name, value in (section.get("parameters") or {}).items():
            for key, exact in RESOLVER.gl_limit_entries(name, value):
                limits[key] = exact
    return limits


class AnchorCapabilityLayerTests(unittest.TestCase):
    """The anchor capability layer, which shipped serving none of itself.

    The filter these cover accepted a scalar only when it was MAX_-prefixed
    and at least 1. That silently dropped five measured parameters from every
    anchor, and a detector read the gap in one call: an ANGLE Direct3D11
    renderer string beside a 16384x16384 viewport.
    """

    def setUp(self):
        self.record = json.loads(ANCHOR_PATH.read_text(encoding="utf-8"))

    def test_every_detector_flagged_limit_is_carried(self):
        limits = anchor_limits(self.record)
        for key, expected in DETECTOR_FLAGGED.items():
            with self.subTest(limit=key):
                self.assertEqual(limits.get(key), expected)

    def test_a_zero_and_a_negative_measurement_survive(self):
        # The two values a positive lower bound excluded. Guarded separately
        # because both look like "missing" to a filter and neither is.
        self.assertEqual(RESOLVER.gl_limit_entries("MAX_SERVER_WAIT_TIMEOUT", 0),
                         [("MAX_SERVER_WAIT_TIMEOUT", 0)])
        self.assertEqual(RESOLVER.gl_limit_entries("MIN_PROGRAM_TEXEL_OFFSET", -8),
                         [("MIN_PROGRAM_TEXEL_OFFSET", -8)])

    def test_a_measured_fraction_survives_on_a_float_valued_parameter(self):
        # The Linux/Vulkan anchor's point size max is 2047.9375 -- 2047 + 15/16
        # from a four-bit subpixel point size. This used to be dropped for not
        # being integral, and the host's [1, 256] was served under an NVIDIA
        # renderer string. Rounding to 2047 would be just as wrong: it is a
        # limit no measurement produced.
        self.assertEqual(
            RESOLVER.gl_limit_entries("ALIASED_POINT_SIZE_RANGE", [1, 2047.9375]),
            [("ALIASED_POINT_SIZE_RANGE_MIN", 1),
             ("ALIASED_POINT_SIZE_RANGE_MAX", 2047.9375)])
        self.assertEqual(
            RESOLVER.gl_limit_entries("MAX_TEXTURE_LOD_BIAS", 1.75),
            [("MAX_TEXTURE_LOD_BIAS", 1.75)])

    def test_a_fraction_is_refused_on_a_count(self):
        # Only the three parameters GL itself defines as float-valued may
        # carry one. A texture size with a fraction in it is not a
        # measurement this can represent, and rounding would invent one.
        self.assertEqual(RESOLVER.gl_limit_entries("MAX_TEXTURE_SIZE", 16384.5), [])
        self.assertEqual(
            RESOLVER.gl_limit_entries("UNIFORM_BUFFER_OFFSET_ALIGNMENT", 256.5), [])

    def test_an_integral_measurement_stays_an_int(self):
        # Byte-stability: a whole number keeps its int type even on a
        # float-valued parameter, so no already-resolved profile changes
        # shape or serialised bytes.
        entries = RESOLVER.gl_limit_entries("ALIASED_POINT_SIZE_RANGE", [1, 1024])
        self.assertEqual(entries, [("ALIASED_POINT_SIZE_RANGE_MIN", 1),
                                   ("ALIASED_POINT_SIZE_RANGE_MAX", 1024)])
        self.assertIsInstance(entries[1][1], int)

    def test_a_webgl_specification_constant_is_not_a_gl_limit(self):
        # Answered by Blink, not the GL binding layer. Admitting it would
        # declare a profile key nothing serves.
        self.assertEqual(
            RESOLVER.gl_limit_entries("MAX_CLIENT_WAIT_TIMEOUT_WEBGL", 0), [])

    def test_an_anchor_with_no_limits_is_refused(self):
        # The defect itself: a valid anchor, a real renderer string, and no
        # capability table. Every consumer used to fall through to the host.
        for context in ("webgl1", "webgl2"):
            self.record["capability_cluster"][context]["parameters"] = {}
        with self.assertRaisesRegex(RESOLVER.ResolverError, "no GL capability limits"):
            RESOLVER._anchor_capability_layer(self.record, "ANGLE (Intel, ...)")


class ProducersAgreeTests(unittest.TestCase):
    """The two pipelines that turn a measurement into gl_limits must agree.

    ``capture/derive/to_profile.py`` builds a profile from a fresh capture and
    the anchor layer builds one from the corpus. They ran on separate rules
    until they were made to share one, and the drift was silent: a capture
    profile and an anchor profile for the same device carried different keys.
    Asserted over every anchor rather than one, since an anchor is exactly a
    measurement both are expected to handle identically.
    """

    def test_capture_and_anchor_pipelines_produce_the_same_limits(self):
        for path in sorted((ROOT / "corpus/anchors").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            cluster = record.get("capability_cluster")
            if not cluster:
                continue
            with self.subTest(anchor=record["anchor_id"]):
                # The Linux/Vulkan anchor's nonintegral point-size endpoint
                # warns on stderr by design; captured so the suite stays quiet.
                noise = io.StringIO()
                with contextlib.redirect_stderr(noise):
                    captured = PROFILE.derive_gl_limits(
                        cluster.get("webgl1") or {}, cluster.get("webgl2") or {})
                anchored = anchor_limits(record)
                # The square-viewport scalar is the one deliberate difference:
                # to_profile emits it for older binaries, the anchor layer does
                # not, and patch 0039 reads it only as a fallback.
                captured.pop("MAX_VIEWPORT_DIMS", None)
                self.assertEqual(captured, anchored)


if __name__ == "__main__":
    unittest.main()
