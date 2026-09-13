"""Minimal RFC 8032 Ed25519 verification for stdlib-only packaging."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re


_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_B_Y = 4 * pow(5, _Q - 2, _Q) % _Q


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q:
        x = x * _I % _Q
    if (x & 1):
        x = _Q - x
    return x


_B = (_xrecover(_B_Y), _B_Y)


def _add(left: tuple[int, int], right: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    denominator = pow(1 + _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    x3 = (x1 * y2 + x2 * y1) * denominator % _Q
    denominator = pow(1 - _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    y3 = (y1 * y2 + x1 * x2) * denominator % _Q
    return x3, y3


def _scalarmult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def _decodepoint(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise ValueError("Ed25519 point must be 32 bytes")
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _Q:
        raise ValueError("Ed25519 point is not canonical")
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _Q - x
    if (x * x + y * y - 1 - _D * x * x * y * y) % _Q:
        raise ValueError("Ed25519 point is not on the curve")
    return x, y


def _der_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("truncated DER")
    first = data[offset]
    offset += 1
    if first < 128:
        return first, offset
    count = first & 0x7F
    if count == 0 or count > 4 or offset + count > len(data):
        raise ValueError("invalid DER length")
    length = int.from_bytes(data[offset:offset + count], "big")
    return length, offset + count


def _der_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    if offset >= len(data):
        raise ValueError("truncated DER tag")
    tag = data[offset]
    length, content = _der_length(data, offset + 1)
    end = content + length
    if end > len(data):
        raise ValueError("truncated DER value")
    return tag, data[content:end], end


def decode_public_key(value: bytes | str) -> bytes:
    """Decode raw 32-byte, base64/hex, or PEM SubjectPublicKeyInfo input."""
    if isinstance(value, str):
        text = value.strip()
        if "BEGIN PUBLIC KEY" in text:
            lines = [line.strip() for line in text.splitlines() if not line.startswith("---")]
            try:
                der = base64.b64decode("".join(lines), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("invalid Ed25519 PEM public key") from exc
            tag, outer, end = _der_tlv(der, 0)
            if tag != 0x30 or end != len(der):
                raise ValueError("invalid SubjectPublicKeyInfo wrapper")
            tag, algorithm, offset = _der_tlv(outer, 0)
            if tag != 0x30:
                raise ValueError("invalid SubjectPublicKeyInfo algorithm")
            tag, bit_string, offset = _der_tlv(outer, offset)
            if tag != 0x03 or not bit_string or bit_string[0] != 0:
                raise ValueError("invalid SubjectPublicKeyInfo bit string")
            raw = bit_string[1:]
        else:
            text = text.removeprefix("ed25519:")
            try:
                raw = bytes.fromhex(text) if len(text) == 64 else base64.b64decode(text, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("invalid Ed25519 public key encoding") from exc
    else:
        raw = bytes(value)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must contain 32 bytes")
    return raw


def decode_signature(value: bytes | str) -> bytes:
    if isinstance(value, str):
        if not re.fullmatch(r"[A-Za-z0-9+/]{86}==", value):
            raise ValueError("invalid canonical Ed25519 signature encoding")
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid Ed25519 signature encoding") from exc
    else:
        raw = bytes(value)
    if len(raw) != 64 or (isinstance(value, str) and base64.b64encode(raw).decode("ascii") != value):
        raise ValueError("Ed25519 signature must use canonical padded base64 for 64 bytes")
    return raw


def verify(signature: bytes | str, message: bytes, public_key: bytes | str) -> bool:
    try:
        signature_bytes = decode_signature(signature)
        public_bytes = decode_public_key(public_key)
        R = _decodepoint(signature_bytes[:32])
        A = _decodepoint(public_bytes)
        S = int.from_bytes(signature_bytes[32:], "little")
        if S >= _L:
            return False
        challenge = int.from_bytes(
            hashlib.sha512(signature_bytes[:32] + public_bytes + message).digest(), "little"
        ) % _L
        return _scalarmult(_B, S) == _add(R, _scalarmult(A, challenge))
    except (ValueError, IndexError, OverflowError):
        return False


__all__ = ["decode_public_key", "decode_signature", "verify"]
