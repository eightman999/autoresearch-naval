# Naval Specification Autoresearch Pipeline

This project is configured to train on SSW naval specification tasks without
downloading the default HuggingFace corpus.

## Scope

- Primary corpus: `/Users/eightman/dev/hoi4/SSW_mod/nvpda-db/NSDB.db`
- Initial class family: battleships, battlecruisers, and coastal battleships
- Primary output: structured ShipSpecRecord and wiki-style specification tables
- Fictional output: bounded fictional specifications derived from historical records
- Excluded for now: flavor text and deep national design commentary

## Build The Local Dataset

```bash
uv run python tools/navspec_extract.py
uv run python tools/navspec_mutate.py
uv run python tools/navspec_tasks.py
uv run python tools/navspec_eval.py \
  data/navspec/battleship_real_specs.jsonl \
  data/navspec/battleship_fictional_specs.jsonl
uv run python tools/navspec_plausibility.py \
  data/navspec/battleship_fictional_specs.jsonl \
  --base-records data/navspec/battleship_real_specs.jsonl
uv run python prepare_navspec.py --force
```

`tools/navspec_plausibility.py` scores fictional records on a 0-100 scale and
labels them as `accepted`, `review`, or `rejected`. The evaluator combines the
existing rule validator with geometry, propulsion, protection, armament,
equipment, and bounded-mutation checks. Use `--fail-on review` for stricter CI
gates, or `--fail-on none` when generating a diagnostic report over a noisy
corpus.

## Smoke Train

```bash
env \
  AUTORESEARCH_CACHE_DIR=/Users/eightman/dev/apps/autoresearch-naval/.cache/autoresearch-navspec \
  AUTORESEARCH_TIME_BUDGET=1 \
  AUTORESEARCH_EVAL_TOKENS=131072 \
  AUTORESEARCH_MAX_SEQ_LEN=512 \
  uv run train.py
```

## Full Local Navspec Run

```bash
env AUTORESEARCH_CACHE_DIR=/Users/eightman/dev/apps/autoresearch-naval/.cache/autoresearch-navspec \
  uv run train.py
```

For early navspec experiments, prefer shorter runs and domain evaluation before
starting the open-ended autoresearch loop.
