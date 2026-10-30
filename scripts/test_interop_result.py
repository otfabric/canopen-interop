#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the verdict of scripts/interop_result.py: python3 -m unittest discover -s scripts"""

import copy
import unittest

import interop_result as ir


def result(outcome, **kw):
    r = {"id": "suite.scenario", "suite": "suite", "roles": {"client": "a", "server": "b"},
         "outcome": outcome, "durationMs": 1}
    if outcome != "pass":
        r["reason"] = "why"
    r.update(kw)
    return r


def doc(*results, **kw):
    d = {"schemaVersion": "1.0", "repository": "otfabric/example", "gitSha": "abcdef0",
         "startedAt": "2026-10-30T00:00:00Z", "finishedAt": "2026-10-30T00:00:01Z",
         "host": {"os": "linux", "arch": "x86_64"}, "implementations": [{"name": "a", "version": "1"}],
         "reproduce": "make", "results": list(results)}
    d.update(kw)
    d["summary"] = ir.summarize(d["results"])
    return d


class Verdict(unittest.TestCase):
    def test_a_pass_passes(self):
        self.assertEqual(ir.problems(doc(result("pass"))), [])

    def test_zero_passes_fail(self):
        for d in (doc(), doc(result("unsupported"), result("not-tested")),
                  doc(result("known-difference", waiver="w"), waivers=[{"id": "w", "evidence": "https://e", "reproduced": True}])):
            self.assertTrue(ir.problems(d), d["results"])

    def test_floor(self):
        self.assertTrue(ir.problems(doc(result("pass")), floor=2))

    def test_fail_and_infra_error_fail(self):
        self.assertTrue(ir.problems(doc(result("pass"), result("fail"))))
        self.assertTrue(ir.problems(doc(result("pass"), result("infra-error"))))

    def test_gate_covers_only_its_implementation(self):
        third_party = doc(result("pass"), result("fail"), gate="sut")
        self.assertEqual(ir.problems(third_party), [])
        gated = doc(result("pass"), result("fail", roles={"client": "sut"}), gate="sut")
        self.assertTrue(ir.problems(gated))

    def test_stale_waiver_fails_even_with_a_gate(self):
        waivers = [{"id": "w", "evidence": "https://e", "reproduced": False}]
        self.assertTrue(ir.problems(doc(result("pass"), waivers=waivers)))
        self.assertTrue(ir.problems(doc(result("pass"), waivers=waivers, gate="sut")))

    def test_summary_counts_every_outcome(self):
        d = doc(*[result(o) for o in ir.OUTCOMES])
        self.assertEqual(set(d["summary"].values()), {1})
        self.assertEqual(set(d["summary"]), set(ir.OUTCOMES))

    def test_schema_accepts_and_rejects(self):
        good = doc(result("pass"), result("known-difference", waiver="w"),
                   waivers=[{"id": "w", "evidence": "https://example.com/x", "reproduced": True}])
        self.assertEqual(ir.schema_errors(good), [])
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema is not installed")
        bad = copy.deepcopy(good)
        bad["results"][0]["id"] = "A display string"
        self.assertTrue(ir.schema_errors(bad))


if __name__ == "__main__":
    unittest.main()
