"""Exceptions raised by the Apostate Python package.

The package deliberately keeps these exceptions small and actionable.  Error
messages are suitable for a terminal, and launch code redacts proxy
credentials before including any user-controlled values in an error.
"""

from __future__ import annotations


class ApostateError(Exception):
    """Base class for all package errors."""


class ConfigurationError(ApostateError, ValueError):
    """A launch option is invalid or cannot be represented canonically."""


class ProfileError(ApostateError, ValueError):
    """A selected profile is missing, malformed, or incoherent."""


class GeoIPError(ApostateError):
    """GeoIP was requested but could not be resolved safely."""


class GeoIPUnavailableError(GeoIPError):
    """No configured GeoIP provider is available."""


class BinaryError(ApostateError):
    """Base class for binary discovery, download, and cache errors."""


class BinaryNotFoundError(BinaryError, FileNotFoundError):
    """No verified Apostate binary is available for the requested target."""


class ManifestError(BinaryError):
    """The release manifest is absent or does not satisfy its contract."""


class IntegrityError(BinaryError):
    """Downloaded or cached bytes do not match their SHA-256 digest."""


class UnpublishedArtifactError(ManifestError):
    """The package manifest deliberately contains no published artifact."""


class UnsupportedArchiveError(BinaryError):
    """The package cannot safely extract the artifact archive format."""


class LaunchError(ApostateError):
    """The native browser process could not be started or managed."""


__all__ = [
    "ApostateError",
    "ConfigurationError",
    "ProfileError",
    "GeoIPError",
    "GeoIPUnavailableError",
    "BinaryError",
    "BinaryNotFoundError",
    "ManifestError",
    "IntegrityError",
    "UnpublishedArtifactError",
    "UnsupportedArchiveError",
    "LaunchError",
]
