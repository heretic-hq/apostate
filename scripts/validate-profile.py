#!/usr/bin/env python3
"""V0: validate a profile against config/profile.schema.json.

    python3 scripts/validate-profile.py PROFILE.json [PROFILE.json ...]

Exit status is 0 only when every profile validates.

This script was an empty file for two days. It was executable, it was wired
into the gate, and it exited 0 on everything — including profiles carrying
top-level keys the schema does not declare under additionalProperties: false.
Two shipped patches (0026 gl_limits, 0028 gl_extensions) added profile fields
with no schema row at all and nothing noticed, because the thing that would
have noticed was zero bytes long.

So the rule this file follows is: never exit 0 for any reason other than having
actually validated the profile. A missing dependency, an unreadable schema, a
profile that is not a JSON object — each is a failure, not a skip. A gate that
cannot run has not passed.
"""

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = REPO / "config" / "profile.schema.json"


def die(msg: str) -> "typing.NoReturn":  # noqa: F821
    print(f"validate-profile: {msg}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        die("usage: validate-profile.py PROFILE.json [PROFILE.json ...]")

    try:
        import jsonschema
    except ImportError:
        die("jsonschema is not installed. Install it rather than skipping the\n"
            "  check — a gate that cannot run has not passed.\n"
            "  pip install jsonschema")

    if not SCHEMA.is_file():
        die(f"schema not found at {SCHEMA}")
    try:
        schema = json.loads(SCHEMA.read_text())
    except json.JSONDecodeError as e:
        die(f"{SCHEMA} is not valid JSON: {e}")

    # Validate the schema itself. A schema with a typo in a keyword name is
    # silently permissive — "minimun" is not an error, it is an annotation —
    # and that failure mode is exactly the one this file exists to stop.
    validator_cls = jsonschema.validators.validator_for(schema)
    try:
        validator_cls.check_schema(schema)
    except jsonschema.exceptions.SchemaError as e:
        die(f"{SCHEMA} is not a valid JSON Schema: {e}")
    validator = validator_cls(schema)

    failed = 0
    for path in paths:
        if not path.is_file():
            print(f"FAIL  {path}: no such file")
            failed += 1
            continue
        try:
            profile = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL  {path}: not valid JSON: {e}")
            failed += 1
            continue
        if not isinstance(profile, dict):
            print(f"FAIL  {path}: top level is {type(profile).__name__}, expected object")
            failed += 1
            continue

        errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.absolute_path))
        if not errors:
            print(f"ok    {path}  ({len(profile)} section(s))")
            continue

        failed += 1
        print(f"FAIL  {path}  ({len(errors)} error(s))")
        for e in errors:
            loc = ".".join(str(p) for p in e.absolute_path) or "<root>"
            print(f"        {loc}: {e.message}")

    print()
    print(f"{len(paths) - failed}/{len(paths)} profile(s) valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
