#!/bin/bash
# 各量子化で llama-server (3060 固定, 127.0.0.1:18765) を起動 -> 評価 -> 必ず停止
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3
export LD_LIBRARY_PATH=$HOME/llama.cpp/build/bin
cd ~/navspec-qwen35
PORT=18765
for q in "$@"; do
  $HOME/llama.cpp/build/bin/llama-server -m gguf/navspec-qwen35-0.8b-$q.gguf -ngl 99 -c 4096 -np 1 --host 127.0.0.1 --port $PORT --chat-template-kwargs "{\"enable_thinking\":false}" > logs/server_$q.log 2>&1 &
  SP=$!
  for i in $(seq 1 120); do curl -sf http://127.0.0.1:$PORT/health >/dev/null && break; kill -0 $SP 2>/dev/null || break; sleep 1; done
  echo "=== $q (pid $SP) ==="
  .venv/bin/python repo/tools/navspec_armament_verdict_eval_gguf.py --base-url http://127.0.0.1:$PORT --data data/stage4a_armament_verdict_v2_1 --split test --label $q --out out/gguf_${q}_test.json --compare out/r2_merged_test_gens.json 2>&1 | grep -v "^  "
  kill $SP; wait $SP 2>/dev/null
done
