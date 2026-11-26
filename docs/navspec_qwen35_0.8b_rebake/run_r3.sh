#!/bin/bash
set -u
cd ~/navspec-qwen35
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
PY="nice -n 10 .venv/bin/python"; D3=data/stage4a_armament_verdict_v3
for s in 0 1 2; do
  echo "[$(date)] R3 seed $s bs4 ga2"
  $PY repo/tools/navspec_qwen35_verdict_train.py --model Qwen/Qwen3.5-0.8B --data $D3 --seed $s --out out/r3_s$s --rank 16 --alpha 32 --dropout 0.05 --lr 2e-4 --scheduler cosine --warmup-ratio 0.05 --epochs 5 --batch-size 4 --grad-accum 2 --eval-every 25 > logs/r3_s$s.log 2>&1
  if [ ! -f out/r3_s$s/summary.json ]; then
    echo "[$(date)] R3 seed $s failed with bs4; retry bs2 ga4"
    mv logs/r3_s$s.log logs/r3_s${s}_bs4_failed.log
    rm -rf out/r3_s$s
    $PY repo/tools/navspec_qwen35_verdict_train.py --model Qwen/Qwen3.5-0.8B --data $D3 --seed $s --out out/r3_s$s --rank 16 --alpha 32 --dropout 0.05 --lr 2e-4 --scheduler cosine --warmup-ratio 0.05 --epochs 5 --batch-size 2 --grad-accum 4 --eval-every 25 > logs/r3_s$s.log 2>&1
  fi
  $PY repo/tools/navspec_armament_verdict_eval_hf.py --model Qwen/Qwen3.5-0.8B --data $D3 --split test --batch-size 8 --adapter out/r3_s$s/best --out out/r3_s${s}_best_test.json > logs/r3_s${s}_eval.log 2>&1
done
echo "[$(date)] done"
