#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of scripts/check-versions.sh: it runs on a copy of its inputs, one of
them changed, and must fail on every disagreement or missing pin."""

import glob
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Everything check-versions.sh reads.
INPUTS = ["versions.yaml", "requirements-dev.txt", "Makefile", "docs/CONTAINER_CONTRACT.md",
          "scripts/check-versions.sh"]
INPUT_GLOBS = ["adapters/*/Dockerfile", "adapters/*/requirements.txt", "own/*/Dockerfile", ".github/workflows/*.yml"]


class CheckVersions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        files = list(INPUTS)
        for pattern in INPUT_GLOBS:
            files += [os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, pattern))]
        for rel in files:
            os.makedirs(os.path.join(self.root, os.path.dirname(rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(self.root, rel))

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self):
        p = subprocess.run(["bash", os.path.join(self.root, "scripts", "check-versions.sh")],
                           capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr).replace("\r\n", "\n")

    def edit(self, rel, pattern, replacement, count=1):
        path = os.path.join(self.root, rel)
        with open(path) as f:
            text = f.read()
        new, n = re.subn(pattern, replacement, text, count=count, flags=re.M)
        self.assertGreater(n, 0, "%s: %s not found" % (rel, pattern))
        with open(path, "w") as f:
            f.write(new)

    def assertFails(self, needle):
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)

    def test_the_repository_agrees(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_a_requirement_without_a_hash(self):
        self.edit("adapters/pythoncanopen/requirements.txt",
                  r"^packaging==26\.3 \\\n(    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+", "packaging==26.3\n")
        self.assertFails("adapters/pythoncanopen/requirements.txt: packaging==26.3 has no --hash=sha256:")

    def test_a_lely_requirement_without_a_hash(self):
        self.edit("adapters/lely/requirements.txt",
                  r"^empy==3\.3\.4 \\\n(    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+", "empy==3.3.4\n")
        self.assertFails("adapters/lely/requirements.txt: empy==3.3.4 has no --hash=sha256:")

    def test_a_requirement_without_a_version(self):
        self.edit("adapters/lely/requirements.txt", r"^PyYAML==6\.0\.3", "PyYAML>=6")
        self.assertFails("PyYAML>=6 is not pinned to one version")

    def test_pip_install_without_require_hashes(self):
        self.edit("adapters/lely/Dockerfile", r" --require-hashes", "")
        self.assertFails("adapters/lely/Dockerfile: pip install without --require-hashes")

    def test_pip_install_of_source_distributions(self):
        self.edit("adapters/pythoncanopen/Dockerfile", r" --only-binary=:all:", "")
        self.assertFails("adapters/pythoncanopen/Dockerfile: pip install without --only-binary=:all:")

    def test_an_implementation_without_source_sha256(self):
        self.edit("versions.yaml", r"^    source_sha256: .*# canopen-.*\n", "")
        self.assertFails("versions.yaml: pythoncanopen has no source_sha256")

    def test_a_source_sha256_that_requirements_txt_does_not_list(self):
        self.edit("versions.yaml", r'(source_sha256: ")651d', r"\g<1>0000")
        self.assertFails("source_sha256 of pythoncanopen (versions.yaml) is not a hash of canopen==2.4.1")

    def test_an_unpinned_pip_install_in_a_workflow(self):
        self.edit(".github/workflows/release.yml", r"pip install -r requirements-dev\.txt", "pip install jsonschema")
        self.assertFails(".github/workflows/release.yml: jsonschema is installed without the version of versions.yaml tools")

    def test_an_unpinned_pip_install_in_the_makefile(self):
        self.edit("Makefile", r"-r requirements-dev\.txt", "jsonschema")
        self.assertFails("Makefile: jsonschema is installed without the version")

    def test_requirements_dev_differs_from_versions_yaml(self):
        self.edit("requirements-dev.txt", r"^jsonschema==.*$", "jsonschema==4.0.0")
        self.assertFails("requirements-dev.txt: jsonschema==4.0.0 is not the version of versions.yaml tools")

    def test_a_floating_shellcheck_image(self):
        self.edit("Makefile", r"koalaman/shellcheck:[^\s]+", "koalaman/shellcheck:stable")
        self.assertFails("Makefile: koalaman/shellcheck:stable")

    def test_a_floating_python_tool_image(self):
        self.edit("Makefile", r"library/python:[^\s]+", "library/python:3.12-slim")
        self.assertFails("Makefile: tool image library/python:3.12-slim is not in versions.yaml base_images")


if __name__ == "__main__":
    unittest.main()
