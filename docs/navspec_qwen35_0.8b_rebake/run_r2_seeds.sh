#!/bin/bash
set -u
cd ~/navspec-qwen35
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
PY="nice -n 10 .venv/bin/python"; D21=data/stage4a_armament_verdict_v2_1
for s in 1 2; do
echo "[$(date)] R2 seed $s"
$PY repo/tools/navspec_qwen35_verdict_train.py --model Qwen/Qwen3.5-0.8B --data $D21 --seed $s --out out/r2_s$s --rank 16 --alpha 32 --dropout 0.05 --lr 2e-4 --scheduler cosine --warmup-ratio 0.05 --epochs 5 --batch-size 4 --grad-accum 2 --eval-every 25 > logs/r2_s$s.log 2>&1
$PY repo/tools/navspec_armament_verdict_eval_hf.py --model Qwen/Qwen3.5-0.8B --data $D21 --split test --batch-size 8 --adapter out/r2_s$s/best --out out/r2_s${s}_best_test.json > logs/r2_s${s}_eval.log 2>&1
done
echo "[$(date)] done"
