#!/bin/bash
set -u
cd ~/navspec-qwen35
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
PY="nice -n 10 .venv/bin/python"
D=data/stage4a_armament_verdict_v2; D21=data/stage4a_armament_verdict_v2_1
echo "[$(date)] C0 qwen2.5-0.5b R0 recipe"
$PY repo/tools/navspec_qwen35_verdict_train.py --model Qwen/Qwen2.5-0.5B-Instruct --data $D --seed 0 --out out/c0_q25 --layers 8-23 --rank 8 --alpha 160 --dropout 0 --lr 5e-6 --scheduler constant --warmup-ratio 0 --iters 500 --batch-size 4 --grad-accum 1 --eval-every 50 > logs/c0.log 2>&1
$PY repo/tools/navspec_armament_verdict_eval_hf.py --model Qwen/Qwen2.5-0.5B-Instruct --data $D --split test --batch-size 8 --adapter out/c0_q25/best --out out/c0_best_test.json > logs/c0_eval.log 2>&1
echo "[$(date)] R2 v2.1 R1 recipe"
$PY repo/tools/navspec_qwen35_verdict_train.py --model Qwen/Qwen3.5-0.8B --data $D21 --seed 0 --out out/r2 --rank 16 --alpha 32 --dropout 0.05 --lr 2e-4 --scheduler cosine --warmup-ratio 0.05 --epochs 5 --batch-size 4 --grad-accum 2 --eval-every 25 > logs/r2.log 2>&1
$PY repo/tools/navspec_armament_verdict_eval_hf.py --model Qwen/Qwen3.5-0.8B --data $D21 --split test --batch-size 8 --adapter out/r2/best --out out/r2_best_test.json > logs/r2_eval.log 2>&1
echo "[$(date)] done"
