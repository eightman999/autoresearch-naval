#!/bin/bash
# shipknow v1 (RAG, k=3): 学習 -> test 全件推論。RTX 3060 のみ。二重起動防止のため lock を使う
cd ~/navspec-qwen35
source .venv/bin/activate
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 HF_HUB_OFFLINE=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
S=shipknow
V=$S/v1
mkdir -p $V/logs
exec 9>$V/run_v1.lock
flock -n 9 || { echo "already running"; exit 1; }
if [ ! -f $V/out/rag_k3/summary.json ]; then
  nice -n 10 python $S/tools/navspec_qwen35_sft_train.py --data $V/data --out $V/out/rag_k3 \
    --rank 64 --alpha 128 --dropout 0.05 --lr 2e-4 --epochs 1 --batch-size 2 --grad-accum 8 \
    --warmup-ratio 0.03 --seed 0 --valid-per-task 300 --eval-every 200 --log-every 10 > $V/logs/rag_k3_train.log 2>&1
fi
[ -f $V/out/rag_k3/summary.json ] || { echo TRAIN_FAILED > $V/logs/run_v1.done; exit 1; }
for bs in 32 12; do
  if [ ! -f $V/out/rag_k3/test_preds.jsonl.gz ]; then
    nice -n 10 python $S/tools/shipknow_infer_hf.py --test $V/data/test.jsonl --adapter $V/out/rag_k3/best --out $V/out/rag_k3/test_preds.jsonl.gz --batch-size $bs > $V/logs/rag_k3_infer_bs$bs.log 2>&1
  fi
done
[ -f $V/out/rag_k3/test_preds.jsonl.gz ] && echo ALLDONE > $V/logs/run_v1.done || echo INFER_FAILED > $V/logs/run_v1.done
