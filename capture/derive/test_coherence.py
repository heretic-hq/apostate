"""Regression checks for the coherence gate.

Every test here defends a decision that a later simplification would silently
reverse, and each one is a shape that was measured on stock Chrome rather than
imagined: the Xvfb window geometry, the 1/64 px layout artifact, UTC on a
UTC-configured host, and two runs that claim the same audio hardware.
"""

import contextlib
import copy
import io
import unittest

import coherence


def window(**over):
    """The geometry measured on stock Chrome 151 headed under Xvfb 1920x1080."""
    value = {"screenX": 10, "screenY": 10, "outerWidth": 945, "outerHeight": 1060,
             "innerWidth": 945, "innerHeight": 973, "availLeft": 0, "availTop": 0,
             "availWidth": 1920, "availHeight": 1080, "screenWidth": 1920,
             "screenHeight": 1080, "isExtended": False, "devicePixelRatio": 1}
    value.update(over)
    return value


def layout(**over):
    samples = [{"id": "offscreen", "declared": {"left": -10000, "top": 0},
                "computed": {"left": "-10000px", "top": "0px"},
                "rect": {"x": -10000, "y": 0, "width": 10, "height": 10}},
               {"id": "positive", "declared": {"left": 37, "top": 11},
                "computed": {"left": "37px", "top": "11px"},
                "rect": {"x": 37, "y": 11, "width": 10, "height": 10}}]
    for index, patch in over.items():
        samples[int(index)]["rect"].update(patch)
    return {"samples": samples}


def timezone(zone="UTC", canonical=None, supported=("Asia/Bangkok", "Europe/London")):
    resolved = zone if canonical is None else canonical
    return {"timezone": zone, "canonical": resolved, "supported": list(supported),
            "worker": {"timezone": zone, "canonical": resolved,
                       "in_supported_set": zone in supported,
                       "supported_count": len(supported), "error": None}}


def audio(rate=48000, channels=2, digest="aa"):
    return {"hardware": {"sampleRate": rate, "maxChannelCount": channels,
                         "channelCount": 2},
            "renders": [{"requested": {"channels": 1, "rate": 44100, "length": 44100},
                         "reported": {"sampleRate": 44100, "length": 44100,
                                      "numberOfChannels": 1},
                         "sha256": digest, "slice_sum": 495.4}]}


def result(edge, value, *, headed=True, repeat=None):
    return {"context": {"headed": headed, "ua": "Chrome/151", "label": "test",
                        "device_pixel_ratio": 1, "secure_context": True,
                        "automation_signals": []},
            "edges": {edge: {"ok": True, "value": value, "error": None,
                             "repeat": copy.deepcopy(value) if repeat is None else repeat}}}


WINDOW = "coh.window-within-avail-rect"
LAYOUT = "coh.layout-integer-positions"
ZONE = "coh.resolved-timezone-in-supported-set"
AUDIO = "coh.audio-render-independent-of-audio-hardware"


class WindowRectTests(unittest.TestCase):
    def test_measured_xvfb_geometry_holds(self):
        verdict = coherence.window_within_avail_rect(result(WINDOW, window()))
        self.assertEqual(verdict.outcome, coherence.PASS)

    def test_window_outside_the_work_area_fails_with_the_overflow(self):
        off = window(screenX=1700, screenY=900, outerWidth=600, outerHeight=400)
        verdict = coherence.window_within_avail_rect(result(WINDOW, off))
        self.assertEqual(verdict.outcome, coherence.FAIL)
        self.assertIn("right +380", "\n".join(verdict.detail))
        self.assertIn("bottom +220", "\n".join(verdict.detail))

    def test_headless_is_inconclusive_rather_than_a_pass(self):
        # outerWidth 0 against an empty work area satisfies every clause
        # arithmetically while measuring nothing.
        blind = window(screenX=0, screenY=0, outerWidth=0, outerHeight=0,
                       availWidth=0, availHeight=0)
        verdict = coherence.window_within_avail_rect(result(WINDOW, blind, headed=False))
        self.assertEqual(verdict.outcome, coherence.INCONCLUSIVE)

    def test_negative_origin_is_only_judged_on_a_single_display(self):
        one = window(screenX=-1200, isExtended=False)
        many = window(screenX=-1200, isExtended=True)
        self.assertEqual(coherence.window_within_avail_rect(result(WINDOW, one)).outcome,
                         coherence.FAIL)
        # Two displays make a negative origin ordinary, so the clause is not
        # applied; the containment clauses still are.
        self.assertEqual(coherence.window_within_avail_rect(result(WINDOW, many)).outcome,
                         coherence.FAIL)
        self.assertNotIn("screenX >= 0",
                         "\n".join(coherence.window_within_avail_rect(
                             result(WINDOW, window(isExtended=True))).detail))


class LayoutTests(unittest.TestCase):
    def test_integer_declarations_are_exact(self):
        self.assertEqual(coherence.layout_integer_positions(result(LAYOUT, layout())).outcome,
                         coherence.PASS)

    def test_one_sixty_fourth_of_a_pixel_fails_and_is_named_in_those_units(self):
        noisy = layout(**{"0": {"x": -10000.015625}, "1": {"y": 10.96875}})
        verdict = coherence.layout_integer_positions(result(LAYOUT, noisy))
        self.assertEqual(verdict.outcome, coherence.FAIL)
        detail = "\n".join(verdict.detail)
        self.assertIn("delta -1/64 px", detail)
        self.assertIn("delta -2/64 px", detail)
        # The measured value survives into the report at full precision.
        self.assertIn("-10000.015625", detail)


class TimezoneTests(unittest.TestCase):
    def test_utc_on_a_utc_host_is_coherent(self):
        # Stock Chrome on a host configured to UTC reports the canonical id UTC,
        # which ICU's own filter keeps out of the enumeration. Failing this was
        # the row's original defect.
        verdict = coherence.resolved_timezone_in_supported_set(result(ZONE, timezone()))
        self.assertEqual(verdict.outcome, coherence.PASS)
        self.assertIn("outside the", verdict.summary)

    def test_enumerated_location_zone_passes(self):
        verdict = coherence.resolved_timezone_in_supported_set(
            result(ZONE, timezone("Asia/Bangkok")))
        self.assertEqual(verdict.outcome, coherence.PASS)

    def test_reported_alias_fails_in_both_realms(self):
        verdict = coherence.resolved_timezone_in_supported_set(
            result(ZONE, timezone("Asia/Calcutta", canonical="Asia/Kolkata")))
        self.assertEqual(verdict.outcome, coherence.FAIL)
        self.assertIn("canonicalises to 'Asia/Kolkata'", "\n".join(verdict.detail))

    def test_location_zone_missing_from_the_enumeration_fails(self):
        verdict = coherence.resolved_timezone_in_supported_set(
            result(ZONE, timezone("Asia/Bangkok", supported=("Europe/London",))))
        self.assertEqual(verdict.outcome, coherence.FAIL)

    def test_profile_claim_and_reported_zone_must_agree(self):
        measured = result(ZONE, timezone("Asia/Bangkok"))
        agree = coherence.resolved_timezone_in_supported_set(
            measured, {"locale": {"timezone": "Asia/Bangkok"}})
        diverge = coherence.resolved_timezone_in_supported_set(
            measured, {"locale": {"timezone": "Asia/Calcutta"}})
        self.assertEqual(agree.outcome, coherence.PASS)
        self.assertEqual(diverge.outcome, coherence.FAIL)
        self.assertIn("silent divergence", "\n".join(diverge.detail))


class AudioTests(unittest.TestCase):
    def test_two_runs_claiming_the_same_hardware_are_not_a_pass(self):
        # The whole point of the edge: agreement that varied nothing is
        # evidence of nothing, and two stock browsers always agree.
        verdict = coherence.audio_independent_pair(result(AUDIO, audio()),
                                                   result(AUDIO, audio()))
        self.assertEqual(verdict.outcome, coherence.INCONCLUSIVE)

    def test_differing_claims_with_identical_renders_pass(self):
        verdict = coherence.audio_independent_pair(
            result(AUDIO, audio(rate=48000, channels=2)),
            result(AUDIO, audio(rate=44100, channels=4)))
        self.assertEqual(verdict.outcome, coherence.PASS)

    def test_two_different_browsers_are_not_comparable(self):
        # A render digest is a property of the build. Pairing a 151 run with a
        # 152 one would report the version change as a hardware coupling.
        other = result(AUDIO, audio(rate=44100, channels=4, digest="bb"))
        other["context"]["ua"] = "Chrome/152"
        verdict = coherence.audio_independent_pair(result(AUDIO, audio()), other)
        self.assertEqual(verdict.outcome, coherence.INCONCLUSIVE)
        self.assertIn("different browsers", verdict.summary)

    def test_render_that_follows_the_hardware_claim_fails(self):
        verdict = coherence.audio_independent_pair(
            result(AUDIO, audio(rate=48000, channels=2)),
            result(AUDIO, audio(rate=44100, channels=4, digest="bb")))
        self.assertEqual(verdict.outcome, coherence.FAIL)
        self.assertIn("44100 Hz", "\n".join(verdict.detail))

    def test_one_run_refutes_the_edge_when_the_constructor_is_overridden(self):
        coupled = audio()
        coupled["renders"][0]["reported"]["sampleRate"] = 48000
        self.assertEqual(coherence.audio_independent_single(result(AUDIO, coupled)).outcome,
                         coherence.FAIL)

    def test_silent_render_is_a_failure_not_an_agreement(self):
        silent = audio()
        silent["renders"][0]["slice_sum"] = 0
        self.assertEqual(coherence.audio_independent_single(result(AUDIO, silent)).outcome,
                         coherence.FAIL)


class DeterminismTests(unittest.TestCase):
    def capture(self, repeat_value=None, drop=()):
        value = {"pixels_sha256": "a"}
        probes, repeat = {}, {}
        for _surface, pid in coherence.RENDER_SURFACES:
            if pid in drop:
                continue
            probes[pid] = {"ok": True, "value": value}
            repeat[pid] = {"ok": True, "value": value if repeat_value is None or pid != "canvas.2d"
                           else repeat_value}
        return {"context": {"label": "test"}, "probes": probes, "repeat": repeat}

    def test_surfaces_that_repeat_themselves_pass(self):
        self.assertEqual(coherence.render_determinism(self.capture()).outcome, coherence.PASS)

    def test_surface_that_does_not_repeat_itself_fails_with_both_digests(self):
        verdict = coherence.render_determinism(self.capture({"pixels_sha256": "b"}))
        self.assertEqual(verdict.outcome, coherence.FAIL)
        detail = "\n".join(verdict.detail)
        self.assertIn("first  : a", detail)
        self.assertIn("repeat : b", detail)

    def test_unmeasurable_surface_is_inconclusive_not_a_pass_or_a_failure(self):
        # A host with no GPU reports webgl1 as failed. That is the host's
        # limitation, not a browser that lost determinism, and it must not read
        # as either verdict.
        verdict = coherence.render_determinism(self.capture(drop=("webgl1", "webgl2")))
        self.assertEqual(verdict.outcome, coherence.INCONCLUSIVE)


class ExitStatusTests(unittest.TestCase):
    def status(self, *outcomes):
        results = {"coh.%d" % i: coherence.Result(o, "x")
                   for i, o in enumerate(outcomes)}
        with contextlib.redirect_stdout(io.StringIO()):
            return coherence.report(results, None)

    def test_pass_and_skip_are_success(self):
        self.assertEqual(self.status(coherence.PASS, coherence.SKIP), 0)

    def test_inconclusive_is_not_success(self):
        self.assertEqual(self.status(coherence.PASS, coherence.INCONCLUSIVE), 2)

    def test_failure_outranks_inconclusive(self):
        self.assertEqual(self.status(coherence.FAIL, coherence.INCONCLUSIVE), 1)

    def test_missing_operands_are_a_failure(self):
        self.assertEqual(self.status(coherence.ERROR), 1)


if __name__ == "__main__":
    unittest.main()
