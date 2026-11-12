# Validation

The package evaluator passed 13 tests. They cover the numerical gate, parity rejection, incomplete inventory, source and dependency invalidation, interrupted probe reconciliation, and exact attestation binding.

Two offline fixtures ran with Python and an optimized Rust build. An independent reviewer checked 140 raw trial observations against their evidence and recomputed the summaries. Each correct fixture used five workloads with seven trials per implementation.

| Fixture | Parity | Timed observations | Decision |
| --- | ---: | ---: | --- |
| Standard | 15/15 | 70 | Inconclusive |
| Changed workload | 16/16 | 70 | Inconclusive |
| Deliberately wrong candidate | 0/1 | 0 | Failed correctness |

An unchanged resume preserved the candidate attempt and evaluation bytes and recorded a matching version probe. Fixture source files remained unchanged.

On macOS, the bundled Python entry point uses `exec` to dispatch to Rust. The `wait4` RSS reading has not been calibrated to include the pre-exec Python peak. Both correct fixture runs therefore lack comparable complete-workflow memory evidence, and neither supports an adoption recommendation. Concurrent process-tree memory is outside the bundled adapter's coverage. Windows execution is unverified.

See [the README](README.md) to reproduce the fixtures. Measurements describe the bundled workloads on the test host.

| Tested file | SHA-256 |
| --- | --- |
| `scripts/optimize.py` | `c9c049ee96b2d0613c4cdc44db100e7e3c1d44a9be160f4831f97764d0410cc2` |
| `tests/test_optimize.py` | `9db62352564fd894cac33b4f75597b4028fc06032bbd41779a15e58dc3b70dd3` |
| `fixtures/contracts/standard.json` | `9d93c5c316346d205d48c9d3b189806c9f9f9aca3cdb2c31c0f7e19a8d44775b` |
| `fixtures/contracts/changed.json` | `cbc14272540869a34293f8e5ea1bba997892cd30ca40463f120036a167856bf6` |
