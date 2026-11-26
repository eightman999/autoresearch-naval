#!/usr/bin/env python3
"""Qwen3.5 (text-only) + PEFT LoRA trainer for the Stage 4a armament verdict task.

自前の学習ループ。vocab 248k のため全位置 logits は OOM になるので、
``base.model(...)`` の last_hidden_state から completion 位置（prompt_len-1 .. len-2）
だけを取り出して lm_head -> float -> CE を計算する（completion-only loss）。

定期評価（valid 全件・teacher forcing、生成なし）:
  (a) completion 平均 loss（トークン加重）
  (b) status トークン正解率（status 値の最初のトークンの argmax が正解か）
best は (b) の最大、同点は (a) の最小で選ぶ。best / final の PEFT アダプタ、
評価ログ jsonl、config.json を --out に保存する。

--iters / --epochs はどちらも optimizer step 数ベース（1 step = batch_size * grad_accum samples）。
--epochs を指定すると steps = round(epochs * n_train / (batch_size * grad_accum))。

使用例（llm_master, RTX 3060 固定）::

    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<3060 UUID> \
    python tools/navspec_qwen35_verdict_train.py \
        --data data/stage4a_armament_verdict_v2 --out out/r1 \
        --rank 16 --alpha 32 --lr 2e-4 --epochs 3
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
from pathlib import Path
from typing import Any

DEFAULT_TARGETS = (
    "q_proj,k_proj,v_proj,o_proj,"  # full attention
    "in_proj_qkv,in_proj_z,in_proj_b,in_proj_a,out_proj,"  # linear attention
    "gate_proj,up_proj,down_proj"  # MLP
)
STATUS_RE = re.compile(r'"status":\s*"')


def parse_layers(spec: str | None) -> list[int] | None:
    """'8-23' / '0-3,8,10-12'（両端含む）を index リストにする。"""
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def encode_row(tok: Any, messages: list[dict[str, str]]) -> dict[str, Any]:
    """full / prompt をトークン化し、completion 開始位置と status 値の最初のトークン位置を返す。"""
    prompt = tok.apply_chat_template(
        messages[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    full = tok.apply_chat_template(messages, tokenize=False, enable_thinking=False)
    if not full.startswith(prompt):
        raise ValueError("prompt 文字列が full の prefix になっていない")
    enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
    ids = enc["input_ids"]
    prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    if ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("prompt トークン列が full の prefix になっていない")
    m = STATUS_RE.search(full, len(prompt))
    if m is None:
        raise ValueError("completion に status が見つからない")
    value_start = m.end()
    status_pos = None
    for i, (a, b) in enumerate(enc["offset_mapping"]):
        if a <= value_start < b:
            status_pos = i
            break
    if status_pos is None or status_pos < len(prompt_ids):
        raise ValueError("status 値のトークン位置を特定できない")
    status = json.loads(messages[-1]["content"]).get("status")
    return {"ids": ids, "pl": len(prompt_ids), "status_pos": status_pos, "status": status}


def make_batch(tok: Any, batch: list[dict[str, Any]], device: Any) -> tuple[Any, Any]:
    import torch

    ml = max(len(e["ids"]) for e in batch)
    ids = torch.full((len(batch), ml), tok.pad_token_id, dtype=torch.long)
    att = torch.zeros((len(batch), ml), dtype=torch.long)
    for i, e in enumerate(batch):
        ids[i, : len(e["ids"])] = torch.tensor(e["ids"])
        att[i, : len(e["ids"])] = 1
    return ids.to(device), att.to(device)


def completion_loss_sum(base: Any, ids: Any, att: Any, batch: list[dict[str, Any]]) -> tuple[Any, int, Any, Any]:
    """completion 位置だけ lm_head を通し、(CE 合計, トークン数, logits, 位置→サンプル対応) を返す。"""
    import torch
    import torch.nn.functional as F

    h = base.model(input_ids=ids, attention_mask=att, use_cache=False).last_hidden_state
    sel_h, sel_y = [], []
    for i, e in enumerate(batch):
        n = len(e["ids"])
        sel_h.append(h[i, e["pl"] - 1 : n - 1])
        sel_y.append(ids[i, e["pl"] : n])
    logits = base.lm_head(torch.cat(sel_h)).float()
    y = torch.cat(sel_y)
    loss_sum = F.cross_entropy(logits, y, reduction="sum")
    return loss_sum, int(y.numel()), logits, y


def evaluate(pm: Any, base: Any, tok: Any, data: list[dict[str, Any]], batch_size: int, device: Any) -> dict[str, Any]:
    import torch

    pm.eval()
    tot_loss, tot_tok = 0.0, 0
    correct = {"ok": 0, "impossible": 0}
    support = {"ok": 0, "impossible": 0}
    pred_counts: dict[str, int] = {}
    with torch.no_grad():
        for s in range(0, len(data), batch_size):
            batch = data[s : s + batch_size]
            ids, att = make_batch(tok, batch, device)
            loss_sum, n_tok, logits, y = completion_loss_sum(base, ids, att, batch)
            tot_loss += float(loss_sum)
            tot_tok += n_tok
            # logits の行は各サンプルの (pl-1 .. n-2) を連結したもの
            offset = 0
            for e in batch:
                n = len(e["ids"])
                row = offset + (e["status_pos"] - 1 - (e["pl"] - 1))
                pred = int(logits[row].argmax())
                gold = e["ids"][e["status_pos"]]
                support[e["status"]] = support.get(e["status"], 0) + 1
                if pred == gold:
                    correct[e["status"]] = correct.get(e["status"], 0) + 1
                pred_tok = tok.convert_ids_to_tokens(pred)
                pred_counts[pred_tok] = pred_counts.get(pred_tok, 0) + 1
                offset += n - e["pl"]
    pm.train()
    n = len(data)
    n_ok = sum(correct.values())
    return {
        "valid_loss": tot_loss / max(tot_tok, 1),
        "status_acc": n_ok / max(n, 1),
        "status_acc_by_class": {
            k: (correct[k] / support[k] if support[k] else None) for k in support
        },
        "status_pred_tokens": dict(sorted(pred_counts.items(), key=lambda kv: -kv[1])[:5]),
        "n_valid": n,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    ap.add_argument("--data", type=Path, required=True, help="train.jsonl / valid.jsonl を含むディレクトリ")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=float, default=32)
    ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--target-modules", default=DEFAULT_TARGETS, help="カンマ区切り（lm_head は含めない）")
    ap.add_argument("--layers", default=None, help="対象層 index（例 '8-23' = 上位16層）。省略で全層")
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--epochs", type=float, default=None)
    ap.add_argument("--iters", type=int, default=None, help="optimizer step 数（--epochs と排他）")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--warmup-ratio", type=float, default=0.05)
    ap.add_argument("--scheduler", choices=["cosine", "constant"], default="cosine")
    ap.add_argument("--weight-decay", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--eval-every", type=int, default=50, help="optimizer step 単位。最終 step でも必ず評価")
    ap.add_argument("--eval-batch-size", type=int, default=8)
    ap.add_argument("--log-every", type=int, default=10)
    ap.add_argument("--train-limit", type=int, default=0, help="デバッグ用: train 先頭 N 行のみ使う")
    args = ap.parse_args(argv)

    if (args.epochs is None) == (args.iters is None):
        ap.error("--epochs か --iters のどちらか一方を指定する")

    import torch
    import transformers
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    device = torch.device("cuda")

    args.out.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token

    train_rows = read_jsonl(args.data / "train.jsonl")
    if args.train_limit > 0:
        train_rows = train_rows[: args.train_limit]
    valid_rows = read_jsonl(args.data / "valid.jsonl")
    train = [encode_row(tok, r["messages"]) for r in train_rows]
    valid = [encode_row(tok, r["messages"]) for r in valid_rows]
    per_step = args.batch_size * args.grad_accum
    steps = args.iters if args.iters is not None else max(1, round(args.epochs * len(train) / per_step))
    warmup = int(round(args.warmup_ratio * steps))

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to(device)
    layers = parse_layers(args.layers)
    lora_cfg = LoraConfig(
        r=args.rank,
        lora_alpha=args.alpha,
        lora_dropout=args.dropout,
        target_modules=[m.strip() for m in args.target_modules.split(",") if m.strip()],
        layers_to_transform=layers,
        bias="none",
        task_type="CAUSAL_LM",
    )
    pm = get_peft_model(model, lora_cfg)
    pm.print_trainable_parameters()
    pm.train()
    base = pm.base_model.model
    params = [p for p in pm.parameters() if p.requires_grad]
    n_trainable = sum(p.numel() for p in params)
    n_lora_modules = sum(1 for n, _ in pm.named_modules() if n.endswith("lora_A"))
    print(f"LoRA modules: {n_lora_modules}, trainable params: {n_trainable:,}, steps: {steps}, warmup: {warmup}")

    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=args.weight_decay, betas=(0.9, 0.999))

    def lr_factor(step: int) -> float:  # step: 0-based optimizer step about to run
        if warmup > 0 and step < warmup:
            return (step + 1) / warmup
        if args.scheduler == "constant":
            return 1.0
        progress = (step - warmup) / max(1, steps - warmup)
        return 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))

    config = {
        **{k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "n_train": len(train),
        "n_valid": len(valid),
        "steps": steps,
        "warmup_steps": warmup,
        "samples_per_step": per_step,
        "layers_resolved": layers,
        "n_lora_modules": n_lora_modules,
        "trainable_params": n_trainable,
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__},
    }
    try:
        import peft

        config["versions"]["peft"] = peft.__version__
    except Exception:
        pass
    (args.out / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_path = args.out / "train_log.jsonl"
    log_f = log_path.open("w", encoding="utf-8")

    def log(rec: dict[str, Any]) -> None:
        log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log_f.flush()

    # データ順: epoch ごとに seed 由来の permutation を連結した無限ストリーム
    rng = random.Random(args.seed)
    stream: list[int] = []

    def next_indices(n: int) -> list[int]:
        while len(stream) < n:
            perm = list(range(len(train)))
            rng.shuffle(perm)
            stream.extend(perm)
        out = stream[:n]
        del stream[:n]
        return out

    best_key: tuple[float, float] | None = None
    best_step = None
    t0 = time.time()
    tok_count = 0
    run_loss, run_n = 0.0, 0
    eval_total = 0.0  # 学習時間から評価時間を除いて tok/s を出す

    def do_eval(step: int, allow_best: bool) -> None:
        nonlocal best_key, best_step, eval_total
        te = time.time()
        res = evaluate(pm, base, tok, valid, args.eval_batch_size, device)
        eval_total += time.time() - te
        key = (res["status_acc"], -res["valid_loss"])
        is_best = allow_best and (best_key is None or key > best_key)
        if is_best:
            best_key, best_step = key, step
            pm.save_pretrained(str(args.out / "best"))
        rec = {"type": "eval", "step": step, **res, "is_best": is_best, "eval_sec": round(time.time() - te, 1)}
        log(rec)
        print(
            f"[eval] step {step} valid_loss {res['valid_loss']:.4f} status_acc {res['status_acc']:.4f} "
            f"by_class {res['status_acc_by_class']} pred {res['status_pred_tokens']}"
            + (" *best*" if is_best else ""),
            flush=True,
        )

    do_eval(0, allow_best=False)
    for step in range(steps):
        factor = lr_factor(step)
        for g in opt.param_groups:
            g["lr"] = args.lr * factor
        idx = next_indices(per_step)
        step_loss = 0.0
        for a in range(args.grad_accum):
            batch = [train[i] for i in idx[a * args.batch_size : (a + 1) * args.batch_size]]
            ids, att = make_batch(tok, batch, device)
            loss_sum, n_tok, _, _ = completion_loss_sum(base, ids, att, batch)
            loss = loss_sum / n_tok
            (loss / args.grad_accum).backward()
            step_loss += loss.item() / args.grad_accum
            tok_count += int(att.sum())
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)
        run_loss += step_loss
        run_n += 1
        done = step + 1
        if done % args.log_every == 0 or done == steps:
            el = time.time() - t0 - eval_total
            rec = {
                "type": "train", "step": done, "loss": run_loss / run_n, "lr": args.lr * factor,
                "tokens_per_s": round(tok_count / el, 1), "elapsed_sec": round(el, 1),
                "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            }
            log(rec)
            print(
                f"[train] step {done}/{steps} loss {rec['loss']:.4f} lr {rec['lr']:.2e} "
                f"{rec['tokens_per_s']} tok/s peak {rec['peak_gib']}GiB",
                flush=True,
            )
            run_loss, run_n = 0.0, 0
        if (args.eval_every > 0 and done % args.eval_every == 0) or done == steps:
            do_eval(done, allow_best=True)

    pm.save_pretrained(str(args.out / "final"))
    summary = {
        "best_step": best_step,
        "best_status_acc": best_key[0] if best_key else None,
        "best_valid_loss": -best_key[1] if best_key else None,
        "steps": steps,
        "train_sec": round(time.time() - t0, 1),
        "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
    }
    log({"type": "summary", **summary})
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("[done]", json.dumps(summary), flush=True)
    log_f.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
