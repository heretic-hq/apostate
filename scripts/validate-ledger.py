#!/usr/bin/env python3
"""V0 gate: validate ledger files against their schemas.

Deliberately dependency-free. This runs before anything else in the pipeline, so
it must work on a bare checkout with no install step — a gate that needs setting
up is a gate that gets skipped.

Implements the subset of JSON Schema the ledger schemas actually use, and fails
loudly on any construct it does not implement rather than passing it silently.

    python3 scripts/validate-ledger.py
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "ledger" / "schema"

FILES = [
    ("ledger/surfaces.jsonl", "surface.schema.json"),
    ("ledger/emitters.jsonl", "emitter.schema.json"),
    ("ledger/coherence.jsonl", "coherence.schema.json"),
]

SUPPORTED = {
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "items", "minItems", "maxItems", "pattern", "description", "title", "$id",
    "$schema", "allOf", "if", "then", "contains", "propertyNames", "minLength",
    "format", "$defs", "$ref",
}

TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def resolve(schema, root):
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise NotImplementedError(f"external $ref not supported: {ref}")
        node = root
        for part in ref[2:].split("/"):
            node = node[part]
        return node
    return schema


def validate(value, schema, root, path, errors):
    schema = resolve(schema, root)

    unknown = set(schema) - SUPPORTED
    if unknown:
        raise NotImplementedError(f"{path}: schema uses unimplemented keywords {sorted(unknown)}")

    if "type" in schema:
        expected = schema["type"]
        names = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(value, TYPES[n]) for n in names):
            # bool is a subclass of int; keep them distinct
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
        if "integer" in names and isinstance(value, bool):
            errors.append(f"{path}: expected integer, got boolean")
            return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")

    if isinstance(value, str):
        if "pattern" in schema:
            import re
            if not re.search(schema["pattern"], value):
                errors.append(f"{path}: {value!r} does not match /{schema['pattern']}/")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: at most {schema['maxItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], root, f"{path}[{i}]", errors)
        if "contains" in schema:
            def satisfies(item):
                probe = []
                validate(item, schema["contains"], root, path, probe)
                return not probe
            if not any(satisfies(item) for item in value):
                errors.append(f"{path}: no item satisfies the 'contains' constraint")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, root, f"{path}.{key}", errors)

    for sub in schema.get("allOf", []):
        if "if" in sub:
            probe = []
            validate(value, sub["if"], root, path, probe)
            if not probe and "then" in sub:
                validate(value, sub["then"], root, path, errors)
        else:
            validate(value, sub, root, path, errors)

    return errors


def main() -> int:
    failures = 0
    checked = 0
    for rel, schema_name in FILES:
        target = ROOT / rel
        schema = json.loads((SCHEMA_DIR / schema_name).read_text())
        if not target.exists():
            print(f"  --  {rel} (not created yet)")
            continue
        for lineno, line in enumerate(target.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            checked += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  FAIL {rel}:{lineno}: invalid JSON: {e}")
                failures += 1
                continue
            errors = validate(row, schema, schema, row.get("id", f"line {lineno}"), [])
            for err in errors:
                print(f"  FAIL {rel}:{lineno}: {err}")
            failures += len(errors)
        print(f"  ok   {rel}")

    print(f"\n{checked} row(s) checked, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
