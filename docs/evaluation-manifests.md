# Frozen benchmark manifests

Skein's fixed-intelligence evaluation uses only DeepSWE 1.1, Terminal-Bench
2.1, and SWE-Atlas-QnA 1.0. The four checked-in manifests live in
`tests/eval/manifests`:

| Manifest | DeepSWE | Terminal-Bench | SWE-Atlas | Use |
| --- | ---: | ---: | ---: | --- |
| `evaluation-smoke-v1` | 2 | 2 | 2 | Adapter qualification only |
| `evaluation-ablation-v1` | 8 | 5 | 5 | Skein profile screening |
| `evaluation-pilot-v1` | 18 | 12 | 12 | Cross-harness screening |
| `evaluation-confirm-v1` | 45 | 30 | 30 | Held-out confirmation |

Every task records its immutable Harbor task digest. Selection uses the fixed
seed `skein-fixed-intelligence-2026-v1` and only public source metadata.
Ablation, pilot, and confirmatory tasks are disjoint. Smoke may overlap because
it is never scored as performance evidence.

## Rebuild

Install the evaluation extra, materialize the three public datasets, cache them
to expose their content digests, and download DeepSWE's public v1.1 trial
metadata. Then run:

```sh
python scripts/build_evaluation_manifests.py \
  --datasets-root DATASETS \
  --cache-root HARBOR_CACHE/tasks/packages \
  --deep-trials deepswe-v1.1-trials.json \
  --output tests/eval/manifests
```

The builder fails if source task counts differ from 113, 89, and 124. It
normalizes task metadata, computes DeepSWE pass-rate bands from official full
scored trials, stratifies the selections, and validates every output before it
writes it. Rebuilding the pinned inputs produces the same manifest hashes.

## Freeze and oracle gate

`manifest_sha256` covers canonical JSON excluding only that hash field. Any
task or metadata edit invalidates the file. The selected task ref for Harbor is
`<harbor_task>@<artifact_sha256>`; never use `latest` for a scored run.

Before the first agent comparison, run the official oracle on every selected
task and store its result beside the experiment ledger. SWE-Atlas's verifier
requires its official judge credential, so its oracle gate remains pending
until that key is approved. A failing oracle is an exclusion only before any
agent outcome is observed; record a reason and rebuild a new manifest version.

## Score contract

Official rewards remain binary. Attempts are averaged within task, tasks are
averaged within benchmark, and the composite is the equal-weight mean of the
three benchmark scores. DeepSWE oversampling improves precision but never
changes its one-third composite weight.
