# Python-to-Rust optimization agent

A manually invoked Codex agent with local deterministic tools. It inventories skill/agent repositories, chooses small ports using profiles, and recommends candidates only after behavior checks and >=20% reductions in both complete-workflow runtime and peak memory.

In Codex, open this folder and ask:

> Read AGENT.md and follow PLAYBOOK.md. Inventory my owned GitHub skill/agent repositories and attempt at most one justified local Rust candidate. Leave adoption to me.

Codex supplies judgment and code editing. The CLI does not autonomously translate arbitrary Python. Use the current connected GitHub account; credentials never belong in this package.

## Local self-check

From this directory, with Python 3.11+ and Rust:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/optimize.py demo --runs runs/demo --variant correct --workload standard --expect-contract 9d93c5c316346d205d48c9d3b189806c9f9f9aca3cdb2c31c0f7e19a8d44775b
python3 scripts/optimize.py demo --runs runs/rejection --variant wrong --workload standard --expect-contract 9d93c5c316346d205d48c9d3b189806c9f9f9aca3cdb2c31c0f7e19a8d44775b
python3 scripts/optimize.py demo --runs runs/changed --variant correct --workload changed --expect-contract cbc14272540869a34293f8e5ea1bba997892cd30ca40463f120036a167856bf6
```

The bundled examples use synthetic repository/commit identifiers; actual fixture and evaluator content hashes are recorded. Inspect the emitted report paths. An actual fixture may correctly return insufficient benefit or inconclusive; success is not promised. See toolbox/contracts.md for general inventory/baseline/evaluate interfaces.

The bundled memory adapter covers reviewer-audited single-process workloads on macOS/Linux, not concurrent process-tree totals. The bundled Python-to-Rust exec fixture has no calibrated full-workflow RSS measurement on Darwin; its memory result must remain inconclusive, even when nominal RSS is lower. Windows is unverified. No publishing, PRs, activation or scheduling.

## Validation

See [validation results](VALIDATION.md) for the test and fixture outcomes. See [toolbox/index.json](toolbox/index.json) for the resources included in this package.

## Template attribution

[LICENSE.agent-forge](LICENSE.agent-forge) preserves the MIT notice for the adapted Agent Forge templates.
