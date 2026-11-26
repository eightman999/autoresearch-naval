#!/bin/bash
# v1_2 は step ~1940 で OOM（2026-10-07 12:35）。best=step1800 を NAS 退避 -> GC 計測 -> v1_2 の残り（step1800 以降の cosine 尾部 1331 step）を GC ありで近似再開
# -> NAS 退避 -> 再開版 best から設計タスクの継続学習 -> NAS 退避。推論はしない。RTX 3060 のみ。
cd ~/navspec-qwen35
source .venv/bin/activate
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=GPU-1d6afaad-67b7-b965-46c7-efe6c9c395f3 HF_HUB_OFFLINE=1 PYTORCH_ALLOC_CONF=expandable_segments:True
S=shipknow; V=$S/v1_2; D=$S/design_v0
NAS=/mnt/nas-other/navspec/navspec-qwen35/shipknow
TR=$D/tools/navspec_qwen35_sft_train.py
mkdir -p $D/logs $D/out $V/logs
exec 9>$D/after_v1_2.lock
flock -n 9 || { echo "already running"; exit 1; }
log(){ echo "[$(date '+%F %T')] $*"; }
fin(){ echo "$1" > $D/logs/after_v1_2.done; log "$1"; exit ${2:-0}; }
nas_copy(){ # $1=src dir, $2=dest dir
  mkdir -p "$2" || return 1
  ionice -c3 nice -n 15 rsync -a --checksum --exclude="*.lock" "$1"/ "$2"/ || return 1
  (cd "$1" && find . -type f ! -name "*.lock" -print0 | sort -z | xargs -0 sha256sum) > /tmp/nas_copy.sha256
  (cd "$2" && sha256sum -c --quiet /tmp/nas_copy.sha256) || return 1
  cp /tmp/nas_copy.sha256 "$2"/SHA256SUMS.txt
}
rm -f $D/logs/after_v1_2.done

log "copy v1_2 (OOM at ~1940, best=step1800) to NAS"
nas_copy $V $NAS/v1_2 && log "NAS v1_2 OK ($(wc -l < /tmp/nas_copy.sha256) files)" || fin "NAS_V12_FAILED" 1

COMMON="--data $D/data --init-adapter $V/out/rag_k3/best --replay-data $V/data --replay-per-task 1500 --replay-tasks T1,T2,T3 --batch-size 2 --grad-accum 8 --lr 1e-4 --warmup-ratio 0.03 --seed 0 --eval-batch-size 8"
log "bench: no GC (15 iters)"
nice -n 10 python $TR $COMMON --out $D/out/bench_nogc --iters 15 --valid-per-task 50 --eval-every 1000 --log-every 1 > $D/logs/bench_nogc.log 2>&1; log "bench no GC exit=$?"
log "bench: GC (15 iters, gc-check)"
nice -n 10 python $TR $COMMON --out $D/out/bench_gc --iters 15 --valid-per-task 50 --eval-every 1000 --log-every 1 --gradient-checkpointing --gc-check > $D/logs/bench_gc.log 2>&1; rc=$?; log "bench GC exit=$rc"
[ $rc -eq 0 ] || fin "BENCH_GC_FAILED" 1

log "resume v1_2 from best(step1800): 1331 iters, lr 8.2e-5 cosine -> 0, no warmup, GC on"
nice -n 10 python $TR --data $V/data --out $V/out/rag_k3_resume --init-adapter $V/out/rag_k3/best \
  --batch-size 2 --grad-accum 8 --lr 8.2e-5 --scheduler cosine --warmup-ratio 0 --iters 1331 --seed 1 \
  --valid-per-task 300 --eval-every 200 --log-every 10 --eval-batch-size 8 --gradient-checkpointing --gc-check > $V/logs/rag_k3_resume_train.log 2>&1
rc=$?; log "v1_2 resume exit=$rc"
[ $rc -eq 0 ] && [ -f $V/out/rag_k3_resume/summary.json ] || fin "V12_RESUME_FAILED" 1
log "copy v1_2 (with resume) to NAS"
nas_copy $V $NAS/v1_2 && log "NAS v1_2 OK ($(wc -l < /tmp/nas_copy.sha256) files)" || fin "NAS_V12_FAILED" 1

log "continued training: design_v0 (+replay T1-T3 x1500) from v1_2 resume best, GC on, 2 epochs"
nice -n 10 python $TR --data $D/data --init-adapter $V/out/rag_k3_resume/best --replay-data $V/data --replay-per-task 1500 --replay-tasks T1,T2,T3 \
  --batch-size 2 --grad-accum 8 --lr 1e-4 --warmup-ratio 0.03 --seed 0 --eval-batch-size 8 \
  --out $D/out/cont_v1_2 --epochs 2 --valid-per-task 300 --eval-every 200 --log-every 10 --best-on main --gradient-checkpointing --gc-check > $D/logs/cont_train.log 2>&1
rc=$?; log "continued training exit=$rc"
[ $rc -eq 0 ] && [ -f $D/out/cont_v1_2/summary.json ] || fin "CONT_TRAIN_FAILED" 1

log "copy design_v0 to NAS"
nas_copy $D $NAS/design_v0 && log "NAS design_v0 OK ($(wc -l < /tmp/nas_copy.sha256) files)" || fin "NAS_DESIGN_FAILED" 1
fin "ALLDONE"
