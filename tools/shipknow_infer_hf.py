#!/usr/bin/env python3
"""shipknow 推論（HF transformers + PEFT）: test を greedy 生成し、tools/shipknow_score.py の予測形式で書き出す。

  - 全タスク: greedy（enable_thinking=False、左パディングのバッチ生成、タスク別 max_new_tokens）。
  - T1: 25 coarse ラベルの ranking（assistant 先頭 ``{"ship_type":"<label>"`` の token logprob 合計）。
        プロンプトを 1 回だけ forward して KV キャッシュを 25 本に複製し、ラベルごとの続き（数トークン）だけを
        バッチで流す。トークン境界: ``{"ship_type":"<label>","ship_type_fine":"`` をトークン化し、
        ``{"ship_type":"<label>"`` の範囲に start offset が入るトークンの logprob を合計する
        （閉じ引用符が ``","`` のように次の文字と 1 トークンに融合しても、その融合トークンは含める。近似でなく
        モデルが実際に出す BPE 列の尤度）。
  - T3: greedy が点推定（"text"）。温度 --temperature（既定 0.7）で --t3-samples（既定 5）本サンプル -> "samples"。

adapter 省略でゼロショット。--per-task N で各タスク N 件（(task,variant) 層化・seed 固定）のサブセットだけ処理する。
--ids FILE で id の集合に絞る（JSON: id の配列 / {"all_ids": [...]} / {"ids": {"T1": [...], ...}}、または 1 行 1 id のテキスト）。

サンプリング生成（既定は従来どおり greedy）: --sample-seed N を付けると、--sample-tasks（既定 D）のタスクを温度 --temperature・--top-p でサンプリング生成する
（各タスクの生成の直前に torch.manual_seed(N) と cuda seed を設定。入力の並びはプロンプト長の安定ソートで決まり、モデルによらず同一。
サンプリング結果はバッチ分割にも依存するので、モデル間比較は同じ --batch-size で行う）。出力行に "seed": N を入れる。
バッチサイズ・ピーク VRAM・生成トークン数・所要時間は <out>_meta.json の gen_stats に記録する。

複数ターン会話の評価（--multi-turn-eval。design-v1 の {id, category, messages, metadata:{train_turns}} 用）:
  各エピソードの「最後の学習対象ターン」k（metadata.train_turns の最大値。無ければ最後の assistant）について、messages[:k] を
  add_generation_prompt=True（enable_thinking=False）でレンダリングして生成する。k より前の assistant は gold をそのまま文脈に使う
  （teacher-forcing 評価）。学習時の prefix（navspec_qwen35_sft_train.render_prefix）と同一の prompt。--tasks / --per-task は使わず、
  --categories（カンマ区切り）/ --per-category N（category x subtype 層化・seed 固定）/ --ids / --limit（各 category 先頭 N 件）で絞る。
  出力行: {"id", "category", "turn_index", "text", "seed"}（seed は --sample-seed 指定時のみ値、greedy は null）。
  --max-new-tokens（既定 512。正解の最長は約 350 トークン）。長い会話（最大 約 4.8k トークン）では --batch-size 8〜16 を推奨。

使用例（llm_master, RTX 3060 固定）::

    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<3060 UUID> python shipknow/tools/shipknow_infer_hf.py \\
        --test shipknow/data/test.jsonl --adapter shipknow/out/pilot/best --out shipknow/out/pilot/test_preds.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import time
from pathlib import Path
from typing import Any

MAX_NEW = {"T1": 64, "T2": 320, "T3": 64, "D": 448}  # D = 設計タスク（設計 JSON、正解は最大約 300 トークン）
PREFIX = '{"ship_type":"'


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def stratified_pick(rows: list[dict[str, Any]], per_task: int, seed: int) -> list[dict[str, Any]]:
    """navspec_qwen35_sft_train.stratified_pick と同じ: 各タスク per_task 件を (task, variant) 比率で層化抽出。"""
    rng = random.Random(seed)
    by_task: dict[str, dict[str, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, r in enumerate(rows):
        by_task[r["task"]][r.get("variant", "")].append(i)
    chosen: set[int] = set()
    for task, variants in by_task.items():
        total = sum(len(v) for v in variants.values())
        if per_task >= total:
            for v in variants.values():
                chosen.update(v)
            continue
        quota = {k: len(v) * per_task / total for k, v in variants.items()}
        take = {k: int(q) for k, q in quota.items()}
        rest = per_task - sum(take.values())
        for k in sorted(variants, key=lambda k: -(quota[k] - take[k]))[:rest]:
            take[k] += 1
        for k, idxs in variants.items():
            idxs = sorted(idxs)
            rng.shuffle(idxs)
            chosen.update(idxs[: take[k]])
    return [rows[i] for i in sorted(chosen)]


def prompt_text(tok: Any, row: dict[str, Any]) -> str:
    msgs = row["messages"]
    if msgs and msgs[-1].get("role") == "assistant":  # 正解付きの行は正解を外す（架空シナリオは正解なし）
        msgs = msgs[:-1]
    return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)


def last_train_turn(row: dict[str, Any]) -> int:
    """評価する assistant ターンの messages index: metadata.train_turns の最大値（無ければ最後の assistant）。"""
    tt = (row.get("metadata") or {}).get("train_turns", row.get("train_turns"))
    if tt:
        return int(max(tt))
    msgs = row["messages"]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "assistant":
            return i
    raise ValueError("assistant ターンが無い: " + str(row.get("id")))


def multi_turn_item(row: dict[str, Any]) -> dict[str, Any]:
    """teacher-forcing 評価の 1 件: prompt の messages = messages[:k]（k より前の assistant は gold）。"""
    k = last_train_turn(row)
    msgs = row["messages"]
    if k < 2 or msgs[k].get("role") != "assistant" or msgs[k - 1].get("role") != "user":
        raise ValueError(f"評価ターン {k} が assistant ターンでない（直前が user でない）: {row.get('id')}")
    return {"id": row["id"], "category": row.get("category") or row.get("task") or "D", "turn_index": k, "messages": msgs[:k]}


def select_multi_turn_items(rows: list[dict[str, Any]], categories: set[str] | None = None, ids: set[str] | None = None,
                            per_category: int = 0, seed: int = 0, limit: int = 0) -> list[dict[str, Any]]:
    """--multi-turn-eval の対象を選ぶ（入力順を保つ）。絞り込み順: category -> ids -> per-category 層化 -> limit。"""
    if categories:
        rows = [r for r in rows if (r.get("category") or r.get("task")) in categories]
    if ids is not None:
        rows = [r for r in rows if r["id"] in ids]
    if per_category > 0:
        tmp = [{"id": r["id"], "task": r.get("category") or r.get("task") or "D", "variant": (r.get("metadata") or {}).get("subtype", "")} for r in rows]
        keep = {t["id"] for t in stratified_pick(tmp, per_category, seed)}
        rows = [r for r in rows if r["id"] in keep]
    if limit > 0:
        cnt: dict[str, int] = collections.defaultdict(int)
        kept = []
        for r in rows:
            c = r.get("category") or r.get("task") or "D"
            if cnt[c] < limit:
                kept.append(r)
                cnt[c] += 1
        rows = kept
    return [multi_turn_item(r) for r in rows]


def run_multi_turn_eval(args: argparse.Namespace, model: Any, tok: Any, device: Any) -> int:
    import gzip

    import torch

    rows = read_jsonl(args.test)
    cats = {c for c in args.categories.split(",") if c} if args.categories else None
    ids = load_id_filter(args.ids) if args.ids is not None else None
    items = select_multi_turn_items(rows, cats, ids, args.per_category, args.seed, args.limit)
    print(f"[multi-turn-eval] {len(items)} episodes: {dict(collections.Counter(i['category'] for i in items))}", flush=True)
    sample = None
    if args.sample_seed is not None:
        set_seed(args.sample_seed)
        sample = {"n": 1, "temperature": args.temperature, "top_p": args.top_p}
    cuda = device.type == "cuda"
    if cuda:
        torch.cuda.reset_peak_memory_stats()
    stats: dict[str, Any] = {}
    t0 = time.time()
    texts = generate(model, tok, [{"messages": it["messages"]} for it in items], args.max_new_tokens, args.batch_size, device, sample=sample, stats=stats)
    sec = round(time.time() - t0, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if args.out.suffix == ".gz" else open
    with opener(args.out, "wt", encoding="utf-8") as f:
        for it, t in zip(items, texts):
            f.write(json.dumps({"id": it["id"], "category": it["category"], "turn_index": it["turn_index"], "text": t[0], "seed": args.sample_seed}, ensure_ascii=False) + "\n")
    meta = {"mode": "multi_turn_eval", "model": args.model, "adapter": str(args.adapter) if args.adapter else None, "merged": bool(args.merge), "n": len(items),
            "by_category": dict(collections.Counter(i["category"] for i in items)), "batch_size": args.batch_size, "max_new_tokens": args.max_new_tokens,
            "sample_seed": args.sample_seed, "temperature": args.temperature if sample else None, "top_p": args.top_p if sample else None, "seed": args.seed,
            "ids_file": str(args.ids) if args.ids else None, "sec": sec, "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2) if cuda else None, **stats,
            "generated_tokens_per_sec": round(stats.get("generated_tokens", 0) / max(sec, 1e-9), 1)}
    args.out.with_name(args.out.name.split(".")[0] + "_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print("[done]", json.dumps(meta), flush=True)
    return 0


def generate(model: Any, tok: Any, rows: list[dict[str, Any]], max_new: int, batch_size: int, device: Any,
             sample: dict[str, Any] | None = None, stats: dict[str, Any] | None = None) -> list[list[str]]:
    """rows を長さ順にバッチ生成。sample=None なら greedy（各 1 本）、指定時は num_return_sequences 本ずつ返す
    （sample: {"n", "temperature", "top_p"(省略時 1.0)}）。stats を渡すと batches / prompt_tokens / generated_tokens を加算する。"""
    import torch

    prompts = [prompt_text(tok, r) for r in rows]
    # generation_config の eos に <|im_end|> が入っていないので明示する（入れないと JSON の後も生成し続ける）
    eos_ids = sorted({tok.convert_tokens_to_ids("<|im_end|>"), tok.eos_token_id} - {None})
    order = sorted(range(len(rows)), key=lambda i: len(prompts[i]))
    out: list[list[str] | None] = [None] * len(rows)
    tok.padding_side = "left"
    for s in range(0, len(order), batch_size):
        idx = order[s : s + batch_size]
        enc = tok([prompts[i] for i in idx], return_tensors="pt", padding=True, add_special_tokens=False).to(device)
        kw: dict[str, Any]
        if sample is None:
            kw = dict(do_sample=False, temperature=None, top_p=None, top_k=None)
            n_ret = 1
        else:
            n_ret = sample["n"]
            kw = dict(do_sample=True, temperature=sample["temperature"], top_p=sample.get("top_p", 1.0), top_k=0, num_return_sequences=n_ret)
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=max_new, repetition_penalty=1.0, eos_token_id=eos_ids, pad_token_id=tok.pad_token_id, **kw)
        texts = tok.batch_decode(gen[:, enc["input_ids"].shape[1] :], skip_special_tokens=True)
        if stats is not None:
            stats["batches"] = stats.get("batches", 0) + 1
            stats["prompt_tokens"] = stats.get("prompt_tokens", 0) + int(enc["attention_mask"].sum())
            stats["generated_tokens"] = stats.get("generated_tokens", 0) + count_generated(gen[:, enc["input_ids"].shape[1] :].tolist(), eos_ids)
        for j, i in enumerate(idx):
            out[i] = [t.strip() for t in texts[j * n_ret : (j + 1) * n_ret]]
    return out  # type: ignore[return-value]


def count_generated(new_ids: list[list[int]], eos_ids: list[int]) -> int:
    """生成トークン数（各系列で最初の eos まで、eos を含む。eos が無ければ全長）の合計。"""
    eos = set(eos_ids)
    tot = 0
    for seq in new_ids:
        n = len(seq)
        for i, t in enumerate(seq):
            if t in eos:
                n = i + 1
                break
        tot += n
    return tot


def load_id_filter(path: Path) -> set[str]:
    """--ids FILE の読み込み。JSON（配列 / {"all_ids": [...]} / {"ids": {task: [...]}}）か、1 行 1 id のテキスト。"""
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
    except ValueError:
        return {l.strip() for l in text.splitlines() if l.strip()}
    if isinstance(obj, list):
        return {str(x) for x in obj}
    if isinstance(obj, dict):
        if "all_ids" in obj:
            return {str(x) for x in obj["all_ids"]}
        if isinstance(obj.get("ids"), dict):
            return {str(x) for v in obj["ids"].values() for x in v}
        if isinstance(obj.get("ids"), list):
            return {str(x) for x in obj["ids"]}
    raise SystemExit(f"--ids {path}: unsupported format")


def set_seed(seed: int) -> None:
    import random as _random

    import numpy as _np
    import torch

    _random.seed(seed)
    _np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def label_continuations(tok: Any, labels: list[str]) -> list[dict[str, Any]]:
    """各ラベルの続きトークン列と、logprob を合計する位置（先頭 .. n_score）を作る。"""
    conts = []
    for lab in labels:
        head = PREFIX + lab + '"'
        full = head + ',"ship_type_fine":"'
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ids = enc["input_ids"]
        n_score = sum(1 for a, _ in enc["offset_mapping"] if a < len(head))
        conts.append({"label": lab, "ids": ids, "n_score": n_score})
    return conts


def expand_cache(cache: Any, n: int) -> None:
    """KV / 線形注意の状態キャッシュをバッチ次元で n 倍に複製（Qwen3.5 は LinearAttentionLayer に batch_repeat_interleave が無い）。"""
    for layer in cache.layers:
        if hasattr(layer, "conv_states"):
            for i in list(layer.conv_states):
                if layer.is_conv_states_initialized[i]:
                    layer.conv_states[i] = layer.conv_states[i].repeat_interleave(n, dim=0)
                if layer.is_recurrent_states_initialized[i]:
                    layer.recurrent_states[i] = layer.recurrent_states[i].repeat_interleave(n, dim=0)
        else:
            layer.batch_repeat_interleave(n)


def t1_ranking(model: Any, tok: Any, row: dict[str, Any], conts: list[dict[str, Any]], device: Any) -> dict[str, float]:
    """プロンプトを 1 回 forward -> KV を len(conts) 本に複製 -> 各ラベルの続きを right-pad してバッチ forward。"""
    import torch

    enc = tok(prompt_text(tok, row), return_tensors="pt", add_special_tokens=False).to(device)
    with torch.no_grad():
        out = model(**enc, use_cache=True)
        last_logp = torch.log_softmax(out.logits[0, -1].float(), dim=-1)  # 続きの先頭トークンの予測分布
        cache = out.past_key_values
        expand_cache(cache, len(conts))
        ml = max(len(c["ids"]) for c in conts)
        ids = torch.full((len(conts), ml), tok.pad_token_id, dtype=torch.long)
        for i, c in enumerate(conts):
            ids[i, : len(c["ids"])] = torch.tensor(c["ids"])
        ids = ids.to(device)
        plen = enc["input_ids"].shape[1]
        att = torch.ones((len(conts), plen + ml), dtype=torch.long, device=device)
        out2 = model(input_ids=ids, attention_mask=att, past_key_values=cache, use_cache=True)
        logp = torch.log_softmax(out2.logits.float(), dim=-1)  # (C, ml, V)
    res: dict[str, float] = {}
    for i, c in enumerate(conts):
        tot = float(last_logp[c["ids"][0]])
        for p in range(1, c["n_score"]):
            tot += float(logp[i, p - 1, c["ids"][p]])
        res[c["label"]] = tot
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    ap.add_argument("--adapter", type=Path, default=None, help="PEFT アダプタのディレクトリ。省略でゼロショット")
    ap.add_argument("--merge", action="store_true", help="アダプタを bf16 重みにマージして推論（既定はアンマージ）")
    ap.add_argument("--test", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="予測 JSONL（.gz なら gzip）")
    ap.add_argument("--tasks", default="T1,T2,T3")
    ap.add_argument("--per-task", type=int, default=0, help="各タスク N 件へ層化サブセット（0 で全件）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="デバッグ用: 各タスク先頭 N 件のみ")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--t3-samples", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--top-p", type=float, default=1.0, help="サンプリング生成の top-p（既定 1.0 = 無効）")
    ap.add_argument("--sample-seed", type=int, default=None, help="指定すると --sample-tasks をサンプリング生成する（温度 --temperature、top-p --top-p）。出力行に seed を記録")
    ap.add_argument("--sample-tasks", default="D", help="サンプリング生成にするタスク（カンマ区切り。all で全タスク）。--sample-seed 指定時のみ有効")
    ap.add_argument("--ids", type=Path, default=None, help="この id の集合だけ処理する（JSON か 1 行 1 id のテキスト。forget_ids.json など）")
    ap.add_argument("--no-ranking", action="store_true")
    ap.add_argument("--multi-turn-eval", action="store_true", help="design-v1 の会話を評価: 各エピソードの最後の学習対象ターンを messages[:k]（過去の assistant は gold）から生成（上記）")
    ap.add_argument("--categories", default=None, help="--multi-turn-eval の対象 category（カンマ区切り。省略で全て）")
    ap.add_argument("--per-category", type=int, default=0, help="--multi-turn-eval: 各 category N 件へ (category, subtype) 層化サブセット（0 で全件）")
    ap.add_argument("--max-new-tokens", type=int, default=512, help="--multi-turn-eval の最大生成トークン数")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--dtype", default="bfloat16")
    args = ap.parse_args()

    import gzip

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=getattr(torch, args.dtype)).to(device)
    if args.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(args.adapter))
        if args.merge:
            model = model.merge_and_unload()
    model.eval()
    if args.multi_turn_eval:
        return run_multi_turn_eval(args, model, tok, device)

    rows = read_jsonl(args.test)
    for r in rows:  # 設計の架空シナリオ（scenarios.jsonl）は task を持たないので D として扱う
        r.setdefault("task", "D")
    tasks = args.tasks.split(",")
    rows = [r for r in rows if r["task"] in tasks]
    if args.ids is not None:
        keep_ids = load_id_filter(args.ids)
        missing = keep_ids - {r["id"] for r in rows}
        rows = [r for r in rows if r["id"] in keep_ids]
        print(f"[ids] {len(rows)} rows kept from {args.ids} ({len(missing)} ids of the file not in --test/--tasks)", flush=True)
    if args.per_task > 0:
        rows = stratified_pick(rows, args.per_task, args.seed)
    if args.limit > 0:
        cnt: dict[str, int] = collections.defaultdict(int)
        keep = []
        for r in rows:
            if cnt[r["task"]] < args.limit:
                keep.append(r)
                cnt[r["task"]] += 1
        rows = keep

    manifest = json.loads((args.test.parent / "manifest.json").read_text(encoding="utf-8"))
    labels = manifest.get("coarse_types")  # T1 の ranking にだけ使う
    preds: dict[str, dict[str, Any]] = {r["id"]: {"id": r["id"]} for r in rows}
    timing: dict[str, float] = {}
    gen_stats: dict[str, Any] = {}
    sample_tasks = set(tasks) if args.sample_tasks == "all" else set(args.sample_tasks.split(","))
    cuda = device.type == "cuda"
    peak_load = torch.cuda.max_memory_allocated() / 2**30 if cuda else None
    for task in tasks:
        trows = [r for r in rows if r["task"] == task]
        if not trows:
            continue
        stats: dict[str, Any] = {}
        if cuda:
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        sampled = args.sample_seed is not None and task in sample_tasks
        if sampled:
            set_seed(args.sample_seed)
            texts = generate(model, tok, trows, MAX_NEW[task], args.batch_size, device, sample={"n": 1, "temperature": args.temperature, "top_p": args.top_p}, stats=stats)
            for r, t in zip(trows, texts):
                preds[r["id"]]["text"] = t[0]
                preds[r["id"]]["seed"] = args.sample_seed
            key = f"{task}_sample_sec"
        else:
            texts = generate(model, tok, trows, MAX_NEW[task], args.batch_size, device, stats=stats)
            for r, t in zip(trows, texts):
                preds[r["id"]]["text"] = t[0]
            key = f"{task}_greedy_sec"
        timing[key] = round(time.time() - t0, 1)
        gen_stats[task] = {"mode": "sample" if sampled else "greedy", "n": len(trows), "batch_size": args.batch_size, "sec": timing[key],
                           "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2) if cuda else None, **stats,
                           "generated_tokens_per_sec": round(stats.get("generated_tokens", 0) / max(timing[key], 1e-9), 1)}
        print(f"[{task}] {'sample seed=' + str(args.sample_seed) if sampled else 'greedy'} done n={len(trows)} {timing[key]}s "
              f"peak={gen_stats[task]['peak_gib']}GiB gen_tokens={stats.get('generated_tokens')}", flush=True)
        if task == "T3" and args.t3_samples > 0:
            t0 = time.time()
            smp = generate(model, tok, trows, MAX_NEW[task], max(1, args.batch_size // 2), device,
                           sample={"n": args.t3_samples, "temperature": args.temperature})
            for r, t in zip(trows, smp):
                preds[r["id"]]["samples"] = t
            timing["T3_sampling_sec"] = round(time.time() - t0, 1)
            print(f"[T3] sampling done {timing['T3_sampling_sec']}s", flush=True)
        if task == "T1" and not args.no_ranking and labels:
            t0 = time.time()
            conts = label_continuations(tok, labels)
            for k, r in enumerate(trows):
                preds[r["id"]]["ranking"] = t1_ranking(model, tok, r, conts, device)
                if (k + 1) % 500 == 0:
                    print(f"[T1] ranking {k + 1}/{len(trows)} {time.time() - t0:.0f}s", flush=True)
            timing["T1_ranking_sec"] = round(time.time() - t0, 1)
            print(f"[T1] ranking done {timing['T1_ranking_sec']}s", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if args.out.suffix == ".gz" else open
    with opener(args.out, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(preds[r["id"]], ensure_ascii=False) + "\n")
    meta = {"model": args.model, "adapter": str(args.adapter) if args.adapter else None, "merged": bool(args.merge),
            "n": len(rows), "tasks": tasks, "per_task": args.per_task, "seed": args.seed, "temperature": args.temperature,
            "t3_samples": args.t3_samples, "max_new_tokens": MAX_NEW, "batch_size": args.batch_size,
            "sample_seed": args.sample_seed, "sample_tasks": sorted(sample_tasks) if args.sample_seed is not None else None, "top_p": args.top_p,
            "ids_file": str(args.ids) if args.ids else None,
            "peak_gib": round(max([g["peak_gib"] for g in gen_stats.values() if g["peak_gib"] is not None] + [peak_load or 0.0]), 2) if cuda else None,
            "peak_gib_after_load": round(peak_load, 2) if peak_load is not None else None, "gen_stats": gen_stats,
            "total_sec": round(sum(timing.values()), 1), **timing}
    args.out.with_name(args.out.name.split(".")[0] + "_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print("[done]", json.dumps(meta), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
