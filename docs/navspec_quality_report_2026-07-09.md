# Navspec Model Quality Report - 2026-07-09

## Scope

This report covers the first local MLX autoresearch run on the naval
specification corpus.

- Corpus: `data/navspec/tasks.train.jsonl` and `data/navspec/tasks.val.jsonl`
- Source: `/Users/eightman/dev/hoi4/SSW_mod/nvpda-db/NSDB.db`
- Class family: battleships, battlecruisers, coastal battleships
- Cache: `.cache/autoresearch-navspec`

## Dataset Quality

Command:

```bash
uv run python tools/navspec_eval.py \
  data/navspec/battleship_real_specs.jsonl \
  data/navspec/battleship_fictional_specs.jsonl
```

Result:

| Dataset | Records | Errors | Warnings | Pass |
|---|---:|---:|---:|---|
| `battleship_real_specs.jsonl` | 203 | 0 | 6 | yes |
| `battleship_fictional_specs.jsonl` | 406 | 0 | 10 | yes |

Warning classes:

- `displacement_area_ratio`
- `belt_range`
- `admiralty_coefficient`
- `missing_displacement`

No fictional record carries a historical `source_url`.

## Training Runs

Both runs used the local navspec cache and the default 300 second training
budget.

| Run | Train commit | Batch | val_bpb | VRAM MB | Steps | Tokens M | Status |
|---|---|---:|---:|---:|---:|---:|---|
| baseline | `40b51f2` | `2^16` | 0.193330 | 4304.2 | 326 | 21.4 | keep |
| experiment | `3c9788b` | `2^15` | 0.132106 | 4294.2 | 566 | 18.5 | keep |

The first experiment improved `val_bpb` by 0.061224, or 31.67% relative to the
baseline, while keeping peak memory effectively unchanged.

## Interpretation

Confirmed:

- The local navspec corpus can train through the MLX autoresearch pipeline.
- Reducing total batch size from `2^16` to `2^15` improved validation BPB for
  this small structured corpus.
- The improvement came from more optimizer updates within the same 5 minute
  budget, not from a larger model.

Not yet confirmed:

- Free-form generation quality from a saved checkpoint.
- JSON schema validity of sampled model outputs.
- Whether the improvement survives multi-seed rigor evaluation.
- Whether the model can repair invalid fictional ship specifications.

## Next Evaluation Step

Add checkpoint saving and a small deterministic generation harness before judging
actual model output quality. Until then, `val_bpb` is the only model metric and
`tools/navspec_eval.py` is a dataset/static-output metric.
