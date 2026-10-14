"""Validate the JSON files of a generated Power BI project against Microsoft's public schemas.

usage: python pbivalidate.py out/Sales            (needs: pip install jsonschema referencing requests)

Each file names its schema in "$schema". The schemas are fetched from developer.microsoft.com (and cached in ./.schema_cache). Power BI Desktop
sometimes writes a newer schema version than the one published; in that case the newest published version of the same schema is used instead.
"""
import json
import re
import sys
from pathlib import Path

import requests
from jsonschema import Draft7Validator
from referencing import Registry, Resource

CACHE = Path(".schema_cache")
CACHE.mkdir(exist_ok=True)
FALLBACKS = {}


def fetch(uri):
    f = CACHE / re.sub(r"[^A-Za-z0-9._-]", "_", uri)
    if not f.exists():
        r = requests.get(uri, timeout=60)
        if r.status_code != 200:
            return None
        f.write_text(r.text, encoding="utf-8")
    return json.loads(f.read_text(encoding="utf-8"))


def retrieve(uri):
    doc = fetch(uri)
    if doc is None:                                      # e.g. visualContainer/2.13.0 is not published yet: try lower minor versions
        m = re.search(r"/(\d+)\.(\d+)\.(\d+)/", uri)
        for minor in range(int(m.group(2)) - 1, -1, -1):
            alt = uri.replace(m.group(0), f"/{m.group(1)}.{minor}.{m.group(3)}/")
            doc = fetch(alt)
            if doc:
                FALLBACKS[uri] = alt
                break
    if doc is None:
        raise RuntimeError(f"schema not found: {uri}")
    return Resource.from_contents(doc)


def validate_file(path, registry):
    data = json.loads(path.read_text(encoding="utf-8"))
    uri = data.get("$schema")
    if not uri:
        return ["no $schema"]
    schema = registry.get_or_retrieve(uri).value.contents
    schema = json.loads(json.dumps(schema))
    if "$schema" in schema.get("properties", {}):        # allow a newer $schema string than the published one
        schema["properties"]["$schema"].pop("const", None)
        schema["properties"]["$schema"].pop("pattern", None)
    validator = Draft7Validator(schema, registry=registry)
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}" for e in validator.iter_errors(data)]


def main():
    root = Path(sys.argv[1])
    registry = Registry(retrieve=retrieve)
    files = [p for p in root.rglob("*.json") if "StaticResources" not in p.parts] + list(root.rglob("*.pbir")) + list(root.rglob("*.pbism")) + list(root.rglob("*.pbip"))
    bad = 0
    for p in sorted(files):
        errors = validate_file(p, registry)
        if errors:
            bad += 1
            print(f"FAIL {p.relative_to(root)}")
            for e in errors[:6]:
                print("   ", e)
    print(f"{len(files)} files checked, {bad} with errors")
    if FALLBACKS:
        print("used older published schemas for:", ", ".join(sorted(FALLBACKS)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
