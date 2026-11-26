#!/bin/bash
# navspec Qwen3.5-0.8B: zero-shot baseline, R0 (v0.1-equivalent), R1 (recipe fix). RTX 3060 only.
set -u
cd ~/navspec-qwen35
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
PY="nice -n 10 .venv/bin/python"
D=data/stage4a_armament_verdict_v2
EV="$PY repo/tools/navspec_armament_verdict_eval_hf.py --model Qwen/Qwen3.5-0.8B --data $D --split test --batch-size 8"
TR="$PY repo/tools/navspec_qwen35_verdict_train.py --model Qwen/Qwen3.5-0.8B --data $D --seed 0"
mkdir -p out logs
echo "[$(date)] zeroshot"; $EV --out out/zeroshot_test.json > logs/zeroshot.log 2>&1
echo "[$(date)] R0"; $TR --out out/r0 --layers 8-23 --rank 8 --alpha 160 --dropout 0 --lr 5e-6 --scheduler constant --warmup-ratio 0 --iters 500 --batch-size 4 --grad-accum 1 --eval-every 50 > logs/r0.log 2>&1
$EV --adapter out/r0/best --out out/r0_best_test.json > logs/r0_eval.log 2>&1
echo "[$(date)] R1"; $TR --out out/r1 --rank 16 --alpha 32 --dropout 0.05 --lr 2e-4 --scheduler cosine --warmup-ratio 0.05 --epochs 5 --batch-size 4 --grad-accum 2 --eval-every 25 > logs/r1.log 2>&1
$EV --adapter out/r1/best --out out/r1_best_test.json > logs/r1_eval.log 2>&1
echo "[$(date)] done"
