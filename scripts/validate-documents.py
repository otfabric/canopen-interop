#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate adapter output against schemas/: every line of every *.jsonl file
under the given directories, by its "type" (capabilities and version
documents by file name: *capabilities*.json, *version*.json)."""
import glob
import json
import os
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    resources, validators = [], {}
    for path in glob.glob(os.path.join(ROOT, "schemas", "*.schema.json")):
        doc = json.load(open(path))
        resources.append((doc["$id"], Resource.from_contents(doc)))
        validators[os.path.basename(path)] = doc
    registry = Registry().with_resources(resources)
    v = lambda name: Draft202012Validator(validators[name], registry=registry)
    event, caps, version = v("event.schema.json"), v("capabilities.schema.json"), v("version.schema.json")
    errors, count = [], 0
    for d in sys.argv[1:]:
        for path in sorted(glob.glob(os.path.join(d, "**", "*.json*"), recursive=True)):
            name = os.path.basename(path)
            if not (name.endswith(".jsonl") or name.endswith("capabilities.json") or name.endswith("version.json")):
                continue
            validator = caps if "capabilities" in name else version if "version" in name else event
            for n, line in enumerate(open(path), 1):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                count += 1
                for e in validator.iter_errors(json.loads(line)):
                    errors.append("%s:%d: %s" % (path, n, e.message[:200]))
    if errors:
        print("\n".join(errors[:50]))
        sys.exit(1)
    print("%d documents valid" % count)


if __name__ == "__main__":
    main()
