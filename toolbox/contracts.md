# Local tools and evidence contracts

Version 0.1.0. Python 3.11+ standard library; Rust toolchain for real candidates. Commands are run from the package root. External GitHub discovery uses [github.md](github.md); the CLI imports those observations and does not fetch or translate repositories itself.

## Commands

| Invocation | Inputs and result | Effects |
|---|---|---|
| python3 scripts/optimize.py inventory MANIFEST --output FILE | Version-1 manifest; emits coverage, issues and classified repositories | Reads sources, writes a new report; refuses overwrite |
| python3 scripts/optimize.py prepare CONFIG --runs DIR | Validated contract; emits run key/path and immutable source/config/evaluator identity | Creates isolated source snapshot and state |
| python3 scripts/optimize.py baseline CONFIG --runs DIR --expect-contract HASH | Independently pinned SHA-256 of CONFIG bytes; verifies original against frozen behavior | Executes reviewed baseline commands locally, records observations |
| python3 scripts/optimize.py evaluate CONFIG --runs DIR --candidate DIR --expect-contract HASH [--attestation FILE] | Requires completed baseline; candidate isolated from source, evaluator and contract | Builds and tests a copied candidate, writes raw results and JSON/Markdown reports |
| python3 scripts/optimize.py demo --runs DIR --workload standard\|changed --variant correct\|wrong --expect-contract HASH | Saved offline fixture invokes baseline then evaluate | Same local effects as above; no remote operations |
| python3 scripts/optimize.py reconcile --runs DIR --run-key HASH --note TEXT | Existing pending key, nonempty independently verified outcome note | Records a terminal interrupted outcome; never reruns commands |

Exit 0 means a decision/report exists, not an optimization pass. Exit 2 means invalid input or unavailable capability. Exit 3 means uncertain/interrupted; inspect retained state and live processes before recovery. All argv are arrays, never shell strings. Read commands before execution; the harness is not an OS sandbox. Native host permissions remain controlling. A timeout kills its process group but cannot prove absence of deliberately detached descendants.

Errors for malformed IDs, unknown placeholders, changed contract, overlapping directories, changed source/evaluator and outstanding uncertainty are nonretryable until corrected or reconciled. Read-only transient provider errors have at most one retry under the playbook; the CLI has no automatic provider retry. Attempts and time bounds remain shared.

## Discovery manifest

schema_version=1; scope={identity: nonempty login, include_affiliated: boolean}; terminal_page_verified:boolean; pages is an ordered array. Each page: status="ok"|"unavailable", cursor:null|string, next_cursor:null|string, optional reason, repositories:array.

Each repository: repository="owner/name", source_url:https URL, source_sha:lowercase 40/64-hex commit ID, archived:boolean, fork:boolean, access="readable"|"unavailable", local_path: path relative to manifest for readable sources, optional exclusion/reason. Map the connector's zero-based offsets to stable string cursors, initial null, terminal next_cursor null. Never fabricate terminal verification. Empty pages are valid only with genuine enumeration evidence; empty lists are not evidence of failure or success by themselves. See fixtures/inventory.json.

## Frozen execution contract

Use fixtures/contracts/standard.json as the schema example, not as a production workload. Required fields: schema_version=1; repository/component/source_root strings; source_sha 40/64 lowercase hex; memory_scope="single_process"|"process_tree"|"unknown"; full_workflow_representative:boolean with representativeness explanation; build_profile string; trials integer5–21; timeout_seconds positive <=300; invocation_seconds positive <=3600.

versions maps python/rust and every relevant external dependency/environment fingerprint to argv arrays. A package-version, lockfile or environment check must cover dependencies outside the snapshotted source; missing coverage blocks reviewer acceptance of reuse. build is argv. cases and workloads are nonempty arrays with unique lowercase IDs and scope="workflow"|"component". baseline/candidate argv support only documented placeholders: source, work, python; candidate additionally candidate/build. Build permits source/candidate/build/python. All paths resolve from the inspected contract/run context.

cases require expect={exit_code:integer0–255, stdout:string, stderr:string, effects:array of work-directory entries}. Empty stdout/stderr and zero exit are valid; missing is invalid. Workloads require required:boolean and description. At least one workflow correctness case and one required workflow workload are mandatory. The reviewer must establish representative inputs and independent expected behavior before freezing.

Measured peak_rss_bytes are bytes; raw wait4 ru_maxrss retains its platform units (Darwin bytes, Linux KiB). Metrics require at least five valid positive trials. Fixed gate20%; max trial range/median15%; all candidate trials must be <=80% of all baseline trials for a robust pass. Threshold-straddling/noisy evidence is inconclusive.

## Reviewer memory attestation

Unknown/process-tree scopes cannot be certified by this adapter. An optional reviewer-owned FILE outside source/candidate/run writes can attest audited single-process scope. Fields: schema_version=1, scope="single_process", contract_sha256, candidate_sha256, run_key, evaluation_sha256, platform, machine, host_sha256, reviewer, audit, rss_validation. run_key is the exact recorded source/config/evaluator identity; evaluation_sha256 is the SHA-256 of the measured evaluation.json bytes, binding the built artifacts and version/dependency observations. Use the returned report fields rather than reconstructing identities. Hashes bind actual contract/candidate/host records; explanations must cite real inspection/calibration. Do not create a false attestation merely to obtain a passing status. Absence keeps memory inconclusive. A text attestation records review; it is not automated proof of process topology.

## Persistence and provenance

Run keys bind repository, source commit/content, component, config and evaluator. Matching completed work can be reused; source/config changes create a distinct identity. An interrupted prior run blocks duplicates until inspected. Cached version/dependency checks are journaled before execution and retain probe evidence. A timeout or interruption leaves a cache-probe reconciliation hold; reconcile records the independently confirmed outcome without altering the accepted historical evaluation. Only a later authorized invocation may run a fresh probe. Successful reuse may append probe history but must not add a candidate build or measurement attempt. File locks coordinate local cooperating invocations, not distributed writers.

Reports retain build duration, versions, parity, raw trial references, measurement scope, absolute/percentage comparisons and reasons. Review report contents before recommendation. Output hashes establish byte identity only.

The package adapts Agent Forge 0.3.3 project templates. [LICENSE.agent-forge](../LICENSE.agent-forge) retains their MIT notice. Python resource/wait4 and Rust Cargo profiles documentation informed the measurement and build contracts.
