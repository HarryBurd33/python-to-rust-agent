# Python-to-Rust playbook

Version 0.1.0. Manual execution only.

## Goal and approach

Inspect skills/agents and port their smallest useful executable Python component. Hybrid implementations are allowed. Markdown instructions are not executable code. Preserve inputs/outputs, ordering, exit/error behavior, file effects and supported platforms.

The pipeline is fixed; Codex judgment selects candidates and port boundaries from source and profile observations. A direct rewrite prompt cannot establish compatibility or performance; that simpler alternative has not been experimentally compared. No scheduler or AI framework is needed.

Prerequisites: current invocation, local files/execution, Python 3.11+, Rust for candidate builds, and an independent reviewer. GitHub read access is needed for live inventory. The bundled memory adapter supports reviewer-audited single-process workloads on macOS/Linux. Concurrent process trees require another validated aggregate adapter; Windows is unverified.

## step-01 — Bind and inventory

Read configuration, toolbox/contracts.md and prior runs. Check current authority, ownership, unfinished jobs and fresh allowance. Honor the limits set for the current invocation. One active writer; no overlapping unattended runs.

Bind repository, immutable commit SHA/content identity, component, configuration and fixture hashes, baseline/candidate paths, frozen acceptance hash, output/evidence paths, reviewer and consumed attempts/time. Search indexed resources before each new artifact.

Prefer the connected GitHub adapter in toolbox/contracts.md. Discover identity; enumerate owned repositories through the observed terminal page and deduplicate IDs. Record counts, page errors, access gaps and archived/fork status. If concurrent changes undermine coverage, reconcile enumeration. Complete accessible enumeration does not prove unrestricted account access.

Pin each source commit and inspect its complete tree with truncation checks. Inspect actual SKILL.md/AGENT.md, linked scripts, tests, dependencies, licenses and applicable policies. Names alone are insufficient. Inaccessible is unknown, not absent. Documentation-only/no-Python projects are excluded with reasons. Archived/fork status affects ranking, not silent exclusion. Normalize observations to the inventory tool schema, keeping private evidence local.

## step-02 — Profile, rank and freeze

Rank using observed runtime/memory, known usage frequency, feasibility, portability, effort and maintenance burden. Unknown frequency/cost remains unknown. Distinguish Python CPU/startup costs, network/LLM/I/O waits and native dependencies. Reuse suitable existing Rust code and inspect rights. Report simpler Python/dependency improvements as alternatives without changing the measured baseline.

Require a working baseline and realistic fixtures. Profile separately; profiler timings are not comparative benchmarks. Inspect commands before running; use fixtures and mocks for costly or mutating external calls, disclosing what mocks cannot establish.

Freeze a reviewer-owned contract before candidate implementation: source identity, full-workflow and optional component commands, expected outputs/errors and relevant file effects, required small/typical/large cases, startup, repetition/noise rule, memory topology and audited evidence, thresholds and timeouts. Independently record its SHA-256. Use prepare then baseline as described in toolbox/contracts.md. Missing inputs or broken baseline blocks positive recommendation.

## step-03 — Port one isolated candidate

Codex implements only in the prepared candidate workspace, never originals or baseline/evaluator. Preserve user changes. Do not silently repair unrelated Python bugs. Keep the CLI contract and complete workflow; test original tests and independent edge cases.

Use explicit optimized Rust release settings. Record toolchain, dependencies, build/install duration, size and platform coverage separately from runtime. Freeze version commands that fingerprint the actual interpreter, compiler and installed dependencies (including lockfile/environment provenance); undocumented external dependencies make reuse unverifiable. A Python entry point may exec a helper; measure startup and every executable phase. Do not assume wait4 preserves pre-exec memory peaks on Darwin: the bundled exec fixture is uncalibrated and must not receive a positive memory attestation. A subprocess wrapper creates a tree: single-process maxima cannot certify total tree memory.

Distinguish invalid input, missing capability, correctness defect, insufficient benefit, noise and uncertain effects. At most three shared attempts and 60 minutes; stop on repeated identical failure. Reconcile pending process/file outcomes before any retry.

## step-04 — Evaluate and review

Evaluate using the frozen hash and unchanged source/config identity. Check stdout, stderr/exit code and all relevant side effects. Include empty/zero/missing, malformed, Unicode and numeric limits where applicable. Stdout parity alone cannot establish unspecified file effects.

For file-writing helpers, compare failure paths with and without overwrite flags against existing files and dangling symlinks. Preserve the original's exclusive-creation behavior even when successful writes allow replacement. Use isolated supplementary checks when the snapshot adapter cannot represent a fixture, retain before/after file evidence, and treat any independent mismatch as blocking a recommendation. After a code repair, retain the old evidence and evaluate the new candidate identity; do not rerun unchanged code merely to seek a favorable noise result.

Benchmark unprofiled complete workflows on the same host with repeated trials and comparable conditions; retain raw trials, versions, exact commands and units. Measure equivalent OS-level peak memory including host/wrapper/children where applicable. Unsupported topology is inconclusive. A boolean assertion cannot replace reviewer inspection of process behavior.

For each metric reduction = 100*(Python-Rust)/Python. Require both ratios <=0.80 on every required workload beyond the frozen noise rule. Invalid/zero baselines are inconclusive. No cherry-picking or weakened criteria; helper gains are explanatory only.

An independent reviewer owns acceptance and checks actual results. Report excluded, deferred, failed correctness, insufficient benefit, inconclusive, or recommended for user review. Include absolute/percentage measurements, source/candidate paths, cost of building/deploying/maintaining, and limitations. Recommendation does not activate the candidate.

## step-05 — Preserve and improve

Index every durable source, report and review. Keep inputs/candidates/raw evidence until explicit cleanup; only scratch build products are disposable. Source/config changes invalidate prior acceptance. Resume matching completed runs without duplication. Interrupted outcomes remain pending until inspected.

For a demonstrated reusable failure, update this playbook or its tool contract, read back the saved procedure, then run it with changed input and fresh evidence against the unchanged acceptance oracle. Record failure → cause → correction → verification. No claim of reuse validation without execution.

## Examples and completion checks

Synthetic 10 s/100 MB versus 7 s/70 MB passes the numeric gate; 7 s/90 MB fails. Wrong output fails regardless of speed. These are evaluator examples, not observed benchmarks. An API-wait-dominated wrapper may fail despite a faster helper.

| ID | Expected outcome | Method | Evidence |
|---|---|---|---|
| check-01 | Manual/local-only behavior | Inspect effects/configuration | Review |
| check-02 | Honest inventory/exclusions | Markdown/incomplete/error fixtures | Tests |
| check-03 | Both >=20% gates | Independent numerical cases | Tests |
| check-04 | Correctness before speed | Wrong candidate and edge cases | Parity |
| check-05 | Full workflow, noise and memory scope | Comparator/scope tests | Reports |
| check-06 | Real Python/Rust saved invocation | Inspect actual trials/versions | Demo |
| check-07 | Resume/source invalidation | Repeat and changed-source tests | State |
| check-08 | Originals/evaluator unchanged | Hashes and write-scope inspection | Review |
| check-09 | Runnable indexed package | Invocation and Agent Forge audits | Audits |
| check-10 | Saved correction reused | Changed-input run after correction | Fresh demo |

Freeze a reviewer-owned acceptance record before work. Each check record needs id, expected, method, status, evidence, observed. Unavailable required evidence forbids completion. Candidate authors may not edit evaluator/criteria. This ownership convention is not itself a filesystem sandbox.

## Execution, limits and recovery

Use the manual request in AGENT.md; local self-check commands are in README.md. No schedules, external writes or paid APIs. Default one candidate, three attempts, 60 shared minutes; depth 1, at most two workers if permitted, nonoverlapping writes. Inventory all declared scope when feasible; preserve backlog/partial coverage otherwise. Honor current user/account/permission stops earlier.

Retry a transient read at most once; never retry deterministic input/permission errors blindly. Inspect uncertain local command effects and process state before any later retry. Retain durable evidence and source references, redact credentials/unnecessary private content, record unavailable tokens/cost as null. No automatic cleanup or continuation.

## Validated lessons

- Missing or noisy required evidence takes precedence over a known insufficient-benefit result.
- Check declared toolchain and dependency fingerprints on cached reuse. Journal version probes before execution and hold an interrupted probe for explicit reconciliation. Reconciliation preserves the prior evaluation; a later authorized invocation may run a new probe.
- Bind memory attestations to the exact run identity and evaluation-file hash. A source, evaluator, build or evidence change invalidates the corresponding attestation.

See [validation results](VALIDATION.md) for the local fixture outcomes and measurement limits.
