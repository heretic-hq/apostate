"""Profile derivation preserves provider identity constraints, never voices."""

import copy
import json
import pathlib
import unittest

import jsonschema
import to_profile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "config/profile.schema.json").read_text())


def capture(voices):
    return {"probes": {"speech.voices": {"ok": True, "value": voices}}}


class SpeechProviderTests(unittest.TestCase):
    def test_known_local_and_remote_are_preserved_as_constraints(self):
        data = capture([{"name": "Local", "lang": "en-US", "voiceURI": "Local",
                         "localService": True, "default": True},
                        {"name": "Remote", "lang": "en-US", "voiceURI": "Remote",
                         "localService": False, "default": False}])
        original = copy.deepcopy(data)
        voices = to_profile.derive_speech_voices(data)
        self.assertEqual([v["local_service"] for v in voices], [True, False])
        self.assertNotIn("voiceURI", voices[0])
        self.assertEqual(data, original)
        jsonschema.Draft202012Validator(SCHEMA).validate({"speech": {"voices": voices}})

    def test_legacy_unknown_locality_is_omitted(self):
        voices = to_profile.derive_speech_voices(capture([{"name": "Provider", "lang": "en-US"}]))
        self.assertNotIn("local_service", voices[0])

    def test_absent_and_explicit_empty_are_distinct(self):
        self.assertIsNone(to_profile.derive_speech_voices({}))
        self.assertEqual(to_profile.derive_speech_voices(capture([])), [])

    def test_malformed_provider_metadata_is_rejected(self):
        for change in ({"voiceURI": "other-id"}, {"localService": "false"},
                       {"localService": None}, {"name": ""}, {"lang": None},
                       {"default": "false"}):
            voice = {"name": "Provider", "lang": "en-US", **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                to_profile.derive_speech_voices(capture([voice]))

    def test_real_reference_identity_is_retained_without_admitting_capability(self):
        for filename in ("unlabelled-20260910T140818Z.json", "m4-max-chrome-20260908T163229Z.json"):
            data = json.loads((ROOT / "resources/fingerprints/raw" / filename).read_text())
            source = data["probes"]["speech.voices"]["value"]
            voices = to_profile.derive_speech_voices(data)
            self.assertEqual(len(voices), len(source))
            self.assertEqual([v["local_service"] for v in voices], [v["localService"] for v in source])
            self.assertEqual(sum(not v["local_service"] for v in voices), 19)
            jsonschema.Draft202012Validator(SCHEMA).validate({"speech": {"voices": voices}})


if __name__ == "__main__":
    unittest.main()
