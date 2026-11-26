#!/usr/bin/env python3
"""Qwen3.5 (text-only) + PEFT LoRA の汎用マルチタスク SFT トレーナー（shipknow 用）。

navspec_qwen35_verdict_train.py と同じ方式の自前ループ。vocab 248k のため全位置 logits は OOM になるので、
``base.model(...)`` の last_hidden_state から completion 位置（prompt_len-1 .. len-2）だけを取り出して
lm_head -> float -> CE を計算する（completion-only loss）。verdict 版のような status トークン評価は無い。

データ: ``--data`` ディレクトリの train.jsonl / valid.jsonl。各行は {id, task, variant, messages:[system,user,assistant]}。

学習データ順:
  epoch ごとに seed 由来の permutation を ``--bucket-chunk`` 件ずつのチャンクに切り、チャンク内を長さ順に並べて
  micro-batch（--batch-size 件）を作り、チャンク内で micro-batch の順序をシャッフルする（padding 削減）。
  1 optimizer step = --batch-size * --grad-accum 件。loss は 1 step 内の completion トークン総数で割る（トークン平均）。

valid:
  (task, variant) で層化した各タスク --valid-per-task 件のサブセット（seed 固定・ids を out/valid_subset_ids.json に記録）で
  completion 平均 loss（トークン加重）を全体・タスク別・(task, variant) 別に記録。best は全体 valid loss で選ぶ。

サブサンプリング:
  --train-per-task N: 各タスクから N 件を (task, variant) 層化で seed 固定抽出し、ids を out/train_ids.json に記録。
  --train-ids FILE: 以前記録した ids（JSON list）だけを使う。
  --tasks T3: 指定タスクだけで学習・評価（診断用の単独学習）。

--iters / --epochs はどちらも optimizer step 数ベース。--epochs 指定時は steps = round(epochs * n_train / (bs * ga))。

勾配チェックポイント:
  --gradient-checkpointing: transformers の gradient_checkpointing_enable(use_reentrant=False) を PEFT 構築前に有効化し、
  config.use_cache=False にする（non-reentrant は入力が requires_grad でなくても動き、RNG（LoRA dropout）も再計算時に復元される）。
  有効化できた層数を表示し、0 ならエラー。--gc-check で学習前に 1 micro-batch を GC あり/なしで流し loss と勾配の一致を確認する
  （fla カーネル等の再計算の決定性を GPU 上で確かめる用。学習 RNG 状態は変えない）。

継続学習:
  --init-adapter PATH: 既存 PEFT LoRA アダプタを trainable で読み込んで続きから学習する。rank / alpha / target-modules / layers は
  アダプタ側の設定を正とし、引数が明示指定されていて食い違えば警告する。--dropout は明示指定された場合のみ上書き。
  保存されるアダプタ（best / final）は同じ構成。

再生データ混合（忘却対策）:
  --replay-data DIR --replay-per-task N [--replay-tasks T1,T2,T3]: DIR/train.jsonl から指定タスクごとに N 件を
  (task, variant) 層化・seed 固定で抽出して主データの train に混ぜる。valid も DIR/valid.jsonl から同じタスクを
  --valid-per-task 件ずつ抽出して追加し、タスク名別（loss_by_group）に記録して忘却を監視する。
  主データとタスク名が衝突する再生タスクは "replay_<task>" に改名して集計する。
  best の選択は --best-on main（既定: 主データ valid の loss）か all（主+再生）。
  出力: train_ids.json / valid_subset_ids.json は主データのみ（従来互換）、再生分は replay_train_ids.json / replay_valid_ids.json。
  eval ログには valid_loss（選択に使った指標）に加えて valid_loss_main / valid_loss_all / valid_loss_replay を記録する。

ログ: train レコードに tokens_per_s（累積）・tokens_per_s_window / sec_per_step（直近 log 窓、eval 除く）・peak_gib（全体）・
  peak_train_gib（eval を除いた学習区間のピーク）を記録する。

複数ターン会話（design-v1: {id, category, messages:[system,user,assistant,user,...], metadata:{train_turns:[...]}}）:
  metadata.train_turns（assistant ターンの messages index）に指定されたターンの「本文 + <|im_end|>」だけが損失対象
  （他の assistant ターン・system・user は対象外。終端 \\n は対象外）。train_turns が無い行は従来どおり最後の assistant（\\n まで）。
  Qwen3.5 の chat template は「最後の user より後の assistant」にだけ ``<think>\\n\\n</think>\\n\\n`` を付け、過去ターンには付けない。
  そのため会話全体を 1 回レンダリングすると、途中の学習対象ターン（modify の [2,4] の 2）の直前 prefix が推論時
  （messages[:k] を add_generation_prompt=True でレンダリング）と食い違う。方式（encode_episode）:
    * 各学習対象ターン k について prefix = tok(apply_chat_template(messages[:k], add_generation_prompt=True)) と
      target = tok(本文 + <|im_end|>) を作る（推論の prompt と同一のトークン列を構成上保証）。
    * 全体レンダリングの ids が prefix・target とトークン列で一致するターンは、1 本の列（複数 span）にまとめて学習する。
    * 一致しないターンは「prefix + target」だけの別サンプルにする（計算量は増える）。
  --audit-prefix N: 各カテゴリ N 件で上記の一致（推論 prefix・本文・全体レンダリングとの関係）を検査して終了する（モデル不要）。
  グループ別 valid loss のキーは category（無ければ task）。--valid-per-task / --train-per-task（= --train-per-category）は category ごとの件数。
  --train-fraction F: category（+ subtype）層化で各 category の F 倍の件数を seed 固定で抽出（id は train_ids.json）。
  --max-seq-len N: N トークン超のサンプルを --over-length で処理（skip: 捨てる / drop-history: 先頭の過去 (user, assistant) を落とす）し件数を表示。
  --max-tokens-per-batch T: micro-batch を「パディング込み T トークン以下」かつ --batch-size 件以下に詰める（長さ順チャンク内）。
    このとき 1 optimizer step = --grad-accum 個の micro-batch（--epochs の step 数は 1 epoch の micro-batch 数から計算）。
  --count-only: category 別のエピソード数・サンプル数・総トークン数・学習対象トークン数を表示し token_count.json に書く
    （--epochs と --est-tok-per-s（既定 2000）から所要時間の見積もりも出す）。

長時間学習の安全策:
  --save-every N [--keep-last K]: N optimizer step ごとに <out>/ckpt_stepNNNNN/（adapter/ + trainer_state.pt = optimizer・RNG・best の進捗・データ順の
    ハッシュ）を保存し、古いものは K 個（既定 3）だけ残す。best / final とは別。最終 step は final に任せるので保存しない。
  --resume-from ckpt_stepNNNNN|latest: 上記から正確に再開する（同じ --out・同じデータ / 引数で呼ぶ。データ順は seed から決定的に再構成し、
    ハッシュが違えばエラー）。学習率 schedule は step だけで決まるので step を復元すれば足りる。train_log.jsonl には追記する。
  --preflight-longest [--preflight-only]: 学習前に最悪ケース（最長サンプル単独・パディング最大のバッチ・損失対象トークン最大のバッチ・最大の eval バッチ）を
    1 回 forward/backward して時間とピーク VRAM を表示する（optimizer・RNG・勾配は動かさない。CUDA OOM は終了コード 3）。

使用例（llm_master, RTX 3060 固定）::

    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<3060 UUID> \\
    python tools/navspec_qwen35_sft_train.py --data shipknow/data --out shipknow/out/pilot \\
        --rank 64 --alpha 128 --dropout 0.05 --lr 2e-4 --epochs 1 --batch-size 4 --grad-accum 4 --warmup-ratio 0.03 --seed 0

    # 継続学習 + 再生 + 勾配チェックポイント
    python tools/navspec_qwen35_sft_train.py --data data/shipknow_design_v0 --out out/design_v0 \\
        --init-adapter out/v1_2/best --replay-data data/shipknow_v1_2_rag --replay-per-task 1500 --replay-tasks T1,T2,T3 \\
        --gradient-checkpointing --batch-size 2 --grad-accum 8 --lr 1e-4 --epochs 2
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import shutil
import time
from pathlib import Path
from typing import Any

DEFAULT_TARGETS = (
    "q_proj,k_proj,v_proj,o_proj,"  # full attention
    "in_proj_qkv,in_proj_z,in_proj_b,in_proj_a,out_proj,"  # linear attention
    "gate_proj,up_proj,down_proj"  # MLP
)


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


IM_END = "<|im_end|>"


def row_group(r: dict[str, Any]) -> str:
    """グループ別集計・層化のキー: category（design-v1）、無ければ task（従来形式）。"""
    return r.get("category") or r["task"]


def row_variant(r: dict[str, Any]) -> str:
    """層化・集計の副キー: variant（従来形式）、無ければ metadata.subtype（design-v1）。"""
    v = r.get("variant")
    if v is None:
        v = (r.get("metadata") or {}).get("subtype", "")
    return str(v or "")


def encode_row(tok: Any, row: dict[str, Any]) -> dict[str, Any]:
    """full / prompt をトークン化し、completion 開始位置 pl を返す（従来形式。最後の assistant が損失対象、終端 \\n まで）。"""
    messages = row["messages"]
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    full = tok.apply_chat_template(messages, tokenize=False, enable_thinking=False)
    if not full.startswith(prompt):
        raise ValueError("prompt 文字列が full の prefix になっていない: " + row.get("id", "?"))
    ids = tok(full, add_special_tokens=False)["input_ids"]
    prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    if ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("prompt トークン列が full の prefix になっていない: " + row.get("id", "?"))
    return {"id": row["id"], "task": row_group(row), "variant": row_variant(row), "ids": ids, "pl": len(prompt_ids)}


# --------------------------------------------------------------------------------------------------
# 複数ターン（train_turns）のエンコード
# --------------------------------------------------------------------------------------------------
def train_turn_indices(row: dict[str, Any]) -> list[int] | None:
    """metadata.train_turns（無ければ row["train_turns"]）。無ければ None（= 従来どおり最後の assistant）。形が不正なら ValueError。"""
    tt = (row.get("metadata") or {}).get("train_turns", row.get("train_turns"))
    if tt is None:
        return None
    msgs = row["messages"]
    out = sorted({int(i) for i in tt})
    if not out:
        raise ValueError("train_turns が空: " + str(row.get("id", "?")))
    for i in out:
        if not (2 <= i < len(msgs)) or msgs[i].get("role") != "assistant" or msgs[i - 1].get("role") != "user":
            raise ValueError(f"train_turns の index {i} が assistant ターンでない（直前が user でない）: {row.get('id', '?')}")
    return out


def render_prefix(tok: Any, messages: list[dict[str, Any]], k: int) -> str:
    """推論時の prompt: messages[:k] を add_generation_prompt=True でレンダリング（shipknow_infer_hf.prompt_text と同じ）。"""
    return tok.apply_chat_template(messages[:k], tokenize=False, add_generation_prompt=True, enable_thinking=False)


def _ids(tok: Any, text: str) -> list[int]:
    return tok(text, add_special_tokens=False)["input_ids"]


def turn_target_text(messages: list[dict[str, Any]], k: int) -> str:
    """学習対象ターンの本文 + 終端。template は content を trim してレンダリングするので同様に strip する。"""
    return messages[k]["content"].strip() + IM_END


def turn_pieces(tok: Any, messages: list[dict[str, Any]], k: int) -> tuple[list[int], list[int]]:
    """(推論時 prompt のトークン列, 本文 + <|im_end|> のトークン列)。"""
    return _ids(tok, render_prefix(tok, messages, k)), _ids(tok, turn_target_text(messages, k))


def _alternating(msgs: list[dict[str, Any]]) -> bool:
    return all(m.get("role") == ("user" if i % 2 == 0 else "assistant") for i, m in enumerate(msgs[1:]))


def _is_feedback(m: dict[str, Any]) -> bool:
    return str(m.get("content", "")).lstrip().startswith('{"validator"')


def _fit_turn(tok: Any, messages: list[dict[str, Any]], k: int, max_seq_len: int, over_length: str, stats: collections.Counter
              ) -> tuple[list[int], list[int]] | None:
    """prefix + target が max_seq_len に収まる (prefix_ids, target_ids)。収まらなければ over_length に従い、駄目なら None。"""
    p, t = turn_pieces(tok, messages, k)
    if not max_seq_len or len(p) + len(t) <= max_seq_len:
        return p, t
    stats["over_length_samples"] += 1
    if over_length == "drop-history" and _alternating(messages[:k]):
        for j in range(1, (k - 2) // 2 + 1):  # 先頭の j 組の (user, assistant) を落とす。最後の user (k-1) は残す
            sub = [messages[0]] + messages[1 + 2 * j : k]
            if _is_feedback(sub[1]):
                continue  # 元の要求を失う（validator のフィードバックだけが先頭になる）ので不可
            p2, t2 = turn_pieces(tok, sub + [messages[k]], len(sub))
            if len(p2) + len(t2) <= max_seq_len:
                stats["history_dropped_samples"] += 1
                stats["history_dropped_pairs"] += j
                return p2, t2
    stats["skipped_samples"] += 1
    return None


def encode_episode(tok: Any, row: dict[str, Any], *, max_seq_len: int = 0, over_length: str = "drop-history",
                   stats: collections.Counter | None = None) -> list[dict[str, Any]]:
    """1 エピソード（行）を学習サンプルのリストにする。サンプル = {id, task(=category), variant, ids, pl, spans, turns}。

    spans = 損失対象の [start, end)（ids 上。本文 + <|im_end|>）。複数 span のサンプルは 1 本の列で複数ターンを学習する。
    train_turns が無い行は従来の encode_row（最後の assistant、終端 \\n まで）。方式の詳細はモジュール docstring。
    """
    stats = stats if stats is not None else collections.Counter()
    stats["episodes"] += 1
    msgs = row["messages"]
    turns = train_turn_indices(row)
    base = {"id": row["id"], "task": row_group(row), "variant": row_variant(row)}
    if turns is None:
        e = encode_row(tok, row)
        if not max_seq_len or len(e["ids"]) <= max_seq_len:
            stats["samples"] += 1
            return [{**e, "spans": [(e["pl"], len(e["ids"]))], "turns": [len(msgs) - 1]}]
        turns = [len(msgs) - 1]  # 超過: 最後の assistant を下の per-turn 経路で処理する（終端の改行は対象外になる）
        stats["over_length_legacy"] += 1
    whole_ids = _ids(tok, tok.apply_chat_template(msgs, tokenize=False, enable_thinking=False))
    pieces = {k: turn_pieces(tok, msgs, k) for k in turns}
    matched, split = [], []
    for k in turns:
        p, t = pieces[k]
        ok = whole_ids[: len(p)] == p and whole_ids[len(p) : len(p) + len(t)] == t
        (matched if ok else split).append(k)
    stats["turns"] += len(turns)
    stats["turns_whole_render_match"] += len(matched)
    stats["turns_whole_render_mismatch"] += len(split)
    out: list[dict[str, Any]] = []
    if matched:
        spans = [(len(pieces[k][0]), len(pieces[k][0]) + len(pieces[k][1])) for k in matched]
        ids = whole_ids[: spans[-1][1]]
        if max_seq_len and len(ids) > max_seq_len:
            split = sorted(matched + split)  # 長すぎる: ターンごとの短い列に分け、各々を --over-length で処理
            matched = []
        else:
            out.append({**base, "ids": ids, "pl": spans[0][0], "spans": spans, "turns": matched})
            if len(matched) > 1:
                stats["multi_span_samples"] += 1
    for k in split:
        fit = _fit_turn(tok, msgs, k, max_seq_len, over_length, stats)
        if fit is None:
            continue
        p, t = fit
        out.append({**base, "ids": p + t, "pl": len(p), "spans": [(len(p), len(p) + len(t))], "turns": [k]})
        stats["split_turn_samples"] += 1
    if not out:
        stats["skipped_episodes"] += 1
    stats["samples"] += len(out)
    return out


def sample_spans(e: dict[str, Any]) -> list[tuple[int, int]]:
    return e.get("spans") or [(e["pl"], len(e["ids"]))]


def n_target(e: dict[str, Any]) -> int:
    """サンプルの損失対象トークン数。"""
    return sum(b - a for a, b in sample_spans(e))


def encode_rows(tok: Any, rows: list[dict[str, Any]], src: str, **kw: Any) -> list[dict[str, Any]]:
    stats = kw.pop("stats", None)
    if stats is None:
        stats = collections.Counter()
    out = []
    for r in rows:
        out.extend({**e, "src": src} for e in encode_episode(tok, r, stats=stats, **kw))
    return out


def _stratified(rows: list[dict[str, Any]], count_of: Any, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by_task: dict[str, dict[str, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, r in enumerate(rows):
        by_task[row_group(r)][row_variant(r)].append(i)
    chosen: set[int] = set()
    for task, variants in by_task.items():
        total = sum(len(v) for v in variants.values())
        per_task = count_of(total)
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


def stratified_pick(rows: list[dict[str, Any]], per_task: int, seed: int) -> list[dict[str, Any]]:
    """各グループ（category、無ければ task）から per_task 件を (グループ, variant|subtype) の比率で層化抽出（決定的）。返り値は元の順序を保つ。"""
    return _stratified(rows, lambda total: per_task, seed)


def stratified_fraction(rows: list[dict[str, Any]], fraction: float, seed: int) -> list[dict[str, Any]]:
    """各グループから round(fraction * 件数)（最低 1）件を層化抽出（決定的）。"""
    return _stratified(rows, lambda total: max(1, int(round(total * fraction))), seed)


def make_batch(tok: Any, batch: list[dict[str, Any]], device: Any) -> tuple[Any, Any]:
    import torch

    ml = max(len(e["ids"]) for e in batch)
    ids = torch.full((len(batch), ml), tok.pad_token_id, dtype=torch.long)
    att = torch.zeros((len(batch), ml), dtype=torch.long)
    for i, e in enumerate(batch):
        ids[i, : len(e["ids"])] = torch.tensor(e["ids"])
        att[i, : len(e["ids"])] = 1
    return ids.to(device), att.to(device)


def completion_loss(base: Any, ids: Any, att: Any, batch: list[dict[str, Any]]) -> tuple[Any, Any, list[int]]:
    """損失対象位置だけ lm_head を通し、(トークン別 CE のベクトル, 目的 y, サンプルごとの損失対象トークン数) を返す。

    損失対象 = sample_spans(e) の各 [a, b)（従来形式は [pl, len) の 1 つ）。位置 a-1 .. b-2 の hidden で a .. b-1 のトークンを予測する。
    """
    import torch
    import torch.nn.functional as F

    h = base.model(input_ids=ids, attention_mask=att, use_cache=False).last_hidden_state
    sel_h, sel_y, lens = [], [], []
    for i, e in enumerate(batch):
        n_i = 0
        for a, b in sample_spans(e):
            sel_h.append(h[i, a - 1 : b - 1])
            sel_y.append(ids[i, a:b])
            n_i += b - a
        lens.append(n_i)
    logits = base.lm_head(torch.cat(sel_h)).float()
    y = torch.cat(sel_y)
    ce = F.cross_entropy(logits, y, reduction="none")
    return ce, y, lens


def micro_step(base: Any, tok: Any, batch: list[dict[str, Any]], device: Any, n_tok_step: int) -> tuple[float, int]:
    """1 micro-batch の forward + backward（勾配は蓄積）。(loss 寄与, 入力トークン数) を返す。"""
    ids, att = make_batch(tok, batch, device)
    ce, _, _ = completion_loss(base, ids, att, batch)
    loss = ce.sum() / n_tok_step
    loss.backward()
    return loss.item(), int(att.sum())


def pack_by_tokens(order: list[int], lengths: list[int], batch_size: int, max_tokens: int) -> list[list[int]]:
    """長さ昇順の index 列 order を micro-batch に詰める。1 batch はパディング込み（件数 x 最大長）で max_tokens 以下、
    かつ batch_size 件以下（単独で max_tokens を超えるサンプルは 1 件だけの batch にする）。max_tokens <= 0 なら batch_size 件ずつ。"""
    if max_tokens <= 0:
        return [order[j : j + batch_size] for j in range(0, len(order), batch_size)]
    out: list[list[int]] = []
    cur: list[int] = []
    for i in order:
        if cur and (len(cur) >= batch_size or (len(cur) + 1) * lengths[i] > max_tokens):
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def evaluate(pm: Any, base: Any, tok: Any, data: list[dict[str, Any]], batch_size: int, device: Any,
             best_on: str = "main", max_tokens: int = 0) -> dict[str, Any]:
    """valid の completion 平均 loss（トークン加重）を全体・グループ（category / task）別・(グループ, variant) 別に返す。

    e["src"]（"main" / "replay"、無ければ main）別の集計も返す。valid_loss は best_on（main / all）で選んだ指標。
    """
    import torch

    pm.eval()
    order = sorted(range(len(data)), key=lambda i: len(data[i]["ids"]))
    groups = pack_by_tokens(order, [len(e["ids"]) for e in data], batch_size, max_tokens)
    tot: dict[str, list[float]] = collections.defaultdict(lambda: [0.0, 0])  # key -> [loss_sum, n_tok]
    by_src: dict[str, list[float]] = collections.defaultdict(lambda: [0.0, 0])
    with torch.no_grad():
        for grp in groups:
            batch = [data[i] for i in grp]
            ids, att = make_batch(tok, batch, device)
            ce, _, lens = completion_loss(base, ids, att, batch)
            off = 0
            for e, ln in zip(batch, lens):
                v = float(ce[off : off + ln].sum())
                for key in ("all", e["task"], f"{e['task']}:{e['variant']}"):
                    tot[key][0] += v
                    tot[key][1] += ln
                src = by_src[e.get("src", "main")]
                src[0] += v
                src[1] += ln
                off += ln
    pm.train()
    loss = {k: v[0] / max(v[1], 1) for k, v in tot.items()}
    n_tok = {k: v[1] for k, v in tot.items()}
    loss_main = by_src["main"][0] / max(by_src["main"][1], 1) if "main" in by_src else None
    loss_replay = by_src["replay"][0] / max(by_src["replay"][1], 1) if "replay" in by_src else None
    chosen = loss["all"] if best_on == "all" or loss_main is None else loss_main
    out = {"valid_loss": chosen, "valid_loss_all": loss["all"], "valid_loss_main": loss_main,
           "loss_by_group": {k: round(v, 5) for k, v in sorted(loss.items()) if k != "all"},
           "tokens_by_group": {k: v for k, v in sorted(n_tok.items()) if k != "all"}, "n_valid": len(data)}
    if loss_replay is not None:
        out["valid_loss_replay"] = loss_replay
    return out


def build_steps(n: int, lengths: list[int], steps: int, per_step: int, batch_size: int, grad_accum: int,
                chunk: int, rng: random.Random, max_tokens: int = 0) -> list[list[list[int]]]:
    """steps 個の optimizer step 分の micro-batch インデックス列を作る: [step][micro][sample idx]。

    epoch ごとに permutation を作り、chunk 件ずつ長さ順 -> micro-batch 化 -> micro-batch をシャッフルする。
    micro-batch を grad_accum 個ずつ 1 step にまとめる（端数は次 epoch に繰り越さず捨てずに連結ストリームで処理）。
    max_tokens > 0 のときは micro-batch を pack_by_tokens（パディング込み max_tokens 以下、batch_size 件以下）で作る。
    この場合 micro-batch の件数は可変で、端数（batch_size 未満）の micro-batch も捨てない。
    """
    micro_stream: list[list[int]] = []
    out: list[list[list[int]]] = []
    need_micro = steps * grad_accum
    if n < batch_size and max_tokens <= 0:
        raise ValueError(f"train が batch_size 未満: n={n} < {batch_size}")
    while sum(1 for m in micro_stream if max_tokens > 0 or len(m) == batch_size) < need_micro:  # 端数 micro-batch は数えない（固定件数時）
        micro_stream.extend(epoch_micro_batches(n, lengths, batch_size, chunk, rng, max_tokens))
    # 末尾の端数 micro-batch（batch_size 未満）は step の途中に入ると per_step が崩れるので除く（固定件数時）
    if max_tokens <= 0:
        micro_stream = [m for m in micro_stream if len(m) == batch_size]
    micro_stream = micro_stream[:need_micro]
    for s in range(steps):
        out.append(micro_stream[s * grad_accum : (s + 1) * grad_accum])
    return out


def epoch_micro_batches(n: int, lengths: list[int], batch_size: int, chunk: int, rng: random.Random, max_tokens: int = 0) -> list[list[int]]:
    """1 epoch 分の micro-batch（permutation -> chunk 件ずつ長さ順 -> micro-batch 化 -> チャンク内で micro-batch をシャッフル）。"""
    perm = list(range(n))
    rng.shuffle(perm)
    ep_micro: list[list[int]] = []
    for c in range(0, n, chunk):
        part = sorted(perm[c : c + chunk], key=lambda i: lengths[i])
        mbs = pack_by_tokens(part, lengths, batch_size, max_tokens)
        rng.shuffle(mbs)
        ep_micro.extend(mbs)
    return ep_micro


# --------------------------------------------------------------------------------------------------
# 引数（明示指定の検出）・LoRA 構築・勾配チェックポイント・再生データ
# --------------------------------------------------------------------------------------------------
LORA_ARG_DESTS = ("rank", "alpha", "dropout", "target_modules", "layers")


def parse_args_with_explicit(ap: argparse.ArgumentParser, argv: list[str] | None) -> tuple[argparse.Namespace, set[str]]:
    """通常どおり parse しつつ、LORA_ARG_DESTS のうちコマンドラインで明示指定されたものの集合も返す（abbrev 対応）。"""
    sentinel = object()
    defaults = {d: ap.get_default(d) for d in LORA_ARG_DESTS}
    ap.set_defaults(**{d: sentinel for d in LORA_ARG_DESTS})
    try:
        args = ap.parse_args(argv)
    finally:
        ap.set_defaults(**defaults)
    explicit: set[str] = set()
    for d in LORA_ARG_DESTS:
        if getattr(args, d) is sentinel:
            setattr(args, d, defaults[d])
        else:
            explicit.add(d)
    return args, explicit


def _as_set(v: Any) -> set[str] | None:
    if v is None:
        return None
    if isinstance(v, str):
        return {v}
    return {str(x) for x in v}


def _as_layers(v: Any) -> list[int] | None:
    if v is None:
        return None
    if isinstance(v, int):
        return [v]
    return sorted(int(x) for x in v)


def reconcile_lora_args(cfg: Any, args: argparse.Namespace, explicit: set[str] | frozenset[str]) -> list[str]:
    """--init-adapter のアダプタ設定 cfg(LoraConfig) を正として args を書き換え、食い違いの警告文を返す。

    rank / alpha / target-modules / layers は常にアダプタ側を採用（明示指定かつ不一致のときだけ警告）。
    dropout は学習ハイパーパラメータなので、明示指定時のみ cfg.lora_dropout を上書きし、未指定ならアダプタ側を使う。
    """
    warns: list[str] = []
    arg_targets = {m.strip() for m in str(args.target_modules).split(",") if m.strip()}
    adapter = {"rank": cfg.r, "alpha": cfg.lora_alpha, "target_modules": _as_set(cfg.target_modules),
               "layers": _as_layers(cfg.layers_to_transform)}
    given = {"rank": args.rank, "alpha": args.alpha, "target_modules": arg_targets, "layers": parse_layers(args.layers)}
    flag = {"rank": "--rank", "alpha": "--alpha", "target_modules": "--target-modules", "layers": "--layers"}
    for k, av in adapter.items():
        if k in explicit and given[k] != av:
            warns.append(f"{flag[k]} {given[k]!r} はアダプタの設定 {av!r} と不一致。アダプタ側を使う")
    args.rank = cfg.r
    args.alpha = cfg.lora_alpha
    tm = _as_set(cfg.target_modules)
    args.target_modules = ",".join(sorted(tm)) if tm is not None else args.target_modules
    lay = _as_layers(cfg.layers_to_transform)
    args.layers = ",".join(str(i) for i in lay) if lay else None
    if "dropout" in explicit:
        if args.dropout != cfg.lora_dropout:
            warns.append(f"--dropout {args.dropout} でアダプタの lora_dropout {cfg.lora_dropout} を上書きする")
        cfg.lora_dropout = args.dropout
    else:
        args.dropout = cfg.lora_dropout
    return warns


def build_peft_model(model: Any, args: argparse.Namespace, explicit: set[str] | frozenset[str] = frozenset()
                     ) -> tuple[Any, dict[str, Any], list[str]]:
    """新規 LoRA か --init-adapter の読み込み（trainable）で PEFT モデルを作る。(pm, info, warnings) を返す。"""
    from peft import LoraConfig, PeftConfig, PeftModel, get_peft_model

    if getattr(args, "init_adapter", None) is None:
        cfg = LoraConfig(
            r=args.rank,
            lora_alpha=args.alpha,
            lora_dropout=args.dropout,
            target_modules=[m.strip() for m in args.target_modules.split(",") if m.strip()],
            layers_to_transform=parse_layers(args.layers),
            bias="none",
            task_type="CAUSAL_LM",
        )
        return get_peft_model(model, cfg), {"mode": "new", "layers_resolved": parse_layers(args.layers)}, []
    path = Path(args.init_adapter)
    if not (path / "adapter_config.json").is_file():
        raise FileNotFoundError(f"--init-adapter に adapter_config.json が無い: {path}")
    cfg = PeftConfig.from_pretrained(str(path))
    if str(getattr(cfg.peft_type, "value", cfg.peft_type)).upper() != "LORA":
        raise ValueError(f"--init-adapter は LoRA のみ対応: {cfg.peft_type}")
    warns = reconcile_lora_args(cfg, args, explicit)
    pm = PeftModel.from_pretrained(model, str(path), is_trainable=True, config=cfg)
    lora = [(n, p) for n, p in pm.named_parameters() if "lora_" in n]
    frozen = [n for n, p in lora if not p.requires_grad]
    if not lora or frozen:
        raise RuntimeError(f"アダプタを trainable で読み込めていない: lora params {len(lora)}, frozen {len(frozen)}")
    info = {"mode": "init_adapter", "path": str(path), "r": cfg.r, "lora_alpha": cfg.lora_alpha,
            "lora_dropout": cfg.lora_dropout, "target_modules": sorted(_as_set(cfg.target_modules) or []),
            "layers_resolved": _as_layers(cfg.layers_to_transform), "adapter_base_model": cfg.base_model_name_or_path,
            "n_lora_tensors": len(lora)}
    return pm, info, warns


def count_checkpointed_layers(model: Any) -> int:
    """gradient checkpointing が有効になっているモジュール数（有効化の実効性確認用）。"""
    return sum(1 for m in model.modules()
               if getattr(m, "gradient_checkpointing", False) is True and getattr(m, "_gradient_checkpointing_func", None) is not None)


def enable_gradient_checkpointing(model: Any) -> int:
    """non-reentrant の勾配チェックポイントを有効化する（PEFT 構築前の素のモデルに対して呼ぶ）。有効化できた数を返す。

    use_reentrant=False: 入力が requires_grad でなくても動き、torch.utils.checkpoint が RNG（LoRA dropout 等）を
    再計算時に復元する。train() 中だけ有効（eval / no_grad の valid には影響しない）。use_cache は無効化する。
    """
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    n = count_checkpointed_layers(model)
    if n == 0:
        raise RuntimeError("gradient checkpointing を有効化できなかった（このモデル/transformers が未対応）")
    return n


def gc_selfcheck(pm: Any, base: Any, tok: Any, batch: list[dict[str, Any]], device: Any, n_tok_step: int,
                 params: list[Any], tol: float) -> dict[str, float]:
    """同じ micro-batch・同じ RNG で GC なし/ありの loss と LoRA 勾配を比べる。学習側の RNG 状態は変えない。

    再計算の決定性（fla カーネル等）の確認用。相対 L2 誤差が tol 超ならエラー。
    """
    import torch

    devices = [device] if getattr(device, "type", "cpu") == "cuda" else []
    was_on = count_checkpointed_layers(base) > 0
    res: dict[str, tuple[float, Any]] = {}
    with torch.random.fork_rng(devices=devices):
        for mode in ("off", "on"):
            if mode == "off":
                base.gradient_checkpointing_disable()
            else:
                enable_gradient_checkpointing(base)
            for p in params:
                p.grad = None
            torch.manual_seed(12345)
            if devices:
                torch.cuda.manual_seed_all(12345)
            loss, _ = micro_step(base, tok, batch, device, n_tok_step)
            res[mode] = (loss, torch.cat([(p.grad if p.grad is not None else torch.zeros_like(p)).detach().float().flatten()
                                          for p in params]).clone())
    for p in params:
        p.grad = None
    if not was_on:
        base.gradient_checkpointing_disable()
    (l0, g0), (l1, g1) = res["off"], res["on"]
    rel = float((g1 - g0).norm() / g0.norm().clamp_min(1e-30))
    out = {"loss_off": l0, "loss_on": l1, "loss_abs_diff": abs(l1 - l0), "grad_rel_l2": rel,
           "grad_max_abs_diff": float((g1 - g0).abs().max()), "grad_norm": float(g0.norm())}
    if not (rel <= tol) or not math.isfinite(l1) or abs(l1 - l0) > tol * max(1.0, abs(l0)):
        raise RuntimeError(f"gradient checkpointing の再計算が一致しない: {out} (tol {tol})")
    return out


def load_replay(args: argparse.Namespace, main_tasks: set[str], main_train_ids: set[str]
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """--replay-data から再生用の train / valid 行を抽出する（タスクごと・seed 固定・(task,variant) 層化）。

    主データとタスク名が衝突するタスクは "replay_<task>" に改名する（グループ別 loss を混ぜないため）。
    返り値: (replay_train_rows, replay_valid_rows, info)
    """
    d = Path(args.replay_data)
    tr_all = read_jsonl(d / "train.jsonl")
    va_all = read_jsonl(d / "valid.jsonl")
    avail = {row_group(r) for r in tr_all}
    tasks = [t.strip() for t in args.replay_tasks.split(",") if t.strip()] if args.replay_tasks else sorted(avail)
    missing = [t for t in tasks if t not in avail]
    if missing:
        raise ValueError(f"--replay-tasks に {d}/train.jsonl に無いタスクがある: {missing}（存在: {sorted(avail)}）")
    tr = [r for r in tr_all if row_group(r) in tasks]
    va = [r for r in va_all if row_group(r) in tasks]
    tr_sel = stratified_pick(tr, args.replay_per_task, args.seed)
    va_sel = stratified_pick(va, args.valid_per_task, args.seed) if args.valid_per_task > 0 else va
    rename = {t: f"replay_{t}" for t in tasks if t in main_tasks}

    def relabel(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key = lambda r: "category" if r.get("category") else "task"  # noqa: E731
        return [{**r, key(r): rename.get(row_group(r), row_group(r))} for r in rows]

    tr_sel, va_sel = relabel(tr_sel), relabel(va_sel)
    dup = main_train_ids & {r["id"] for r in tr_sel}
    if dup:
        raise ValueError(f"再生データの id が主データ train と重複: {sorted(dup)[:5]} ...")
    short = {t: n for t in tasks if (n := sum(1 for r in tr if row_group(r) == t)) < args.replay_per_task}
    info = {"replay_tasks": tasks, "renamed": rename, "available_train_rows": {t: sum(1 for r in tr if row_group(r) == t) for t in tasks},
            "short_tasks": short}
    return tr_sel, va_sel, info


class PeakMem:
    """CUDA ピークメモリの追跡。eval 区間を除いた学習区間のピーク(train)と全体のピーク(all)を分けて持つ。"""

    def __init__(self, device: Any) -> None:
        import torch

        self.torch = torch
        self.cuda = getattr(device, "type", "cpu") == "cuda"
        self.all = 0.0
        self.train = 0.0

    def _cur(self) -> float:
        return self.torch.cuda.max_memory_allocated() / 2**30 if self.cuda else 0.0

    def _reset(self) -> None:
        if self.cuda:
            self.torch.cuda.reset_peak_memory_stats()

    def end_train_segment(self) -> None:
        c = self._cur()
        self.train, self.all = max(self.train, c), max(self.all, c)
        self._reset()

    def end_other_segment(self) -> None:
        self.all = max(self.all, self._cur())
        self._reset()

    def snapshot(self) -> tuple[float, float]:
        c = self._cur()
        return max(self.all, c), max(self.train, c)


# --------------------------------------------------------------------------------------------------
# 推論との一致検査（--audit-prefix）
# --------------------------------------------------------------------------------------------------
def audit_episode(tok: Any, row: dict[str, Any], *, max_seq_len: int = 0, over_length: str = "skip") -> dict[str, Any]:
    """1 エピソードについて、学習サンプルが推論と一致するかを検査する。

    各学習対象ターン k:
      prefix_ok  : サンプルの ids[:span 開始] == tok(apply_chat_template(messages[:k], add_generation_prompt=True))（推論の prompt）
      target_ok  : ids[span] == tok(本文 + <|im_end|>)、末尾が <|im_end|>、decode(本文部分) == 本文
      whole_ok   : 会話全体を 1 回レンダリングしたトークン列でも prefix・本文が一致する（False なら別サンプルに分けたターン）
      whole_reason: whole_ok が False のとき、最初に食い違う位置の周辺（<think> ブロックの有無など）
      joint_ok   : prefix 文字列 + 本文 を結合してトークン化しても prefix + target と同じ（境界でトークンが融合しない）
    """
    stats: collections.Counter = collections.Counter()
    samples = encode_episode(tok, row, max_seq_len=max_seq_len, over_length=over_length, stats=stats)
    msgs = row["messages"]
    turns = train_turn_indices(row) or [len(msgs) - 1]
    by_turn = {}
    for sm in samples:
        for k, (a, b) in zip(sm["turns"], sample_spans(sm)):
            by_turn[k] = (sm, a, b)
    whole_ids = _ids(tok, tok.apply_chat_template(msgs, tokenize=False, enable_thinking=False))
    im_end = tok.convert_tokens_to_ids(IM_END)
    res = []
    for k in turns:
        rec: dict[str, Any] = {"turn": k, "present": k in by_turn}
        p_text = render_prefix(tok, msgs, k)
        p = _ids(tok, p_text)  # 推論: shipknow_infer_hf.generate が prompt_text をこの形でトークン化する
        t = _ids(tok, turn_target_text(msgs, k))
        rec["whole_ok"] = whole_ids[: len(p)] == p and whole_ids[len(p) : len(p) + len(t)] == t
        if not rec["whole_ok"]:
            n = 0
            while n < min(len(p), len(whole_ids)) and p[n] == whole_ids[n]:
                n += 1
            rec["whole_reason"] = {"first_diff_pos": n, "prefix_len": len(p),
                                   "infer_tail": tok.decode(p[max(0, n - 3) : n + 8]), "whole_tail": tok.decode(whole_ids[max(0, n - 3) : n + 8])}
        rec["joint_ok"] = _ids(tok, p_text + turn_target_text(msgs, k)) == p + t
        if k in by_turn:
            sm, a, b = by_turn[k]
            ids = sm["ids"]
            rec["prefix_ok"] = ids[:a] == p
            rec["target_ok"] = ids[a:b] == t and ids[b - 1] == im_end and tok.decode(ids[a : b - 1]) == msgs[k]["content"].strip()
            rec["span_tokens"] = b - a
        res.append(rec)
    last_full = None
    if msgs[-1].get("role") == "assistant" and (len(msgs) - 1) in by_turn:  # 最後のターンは従来形式の full レンダリングの prefix になっているはず
        sm, a, b = by_turn[len(msgs) - 1]
        full = encode_row(tok, row)["ids"]
        last_full = full[: len(sm["ids"])] == sm["ids"] if len(sm["ids"]) <= len(full) else False
    return {"id": row["id"], "category": row_group(row), "turns": res, "last_turn_is_full_render_prefix": last_full, "samples": len(samples)}


def audit_rows(tok: Any, rows: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """audit_episode を集計する。by_category: {episodes, turns, 各 ok の件数, whole_ok_turns_by_index, 失敗例}。"""
    by: dict[str, dict[str, Any]] = {}
    fails: list[dict[str, Any]] = []
    for r in rows:
        a = audit_episode(tok, r, **kw)
        c = by.setdefault(a["category"], collections.defaultdict(int))
        c["episodes"] += 1
        if a["last_turn_is_full_render_prefix"] is False:
            c["last_turn_not_full_render_prefix"] += 1
            fails.append({"id": a["id"], "problem": "last_turn_not_full_render_prefix"})
        for t in a["turns"]:
            c["turns"] += 1
            for key in ("prefix_ok", "target_ok", "whole_ok", "joint_ok"):
                if key in t:
                    c[key] += 1 if t[key] else 0
                    c[key + "_checked"] += 1
            if not t["present"]:
                c["turns_missing_from_samples"] += 1
            for key in ("prefix_ok", "target_ok", "joint_ok"):
                if key in t and not t[key]:
                    fails.append({"id": a["id"], "turn": t["turn"], "problem": key})
            if not t["whole_ok"]:
                c["whole_mismatch"] += 1
                c.setdefault("whole_mismatch_examples", [])
                if len(c["whole_mismatch_examples"]) < 2:
                    c["whole_mismatch_examples"].append({"id": a["id"], "turn": t["turn"], **t["whole_reason"]})
            c[f"turn_{t['turn']}_whole_ok"] += 1 if t["whole_ok"] else 0
            c[f"turn_{t['turn']}_n"] += 1
    ok = not fails and all(c["turns_missing_from_samples"] == 0 for c in by.values())
    return {"ok": ok, "episodes": len(rows), "by_category": {k: dict(v) for k, v in sorted(by.items())}, "failures": fails[:30], "n_failures": len(fails)}


# --------------------------------------------------------------------------------------------------
# 長時間学習の安全策: 定期 checkpoint / 再開 / 最長バッチの事前検査
# --------------------------------------------------------------------------------------------------
CKPT_PREFIX = "ckpt_step"


def list_checkpoints(out: Path) -> list[Path]:
    """out 直下の完成した checkpoint（ckpt_stepNNNNN/）を step 昇順で返す（書きかけの .tmp_* は含めない）。"""
    return sorted((d for d in out.glob(CKPT_PREFIX + "*") if d.is_dir() and (d / "trainer_state.pt").is_file()), key=lambda d: int(d.name[len(CKPT_PREFIX):]))


def plan_fingerprint(plan: list[list[list[int]]], train: list[dict[str, Any]]) -> str:
    """データ順（plan）と、それが指すサンプル（id・turns・長さ）のハッシュ。再開時にデータ順が一致するかの確認に使う。"""
    h = hashlib.sha256()
    h.update(json.dumps(plan, separators=(",", ":")).encode())
    h.update(json.dumps([[e["id"], e.get("turns"), len(e["ids"])] for e in train], separators=(",", ":")).encode())
    return h.hexdigest()


def save_checkpoint(out: Path, step: int, pm: Any, opt: Any, state: dict[str, Any], keep_last: int) -> Path:
    """アダプタ + optimizer + RNG + 進捗を ckpt_stepNNNNN/ に保存する（一時ディレクトリに書いて rename。古いものは keep_last 個だけ残す）。"""
    import torch

    final = out / f"{CKPT_PREFIX}{step:05d}"
    tmp = out / f".tmp_{final.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    pm.save_pretrained(str(tmp / "adapter"))
    rng = {"python": random.getstate(), "torch": torch.get_rng_state(),
           "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}
    torch.save({"version": 1, "step": step, "optimizer": opt.state_dict(), "rng": rng, **state}, tmp / "trainer_state.pt")
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    if keep_last > 0:
        for old in list_checkpoints(out)[:-keep_last]:
            shutil.rmtree(old, ignore_errors=True)
    return final


def resolve_resume(spec: str, out: Path) -> Path:
    """--resume-from の解決。'latest' は out 内の最新の完成した checkpoint。"""
    if spec == "latest":
        ck = list_checkpoints(out)
        if not ck:
            raise FileNotFoundError(f"--resume-from latest: {out} に checkpoint (ckpt_step*) が無い")
        return ck[-1]
    d = Path(spec)
    if not ((d / "trainer_state.pt").is_file() and (d / "adapter" / "adapter_config.json").is_file()):
        raise FileNotFoundError(f"--resume-from: {d} に trainer_state.pt と adapter/ が無い（ckpt_stepNNNNN/ を指定する）")
    return d


def preflight_longest(base: Any, tok: Any, train: list[dict[str, Any]], valid: list[dict[str, Any]], plan: list[list[list[int]]], device: Any,
                      params: list[Any], eval_batch_size: int, eval_max_tokens: int) -> list[dict[str, Any]]:
    """学習前に最悪ケースの micro-batch を forward/backward して時間とピーク VRAM を測る（optimizer は動かさない・RNG と勾配は元に戻す）。

    対象: 最長サンプル単独 / plan 中のパディング込みトークン最大の micro-batch / 損失対象トークン最大の micro-batch /
    valid の最大 eval micro-batch（no_grad の forward のみ。評価時のピークはここで決まる）。OOM は例外のまま上げる。
    """
    import torch

    cuda = getattr(device, "type", "cpu") == "cuda"
    pad_tokens = lambda mb: len(mb) * max(len(train[i]["ids"]) for i in mb)  # noqa: E731
    flat = [mb for st in plan for mb in st]
    cases = {"longest_sample": [max(range(len(train)), key=lambda i: len(train[i]["ids"]))]}
    if flat:
        cases["largest_padded_batch"] = max(flat, key=pad_tokens)
        cases["most_loss_tokens_batch"] = max(flat, key=lambda mb: sum(n_target(train[i]) for i in mb))
    out = []
    devices = [device] if cuda else []
    with torch.random.fork_rng(devices=devices):
        for name, mb in cases.items():
            batch = [train[i] for i in mb]
            n_tok = max(1, sum(n_target(e) for e in batch))
            if cuda:
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize(device)
            t = time.perf_counter()
            micro_step(base, tok, batch, device, n_tok)
            if cuda:
                torch.cuda.synchronize(device)
            for p in params:
                p.grad = None
            out.append({"case": name, "batch": len(batch), "max_len": max(len(e["ids"]) for e in batch), "padded_tokens": pad_tokens(mb),
                        "loss_tokens": n_tok, "sec": round(time.perf_counter() - t, 2),
                        "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2) if cuda else None})
        if valid:
            order = sorted(range(len(valid)), key=lambda i: len(valid[i]["ids"]))
            groups = pack_by_tokens(order, [len(e["ids"]) for e in valid], eval_batch_size, eval_max_tokens)
            mb = max(groups, key=lambda g: len(g) * max(len(valid[i]["ids"]) for i in g))
            batch = [valid[i] for i in mb]
            if cuda:
                torch.cuda.reset_peak_memory_stats()
            t = time.perf_counter()
            with torch.no_grad():
                ids, att = make_batch(tok, batch, device)
                completion_loss(base, ids, att, batch)
            if cuda:
                torch.cuda.synchronize(device)
            out.append({"case": "largest_eval_batch", "batch": len(batch), "max_len": max(len(e["ids"]) for e in batch),
                        "padded_tokens": len(batch) * max(len(e["ids"]) for e in batch), "loss_tokens": sum(n_target(e) for e in batch),
                        "sec": round(time.perf_counter() - t, 2), "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2) if cuda else None})
    if cuda:
        torch.cuda.reset_peak_memory_stats()
    return out


DTYPES = {"bfloat16": "bfloat16", "float32": "float32"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    ap.add_argument("--data", type=Path, required=True, help="train.jsonl / valid.jsonl を含むディレクトリ")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rank", type=int, default=64)
    ap.add_argument("--alpha", type=float, default=128)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--target-modules", default=DEFAULT_TARGETS, help="カンマ区切り（lm_head は含めない）")
    ap.add_argument("--layers", default=None, help="対象層 index（例 '8-23'）。省略で全層")
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--epochs", type=float, default=None)
    ap.add_argument("--iters", type=int, default=None, help="optimizer step 数（--epochs と排他）")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--warmup-ratio", type=float, default=0.03)
    ap.add_argument("--scheduler", choices=["cosine", "constant"], default="cosine")
    ap.add_argument("--weight-decay", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eval-every", type=int, default=250, help="optimizer step 単位。最終 step でも必ず評価")
    ap.add_argument("--eval-batch-size", type=int, default=16)
    ap.add_argument("--valid-per-task", type=int, default=300, help="valid の層化サブセット件数（タスク / category あたり、再生データにも適用）。0 で全件")
    ap.add_argument("--log-every", type=int, default=10)
    ap.add_argument("--bucket-chunk", type=int, default=512, help="長さ順バケットのチャンクサイズ（件）。1 で無効")
    ap.add_argument("--tasks", default=None, help="使うタスク / category（カンマ区切り、例 T3 / modify,repair）。主データの train/valid 両方に適用")
    ap.add_argument("--train-per-task", "--train-per-category", dest="train_per_task", type=int, default=0,
                    help="各タスク / category から N 件へ層化サブサンプル（0 で全件、主データのみ。seed 固定、id は train_ids.json）")
    ap.add_argument("--train-fraction", type=float, default=0.0,
                    help="各タスク / category から割合 F（0 < F < 1）へ層化サブサンプル（試し学習用。--train-per-task と排他、seed 固定）")
    ap.add_argument("--train-ids", type=Path, default=None, help="使う train id の JSON list（主データのみ）")
    ap.add_argument("--train-limit", type=int, default=0, help="デバッグ用: train 先頭 N 行のみ使う（主データのみ）")
    ap.add_argument("--count-only", action="store_true", help="エピソード数・サンプル数・トークン数（総数 / 損失対象）を category 別に数えて終了（モデルは読まない）")
    ap.add_argument("--audit-prefix", type=int, default=0, metavar="N",
                    help="各 category N 件で、学習サンプルが推論時の prompt / 本文とトークン列で一致するか検査して終了（prefix_audit.json。モデル不要）")
    ap.add_argument("--max-seq-len", type=int, default=0, help="サンプルの最大トークン数（0 で無制限）。超過は --over-length で処理し件数を表示")
    ap.add_argument("--over-length", choices=["skip", "drop-history"], default="skip",
                    help="--max-seq-len 超過サンプルの扱い: skip=捨てる / drop-history=先頭の過去 (user, assistant) を落として収める（収まらなければ捨てる。"
                         "元の要求を失う repair の feedback 先頭になる落とし方はしない）")
    ap.add_argument("--max-tokens-per-batch", type=int, default=0,
                    help="micro-batch をパディング込みこのトークン数以下に詰める（--batch-size は件数の上限。0 で従来の固定件数）")
    ap.add_argument("--eval-max-tokens", type=int, default=0, help="valid の micro-batch のトークン上限（0 なら --max-tokens-per-batch）")
    ap.add_argument("--save-every", type=int, default=0, metavar="N",
                    help="N optimizer step ごとに <out>/ckpt_stepNNNNN/（アダプタ + optimizer + RNG + 進捗）を保存。best / final とは別。0 で無効")
    ap.add_argument("--keep-last", type=int, default=3, metavar="K", help="--save-every の checkpoint を新しい順に K 個だけ残す（0 で全て残す）")
    ap.add_argument("--resume-from", default=None, metavar="DIR",
                    help="ckpt_stepNNNNN/（または 'latest' = --out 内の最新）から正確に再開する。アダプタ・optimizer・RNG・データ順の位置を復元する。"
                         "同じ --out・同じデータ / 引数で呼ぶこと（データ順が違えばエラー）。--init-adapter より優先")
    ap.add_argument("--preflight-longest", action="store_true",
                    help="学習前に最悪ケース（最長サンプル・最大パディングのバッチ・最大 eval バッチ）を 1 回 forward/backward してピーク VRAM を表示（OOM なら終了コード 3）")
    ap.add_argument("--preflight-only", action="store_true", help="--preflight-longest の結果を表示して終了（学習しない）")
    ap.add_argument("--est-tok-per-s", type=float, default=2000.0, help="--count-only の所要時間見積もりに使う学習速度（tok/s。3060・GC あり実測 約 2000）")
    ap.add_argument("--device", default="cuda", help="既定 cuda（従来どおり）。テスト等で cpu")
    ap.add_argument("--dtype", choices=sorted(DTYPES), default="bfloat16", help="モデル精度。既定 bfloat16。cpu テストでは float32")
    ap.add_argument("--gradient-checkpointing", action="store_true", help="勾配チェックポイント（non-reentrant）。VRAM 削減、速度は低下")
    ap.add_argument("--gc-check", action="store_true", help="学習前に GC あり/なしの loss・勾配一致を確認（--gradient-checkpointing 必須）")
    ap.add_argument("--gc-check-tol", type=float, default=1e-2, help="--gc-check の勾配相対 L2 誤差の許容値（bf16 想定）")
    ap.add_argument("--init-adapter", type=Path, default=None, help="続きから学習する既存 PEFT LoRA アダプタ（trainable で読み込む）")
    ap.add_argument("--replay-data", type=Path, default=None, help="再生データのディレクトリ（train.jsonl / valid.jsonl）")
    ap.add_argument("--replay-per-task", type=int, default=0, help="再生データのタスクあたり train 件数（--replay-data 指定時は必須で 1 以上）")
    ap.add_argument("--replay-tasks", default=None, help="再生するタスク（カンマ区切り、例 T1,T2,T3）。省略で再生データの全タスク")
    ap.add_argument("--best-on", choices=["main", "all"], default="main", help="best の選択基準: 主データ valid の loss / 主+再生の全体 loss")
    args, explicit = parse_args_with_explicit(ap, argv)

    if not args.count_only and not args.audit_prefix and (args.epochs is None) == (args.iters is None):
        ap.error("--epochs か --iters のどちらか一方を指定する")
    if args.replay_data is None:
        if args.replay_tasks or args.replay_per_task:
            ap.error("--replay-tasks / --replay-per-task は --replay-data と一緒に指定する")
    elif args.replay_per_task <= 0:
        ap.error("--replay-data には --replay-per-task N（1 以上）が必要")
    if args.gc_check and not args.gradient_checkpointing:
        ap.error("--gc-check は --gradient-checkpointing と一緒に指定する")
    if args.train_fraction and not (0.0 < args.train_fraction < 1.0):
        ap.error("--train-fraction は 0 < F < 1")
    if args.train_fraction and args.train_per_task:
        ap.error("--train-fraction と --train-per-task / --train-per-category は同時に指定できない")
    if args.preflight_only:
        args.preflight_longest = True
    if args.save_every < 0 or args.keep_last < 0:
        ap.error("--save-every / --keep-last は 0 以上")
    if args.max_tokens_per_batch < 0 or args.max_seq_len < 0 or args.eval_max_tokens < 0:
        ap.error("--max-tokens-per-batch / --max-seq-len / --eval-max-tokens は 0 以上")

    from transformers import AutoTokenizer

    args.out.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token

    train_rows = read_jsonl(args.data / "train.jsonl")
    valid_rows = read_jsonl(args.data / "valid.jsonl")
    if args.tasks:
        keep = set(args.tasks.split(","))
        train_rows = [r for r in train_rows if row_group(r) in keep]
        valid_rows = [r for r in valid_rows if row_group(r) in keep]
    if args.audit_prefix > 0:
        sub = stratified_pick(train_rows, args.audit_prefix, args.seed)
        rep = audit_rows(tok, sub, max_seq_len=args.max_seq_len, over_length=args.over_length)
        (args.out / "prefix_audit.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for cat, c in rep["by_category"].items():
            print(f"[audit] {cat}: episodes {c['episodes']} turns {c['turns']} prefix_ok {c.get('prefix_ok', 0)}/{c.get('prefix_ok_checked', 0)} "
                  f"target_ok {c.get('target_ok', 0)}/{c.get('target_ok_checked', 0)} whole_render_ok {c.get('whole_ok', 0)}/{c.get('whole_ok_checked', 0)} "
                  f"joint_ok {c.get('joint_ok', 0)}/{c.get('joint_ok_checked', 0)}", flush=True)
        print(f"[audit] {'PASS' if rep['ok'] else 'FAIL'} ({rep['n_failures']} failures) -> {args.out / 'prefix_audit.json'}", flush=True)
        return 0 if rep["ok"] else 1
    if args.train_ids:
        keep_ids = set(json.loads(args.train_ids.read_text()))
        train_rows = [r for r in train_rows if r["id"] in keep_ids]
    elif args.train_per_task > 0:
        train_rows = stratified_pick(train_rows, args.train_per_task, args.seed)
    elif args.train_fraction > 0:
        train_rows = stratified_fraction(train_rows, args.train_fraction, args.seed)
    if args.train_limit > 0:
        train_rows = train_rows[: args.train_limit]
    (args.out / "train_ids.json").write_text(json.dumps([r["id"] for r in train_rows]) + "\n", encoding="utf-8")
    valid_sub = stratified_pick(valid_rows, args.valid_per_task, args.seed) if args.valid_per_task > 0 else valid_rows
    (args.out / "valid_subset_ids.json").write_text(json.dumps([r["id"] for r in valid_sub]) + "\n", encoding="utf-8")

    replay_train_rows: list[dict[str, Any]] = []
    replay_valid_rows: list[dict[str, Any]] = []
    replay_info: dict[str, Any] = {}
    if args.replay_data is not None:
        main_tasks = {row_group(r) for r in train_rows} | {row_group(r) for r in valid_sub}
        replay_train_rows, replay_valid_rows, replay_info = load_replay(args, main_tasks, {r["id"] for r in train_rows})
        (args.out / "replay_train_ids.json").write_text(json.dumps([r["id"] for r in replay_train_rows]) + "\n", encoding="utf-8")
        (args.out / "replay_valid_ids.json").write_text(json.dumps([r["id"] for r in replay_valid_rows]) + "\n", encoding="utf-8")
        for t, n in replay_info["short_tasks"].items():
            print(f"[warn] 再生タスク {t}: 利用可能 {n} 件 < --replay-per-task {args.replay_per_task}（全件使う）", flush=True)
        if replay_info["renamed"]:
            print(f"[info] 主データとタスク名が衝突するため再生タスクを改名: {replay_info['renamed']}", flush=True)

    enc_kw = {"max_seq_len": args.max_seq_len, "over_length": args.over_length}
    enc_stats: collections.Counter = collections.Counter()
    valid_stats: collections.Counter = collections.Counter()
    t_enc = time.time()
    train = encode_rows(tok, train_rows, "main", stats=enc_stats, **enc_kw) + encode_rows(tok, replay_train_rows, "replay", stats=enc_stats, **enc_kw)
    valid = encode_rows(tok, valid_sub, "main", stats=valid_stats, **enc_kw) + encode_rows(tok, replay_valid_rows, "replay", stats=valid_stats, **enc_kw)
    n_ep_by_task: dict[str, int] = collections.Counter(row_group(r) for r in train_rows + replay_train_rows)
    tok_by_task: dict[str, int] = collections.defaultdict(int)
    tgt_by_task: dict[str, int] = collections.defaultdict(int)
    n_by_task: dict[str, int] = collections.defaultdict(int)
    n_by_src: dict[str, int] = collections.defaultdict(int)
    for e in train:
        tok_by_task[e["task"]] += len(e["ids"])
        tgt_by_task[e["task"]] += n_target(e)
        n_by_task[e["task"]] += 1
        n_by_src[e["src"]] += 1
    total_tokens = sum(tok_by_task.values())
    target_tokens = sum(tgt_by_task.values())
    mix = f" (main {n_by_src['main']} + replay {n_by_src['replay']})" if replay_train_rows else ""
    print(f"train {len(train_rows) + len(replay_train_rows)} episodes -> {len(train)} samples{mix}, {total_tokens:,} tokens, {target_tokens:,} loss tokens "
          f"(Qwen3.5 tokenizer, encode {time.time() - t_enc:.0f}s)", flush=True)
    for t in sorted(n_by_task):
        print(f"  {t}: {n_ep_by_task[t]} episodes / {n_by_task[t]} samples / {tok_by_task[t]:,} tok / {tgt_by_task[t]:,} loss tok", flush=True)
    enc_report = {k: int(v) for k, v in sorted(enc_stats.items())}
    print(f"encode stats (train): {enc_report}", flush=True)
    if valid_stats.get("skipped_samples") or valid_stats.get("over_length_samples"):
        print(f"encode stats (valid): {dict(valid_stats)}", flush=True)
    print(f"valid subset {len(valid)} samples: {dict(collections.Counter(e['task'] for e in valid))}", flush=True)
    if args.count_only:
        (args.out / "token_count.json").write_text(json.dumps(
            {"train_episodes": len(train_rows) + len(replay_train_rows), "train_rows": len(train), "train_samples": len(train), "train_tokens": total_tokens,
             "train_loss_tokens": target_tokens, "by_task_episodes": dict(n_ep_by_task), "by_task_rows": n_by_task, "by_task_samples": n_by_task,
             "by_task_tokens": tok_by_task, "by_task_loss_tokens": tgt_by_task, "by_source_rows": dict(n_by_src), "encode_stats": enc_report,
             "valid_rows_by_task": dict(collections.Counter(e["task"] for e in valid)), "valid_encode_stats": dict(valid_stats)}, indent=2) + "\n")
        lens_ = [len(e["ids"]) for e in train]
        n_micro = len(epoch_micro_batches(len(train), lens_, args.batch_size, max(args.batch_size, args.bucket_chunk), random.Random(args.seed), args.max_tokens_per_batch))
        ep = args.epochs if args.epochs is not None else 1.0
        hours = total_tokens * ep / args.est_tok_per_s / 3600
        est = {"epochs": ep, "tokens_per_epoch": total_tokens, "loss_tokens_per_epoch": target_tokens, "tok_per_s_assumed": args.est_tok_per_s,
               "train_hours_excl_eval": round(hours, 4), "micro_batches_per_epoch": n_micro,
               "steps_per_epoch": round(n_micro / args.grad_accum, 1) if args.max_tokens_per_batch > 0 else round(len(train) / (args.batch_size * args.grad_accum), 1),
               "max_tokens_per_batch": args.max_tokens_per_batch, "batch_size": args.batch_size, "grad_accum": args.grad_accum,
               "max_sample_tokens": max(lens_) if lens_ else 0}
        (args.out / "token_count.json").write_text(json.dumps({**json.loads((args.out / "token_count.json").read_text()), "estimate": est}, indent=2) + "\n")
        print(f"[estimate] {total_tokens:,} tok x {ep:g} epoch / {args.est_tok_per_s:g} tok/s = {hours:.1f} h (train only, eval excluded); "
              f"{n_micro} micro-batches/epoch -> {est['steps_per_epoch']} steps/epoch (batch {args.batch_size}, accum {args.grad_accum}, "
              f"max tokens/batch {args.max_tokens_per_batch or '-'}); longest sample {est['max_sample_tokens']} tok", flush=True)
        return 0

    import torch
    import transformers
    from transformers import AutoModelForCausalLM

    resume_dir = resolve_resume(args.resume_from, args.out) if args.resume_from else None
    if resume_dir is not None:
        if args.init_adapter is not None:
            print(f"[info] --resume-from があるので --init-adapter {args.init_adapter} は使わない（{resume_dir}/adapter から再開）", flush=True)
        args.init_adapter = resume_dir / "adapter"
        print(f"[resume] {resume_dir}", flush=True)
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--device cuda だが CUDA が使えない（cpu で試すなら --device cpu）")
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    per_step = args.batch_size * args.grad_accum
    lengths = [len(e["ids"]) for e in train]
    chunk = max(args.batch_size, args.bucket_chunk)
    n_micro_epoch = len(epoch_micro_batches(len(train), lengths, args.batch_size, chunk, random.Random(args.seed), args.max_tokens_per_batch))
    if args.max_tokens_per_batch > 0:  # 1 step = grad_accum 個の micro-batch（件数は可変）。epoch あたりの step 数は micro-batch 数から
        per_step = max(1, round(len(train) / max(1, n_micro_epoch) * args.grad_accum))  # 平均の 1 step あたりサンプル数（記録用）
        steps = args.iters if args.iters is not None else max(1, round(args.epochs * n_micro_epoch / args.grad_accum))
    else:
        steps = args.iters if args.iters is not None else max(1, round(args.epochs * len(train) / per_step))
    eval_max_tokens = args.eval_max_tokens or args.max_tokens_per_batch
    warmup = int(round(args.warmup_ratio * steps))

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=getattr(torch, DTYPES[args.dtype])).to(device)
    n_gc = enable_gradient_checkpointing(model) if args.gradient_checkpointing else 0
    if args.gradient_checkpointing:
        print(f"gradient checkpointing: ON (non-reentrant, {n_gc} modules), use_cache=False", flush=True)
    pm, adapter_info, adapter_warns = build_peft_model(model, args, explicit)
    layers = adapter_info["layers_resolved"]
    if adapter_info["mode"] == "init_adapter":
        print(f"init adapter: {adapter_info['path']} (r={adapter_info['r']}, alpha={adapter_info['lora_alpha']}, "
              f"dropout={adapter_info['lora_dropout']}, base={adapter_info['adapter_base_model']})", flush=True)
    for w in adapter_warns:
        print(f"[warn] {w}", flush=True)
    pm.print_trainable_parameters()
    pm.train()
    base = pm.base_model.model
    params = [p for p in pm.parameters() if p.requires_grad]
    n_trainable = sum(p.numel() for p in params)
    n_lora_modules = sum(1 for n, _ in pm.named_modules() if n.endswith("lora_A"))
    print(f"LoRA modules: {n_lora_modules}, trainable params: {n_trainable:,}, steps: {steps}, warmup: {warmup}", flush=True)

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
        "n_train_main": n_by_src["main"],
        "n_train_replay": n_by_src["replay"],
        "n_valid_subset": len(valid),
        "train_tokens": total_tokens,
        "train_rows_by_task": n_by_task,
        "steps": steps,
        "warmup_steps": warmup,
        "samples_per_step": per_step,
        "micro_batches_per_epoch": n_micro_epoch,
        "train_episodes": len(train_rows) + len(replay_train_rows),
        "train_loss_tokens": target_tokens,
        "encode_stats": enc_report,
        "eval_max_tokens": eval_max_tokens,
        "layers_resolved": layers,
        "n_lora_modules": n_lora_modules,
        "trainable_params": n_trainable,
        "adapter": adapter_info,
        "adapter_warnings": adapter_warns,
        "replay": replay_info,
        "gradient_checkpointing_modules": n_gc,
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__},
    }
    try:
        import peft

        config["versions"]["peft"] = peft.__version__
    except Exception:
        pass
    config["resume_from"] = str(resume_dir) if resume_dir else None
    cfg_name = "config.json" if resume_dir is None else f"config_resume_{resume_dir.name}.json"
    (args.out / cfg_name).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_f = (args.out / "train_log.jsonl").open("w" if resume_dir is None else "a", encoding="utf-8")

    def log(rec: dict[str, Any]) -> None:
        log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log_f.flush()

    rng = random.Random(args.seed)
    plan = build_steps(len(train), lengths, steps, per_step, args.batch_size, args.grad_accum, chunk, rng, args.max_tokens_per_batch)

    if args.gc_check:
        first = [train[i] for i in plan[0][0]]
        n_tok0 = sum(n_target(e) for e in first)
        chk = gc_selfcheck(pm, base, tok, first, device, n_tok0, params, args.gc_check_tol)
        log({"type": "gc_check", **chk})
        print(f"[gc-check] OK loss off/on {chk['loss_off']:.6f}/{chk['loss_on']:.6f} grad rel L2 {chk['grad_rel_l2']:.2e} "
              f"max abs {chk['grad_max_abs_diff']:.2e}", flush=True)

    if args.preflight_longest:
        try:
            pre = preflight_longest(base, tok, train, valid, plan, device, params, args.eval_batch_size, eval_max_tokens)
        except torch.cuda.OutOfMemoryError as exc:  # type: ignore[attr-defined]
            print(f"[preflight] CUDA OOM: {str(exc).splitlines()[0]}\n  --max-tokens-per-batch / --eval-max-tokens を下げる（または --gradient-checkpointing）", flush=True)
            return 3
        for r in pre:
            print(f"[preflight] {r['case']}: {r['batch']} x <= {r['max_len']} tok (padded {r['padded_tokens']}, loss tok {r['loss_tokens']}) "
                  f"{r['sec']}s peak {r['peak_gib'] if r['peak_gib'] is not None else 'n/a (cpu)'} GiB", flush=True)
        log({"type": "preflight", "cases": pre})
        if args.preflight_only:
            return 0

    mem = PeakMem(device)
    mem.end_other_segment()  # モデル構築までのピークを全体側に計上してリセット
    best_loss: float | None = None
    best_step = None
    t0 = time.time()
    tok_count = 0
    run_loss, run_n = 0.0, 0
    win_sec, win_tok, win_steps = 0.0, 0, 0
    eval_total = 0.0
    start_step = 0
    fingerprint = plan_fingerprint(plan, train)
    if resume_dir is not None:
        st = torch.load(resume_dir / "trainer_state.pt", map_location="cpu", weights_only=False)
        if st["plan_fingerprint"] != fingerprint or st["steps"] != steps:
            raise RuntimeError("--resume-from: データ順 / 総 step 数が checkpoint と一致しない（同じ --data・サブサンプル・--seed・バッチ設定・--epochs/--iters で再開する）")
        opt.load_state_dict(st["optimizer"])
        random.setstate(st["rng"]["python"])
        torch.set_rng_state(st["rng"]["torch"])
        if st["rng"]["cuda"] is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(st["rng"]["cuda"])
        start_step = st["step"]
        best_loss, best_step = st["best_loss"], st["best_step"]
        tok_count, eval_total = st["tok_count"], st["eval_total"]
        t0 = time.time() - st["wall_sec"]
        log({"type": "resume", "from": str(resume_dir), "step": start_step})
        print(f"[resume] step {start_step}/{steps}, best {best_loss} @ {best_step}", flush=True)

    def do_eval(step: int, allow_best: bool) -> None:
        nonlocal best_loss, best_step, eval_total
        if step > 0:
            mem.end_train_segment()
        te = time.time()
        res = evaluate(pm, base, tok, valid, args.eval_batch_size, device, best_on=args.best_on, max_tokens=eval_max_tokens)
        eval_total += time.time() - te
        mem.end_other_segment()
        is_best = allow_best and (best_loss is None or res["valid_loss"] < best_loss)
        if is_best:
            best_loss, best_step = res["valid_loss"], step
            pm.save_pretrained(str(args.out / "best"))
        log({"type": "eval", "step": step, **res, "best_on": args.best_on, "is_best": is_best, "eval_sec": round(time.time() - te, 1)})
        per_task = {k: v for k, v in res["loss_by_group"].items() if ":" not in k}
        extra = ""
        if "valid_loss_replay" in res:
            extra = f" main {res['valid_loss_main']:.4f} replay {res['valid_loss_replay']:.4f} (best-on {args.best_on})"
        print(f"[eval] step {step} valid_loss {res['valid_loss']:.4f}{extra} by_task {per_task}" + (" *best*" if is_best else ""), flush=True)

    if resume_dir is None:
        do_eval(0, allow_best=False)
    for step in range(start_step, steps):
        t_step = time.perf_counter()
        factor = lr_factor(step)
        for g in opt.param_groups:
            g["lr"] = args.lr * factor
        micro = plan[step]
        n_tok_step = sum(n_target(train[i]) for mb in micro for i in mb)
        step_loss = 0.0
        step_in_tok = 0
        for mb in micro:
            l, nt = micro_step(base, tok, [train[i] for i in mb], device, n_tok_step)
            step_loss += l
            step_in_tok += nt
        tok_count += step_in_tok
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        win_sec += time.perf_counter() - t_step
        win_tok += step_in_tok
        win_steps += 1
        run_loss += step_loss
        run_n += 1
        done = step + 1
        if done % args.log_every == 0 or done == steps:
            el = time.time() - t0 - eval_total
            peak_all, peak_train = mem.snapshot()
            rec = {
                "type": "train", "step": done, "loss": run_loss / run_n, "lr": args.lr * factor,
                "tokens_per_s": round(tok_count / el, 1), "elapsed_sec": round(el, 1),
                "sec_per_step": round(win_sec / win_steps, 3), "tokens_per_s_window": round(win_tok / max(win_sec, 1e-9), 1),
                "peak_gib": round(peak_all, 2), "peak_train_gib": round(peak_train, 2),
            }
            log(rec)
            print(f"[train] step {done}/{steps} loss {rec['loss']:.4f} lr {rec['lr']:.2e} "
                  f"{rec['tokens_per_s']} tok/s ({rec['tokens_per_s_window']} window) {rec['sec_per_step']}s/step "
                  f"peak {rec['peak_gib']}GiB (train {rec['peak_train_gib']}GiB) elapsed {rec['elapsed_sec']}s", flush=True)
            run_loss, run_n = 0.0, 0
            win_sec, win_tok, win_steps = 0.0, 0, 0
        if (args.eval_every > 0 and done % args.eval_every == 0) or done == steps:
            do_eval(done, allow_best=True)
        if args.save_every > 0 and done % args.save_every == 0 and done < steps:
            ck = save_checkpoint(args.out, done, pm, opt, {
                "steps": steps, "plan_fingerprint": fingerprint, "best_loss": best_loss, "best_step": best_step, "tok_count": tok_count,
                "eval_total": eval_total, "wall_sec": time.time() - t0}, args.keep_last)
            log({"type": "checkpoint", "step": done, "path": str(ck)})
            print(f"[checkpoint] step {done} -> {ck}", flush=True)

    pm.save_pretrained(str(args.out / "final"))
    peak_all, peak_train = mem.snapshot()
    train_sec_excl = time.time() - t0 - eval_total
    summary = {
        "best_step": best_step,
        "best_valid_loss": best_loss,
        "best_on": args.best_on,
        "steps": steps,
        "train_sec": round(time.time() - t0, 1),
        "train_sec_excl_eval": round(train_sec_excl, 1),
        "sec_per_step_mean": round(train_sec_excl / max(steps, 1), 3),
        "tokens_per_s_mean": round(tok_count / max(train_sec_excl, 1e-9), 1),
        "peak_gib": round(peak_all, 2),
        "peak_train_gib": round(peak_train, 2),
        "gradient_checkpointing": bool(args.gradient_checkpointing),
        "train_tokens": total_tokens,
    }
    log({"type": "summary", **summary})
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("[done]", json.dumps(summary), flush=True)
    log_f.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
