# Local ADK/Magnitude coding evaluation

Date: 2026-08-29

Harness revision tested through: `04d06c3`

## Outcome

The ten-task WebSocket/ADK/Magnitude suite passed **4/10 (40%)** with **zero
false-positive completions**. Every credited pass emitted `RUN_FINISHED`, passed the
harness behavioral verifier, passed a fresh out-of-band held-out verifier, and passed
`py_compile`. Every failed or cancelled run remained a non-completion.

| Task | Artifact | Result | Canonical elapsed | Evidence |
|---:|---|---|---:|---|
| 1 | `hello_world.py` | PASS | 135.7 s | held-out passed |
| 2 | `rle_codec.py` | PASS | 364.5 s | held-out passed |
| 3 | `expr_lang.py` | FAIL | bounded retries | confinement recovery and provider timeouts |
| 4 | `config_lang.py` | PASS | 770.9 s | held-out passed after durable reconnect |
| 5 | `state_machine.py` | PASS | 1040.2 s | held-out passed; steering exercised |
| 6 | `json_patch.py` | FAIL | bounded retries | repeated idle and throughput failures |
| 7 | `query_lang.py` | FAIL | 516.5 s | idle after inspections; steering accepted |
| 8 | `dependency_resolver.py` | FAIL | 355.6 s | five-minute throughput cutoff |
| 9 | `template_lang.py` | FAIL | 240.2 s | first-event timeout after retry |
| 10 | `tiny_vm.py` | FAIL | 316.9 s | idle after one inspection |

Qwen3.6 35B-A3B Q5 and Qwen3.8 27B Q8 were exercised with provider-default and
explicit `none` reasoning. Direct completion probes could succeed even when a later
multi-turn coding request stalled, so discovery and one-turn responsiveness are
necessary but not sufficient readiness signals.

## Recorded economics

Provider metrics are lower bounds because attempts cancelled before a provider
response report no usage.

- 403,392 input tokens, 25,857 output tokens, and 8,832 reasoning tokens
- 340,154 cache-read tokens
- 75 tool metric rows and 75 public tool starts (exact reconciliation)
- 373,303 input tokens on passing runs, or 93,326 per pass
- 55 tool calls on passing runs, or 13.75 per pass
- 567.7-second median passing latency; 1040.2-second slowest pass
- local provider price data unavailable

## Trace-derived refinements completed

1. Invalid or denied model tool inputs now return bounded recoverable errors instead
   of escaping as fatal ADK workflow exceptions.
2. Hidden reasoning renews liveness without exposing chain-of-thought or persisting
   token-level heartbeat noise.
3. Model tools run off the async server loop, and obvious `find /` traversal requires
   approval, preventing long commands from starving WebSocket control traffic.
4. Real task 4 reconnect resumed from the durable cursor without duplicate execution.
5. Mid-run steering was accepted and delivered at a safe point in task 5.
6. Magnitude reasoning effort is explicit, and startup now performs a real completion
   probe before announcing responsiveness.

## Remaining gaps

- Add a provider circuit breaker and declared cross-model fallback/handover.
- Opportunistically run trusted verification after a valid mutation; task 5's file
  passed held-out checks well before the model converged on completion.
- Add active inference cancellation/preemption; safe-point steering cannot interrupt
  a currently stalled provider generation.
- Reduce context and tool cost from the 93k input-token/13.75-tool-call passing
  baseline with paired ablations.
- Run the excluded live Gemini credential test and a repeated PR-derived suite before
  claiming the 90% SOTA release gate.
