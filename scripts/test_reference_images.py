#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of scripts/reference-images.sh: the reference images of the own-stack
workflow come from manifest.json by digest, never by tag."""

import glob
import json
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "reference-images.sh")
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


class ReferenceImages(unittest.TestCase):
    def run_script(self, images, *adapters):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "manifest.json")
            with open(path, "w") as f:
                json.dump({"version": "v0.0.0", "images": images}, f)
            env = dict(os.environ)
            env.pop("REGISTRY", None)
            p = subprocess.run(["bash", SCRIPT, path] + list(adapters), capture_output=True, text=True, env=env)
        return p.returncode, p.stdout.replace("\r\n", "\n"), p.stderr

    def test_digests_of_the_manifest(self):
        code, out, err = self.run_script({"canopennode": "ghcr.io/otfabric/canopen-interop-canopennode@" + D1,
                                          "lely": "ghcr.io/otfabric/canopen-interop-lely@" + D2},
                                         "canopennode", "lely")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "ADAPTER_IMAGE_canopennode=ghcr.io/otfabric/canopen-interop-canopennode@%s\n"
                              "ADAPTER_IMAGE_lely=ghcr.io/otfabric/canopen-interop-lely@%s\n" % (D1, D2))

    def test_a_hyphen_is_an_underscore_in_the_variable(self):
        code, out, err = self.run_script({"some-stack": "ghcr.io/otfabric/canopen-interop-some-stack@" + D1}, "some-stack")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "ADAPTER_IMAGE_some_stack=ghcr.io/otfabric/canopen-interop-some-stack@%s\n" % D1)

    def test_an_adapter_missing_from_the_manifest(self):
        code, _, err = self.run_script({"canopennode": "ghcr.io/otfabric/canopen-interop-canopennode@" + D1},
                                       "canopennode", "lely")
        self.assertEqual(code, 1)
        self.assertIn("lely", err)

    def test_a_tag_is_not_a_digest(self):
        code, _, err = self.run_script({"lely": "ghcr.io/otfabric/canopen-interop-lely:v0.4.0"}, "lely")
        self.assertEqual(code, 1)
        self.assertIn("canopen-interop-lely@sha256:<digest>", err)

    def test_the_image_of_another_adapter(self):
        code, _, _ = self.run_script({"lely": "ghcr.io/otfabric/canopen-interop-canopennode@" + D1}, "lely")
        self.assertEqual(code, 1)

    def test_the_own_stack_workflow_pulls_by_digest(self):
        workflows = glob.glob(os.path.join(ROOT, ".github", "workflows", "own-*.yml"))
        self.assertTrue(workflows)
        for path in workflows:
            with open(path) as f:
                text = f.read()
            self.assertTrue("scripts/reference-images.sh harness-release/manifest.json" in text,
                            "%s does not pull the references by the digests of manifest.json" % path)
            self.assertEqual(re.findall(r"docker pull[^\n]*canopen-interop-[^\n]*:\$\{VERSION\}", text), [], path)


if __name__ == "__main__":
    unittest.main()
