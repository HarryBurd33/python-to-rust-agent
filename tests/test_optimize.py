"""Deterministic acceptance tests. Real measurements run through the demo separately."""

import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts" / "optimize.py"
SPEC = importlib.util.spec_from_file_location("optimize", SCRIPT)
optimize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(optimize)


def observations(seconds, memory):
    return [{"elapsed_seconds": seconds, "peak_rss_bytes": memory}] * 5


def workload(metrics, scope="workflow"):
    return {"scope": scope, "required": True, "metrics": metrics}


class GateTests(unittest.TestCase):
    def test_frozen_numeric_gate(self):
        baseline = observations(10, 100_000_000)
        passing = optimize.compare_metrics(baseline, observations(7, 70_000_000), True)
        failing = optimize.compare_metrics(baseline, observations(7, 90_000_000), True)
        boundary = optimize.compare_metrics(baseline, observations(8, 80_000_000), True)
        self.assertEqual(optimize.recommendation([workload(passing)], True), "recommended for review")
        self.assertEqual(optimize.recommendation([workload(failing)], True), "insufficient benefit")
        self.assertEqual(optimize.recommendation([workload(boundary)], True), "recommended for review")
        self.assertEqual(passing["elapsed_seconds"]["reduction_percent"], 30)
        self.assertEqual(passing["peak_rss_bytes"]["reduction_percent"], 30)
        self.assertEqual(failing["peak_rss_bytes"]["reduction_percent"], 10)

    def test_helper_only_speedup_cannot_pass(self):
        baseline = observations(10, 100_000_000)
        fast = optimize.compare_metrics(baseline, observations(1, 10_000_000), True)
        slow = optimize.compare_metrics(baseline, observations(9, 90_000_000), True)
        helper = workload(fast, "component")
        self.assertEqual(optimize.recommendation([helper], True), "inconclusive")
        self.assertEqual(optimize.recommendation([helper, workload(slow)], True), "insufficient benefit")
        self.assertEqual(optimize.recommendation([workload(fast)], False), "inconclusive")

    def test_noisy_unmeasured_invalid_or_unscoped_data(self):
        baseline = observations(10, 100_000_000)
        noisy = observations(7, 70_000_000)[:4] + observations(5, 70_000_000)[:1]
        inconclusive = (
            optimize.compare_metrics(baseline, noisy, True),
            optimize.compare_metrics(baseline, observations(7, None), True),
            optimize.compare_metrics(baseline, observations(9, None), True),
            optimize.compare_metrics(baseline, observations(7, 70_000_000), False),
            optimize.compare_metrics(observations(0, 100_000_000), observations(7, 70_000_000), True),
            optimize.compare_metrics(baseline, observations(float("nan"), 70_000_000), True),
            optimize.compare_metrics(baseline[:4], observations(7, 70_000_000), True),
        )
        for metrics in inconclusive:
            self.assertEqual(optimize.recommendation([workload(metrics)], True), "inconclusive")


class InventoryTests(unittest.TestCase):
    def test_fixture_contents(self):
        result = optimize.manifest_inventory(PACKAGE / "fixtures" / "inventory.json")
        self.assertEqual(result["coverage"], "complete")
        markdown, compute = result["repositories"]
        self.assertEqual(markdown["status"], "excluded")
        self.assertEqual(markdown["markers"], ["SKILL.md"])
        self.assertEqual(compute["status"], "deferred")
        self.assertIn("worker.py", compute["python_files"])

    def test_partial_pages_ownership_and_access(self):
        data = {
            "schema_version": 1,
            "scope": {"identity": "owner", "include_affiliated": False},
            "terminal_page_verified": False,
            "pages": [
                {"cursor": None, "next_cursor": "next", "status": "ok", "repositories": [
                    {"repository": "other/something", "access": "readable"},
                    {"repository": "owner/inaccessible", "access": "unavailable", "reason": "403"},
                ]},
                {"cursor": "bad", "next_cursor": "another", "status": "unavailable", "reason": "page unavailable"},
            ],
        }
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "manifest.json"
            path.write_text(json.dumps(data))
            result = optimize.manifest_inventory(path)
        self.assertEqual(result["coverage"], "incomplete")
        for phrase in ("owner scope", "403", "pagination", "page unavailable", "terminal page"):
            self.assertTrue(any(phrase in issue for issue in result["issues"]), result)


class AttestationTests(unittest.TestCase):
    def test_attestation_binds_source_evaluator_and_exact_measurement_evidence(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source, candidate, runs = [root / name for name in ("source", "candidate", "runs")]
            config = {"memory_scope": "single_process"}
            record = {
                "repository": "owner/local", "component": "fixture", "source_sha": "a" * 40,
                "source_tree_sha256": "b" * 64, "contract_sha256": "c" * 64, "evaluator_sha256": "d" * 64,
            }
            evaluation = {
                "candidate_sha256": "e" * 64, "host": {"test": "synthetic"},
                "build_tree_sha256": "f" * 64, "versions": {"rust": {"version_output": "v1"}},
                "workloads": [{"elapsed_seconds": 1, "peak_rss_bytes": 1}],
            }
            evaluation_file = root / "synthetic-evaluation.json"
            optimize.save(evaluation_file, evaluation)
            binding = {
                "run_key": optimize.digest(optimize.json_bytes(record)),
                "evaluation_sha256": optimize.file_digest(evaluation_file),
            }
            path = root / "synthetic-attestation.json"
            evidence = {
                "schema_version": 1, "scope": "single_process", **binding,
                "contract_sha256": record["contract_sha256"], "candidate_sha256": evaluation["candidate_sha256"],
                "platform": platform.system(), "machine": platform.machine(),
                "host_sha256": optimize.digest(optimize.json_bytes(evaluation["host"])),
                "reviewer": "synthetic", "audit": "synthetic", "rss_validation": "synthetic",
            }

            def validate(current_binding, current_evaluation):
                return optimize.process_attestation(
                    path, current_binding, record["contract_sha256"], config,
                    current_evaluation, source, candidate, runs,
                )

            optimize.save(path, evidence)
            self.assertEqual(validate(binding, evaluation)["evidence"], evidence)
            for field in ("source_tree_sha256", "evaluator_sha256"):
                with self.subTest(field=field):
                    updated_record = dict(record, **{field: "9" * 64})
                    stale = dict(binding, run_key=optimize.digest(optimize.json_bytes(updated_record)))
                    with self.assertRaisesRegex(optimize.InputError, "attestation run identity mismatch"):
                        validate(stale, evaluation)
            for field, value in (
                ("build_tree_sha256", "8" * 64),
                ("versions", {"rust": {"version_output": "v2"}}),
                ("workloads", [{"elapsed_seconds": 2, "peak_rss_bytes": 1}]),
            ):
                with self.subTest(field=field):
                    updated_evaluation = dict(evaluation, **{field: value})
                    optimize.save(evaluation_file, updated_evaluation)
                    stale = dict(binding, evaluation_sha256=optimize.file_digest(evaluation_file))
                    with self.assertRaisesRegex(optimize.InputError, "attestation evaluation evidence mismatch"):
                        validate(stale, updated_evaluation)
            for field in binding:
                with self.subTest(missing=field):
                    optimize.save(path, {key: value for key, value in evidence.items() if key != field})
                    with self.assertRaisesRegex(optimize.InputError, "attestation .* mismatch"):
                        validate(binding, evaluation)


class LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "source.txt").write_text("original\n")
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.candidate / "candidate.txt").write_text("candidate\n")
        self.runs = self.root / "runs"
        self.contract = self.root / "contract.json"
        args = ["{python}", "-c", "print('example')"]
        self.config = {
            "schema_version": 1,
            "repository": "owner/local",
            "component": "fixture",
            "source_root": "source",
            "source_sha": "a" * 40,
            "memory_scope": "unknown",
            "full_workflow_representative": True,
            "representativeness": "Synthetic CLI test boundary.",
            "build_profile": "Synthetic command fixture only.",
            "trials": 5,
            "timeout_seconds": 2,
            "invocation_seconds": 20,
            "build": ["{python}", "-c", "pass"],
            "versions": {"python": [sys.executable, "--version"], "rust": [sys.executable, "--version"]},
            "cases": [{
                "id": "basic", "scope": "workflow", "baseline": args[:], "candidate": args[:],
                "expect": {"exit_code": 0, "stdout": "example\n", "stderr": "", "effects": []},
            }],
            "workloads": [{
                "id": "work", "scope": "workflow", "baseline": args[:], "candidate": args[:],
                "required": True, "description": "Synthetic command fixture.",
            }],
        }

    def write_config(self):
        payload = json.dumps(self.config).encode()
        self.contract.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def cli(self, action, *, digest=None, returncode=0, extra=()):
        args = [sys.executable, str(SCRIPT), action, str(self.contract), "--runs", str(self.runs)]
        if action != "prepare":
            args.extend(("--expect-contract", digest or self.write_config()))
        if action == "evaluate":
            args.extend(("--candidate", str(self.candidate)))
        args.extend(extra)
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(result.returncode, returncode, result.stdout + result.stderr)
        return json.loads(result.stdout if returncode == 0 else result.stderr)

    def reconcile(self, run_key):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "reconcile", "--runs", str(self.runs),
             "--run-key", run_key, "--note", "Test confirmed wait4 reaped the timed-out command."],
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_broken_baseline_never_runs_candidate(self):
        self.config["cases"][0]["expect"]["stderr"] = "bad\n"
        self.assertEqual(self.cli("baseline")["status"], "broken baseline")
        result = self.cli("evaluate")
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(list(Path(result["run_directory"]).glob("attempt-*")))

    def test_wrong_output_error_and_file_effects_are_rejected(self):
        self.config["cases"][0]["candidate"] = ["{python}", "-c", "import pathlib,sys; pathlib.Path('extra').write_text('x'); print('wrong'); print('unexpected',file=sys.stderr)"]
        self.assertEqual(self.cli("baseline")["status"], "passed")
        result = self.cli("evaluate")
        self.assertEqual(result["status"], "failed correctness")
        report = json.loads(Path(result["report_json"]).read_text())
        mismatch = report["parity"]["basic"]
        for key in ("stdout", "stderr", "effects"):
            self.assertNotEqual(mismatch["baseline"][key], mismatch["candidate"][key])
        self.assertEqual(report["workloads"], [])
        self.assertFalse((self.candidate / "extra").exists())
        self.assertEqual((self.source / "source.txt").read_text(), "original\n")

    def test_unchanged_reuses_and_candidate_or_source_changes_invalidate(self):
        first = self.cli("baseline")
        result = self.cli("evaluate")
        self.assertFalse(result["reused_evaluation"])
        again = self.cli("evaluate")
        self.assertTrue(again["reused_evaluation"])
        self.assertEqual(first["run_key"], again["run_key"])
        self.assertEqual(first["run_directory"], again["run_directory"])
        self.assertEqual(result["report_json"], again["report_json"])
        (self.candidate / "candidate.txt").write_text("changed candidate\n")
        changed_candidate = self.cli("evaluate")
        self.assertFalse(changed_candidate["reused_evaluation"])
        self.assertEqual(first["run_key"], changed_candidate["run_key"])
        self.assertNotEqual(result["report_json"], changed_candidate["report_json"])
        self.config["source_sha"] = "b" * 40
        self.write_config()
        changed_sha = self.cli("prepare")
        self.assertNotEqual(first["run_key"], changed_sha["run_key"])
        self.assertEqual(changed_sha["status"], "prepared")
        (self.source / "source.txt").write_text("changed source\n")
        changed_tree = self.cli("prepare")
        self.assertNotEqual(changed_sha["run_key"], changed_tree["run_key"])
        self.assertEqual(changed_tree["status"], "prepared")
        self.config["trials"] = 7
        self.write_config()
        self.assertNotEqual(changed_tree["run_key"], self.cli("prepare")["run_key"])

    def test_uncertain_timeout_blocks_distinct_process_until_reconciliation(self):
        self.config["timeout_seconds"] = 0.2
        self.config["cases"][0]["baseline"] = ["{python}", "-c", "import time; time.sleep(1)"]
        first = self.cli("baseline", returncode=3)
        self.assertEqual(first["status"], "uncertain")
        blocked = self.cli("baseline", returncode=3)
        self.assertIn("reconciliation", blocked["reason"])
        run, = [item for item in self.runs.iterdir() if item.is_dir()]
        self.assertEqual(self.reconcile(run.name)["status"], "reconciled")
        self.assertEqual(self.cli("baseline")["status"], "broken baseline")
        self.assertEqual(self.cli("evaluate")["status"], "inconclusive")

    def test_cached_probe_timeout_blocks_execution_until_reconciled(self):
        sleep_flag, launches = self.root / "sleep-on-version", self.root / "version-launches"
        self.config["timeout_seconds"] = 0.5
        self.config["versions"]["rust"] = [
            sys.executable, "-c",
            "from pathlib import Path; import time; "
            f"with_file = Path({str(launches)!r}); with_file.open('a').write('1'); "
            f"time.sleep(5) if Path({str(sleep_flag)!r}).exists() else None; print('fixed version')",
        ]
        self.assertEqual(self.cli("baseline")["status"], "passed")
        first = self.cli("evaluate")
        run = Path(first["run_directory"])
        attempt = Path(first["report_json"]).parent
        evaluation = attempt / "evaluation.json"
        original_evaluation = evaluation.read_bytes()
        report = json.loads(Path(first["report_json"]).read_text())
        self.assertEqual(report["run_key"], first["run_key"])
        self.assertEqual(report["evaluation_sha256"], hashlib.sha256(original_evaluation).hexdigest())
        self.assertEqual(launches.read_text(), "1")

        sleep_flag.touch()
        timeout = self.cli("evaluate", returncode=3)
        self.assertIn("cache probe evidence", timeout["reason"])
        state_path = run / "state.json"
        failed_state = json.loads(state_path.read_text())
        self.assertEqual((failed_state["status"], failed_state["active"]), ("uncertain", "cache-probe"))
        probe, = failed_state["cache_probes"]
        self.assertEqual(probe["status"], "uncertain")
        self.assertEqual(probe["evaluation_sha256"], report["evaluation_sha256"])
        self.assertEqual(probe["versions"], self.config["versions"])
        observation = json.loads((run / probe["directory"] / "versions" / "rust.json").read_text())
        self.assertEqual(observation["argv"], self.config["versions"]["rust"])
        self.assertTrue(observation["timeout"])

        sleep_flag.unlink()
        held = self.cli("evaluate", returncode=3)
        self.assertIn("reconciliation", held["reason"])
        self.assertEqual(launches.read_text(), "11")
        self.assertEqual(self.reconcile(first["run_key"])["status"], "reconciled")
        self.assertEqual(launches.read_text(), "11")
        resumed = self.cli("evaluate")
        self.assertTrue(resumed["reused_evaluation"])
        self.assertEqual(resumed["report_json"], first["report_json"])
        self.assertEqual(launches.read_text(), "111")
        recovered_state = json.loads(state_path.read_text())
        self.assertEqual((recovered_state["status"], recovered_state["active"]), ("evaluated", None))
        self.assertEqual([entry["status"] for entry in recovered_state["cache_probes"]], ["interrupted", "matched"])
        self.assertEqual(recovered_state["attempts"], failed_state["attempts"])
        self.assertEqual(evaluation.read_bytes(), original_evaluation)

    def test_changed_toolchain_version_refuses_cached_measurements(self):
        tool_version = self.root / "version.txt"
        tool_version.write_text("v1")
        self.config["versions"]["rust"] = [sys.executable, "-c", f"print(open({str(tool_version)!r}).read())"]
        self.assertEqual(self.cli("baseline")["status"], "passed")
        first = self.cli("evaluate")
        self.assertFalse(first["reused_evaluation"])
        self.assertTrue(self.cli("evaluate")["reused_evaluation"])
        tool_version.write_text("v2")
        rejected = self.cli("evaluate", returncode=2)
        self.assertIn("version or dependency evidence changed", rejected["reason"])
        run = Path(first["run_directory"])
        self.assertEqual(len(list(run.glob("attempt-*"))), 1)
        state = json.loads((run / "state.json").read_text())
        self.assertEqual((state["status"], state["active"]), ("evaluated", None))
        self.assertEqual([entry["status"] for entry in state["cache_probes"]], ["matched", "rejected"])

    def test_malformed_contract_and_no_baseline(self):
        good_sha = self.write_config()
        self.assertIn("freeze and execute baseline", self.cli("evaluate", digest=good_sha, returncode=2)["reason"])
        self.config["trials"] = 0
        self.assertIn("trials", self.cli("baseline", returncode=2)["reason"])
        self.assertIn("SHA-256 mismatch", self.cli("baseline", digest=good_sha, returncode=2)["reason"])


if __name__ == "__main__":
    unittest.main()
