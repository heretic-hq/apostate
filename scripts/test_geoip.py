#!/usr/bin/env python3
"""Focused, network-free tests for the public GeoIP launch helper."""

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

try:
    from . import geoip
except ImportError:
    spec = importlib.util.spec_from_file_location("geoip", Path(__file__).with_name("geoip.py"))
    geoip = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = geoip
    spec.loader.exec_module(geoip)


class FakeTransport:
    def __init__(self, payload=None, error=None, status=200):
        self.payload = payload or {
            "ip": "203.0.113.7",
            "country_code": "DE",
            "region": "Berlin",
            "timezone": {"id": "Europe/Berlin"},
        }
        self.error = error
        self.status = status
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return geoip.HTTPTransportResponse(
            status=self.status,
            body=json.dumps(self.payload),
        )


class GeoIPTests(unittest.TestCase):
    def test_direct_and_proxy_modes_select_distinct_requests(self):
        direct_transport = FakeTransport()
        direct = geoip.GeoIPResolver(
            transport=direct_transport,
            endpoint="https://geo.example.test/json",
            timeout=0.25,
        ).resolve()
        self.assertEqual(direct.lookup_mode, "direct")
        self.assertEqual(direct_transport.requests[0].mode, "direct")
        self.assertIsNone(direct_transport.requests[0].proxy)

        proxy_transport = FakeTransport()
        proxy = geoip.GeoIPResolver(
            proxy="http://user:password@proxy.example.test:8080",
            transport=proxy_transport,
            endpoint="https://geo.example.test/json",
            timeout=0.25,
        ).resolve()
        request = proxy_transport.requests[0]
        self.assertEqual(proxy.lookup_mode, "proxy")
        self.assertEqual(request.mode, "proxy")
        self.assertEqual(request.proxy.host, "proxy.example.test")
        self.assertEqual(request.proxy.port, 8080)
        self.assertEqual(request.proxy.redacted_url, "http://proxy.example.test:8080")
        self.assertEqual(proxy.diagnostics.lookup_mode, "proxy")

    def test_explicit_values_win_over_geoip_and_profile_values(self):
        transport = FakeTransport()
        result = geoip.GeoIPResolver(
            locale="ja-JP",
            timezone="Asia/Tokyo",
            profile_locale="fr-FR",
            profile_timezone="Europe/Paris",
            transport=transport,
        ).resolve()
        self.assertEqual(result.derived_locale, "de-DE")
        self.assertEqual(result.derived_timezone, "Europe/Berlin")
        self.assertEqual(result.locale, "ja-JP")
        self.assertEqual(result.timezone, "Asia/Tokyo")
        self.assertEqual(result.accept_languages, "ja-JP,ja")
        self.assertEqual(result.locale_source, "explicit")
        self.assertEqual(result.timezone_source, "explicit")

        # An explicit value can override one axis while the other axis follows
        # GeoIP, with no host/default value being fabricated.
        partial = geoip.GeoIPResolver(
            locale="fr-FR", transport=FakeTransport()
        ).resolve()
        self.assertEqual(partial.locale, "fr-FR")
        self.assertEqual(partial.timezone, "Europe/Berlin")
        self.assertEqual(partial.timezone_source, "geoip")

    def test_timeout_is_visible_and_does_not_fabricate_defaults(self):
        transport = FakeTransport(error=TimeoutError("socket timed out"))
        resolver = geoip.GeoIPResolver(
            proxy="http://user:secret@proxy.example.test:8080",
            transport=transport,
            timeout=0.5,
        )
        with self.assertRaises(geoip.GeoIPTimeoutError) as raised:
            resolver.resolve()
        error = raised.exception
        self.assertIn("timed out", str(error))
        self.assertEqual(error.diagnostics.status, "timeout")
        self.assertIsNone(resolver.result)
        self.assertNotIn("UTC", str(error))
        self.assertNotIn("en-US", str(error))
        self.assertEqual(transport.requests[0].timeout, 0.5)

    def test_error_and_diagnostics_redact_proxy_credentials(self):
        transport = FakeTransport()

        def fail(request):
            transport.requests.append(request)
            raise RuntimeError("connect failed for " + request.proxy.url)

        resolver = geoip.GeoIPResolver(
            proxy="https://alice:super-secret@proxy.example.test:8443",
            transport=fail,
        )
        with self.assertRaises(geoip.GeoIPLookupError) as raised:
            resolver.resolve()
        error = raised.exception
        self.assertNotIn("super-secret", str(error))
        self.assertNotIn("alice", str(error))
        self.assertNotIn("super-secret", error.diagnostics.error)
        self.assertEqual(
            error.diagnostics.proxy,
            "https://proxy.example.test:8443",
        )

        parsed = geoip.parse_proxy_url("http://alice:super-secret@[2001:db8::1]:8080")
        self.assertNotIn("super-secret", repr(parsed))
        self.assertEqual(parsed.redacted_url, "http://[2001:db8::1]:8080")

    def test_result_is_fixed_and_immutable_after_resolution(self):
        transport = FakeTransport()
        resolver = geoip.GeoIPResolver(transport=transport)
        first = resolver.resolve()
        second = resolver.resolve()
        self.assertIs(first, second)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(first.provider, geoip.PROVIDER_NAME)
        self.assertEqual(first.provider_version, geoip.PROVIDER_VERSION)
        self.assertEqual(first.mapping_version, geoip.MAPPING_VERSION)
        self.assertTrue(first.native_capability_pending)
        self.assertEqual(first.webrtc.status, "pending-native-capability")
        self.assertFalse(first.webrtc.enforced)
        with self.assertRaises(FrozenInstanceError):
            first.locale = "fr-FR"
        with self.assertRaises(TypeError):
            first.launch_overrides()["locale"] = "fr-FR"
        with self.assertRaises(geoip.GeoIPResolutionError):
            resolver.resolve(locale="fr-FR")

    def test_disabled_geoip_does_not_touch_network_or_invent_values(self):
        with mock.patch.object(geoip, "urllib_transport", side_effect=AssertionError("network")):
            result = geoip.GeoIPResolver(geoip=False).resolve()
        self.assertEqual(result.lookup_mode, "disabled")
        self.assertIsNone(result.locale)
        self.assertIsNone(result.timezone)
        self.assertEqual(result.diagnostics.status, "disabled")


if __name__ == "__main__":
    unittest.main()
