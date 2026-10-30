#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the results of scripts/selftest.py that need no Docker."""

import importlib.util
import os
import unittest
from unittest import mock

import interop_result

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("selftest", os.path.join(HERE, "selftest.py"))
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


class Results(unittest.TestCase):
    def setUp(self):
        st.results.clear()
        st.KNOWN.clear()
        st.REPRODUCED.clear()
        st.load_known()

    def document(self):
        with mock.patch.object(interop_result, "repo_digest", return_value=None):
            doc = st.result_document({"lely": {}}, {"lely": {"version": "1"}}, "2026-10-30T00:00:00Z")
        doc["summary"] = interop_result.summarize(doc["results"])
        return doc

    def test_a_reproduced_difference_is_a_waiver_not_a_pass(self):
        st.record("lss", "lss-fastscan-configured", "fail", "answered", "canopennode", "lely")
        st.record("sdo", "upload", "pass", "", "lely", "lely")
        doc = self.document()
        self.assertEqual(doc["results"][0]["outcome"], "known-difference")
        self.assertEqual(doc["results"][0]["id"], "lss.lss-fastscan-configured")
        self.assertEqual(interop_result.problems(doc), [])
        self.assertEqual(interop_result.schema_errors(doc), [])

    def test_a_difference_that_passes_is_stale_and_fails_the_run(self):
        st.record("lss", "lss-fastscan-configured", "pass", "", "canopennode", "lely")
        doc = self.document()
        self.assertFalse(doc["waivers"][0]["reproduced"])
        self.assertTrue(interop_result.problems(doc))

    def test_an_all_skipped_run_fails(self):
        st.record("sdo", "block", "skip", "no block client", "lely", "lely")
        doc = self.document()
        self.assertEqual(doc["results"][0]["outcome"], "unsupported")
        self.assertTrue(interop_result.problems(doc))

    def test_a_single_stack_scenario_has_a_role(self):
        st.record("eds", "x.eds", "pass", "", "", "")
        self.assertEqual(self.document()["results"][0]["roles"], {"harness": "selftest"})


if __name__ == "__main__":
    unittest.main()
