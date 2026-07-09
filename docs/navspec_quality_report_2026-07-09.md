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
| checkpoint | `7aa0e1a` | `2^15` | 0.103019 | 4294.2 | 657 | 21.5 | keep |

The checkpoint run re-ran the halve-batch config (train.py only gained
checkpoint saving on top of `3c9788b`). The lower `val_bpb` versus the prior
experiment came from nondeterministic step timing fitting more optimizer
updates (657 vs 566) inside the same 300 second budget, not from a code or
hyperparameter change.

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

- Whether the improvement survives multi-seed rigor evaluation.
- Whether the model can repair invalid fictional ship specifications.

## Generation Harness Evaluation

Command:

```bash
env AUTORESEARCH_CACHE_DIR=/Users/eightman/dev/apps/autoresearch-naval/.cache/autoresearch-navspec \
  uv run train.py
PYTHONPATH="$(pwd):$(pwd)/tools" uv run python generate_navspec.py --max-new-tokens 1024
PYTHONPATH="$(pwd):$(pwd)/tools" uv run python validate_navspec_output.py \
  data/navspec/generated/latest.json
```

Result (checkpoint `7aa0e1a`, `val_bpb=0.103019`, greedy/temperature 0, profile 0):

- The model emitted a single compact JSON object terminated by `</output>`.
- `validate_navspec_output.py`: `records=1 errors=0 warnings=0`, exit 0.
- All 13 required top-level fields present; `source_kind="fictional_bounded"`
  with `source_url=null`, so the fictional-source rule is respected.

Confirmed:

- Free-form generation from a saved checkpoint produces a schema-valid
  `ShipSpecRecord`.
- JSON schema validity of a sampled model output passes with zero errors and
  zero warnings.

Caveats observed:

- `--max-new-tokens` defaults to 512, which truncates the record mid-field
  (`"source_kind":"`); a complete record needs roughly 1024 tokens at
  vocab_size 2048. Use `--max-new-tokens 1024` or higher.
- Prompt conditioning is weak. With `--country Japan --class-like KONGO` the
  harness correctly selects `KONGO1915) Class` as the base, but the 5.2 M
  parameter model ignored it and produced `country="United-States"`,
  `class_name="SOUTHDAKOTA Class alternate 2"`, and a `derived_from` pointing
  at a United-Kingdom `ST.VINCENT` record. Low `val_bpb` reflects corpus
  fitting, not instruction/conditioning control.
- `generate_navspec.py` and `validate_navspec_output.py` import
  `tools.navspec_eval`, whose body uses `from navspec_common import ...`, so
  both scripts need `PYTHONPATH="$(pwd):$(pwd)/tools"` when launched from the
  repo root (the `tools/*.py` scripts run directly by the docs still work).
- MLX `Module.load_weights` coerces `GPT.value_embeds` (a dict with numeric
  string keys) into a list and raises `KeyError` on load;
  `generate_navspec.load_weights_preserving_dicts` traverses the model's own
  parameter tree to avoid this.
