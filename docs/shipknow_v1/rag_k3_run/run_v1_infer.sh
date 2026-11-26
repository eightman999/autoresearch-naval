#!/bin/bash
# ユーザー判断で学習を step ~2360 で停止（2026-10-06 20:57）。best = step 2200 で test 推論のみ
cd ~/navspec-qwen35
source .venv/bin/activate
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 HF_HUB_OFFLINE=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
S=shipknow; V=$S/v1
exec 9>$V/run_v1.lock
flock -n 9 || { echo "already running"; exit 1; }
for bs in 32 12; do
  if [ ! -f $V/out/rag_k3/test_preds.jsonl.gz ]; then
    nice -n 10 python $S/tools/shipknow_infer_hf.py --test $V/data/test.jsonl --adapter $V/out/rag_k3/best --out $V/out/rag_k3/test_preds.jsonl.gz --batch-size $bs > $V/logs/rag_k3_infer_bs$bs.log 2>&1
  fi
done
[ -f $V/out/rag_k3/test_preds.jsonl.gz ] && echo "ALLDONE (train stopped at user request; best=step2200)" > $V/logs/run_v1.done || echo INFER_FAILED > $V/logs/run_v1.done
