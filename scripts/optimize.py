#!/usr/bin/env python3
"""Local Python-to-Rust evidence runner. Commands must be reviewed before execution."""

import argparse
import contextlib
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import statistics
import string
import subprocess
import sys
import tempfile
import time


PACKAGE = Path(__file__).resolve().parents[1]
VERSION = 1
MIN_TRIALS = 5
MAX_TRIALS = 21
MAX_ATTEMPTS = 3
MAX_SECONDS = 3600
MAX_SPREAD = 0.15
FACTOR = 0.80
SHA = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
ENV_KEYS = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "RUSTUP_HOME", "CARGO_HOME")


class InputError(ValueError):
    pass


class Uncertain(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise InputError(message)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode()


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=lambda value: fail(f"invalid JSON number: {value}"))


def fail(message):
    raise InputError(message)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def positive(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def inside(path, directory):
    return path == directory or directory in path.parents


def separate(left, right):
    require(not inside(left, right) and not inside(right, left), f"paths must be separate: {left} and {right}")


def tree(root, *, exclude_git=False):
    require(root.is_dir(), f"missing directory: {root}")
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if exclude_git and ".git" in relative.parts:
            continue
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            entries.append([relative.as_posix(), "dir", mode])
        elif stat.S_ISREG(info.st_mode):
            entries.append([relative.as_posix(), "file", mode, info.st_size, file_digest(path)])
        else:
            fail(f"unsupported symlink or special file in tree: {path}")
    return entries


def tree_digest(root, *, exclude_git=False):
    return digest(json_bytes(tree(root, exclude_git=exclude_git)))


def copy_tree(source, target, expected, *, exclude_git=False):
    ignore = shutil.ignore_patterns(".git") if exclude_git else None
    shutil.copytree(source, target, symlinks=True, ignore=ignore)
    require(tree_digest(target) == expected, "snapshot contents differ from frozen source")


def manifest_inventory(path):
    manifest = read_json(path)
    require(isinstance(manifest, dict) and manifest.get("schema_version") == VERSION, "invalid inventory manifest version")
    scope = manifest.get("scope")
    require(isinstance(scope, dict) and nonempty_string(scope.get("identity")), "inventory requires an explicit scope identity")
    require(type(scope.get("include_affiliated")) is bool, "scope include_affiliated must be a boolean")
    pages = manifest.get("pages")
    require(isinstance(pages, list), "inventory pages must be a list")
    problems, repositories = [], []
    seen_repositories, seen_cursors = set(), set()
    expected_cursor = None
    for index, page in enumerate(pages):
        require(isinstance(page, dict), "invalid inventory page")
        page_status = page.get("status")
        require(page_status in ("ok", "unavailable"), "invalid page status")
        cursor = page.get("cursor")
        next_cursor = page.get("next_cursor")
        require(cursor is None or nonempty_string(cursor), "invalid page cursor")
        require(next_cursor is None or nonempty_string(next_cursor), "invalid next cursor")
        if (index and expected_cursor is None) or cursor != expected_cursor or cursor in seen_cursors:
            problems.append(f"page {index + 1}: broken, duplicate or unexpected pagination cursor")
        seen_cursors.add(cursor)
        expected_cursor = next_cursor
        if page_status == "unavailable":
            problems.append(f"page {index + 1}: {page.get('reason') or 'unavailable'}")
        entries = page.get("repositories", [])
        require(isinstance(entries, list), "page repositories must be a list")
        for repo in entries:
            require(isinstance(repo, dict) and nonempty_string(repo.get("repository")), "repository identity required")
            name = repo["repository"]
            owner, separator, slug = name.partition("/")
            require(separator and nonempty_string(owner) and nonempty_string(slug) and "/" not in slug, "repository must be owner/name")
            if name in seen_repositories:
                problems.append(f"duplicate repository: {name}")
                continue
            seen_repositories.add(name)
            if not scope["include_affiliated"] and owner.casefold() != scope["identity"].casefold():
                problems.append(f"{name}: repository is outside the declared owner scope")
                repositories.append({
                    "repository": name, "status": "excluded", "reason": "outside declared owner scope",
                    **{key: repo.get(key) for key in ("source_url", "source_sha", "archived", "fork", "access")},
                })
                continue
            entry = inventory_entry(path.parent, repo)
            repositories.append(entry)
            if entry["status"] == "inconclusive":
                problems.append(f"{name}: {entry['reason']}")
    if not pages or expected_cursor is not None or manifest.get("terminal_page_verified") is not True:
        problems.append("terminal page was not verified")
    return {
        "schema_version": VERSION, "manifest_sha256": file_digest(path), "scope": scope,
        "coverage": "complete" if not problems else "incomplete",
        "pages_observed": len(pages), "issues": problems, "repositories": repositories,
    }


def inventory_entry(root, repo):
    fields = ("repository", "source_url", "source_sha", "archived", "fork", "access", "exclusion")
    entry = {key: repo.get(key) for key in fields}
    url = entry["source_url"]
    sha = entry["source_sha"]
    if entry["access"] != "readable":
        return dict(entry, status="inconclusive", reason=repo.get("reason") or "repository contents unavailable")
    if not nonempty_string(url) or not url.startswith("https://") or not isinstance(sha, str) or not COMMIT.fullmatch(sha):
        return dict(entry, status="inconclusive", reason="missing source URL or commit identity")
    if type(entry["archived"]) is not bool or type(entry["fork"]) is not bool:
        return dict(entry, status="inconclusive", reason="archived or fork status unavailable")
    if nonempty_string(entry["exclusion"]):
        return dict(entry, status="excluded", reason=entry["exclusion"])
    location = repo.get("local_path")
    require(nonempty_string(location), f"readable repository requires local_path: {entry['repository']}")
    checkout = (root / location).resolve()
    try:
        files = [item[0] for item in tree(checkout, exclude_git=True) if item[1] == "file"]
    except (InputError, OSError) as error:
        return dict(entry, status="inconclusive", reason=str(error))
    markers = [
        item for item in files if Path(item).name in ("SKILL.md", "AGENT.md")
        or (Path(item).name == "agent.json" and (Path(item).parent / "PLAYBOOK.md").as_posix() in files)
    ]
    python = [item for item in files if Path(item).suffix == ".py"]
    rust = [item for item in files if Path(item).suffix == ".rs" or Path(item).name == "Cargo.toml"]
    entry.update(markers=markers, python_files=python, rust_files=rust)
    if not markers:
        return dict(entry, status="excluded", reason="no skill or agent markers observed")
    if not python:
        return dict(entry, status="excluded", reason="skill or agent markers observed without Python source")
    return dict(entry, status="deferred", reason="inspect actual invocation, dependency feasibility and representative costs")


def command_spec(argv, label, fields):
    require(isinstance(argv, list) and argv and all(isinstance(part, str) and "\0" not in part for part in argv), f"{label}: argv must be a nonempty string list")
    require(bool(argv[0].strip()), f"{label}: missing executable")
    for part in argv:
        for _, field, spec, conversion in string.Formatter().parse(part):
            require(field is None or (field in fields and not spec and conversion is None), f"{label}: invalid placeholder {{{field}}}")


def validate_config(config):
    require(isinstance(config, dict) and config.get("schema_version") == VERSION, "invalid contract version")
    for key in ("repository", "component", "source_root"):
        require(nonempty_string(config.get(key)), f"missing {key}")
    require(isinstance(config.get("source_sha"), str) and COMMIT.fullmatch(config["source_sha"]), "source_sha must be a lowercase commit hash")
    require(config.get("memory_scope") in ("single_process", "process_tree", "unknown"), "invalid memory_scope")
    require(type(config.get("full_workflow_representative")) is bool, "full_workflow_representative must be a boolean")
    require(nonempty_string(config.get("representativeness")), "representativeness explanation required")
    require(nonempty_string(config.get("build_profile")), "build profile explanation required")
    require(type(config.get("trials")) is int and MIN_TRIALS <= config["trials"] <= MAX_TRIALS, "trials must be an integer in [5, 21]")
    for key, ceiling in (("timeout_seconds", 300), ("invocation_seconds", MAX_SECONDS)):
        require(positive(config.get(key)) and config[key] <= ceiling, f"{key} must be positive and <= {ceiling}")
    commands = config.get("versions")
    require(isinstance(commands, dict) and {"python", "rust"}.issubset(commands), "versions must include python and rust argv")
    for name, argv in commands.items():
        require(LABEL.fullmatch(name), "invalid toolchain version identifier")
        command_spec(argv, f"version {name}", set())
    command_spec(config.get("build"), "build", {"source", "candidate", "build", "python"})
    names = set()
    for group in ("cases", "workloads"):
        items = config.get(group)
        require(isinstance(items, list) and bool(items), f"{group} must be a nonempty list")
        for item in items:
            require(isinstance(item, dict) and isinstance(item.get("id"), str) and LABEL.fullmatch(item["id"]), f"invalid {group} id")
            identifier = item["id"]
            require(identifier not in names, f"duplicate case/workload id: {identifier}")
            names.add(identifier)
            require(item.get("scope") in ("component", "workflow"), f"{identifier}: scope must be component or workflow")
            for side, fields in (("baseline", {"source", "work", "python"}), ("candidate", {"source", "candidate", "build", "work", "python"})):
                command_spec(item.get(side), f"{identifier} {side}", fields)
            if group == "cases":
                expected = item.get("expect")
                require(isinstance(expected, dict), f"{identifier}: independent expected behavior required")
                require(type(expected.get("exit_code")) is int and 0 <= expected["exit_code"] <= 255, f"{identifier}: invalid expected exit code")
                require(all(isinstance(expected.get(key), str) for key in ("stdout", "stderr")), f"{identifier}: stdout and stderr expectations must be UTF-8 strings")
                effects = expected.get("effects")
                require(isinstance(effects, list) and all(isinstance(entry, list) for entry in effects), f"{identifier}: effects must list expected work-directory entries")
            else:
                require(type(item.get("required")) is bool, f"{identifier}: required must be a boolean")
                require(nonempty_string(item.get("description")), f"{identifier}: workload description required")
    require(any(item["scope"] == "workflow" for item in config["cases"]), "at least one workflow correctness case is required")
    require(any(item["scope"] == "workflow" and item["required"] for item in config["workloads"]), "at least one required workflow workload is required")
    return config


def frozen_config(path, expected=None):
    payload = path.read_bytes()
    actual = digest(payload)
    if expected is not None:
        require(SHA.fullmatch(expected) and actual == expected, f"contract SHA-256 mismatch, observed {actual}")
    try:
        config = json.loads(payload, parse_constant=lambda value: fail(f"invalid JSON number: {value}"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid contract JSON: {error}")
    return validate_config(config), actual, payload


def identity(path, config, contract_sha, runs):
    source = (path.parent / config["source_root"]).resolve()
    separate(source, runs)
    require(not inside(path, source), "frozen contract must be outside source root")
    require(not inside(Path(__file__).resolve(), source), "evaluator must be outside source root")
    content_sha = tree_digest(source, exclude_git=True)
    record = {
        "repository": config["repository"], "source_sha": config["source_sha"],
        "source_tree_sha256": content_sha, "component": config["component"],
        "contract_sha256": contract_sha, "evaluator_sha256": file_digest(Path(__file__)),
    }
    return source, record, digest(json_bytes(record))


@contextlib.contextmanager
def exclusive(runs):
    require(os.name == "posix", "execution requires a POSIX host")
    import fcntl

    runs.mkdir(parents=True, exist_ok=True)
    with (runs / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InputError("another local invocation owns this runs directory") from error
        yield


def ensure_quiescent(runs):
    for path in sorted(runs.glob("*/state.json")):
        state = read_json(path)
        if state.get("status") in ("running", "uncertain"):
            raise Uncertain(f"prior outcome requires reconciliation: {path}")


def create_run(runs, run_key, source, record, payload):
    destination = runs / run_key
    if destination.exists():
        require(read_json(destination / "identity.json") == record, "existing run identity conflict")
        require(file_digest(destination / "contract.json") == record["contract_sha256"], "frozen contract snapshot changed")
        require(tree_digest(destination / "source") == record["source_tree_sha256"], "frozen source snapshot changed")
        return destination
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=runs) as scratch:
        staged = Path(scratch) / run_key
        staged.mkdir()
        copy_tree(source, staged / "source", record["source_tree_sha256"], exclude_git=True)
        (staged / "contract.json").write_bytes(payload)
        save(staged / "identity.json", record)
        save(staged / "state.json", {"status": "prepared", "baseline": None, "attempts": []})
        staged.rename(destination)
    return destination


def runtime_environment():
    environment = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    environment.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1", CARGO_NET_OFFLINE="true")
    return environment


def process_group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def kill_group(pid):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def wait_for_child(child, deadline):
    while True:
        pid, status, usage = os.wait4(child.pid, os.WNOHANG)
        if pid:
            return os.waitstatus_to_exitcode(status), usage.ru_maxrss, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            kill_group(child.pid)
            _, status, usage = os.wait4(child.pid, 0)
            return os.waitstatus_to_exitcode(status), usage.ru_maxrss, True
        time.sleep(min(0.0005, remaining))


def stream_signature(path):
    return {"size": path.stat().st_size, "sha256": file_digest(path)}


def measure(argv, cwd, environment, deadline, *, capture_version=False, record_effects=True):
    require(hasattr(os, "wait4"), "this POSIX host lacks wait4")
    require(time.monotonic() < deadline, "invocation time budget exhausted before command")
    with tempfile.TemporaryDirectory(prefix=".stdio-", dir=cwd.parent) as scratch:
        stdout, stderr = Path(scratch) / "stdout", Path(scratch) / "stderr"
        started = time.monotonic()
        with stdout.open("wb") as out, stderr.open("wb") as err:
            child = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            try:
                child.returncode, rss, timeout = wait_for_child(child, deadline)
            except BaseException:
                kill_group(child.pid)
                with contextlib.suppress(ChildProcessError):
                    _, status, _ = os.wait4(child.pid, 0)
                    child.returncode = os.waitstatus_to_exitcode(status)
                raise
        duration = time.monotonic() - started
        residual = process_group_exists(child.pid)
        if residual:
            kill_group(child.pid)
        system = platform.system()
        unit = {"Darwin": "bytes", "Linux": "KiB"}.get(system, "unknown")
        peak_bytes = (rss * 1024 if unit == "KiB" else rss) if unit != "unknown" and rss > 0 else None
        observed = {
            "argv": argv, "cwd": str(cwd), "elapsed_seconds": duration,
            "peak_rss_bytes": peak_bytes, "wait4_ru_maxrss": rss, "wait4_ru_maxrss_unit": unit,
            "exit_code": child.returncode, "timeout": timeout, "residual_process_group": residual,
            "stdout": stream_signature(stdout), "stderr": stream_signature(stderr),
            "effects": tree(cwd) if record_effects and not (timeout or residual) else None,
        }
        if capture_version:
            with stdout.open("rb") as stream:
                observed["version_output"] = stream.read(2048).decode("utf-8", "replace").strip()
        return observed


def bounded_measure(argv, cwd, environment, deadline, config, **kwargs):
    stop = min(deadline, time.monotonic() + config["timeout_seconds"])
    return measure(argv, cwd, environment, stop, **kwargs)


def checked_observation(path, result):
    save(path, result)
    if result["timeout"] or result["residual_process_group"]:
        raise Uncertain(f"command exceeded its bound or left a live process group; inspect {path}")
    return result


def parity(result):
    return {key: result[key] for key in ("exit_code", "stdout", "stderr", "effects")}


def oracle(item):
    expected = item["expect"]
    value = {"exit_code": expected["exit_code"], "effects": expected["effects"]}
    for key in ("stdout", "stderr"):
        data = expected[key].encode()
        value[key] = {"size": len(data), "sha256": digest(data)}
    return value


def expand(argv, values):
    return [part.format_map(values) for part in argv]


def execute_case(item, side, paths, work_parent, evidence, environment, deadline, config):
    with tempfile.TemporaryDirectory(prefix=".work-", dir=work_parent) as scratch:
        work = Path(scratch)
        argv = expand(item[side], {**paths, "work": str(work)})
        result = bounded_measure(argv, work, environment, deadline, config)
        return checked_observation(evidence, result)


def metric_summary(values):
    if not isinstance(values, list) or len(values) < MIN_TRIALS or not all(positive(value) for value in values):
        return None
    center = statistics.median(values)
    return {"median": center, "min": min(values), "max": max(values), "range_over_median": (max(values) - min(values)) / center}


def compare_metrics(baseline, candidate, comparable_memory):
    metrics = {}
    for key in ("elapsed_seconds", "peak_rss_bytes"):
        left = metric_summary([row.get(key) for row in baseline])
        right = metric_summary([row.get(key) for row in candidate])
        reduction = 100 * (left["median"] - right["median"]) / left["median"] if left and right else None
        result = "inconclusive"
        reason = "missing, invalid or insufficient repeated measurements"
        if key == "peak_rss_bytes" and not comparable_memory:
            reason = "OS peak RSS was not verified to cover both complete measured process scopes"
        elif left and right:
            if max(left["range_over_median"], right["range_over_median"]) > MAX_SPREAD:
                reason = "trial spread exceeds 15% of the corresponding median"
            elif right["max"] <= FACTOR * left["min"]:
                result, reason = "pass", "every candidate trial is at most 80% of the fastest/smallest baseline trial"
            elif right["min"] > FACTOR * left["max"]:
                result, reason = "fail", "even the most favorable observed pairing misses the 20% reduction"
            else:
                reason = "observed range straddles the threshold"
        metrics[key] = {"baseline": left, "candidate": right, "reduction_percent": reduction, "result": result, "reason": reason}
    return metrics


def recommendation(workloads, representative):
    required = [item for item in workloads if item["required"]]
    if not representative or not any(item["scope"] == "workflow" for item in required):
        return "inconclusive"
    statuses = {metric["result"] for item in required for metric in item["metrics"].values()}
    if "inconclusive" in statuses:
        return "inconclusive"
    if "fail" in statuses:
        return "insufficient benefit"
    if statuses == {"pass"}:
        return "recommended for review"
    return "inconclusive"


def process_attestation(path, binding, contract_sha, config, evaluation, source, candidate, runs):
    if path is None:
        return None
    path = path.resolve()
    require(not any(inside(path, root) for root in (source, candidate, runs)), "attestation must be outside source, candidate and run writes")
    evidence = read_json(path)
    require(isinstance(evidence, dict) and evidence.get("schema_version") == VERSION, "invalid attestation")
    require(evidence.get("scope") == "single_process" and config["memory_scope"] == "single_process", "only audited single-process scopes can be attested")
    require(evidence.get("contract_sha256") == contract_sha and evidence.get("candidate_sha256") == evaluation["candidate_sha256"], "attestation identity mismatch")
    require(evidence.get("run_key") == binding["run_key"], "attestation run identity mismatch")
    require(evidence.get("evaluation_sha256") == binding["evaluation_sha256"], "attestation evaluation evidence mismatch")
    require(evidence.get("platform") == platform.system() and evidence.get("machine") == platform.machine(), "attestation host mismatch")
    require(evidence.get("host_sha256") == digest(json_bytes(evaluation["host"])), "attestation host evidence mismatch")
    for field in ("reviewer", "audit", "rss_validation"):
        require(nonempty_string(evidence.get(field)), f"attestation requires {field}")
    return {"sha256": file_digest(path), "evidence": evidence}


def host_info(environment):
    return {
        "platform": platform.system(), "release": platform.release(), "machine": platform.machine(),
        "cpu_count": os.cpu_count(), "runner_python": sys.version, "runner_executable": sys.executable,
        "environment": environment,
        "memory_method": "wait4 ru_maxrss for the directly launched PID; Darwin bytes, Linux KiB",
        "timing_method": "monotonic launch-to-reap wall time; wait4 sampled at up to 0.5 ms sleep intervals",
    }


def baseline_run(run, state, config, record, source_origin, deadline):
    if state["baseline"] is not None:
        return read_json(run / "baseline" / "summary.json")
    directory = run / "baseline"
    state.update(status="running", active="baseline", started=utc_now())
    save(run / "state.json", state)
    directory.mkdir()
    env = runtime_environment()
    summary = {"status": "passed", "observations": {}, "host": host_info(env)}
    try:
        for item in config["cases"] + config["workloads"]:
            observation = execute_case(item, "baseline", {"source": str(run / "source"), "python": sys.executable}, directory, directory / f'{item["id"]}.json', env, deadline, config)
            summary["observations"][item["id"]] = parity(observation)
            if "expect" in item and parity(observation) != oracle(item):
                summary.update(status="broken baseline", reason=f'{item["id"]}: independent oracle mismatch')
                break
            if "expect" not in item and observation["exit_code"] != 0:
                summary.update(status="broken baseline", reason=f'{item["id"]}: workload exited nonzero')
                break
        require(tree_digest(run / "source") == record["source_tree_sha256"], "baseline command modified frozen source snapshot")
        require(tree_digest(source_origin, exclude_git=True) == record["source_tree_sha256"], "source origin changed during baseline")
    except (OSError, InputError) as error:
        summary.update(status="broken baseline", reason=str(error))
    except (Uncertain, KeyboardInterrupt) as error:
        state.update(status="uncertain", reason=str(error) or "baseline interrupted")
        save(run / "state.json", state)
        raise
    save(directory / "summary.json", summary)
    state.update(status="baseline recorded", baseline=summary["status"], active=None)
    save(run / "state.json", state)
    return summary


def check_unchanged(run, attempt_dir, origin, candidate, record, candidate_sha, build_sha):
    require(tree_digest(run / "source") == record["source_tree_sha256"], "frozen source snapshot changed during evaluation")
    require(tree_digest(origin, exclude_git=True) == record["source_tree_sha256"], "source origin changed during evaluation")
    require(tree_digest(attempt_dir / "candidate") == candidate_sha, "candidate input snapshot changed during evaluation")
    require(tree_digest(candidate, exclude_git=True) == candidate_sha, "candidate origin changed during evaluation")
    require(tree_digest(attempt_dir / "build") == build_sha, "built candidate artifacts changed during evaluation")
    require(file_digest(run / "contract.json") == record["contract_sha256"], "frozen contract changed during evaluation")
    require(file_digest(Path(__file__)) == record["evaluator_sha256"], "evaluator changed during evaluation")


def observe_versions(config, directory, env, deadline):
    observations = {}
    for name, argv in config["versions"].items():
        with tempfile.TemporaryDirectory(prefix=".version-", dir=directory) as scratch:
            result = bounded_measure(argv, Path(scratch), env, deadline, config, capture_version=True)
        checked_observation(directory / "versions" / f"{name}.json", result)
        require(result["exit_code"] == 0 and result["version_output"], f"{name} version command failed")
        observations[name] = result
    return observations


def check_cached_versions(run, state, config, attempt, evaluation, evaluation_sha, env, deadline):
    probes = state.setdefault("cache_probes", [])
    directory = run / f"cache-probe-{len(probes) + 1:04d}"
    probe = {
        "directory": directory.name, "attempt": attempt.name, "evaluation_sha256": evaluation_sha,
        "versions": config["versions"], "status": "running", "started": utc_now(),
    }
    probes.append(probe)
    state.update(status="running", active="cache-probe", started=probe["started"])
    save(run / "state.json", state)
    try:
        directory.mkdir()
        versions = observe_versions(config, directory, env, deadline)
        for name, observation in versions.items():
            require(parity(observation) == parity(evaluation["versions"][name]), f"{name} version or dependency evidence changed; freeze a new contract and baseline")
        require(file_digest(attempt / "evaluation.json") == evaluation_sha, "cached evaluation evidence changed during version probing")
    except (Uncertain, KeyboardInterrupt, OSError) as error:
        probe.update(status="uncertain", reason=str(error) or "cache probe interrupted")
        state.update(status="uncertain", reason=probe["reason"])
        save(run / "state.json", state)
        raise Uncertain(f'{probe["reason"]}; cache probe evidence: {directory}') from error
    except InputError as error:
        probe.update(status="rejected", reason=str(error), finished=utc_now())
        state.update(status="evaluated", active=None)
        save(run / "state.json", state)
        raise
    probe.update(status="matched", finished=utc_now())
    state.update(status="evaluated", active=None)
    save(run / "state.json", state)


def collect_evaluation(run, directory, config, record, baseline, origin, candidate, candidate_sha, deadline):
    env = runtime_environment()
    results = {"status": "inconclusive", "host": host_info(env), "versions": {}, "workloads": [], "parity": {}, "candidate_sha256": candidate_sha}
    source_snapshot = run / "source"
    candidate_snapshot, build = directory / "candidate", directory / "build"
    copy_tree(candidate, candidate_snapshot, candidate_sha, exclude_git=True)
    build.mkdir()
    paths = {"source": str(source_snapshot), "candidate": str(candidate_snapshot), "build": str(build), "python": sys.executable}
    if results["host"] != baseline["host"]:
        return dict(results, reason="host or benchmark environment changed since baseline")
    results["versions"] = observe_versions(config, directory, env, deadline)
    argv = expand(config["build"], paths)
    build_result = bounded_measure(argv, build, env, deadline, config, record_effects=False)
    results["build"] = checked_observation(directory / "build.json", build_result)
    if build_result["exit_code"] != 0:
        return dict(results, reason="candidate build failed")
    build_sha = tree_digest(build)
    results["build_tree_sha256"] = build_sha
    check_unchanged(run, directory, origin, candidate, record, candidate_sha, build_sha)

    for item in config["cases"] + config["workloads"]:
        identifier = item["id"]
        result = execute_case(item, "candidate", paths, directory, directory / "parity" / f"{identifier}.json", env, deadline, config)
        reference = baseline["observations"][identifier]
        candidate_observation = parity(result)
        match = reference == candidate_observation
        results["parity"][identifier] = {"match": match, "baseline": reference, "candidate": candidate_observation}
        if not match:
            check_unchanged(run, directory, origin, candidate, record, candidate_sha, build_sha)
            return dict(results, status="failed correctness", reason=f"{identifier}: observable behavior differs")
    check_unchanged(run, directory, origin, candidate, record, candidate_sha, build_sha)

    for index, item in enumerate(config["workloads"]):
        identifier = item["id"]
        workload = {key: item[key] for key in ("id", "scope", "required", "description")}
        workload["raw"] = {"baseline": [], "candidate": []}
        results["workloads"].append(workload)
        for trial in range(config["trials"]):
            sides = ("baseline", "candidate") if (index + trial) % 2 == 0 else ("candidate", "baseline")
            for side in sides:
                evidence = directory / "trials" / identifier / f"{trial + 1:02d}-{side}.json"
                result = execute_case(item, side, paths, directory, evidence, env, deadline, config)
                workload["raw"][side].append(result)
                if parity(result) != baseline["observations"][identifier]:
                    check_unchanged(run, directory, origin, candidate, record, candidate_sha, build_sha)
                    status = "failed correctness" if side == "candidate" else "inconclusive"
                    return dict(results, status=status, reason=f"{identifier}: {side} behavior changed during trial {trial + 1}")
    check_unchanged(run, directory, origin, candidate, record, candidate_sha, build_sha)
    return dict(results, status="measured", reason="paired parity and measurement trials completed")


def evaluation_attempt(run, state, config, record, baseline, origin, candidate, deadline):
    candidate_sha = tree_digest(candidate, exclude_git=True)
    for entry in state["attempts"]:
        if entry["candidate_sha256"] == candidate_sha:
            path = run / entry["directory"] / "evaluation.json"
            require(path.is_file(), "this candidate has a reconciled interruption and will not be executed again")
            return read_json(path), path.parent, True
    require(len(state["attempts"]) < MAX_ATTEMPTS, "candidate attempt limit reached for this run")
    number = len(state["attempts"]) + 1
    directory = run / f"attempt-{number:02d}-{candidate_sha[:12]}"
    entry = {"candidate_sha256": candidate_sha, "directory": directory.name, "status": "running", "started": utc_now()}
    state["attempts"].append(entry)
    state.update(status="running", active=directory.name, started=entry["started"])
    save(run / "state.json", state)
    directory.mkdir()
    try:
        evaluation = collect_evaluation(run, directory, config, record, baseline, origin, candidate, candidate_sha, deadline)
    except (OSError, InputError) as error:
        evaluation = {"status": "inconclusive", "reason": str(error), "candidate_sha256": candidate_sha, "workloads": [], "parity": {}}
    except (Uncertain, KeyboardInterrupt) as error:
        entry.update(status="uncertain", reason=str(error) or "candidate interrupted")
        state.update(status="uncertain", reason=entry["reason"])
        save(run / "state.json", state)
        raise
    save(directory / "evaluation.json", evaluation)
    entry.update(status=evaluation["status"], finished=utc_now())
    state.update(status="evaluated", active=None)
    save(run / "state.json", state)
    return evaluation, directory, False


def format_number(value, digits=4):
    return f"{value:.{digits}f}" if value is not None else "unavailable"


def markdown_report(report):
    lines = [
        "# Python-to-Rust evaluation", "",
        f'**Status** {report["status"]}', "", f'{report["reason"]}', "",
        f'- Repository and component `{report["identity"]["repository"]}` / `{report["identity"]["component"]}`',
        f'- Source commit `{report["identity"]["source_sha"]}`',
        f'- Frozen contract `{report["identity"]["contract_sha256"]}`',
        f'- Candidate snapshot SHA-256 `{report.get("candidate_sha256", "unavailable")}`',
        f'- Candidate build profile {report["build_profile"]}',
        f'- Candidate build wall time {format_number(report.get("build_seconds"))} seconds',
        f'- Memory method {report["memory_method"]}',
        f'- Workload rationale {report["representativeness"]}',
        "",
        "| Scope | Workload | Required | Python seconds | Rust seconds | Time reduction | Python RSS bytes | Rust RSS bytes | RSS reduction | Runtime gate | Memory gate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for workload in report["workloads"]:
        metrics = workload["metrics"]
        wall, rss = metrics["elapsed_seconds"], metrics["peak_rss_bytes"]
        median = lambda metric, side: (metric[side] or {}).get("median")
        lines.append(
            f'| {workload["scope"]} | {workload["id"]} | {workload["required"]} | '
            f'{format_number(median(wall, "baseline"))} | {format_number(median(wall, "candidate"))} | '
            f'{format_number(wall["reduction_percent"], 2)}% | {format_number(median(rss, "baseline"), 0)} | '
            f'{format_number(median(rss, "candidate"), 0)} | {format_number(rss["reduction_percent"], 2)}% | '
            f'{wall["result"]} | {rss["result"]} |'
        )
    lines += ["", f'Parity cases and preflight workloads matched {sum(item["match"] for item in report["parity"].values())}/{len(report["parity"])}.', ""]
    return "\n".join(lines)


def report_evaluation(run, attempt_dir, config, record, evaluation, binding, attestation):
    workloads = []
    for item in evaluation["workloads"]:
        metrics = compare_metrics(item["raw"]["baseline"], item["raw"]["candidate"], attestation is not None)
        workloads.append(dict(item, metrics=metrics))
    status = evaluation["status"]
    reason = evaluation["reason"]
    if status == "measured":
        status = recommendation(workloads, config["full_workflow_representative"])
        reason = {
            "inconclusive": "Required representative workflow evidence remains inconclusive. Inspect each metric and its measurement scope.",
            "insufficient benefit": "A required workload misses the 20% reduction gate.",
            "recommended for review": "All required workloads meet both 20% reduction gates after behavior parity.",
        }[status]
    report = {
        "schema_version": VERSION, "status": status, "reason": reason, "identity": record, **binding,
        "candidate_sha256": evaluation["candidate_sha256"], "workloads": workloads,
        "parity": evaluation["parity"], "host": evaluation.get("host"), "versions": evaluation.get("versions"),
        "build_profile": config["build_profile"], "build_seconds": (evaluation.get("build") or {}).get("elapsed_seconds"),
        "build_tree_sha256": evaluation.get("build_tree_sha256"), "representativeness": config["representativeness"],
        "memory_scope": config["memory_scope"], "memory_attestation": attestation,
        "memory_method": "wait4 directly launched PID peak RSS; process-tree peaks are unavailable",
        "threshold_percent": 20, "maximum_trial_spread_percent": 15,
        "evidence_directory": str(attempt_dir), "baseline_directory": str(run / "baseline"),
    }
    tag = attestation["sha256"][:16] if attestation else "unattested"
    json_path = attempt_dir / f"report-{tag}.json"
    markdown_path = attempt_dir / f"report-{tag}.md"
    save(json_path, report)
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    return {"status": status, "reason": reason, "report_json": str(json_path), "report_markdown": str(markdown_path)}


def local_command(args):
    path, runs = args.config.resolve(), args.runs.resolve()
    config, contract_sha, payload = frozen_config(path, getattr(args, "expect_contract", None))
    require(not inside(path, runs), "frozen contract must be outside run writes")
    require(not inside(Path(__file__).resolve(), runs), "evaluator must be outside run writes")
    deadline = time.monotonic() + config["invocation_seconds"]
    origin, record, run_key = identity(path, config, contract_sha, runs)
    with exclusive(runs):
        ensure_quiescent(runs)
        run = create_run(runs, run_key, origin, record, payload)
        state = read_json(run / "state.json")
        result = {"run_key": run_key, "run_directory": str(run), "identity": record}
        if args.action == "prepare":
            return dict(result, status=state["status"])
        if args.action == "baseline":
            summary = baseline_run(run, state, config, record, origin, deadline)
            return dict(result, status=summary["status"], evidence=str(run / "baseline" / "summary.json"))
        require(state["baseline"] is not None, "freeze and execute baseline before evaluating candidate")
        baseline = read_json(run / "baseline" / "summary.json")
        if baseline["status"] != "passed":
            return dict(result, status="inconclusive", reason=baseline["reason"], evidence=str(run / "baseline" / "summary.json"))
        candidate = args.candidate.resolve()
        for protected in (origin, runs):
            separate(candidate, protected)
        require(not inside(path, candidate) and not inside(Path(__file__).resolve(), candidate), "contract and evaluator must be outside candidate writes")
        evaluation, attempt, reused = evaluation_attempt(run, state, config, record, baseline, origin, candidate, deadline)
        binding = {"run_key": run_key, "evaluation_sha256": file_digest(attempt / "evaluation.json")}
        attestation = None
        if evaluation["status"] == "measured":
            env = runtime_environment()
            require(evaluation["host"] == host_info(env), "current host differs from stored measurements")
            check_unchanged(run, attempt, origin, candidate, record, evaluation["candidate_sha256"], evaluation["build_tree_sha256"])
            if reused:
                check_cached_versions(run, state, config, attempt, evaluation, binding["evaluation_sha256"], env, deadline)
                check_unchanged(run, attempt, origin, candidate, record, evaluation["candidate_sha256"], evaluation["build_tree_sha256"])
            attestation = process_attestation(args.attestation, binding, contract_sha, config, evaluation, origin, candidate, runs)
        return dict(result, **report_evaluation(run, attempt, config, record, evaluation, binding, attestation), reused_evaluation=reused)


def reconcile(args):
    require(SHA.fullmatch(args.run_key), "run key must be a SHA-256")
    require(nonempty_string(args.note), "provide reviewed evidence of the prior process outcome")
    runs = args.runs.resolve()
    with exclusive(runs):
        path = runs / args.run_key / "state.json"
        state = read_json(path)
        require(state["status"] in ("running", "uncertain"), "run has no outstanding uncertainty")
        active = state["active"]
        if active == "cache-probe":
            probe = state["cache_probes"][-1]
            evaluation_path = path.parent / probe["attempt"] / "evaluation.json"
            require(file_digest(evaluation_path) == probe["evaluation_sha256"], "cached evaluation changed; inspect recorded state manually")
            probe.update(status="interrupted", finished=utc_now(), reconciliation=args.note)
            state.update(status="evaluated", active=None)
            state.pop("reason", None)
            save(path, state)
            return {"status": "reconciled", "run_key": args.run_key, "evidence": str(path)}
        if active == "baseline":
            # Never synthesize a passing baseline from an interrupted preflight.
            require(not (path.parent / "baseline" / "summary.json").exists(), "baseline summary exists; inspect recorded state manually")
            summary = {"status": "broken baseline", "reason": "preflight interrupted and reconciled", "observations": {}}
            save(path.parent / "baseline" / "summary.json", summary)
            state["baseline"] = summary["status"]
        else:
            attempt = next(entry for entry in state["attempts"] if entry["directory"] == active)
            require(not (path.parent / active / "evaluation.json").exists(), "evaluation exists; inspect recorded state manually")
            attempt.update(status="interrupted", finished=utc_now(), reconciliation=args.note)
        state.update(status="reconciled", active=None, reconciliation={"note": args.note, "at": utc_now()})
        save(path, state)
        return {"status": "reconciled", "run_key": args.run_key, "evidence": str(path)}


def demo(args):
    config_path = PACKAGE / "fixtures" / "contracts" / f"{args.workload}.json"
    _, sha, _ = frozen_config(config_path)
    # Expected hashes are declared in the saved invocation by the reviewer.
    require(sha == args.expect_contract, f"fixture contract SHA-256 mismatch, observed {sha}")
    first = local_command(argparse.Namespace(action="baseline", config=config_path, runs=args.runs, expect_contract=sha))
    if first["status"] != "passed":
        return first
    candidate = PACKAGE / "fixtures" / ("candidate" if args.variant == "correct" else "wrong-candidate")
    second = local_command(argparse.Namespace(action="evaluate", config=config_path, runs=args.runs, expect_contract=sha, candidate=candidate, attestation=args.attestation))
    return dict(second, fixture=args.workload, variant=args.variant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    inventory = subcommands.add_parser("inventory", help="classify inspected local repositories from a discovery manifest")
    inventory.add_argument("manifest", type=Path)
    inventory.add_argument("--output", type=Path)
    for action in ("prepare", "baseline", "evaluate"):
        command = subcommands.add_parser(action)
        command.add_argument("config", type=Path)
        command.add_argument("--runs", type=Path, required=True)
        if action != "prepare":
            command.add_argument("--expect-contract", required=True)
        if action == "evaluate":
            command.add_argument("--candidate", type=Path, required=True)
            command.add_argument("--attestation", type=Path)
    reconcile_command = subcommands.add_parser("reconcile", help="record reviewed terminal evidence; never re-execute the interrupted candidate")
    reconcile_command.add_argument("--runs", type=Path, required=True)
    reconcile_command.add_argument("--run-key", required=True)
    reconcile_command.add_argument("--note", required=True)
    demo_command = subcommands.add_parser("demo", help="run the packaged offline fixture through baseline and evaluation")
    demo_command.add_argument("--runs", type=Path, required=True)
    demo_command.add_argument("--variant", choices=("correct", "wrong"), default="correct")
    demo_command.add_argument("--workload", choices=("standard", "changed"), default="standard")
    demo_command.add_argument("--expect-contract", required=True)
    demo_command.add_argument("--attestation", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "inventory":
            value = manifest_inventory(args.manifest.resolve())
            if args.output is not None:
                require(not args.output.exists(), "inventory output already exists")
                save(args.output, value)
        elif args.action == "reconcile":
            value = reconcile(args)
        elif args.action == "demo":
            value = demo(args)
        else:
            value = local_command(args)
    except (InputError, OSError, ValueError) as error:
        print(json_bytes({"status": "invalid or unavailable", "reason": str(error)}).decode(), file=sys.stderr, end="")
        return 2
    except (Uncertain, KeyboardInterrupt) as error:
        print(json_bytes({"status": "uncertain", "reason": str(error) or "interrupted"}).decode(), file=sys.stderr, end="")
        return 3
    print(json_bytes(value).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
