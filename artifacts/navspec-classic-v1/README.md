# navspec-classic-v1

Created: 2026-07-10T08:02:09.730237+00:00

This archive freezes the classic navspec stack: 5.24M scratch checkpoint,
tokenizer, schema, datasets, rule-backed eval reports, and raw model eval.

## Git

- commit: `632c8b87b4652900faf131b01bb26606157fd31e`
- branch: `autoresearch/jul9-navspec`
- tag recommended: `navspec-classic-v1`
- tag created by archive script: `False`

The working tree status at archive time is recorded in `dataset_manifest.json`.

## Checkpoint

- params: 5.243016M
- val_bpb: 0.0473391504970592
- steps: 36760
- tokens seen: 1204.55168M
- training seconds: 18000.484362602234

## Integrated Eval

- total: 122
- patched JSON success: 122/122
- schema ok: 122/122
- L/B FAIL: 0
- contradiction FAIL: 0
- tier FAIL: 0

## Raw Model Eval

- total: 122
- raw JSON rate: 0.38524590163934425
- raw schema complete rate: 0.0
- field adoption rate: 0.0
- patch dependency rate: 1.0

## Restore Notes

Use this archive as a baseline and fallback. Do not overwrite it with LoRA
experiments; store LoRA adapters under `models/navspec-lora-*`.
