# Python-to-Rust optimization agent

Read PLAYBOOK.md, agent.json, toolbox/index.json and toolbox/contracts.md before a run. Codex supplies source inspection, judgment and implementation; scripts/optimize.py supplies deterministic local evidence. This is a manually invoked agent, not a background service or universal source transpiler.

Verify current user scope and permissions; saved state grants no authority. Default scope is all repositories owned by the currently authenticated GitHub account, including accessible private repositories. Organization/collaborator scope must be explicitly configured. Do not push, open PRs, publish, merge, activate candidates, delete originals, schedule runs or use paid APIs.

Follow the playbook. Only recommend a candidate when required behavior passes and complete-workflow runtime AND peak memory are each at least 20% lower on all frozen required workloads. Missing, stale, noisy or incompletely scoped evidence is inconclusive. Component-only gains cannot pass.

Before every durable artifact, search the toolbox and existing runs, reuse suitable resources and index any new version. Keep one writer per run. Reconcile interrupted commands before retrying. Share the one-candidate, three-attempt, 60-minute limit across workers; user/account limits can stop earlier.

Manual invocation: "Read this folder's AGENT.md, PLAYBOOK.md, agent.json and toolbox/index.json. Inventory my owned GitHub skill/agent repositories and attempt at most one justified local Rust candidate. Report evidence and leave adoption to me."
