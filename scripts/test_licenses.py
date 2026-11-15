#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Every image carries its licence texts under /licenses/<component>/: the
texts THIRD_PARTY_NOTICES.md names for it, from licenses/, read from the COPY
lines of its Dockerfile (scripts/smoke.sh checks the built images)."""

import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OWN = "/licenses/canopen-interop/"


def logical_lines(path):
    with open(path) as f:
        return f.read().replace("\\\n", " ").splitlines()


def copies(dockerfile):
    """(source, destination) of every COPY from the build context."""
    out = []
    for line in logical_lines(dockerfile):
        words = line.split()
        if not words or words[0] != "COPY" or any(w.startswith("--from") for w in words):
            continue
        words = [w for w in words[1:] if not w.startswith("--")]
        out += [(src, words[-1]) for src in words[:-1]]
    return out


def notices():
    """image → licence texts its rows of THIRD_PARTY_NOTICES.md link to."""
    want = {}
    with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md")) as f:
        for line in f:
            if not line.startswith("| "):
                continue
            images = [i.strip() for i in line.split("|")[1].split(",")]
            for image in images:
                if os.path.isdir(os.path.join(ROOT, "adapters", image)):
                    want.setdefault(image, set()).update(re.findall(r"\]\((licenses/[^)]+)\)", line))
    return want


ADAPTERS = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(ROOT, "adapters", "*", "Dockerfile")))


class Licences(unittest.TestCase):
    def test_every_image_carries_the_texts_of_its_notices(self):
        want = notices()
        self.assertTrue(want)
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter):
                c = copies(os.path.join(ROOT, "adapters", adapter, "Dockerfile"))
                self.assertIn(("LICENSE", OWN), c)
                self.assertIn(("THIRD_PARTY_NOTICES.md", OWN), c)
                self.assertTrue(want.get(adapter), "%s has no licence text in THIRD_PARTY_NOTICES.md" % adapter)
                for text in sorted(want[adapter]):
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, text)), text)
                    dests = [d for s, d in c if s == text]
                    self.assertTrue(any(re.match(r"^/licenses/[A-Za-z0-9._-]+/$", d) and d != OWN for d in dests),
                                    "%s: %s is not copied to /licenses/<component>/ (%s)" % (adapter, text, dests))

    def test_every_text_is_in_an_image(self):
        copied = set()
        for adapter in ADAPTERS:
            copied.update(src for src, dst in copies(os.path.join(ROOT, "adapters", adapter, "Dockerfile"))
                          if dst.startswith("/licenses/"))
        for path in sorted(glob.glob(os.path.join(ROOT, "licenses", "*.txt"))):
            self.assertIn(os.path.relpath(path, ROOT), copied)


if __name__ == "__main__":
    unittest.main()
