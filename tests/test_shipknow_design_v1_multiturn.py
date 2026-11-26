"""design-v1（複数ターン会話）向けの学習・推論の拡張のテスト（CPU・ネットワーク不要）。

  * tools/navspec_qwen35_sft_train.py: 複数ターンの損失対象（train_turns）、推論 prefix とのトークン列一致、全体レンダリングと食い違うターンの別サンプル化、
    category 層化サブサンプル、--count-only、--max-seq-len、トークン数ベースのバッチ化、定期 checkpoint と再開、--preflight-longest
  * tools/shipknow_infer_hf.py: --multi-turn-eval のプロンプト構成（teacher-forcing）と出力行

tokenizer は ChatML 風テンプレート付きの極小 byte-level BPE をその場で作る。テンプレートは Qwen3.5 と同じ規則（「最後の user より後の assistant にだけ
``<think>\\n\\n</think>\\n\\n`` を付ける」）と、全 assistant に付ける形の 2 種類で試す。torch / transformers(Qwen3.5) / peft が無い環境では skip する::

    uv run --no-project --with torch --with transformers --with peft --with pytest pytest tests/test_shipknow_design_v1_multiturn.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")
transformers = pytest.importorskip("transformers")
if not hasattr(transformers, "Qwen3_5TextConfig"):
    pytest.skip("transformers に Qwen3.5 が無い", allow_module_level=True)

from tools import navspec_qwen35_sft_train as T  # noqa: E402
from tools import shipknow_infer_hf as INF  # noqa: E402

LAYER_TYPES = ["linear_attention", "linear_attention", "full_attention", "linear_attention"]
SYSTEM = "You are a naval design assistant."
# Qwen3.5 と同じ規則: think ブロックは「最後の user より後の assistant」にだけ付く（過去ターンには付かない）
QWEN_LIKE = (
    r"{%- set ns = namespace(found=false, lq=0) -%}"
    r"{%- for m in messages[::-1] -%}{%- if not ns.found and m.role == 'user' -%}{%- set ns.found = true -%}{%- set ns.lq = (messages|length - 1) - loop.index0 -%}{%- endif -%}{%- endfor -%}"
    r"{%- for m in messages -%}"
    r"{%- if m.role == 'assistant' -%}"
    r"{%- if loop.index0 > ns.lq -%}{{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' + m.content|trim -}}{%- else -%}{{- '<|im_start|>assistant\n' + m.content|trim -}}{%- endif -%}"
    r"{{- '<|im_end|>\n' -}}"
    r"{%- else -%}{{- '<|im_start|>' + m.role + '\n' + m.content|trim + '<|im_end|>\n' -}}{%- endif -%}"
    r"{%- endfor -%}"
    r"{%- if add_generation_prompt -%}{{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' -}}{%- endif -%}"
)
ALWAYS_THINK = (
    r"{%- for m in messages -%}{%- if m.role == 'assistant' -%}{{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' + m.content|trim + '<|im_end|>\n' -}}"
    r"{%- else -%}{{- '<|im_start|>' + m.role + '\n' + m.content|trim + '<|im_end|>\n' -}}{%- endif -%}{%- endfor -%}"
    r"{%- if add_generation_prompt -%}{{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' -}}{%- endif -%}"
)


# --------------------------------------------------------------------------------------------------
# 合成の design-v1 風データ・tokenizer・モデル
# --------------------------------------------------------------------------------------------------
def _ans(rng: random.Random, status: str = "feasible") -> str:
    d = {"ship_type": "frigate", "speed_kn": rng.randrange(20, 40), "power_hp": rng.randrange(1000, 9000)} if status != "infeasible" else None
    return json.dumps({"status": status, "conflicts": [], "relaxations": [], "design": d}, separators=(",", ":"))


def make_episode(i: int, category: str, rng: random.Random) -> dict:
    u = lambda: json.dumps({"request": f"design {rng.randrange(999)} " + " ".join(f"w{rng.randrange(30)}" for _ in range(rng.randrange(2, 9))), "req": {"year": 1900 + i}}, separators=(",", ":"))  # noqa: E731
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": u()}, {"role": "assistant", "content": _ans(rng)}]
    train = [2]
    if category == "modify":
        msgs += [{"role": "user", "content": u()}, {"role": "assistant", "content": _ans(rng)}]
        train = [2, 4]
    elif category == "repair":
        msgs += [{"role": "user", "content": '{"validator":{"valid":false,"violations":[]},"instruction":"Fix"}'}, {"role": "assistant", "content": _ans(rng)}]
        train = [4]
    elif category == "context":
        msgs += [{"role": "user", "content": u()}, {"role": "assistant", "content": _ans(rng)}, {"role": "user", "content": u()}, {"role": "assistant", "content": _ans(rng)}]
        train = [6]
    return {"id": f"dv1_{category}_{i:03d}", "category": category, "messages": msgs, "metadata": {"train_turns": train, "subtype": f"{category}{i % 2}", "split": "train"}}


def make_episodes(per_cat: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    return [make_episode(i, c, rng) for c in ("single", "modify", "repair", "context") for i in range(per_cat)]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def build_tokenizer(dst: Path, corpus: list[str]):
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast

    tk = Tokenizer(models.BPE())
    tk.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tk.decoder = decoders.ByteLevel()
    specials = ["<|endoftext|>", "<|im_start|>", "<|im_end|>"]
    tk.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=400, special_tokens=specials, initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    fast = PreTrainedTokenizerFast(tokenizer_object=tk, eos_token="<|im_end|>", pad_token="<|endoftext|>", chat_template=QWEN_LIKE)
    fast.save_pretrained(dst)


def build_tiny_model(dst: Path, vocab: int) -> None:
    from transformers import Qwen3_5ForCausalLM, Qwen3_5TextConfig

    cfg = Qwen3_5TextConfig(
        vocab_size=vocab, hidden_size=128, intermediate_size=256, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=2, head_dim=32,
        layer_types=LAYER_TYPES, linear_conv_kernel_dim=4, linear_key_head_dim=16, linear_value_head_dim=16, linear_num_key_heads=2, linear_num_value_heads=4,
        max_position_embeddings=2048, partial_rotary_factor=0.25, tie_word_embeddings=False,
        rope_parameters={"rope_type": "default", "rope_theta": 10000.0, "partial_rotary_factor": 0.25})
    torch.manual_seed(0)
    model = Qwen3_5ForCausalLM(cfg)
    g = torch.Generator().manual_seed(1)
    with torch.no_grad():
        for p in model.parameters():
            p.add_(torch.randn(p.shape, generator=g) * 0.02)
    model.save_pretrained(dst)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("mt")
    train, valid = make_episodes(8, 1), make_episodes(4, 2)
    for r in valid:
        r["id"] = r["id"].replace("dv1_", "dv1v_")
        r["metadata"]["split"] = "valid"
    write_jsonl(root / "data" / "train.jsonl", train)
    write_jsonl(root / "data" / "valid.jsonl", valid)
    write_jsonl(root / "data" / "test.jsonl", valid)
    model_dir = root / "model"
    model_dir.mkdir()
    corpus = [m["content"] for r in train + valid for m in r["messages"]] + ["日本語 test", "<|im_start|>assistant"]
    build_tokenizer(model_dir, corpus)
    vocab = max(len(transformers.AutoTokenizer.from_pretrained(model_dir)), 512)
    build_tiny_model(model_dir, vocab)
    return {"root": root, "data": root / "data", "model": model_dir, "train": train, "valid": valid}


@pytest.fixture()
def tok(env):
    return transformers.AutoTokenizer.from_pretrained(env["model"])


def span_text(tok, sm):
    return [tok.decode(sm["ids"][a:b]) for a, b in T.sample_spans(sm)]


# --------------------------------------------------------------------------------------------------
# 損失対象・prefix 一致
# --------------------------------------------------------------------------------------------------
def test_qwen_like_template_differs_between_middle_and_last_turn(tok):
    """前提の確認: 過去ターンの assistant には think ブロックが付かず、生成 prompt には付く（Qwen3.5 template と同じ性質）。"""
    ep = make_episode(0, "modify", random.Random(0))
    full = tok.apply_chat_template(ep["messages"], tokenize=False, enable_thinking=False)
    gen = tok.apply_chat_template(ep["messages"][:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    assert full.count("<think>") == 1  # 最後の assistant にだけ
    assert gen.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n") and "<think>" not in gen[: -len("<think>\n\n</think>\n\n")]


def test_modify_middle_turn_is_split_and_prefix_matches_inference(tok):
    ep = make_episode(0, "modify", random.Random(0))
    stats = __import__("collections").Counter()
    samples = T.encode_episode(tok, ep, stats=stats)
    # 全体レンダリングは最後のターンだけ推論 prefix と一致する -> 最後のターン = 全体列の 1 span、途中のターン = prefix + 本文だけの別サンプル
    assert len(samples) == 2 and stats["split_turn_samples"] == 1 and stats["turns_whole_render_mismatch"] == 1
    by_turn = {sm["turns"][0]: sm for sm in samples}
    assert set(by_turn) == {2, 4} and all(len(sm["spans"]) == 1 for sm in samples)
    for k, sm in by_turn.items():
        a, b = sm["spans"][0]
        infer_prompt_ids = tok(INF.prompt_text(tok, {"messages": ep["messages"][:k]}), add_special_tokens=False)["input_ids"]  # 推論で使う prompt
        assert sm["ids"][:a] == infer_prompt_ids  # prefix がトークン列で一致
        assert tok.decode(sm["ids"][a : b - 1]) == ep["messages"][k]["content"]  # 本文
        assert sm["ids"][b - 1] == tok.convert_tokens_to_ids("<|im_end|>")
    # 途中のターンのサンプルは、そのターンで終わる（後続の user / assistant は入らない）
    assert by_turn[2]["ids"][-1] == tok.convert_tokens_to_ids("<|im_end|>")
    # 最後のターンは従来形式の full レンダリングの prefix（終端の改行を除く）
    full_ids = T.encode_row(tok, ep)["ids"]
    assert full_ids[: len(by_turn[4]["ids"])] == by_turn[4]["ids"] and len(full_ids) - len(by_turn[4]["ids"]) == 1


def test_always_think_template_packs_turns_into_one_sequence(tok):
    tok.chat_template = ALWAYS_THINK  # 全 assistant に think が付く = どのターンも全体列で推論 prefix と一致する
    ep = make_episode(0, "modify", random.Random(0))
    stats = __import__("collections").Counter()
    samples = T.encode_episode(tok, ep, stats=stats)
    assert len(samples) == 1 and samples[0]["turns"] == [2, 4] and len(samples[0]["spans"]) == 2 and stats["multi_span_samples"] == 1
    sm = samples[0]
    assert span_text(tok, sm) == [ep["messages"][2]["content"] + "<|im_end|>", ep["messages"][4]["content"] + "<|im_end|>"]
    for k, (a, b) in zip(sm["turns"], sm["spans"]):
        assert sm["ids"][:a] == tok(T.render_prefix(tok, ep["messages"], k), add_special_tokens=False)["input_ids"]


def test_loss_mask_only_target_turns_and_counts(env, tok):
    """損失対象 = train_turns の本文 + <|im_end|> だけ。system・user・他ターンは対象外。completion_loss が選ぶ位置・目的 y・CE を全 logits から再計算して照合。"""
    model = transformers.AutoModelForCausalLM.from_pretrained(env["model"], dtype=torch.float32)
    model.eval()
    for template in (QWEN_LIKE, ALWAYS_THINK):
        tok.chat_template = template
        for cat in ("single", "modify", "repair", "context"):
            ep = make_episode(3, cat, random.Random(5))
            samples = T.encode_episode(tok, ep)
            want = sum(len(tok(ep["messages"][k]["content"] + "<|im_end|>", add_special_tokens=False)["input_ids"]) for k in ep["metadata"]["train_turns"])
            assert sum(T.n_target(sm) for sm in samples) == want
            for sm in samples:
                labels = [-100] * len(sm["ids"])
                for a, b in T.sample_spans(sm):
                    labels[a:b] = sm["ids"][a:b]
                assert sum(1 for x in labels if x != -100) == T.n_target(sm)
                assert all(x == -100 for x in labels[: sm["spans"][0][0]])  # system / user / 最初の prefix は対象外
                ids, att = T.make_batch(tok, [sm], torch.device("cpu"))
                with torch.no_grad():
                    ce, y, lens = T.completion_loss(model, ids, att, [sm])
                    logits = model(input_ids=ids, attention_mask=att).logits[0].float()
                assert lens == [T.n_target(sm)] and y.tolist() == [x for x in labels if x != -100]
                pos = [p for p, x in enumerate(labels) if x != -100]
                manual = torch.nn.functional.cross_entropy(logits[[p - 1 for p in pos]], torch.tensor([labels[p] for p in pos]), reduction="none")
                assert torch.allclose(ce, manual, atol=1e-4)
    tok.chat_template = QWEN_LIKE


def test_legacy_rows_without_train_turns_are_unchanged(tok):
    row = {"id": "x", "task": "D", "variant": "v", "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "q w1 w2"}, {"role": "assistant", "content": "a1 a2"}]}
    sm = T.encode_episode(tok, row)
    legacy = T.encode_row(tok, row)
    assert len(sm) == 1 and sm[0]["ids"] == legacy["ids"] and sm[0]["pl"] == legacy["pl"] and sm[0]["spans"] == [(legacy["pl"], len(legacy["ids"]))]
    assert (sm[0]["task"], sm[0]["variant"]) == ("D", "v") and T.n_target(legacy) == len(legacy["ids"]) - legacy["pl"]
    # train_turns の無い複数ターンの行は最後の assistant（終端の改行まで）
    ep = make_episode(0, "modify", random.Random(0))
    ep["metadata"].pop("train_turns")
    sm2 = T.encode_episode(tok, ep)
    assert len(sm2) == 1 and sm2[0]["ids"] == T.encode_row(tok, ep)["ids"]


def test_invalid_train_turns_rejected(tok):
    ep = make_episode(0, "single", random.Random(0))
    ep["metadata"]["train_turns"] = [1]  # user ターン
    with pytest.raises(ValueError):
        T.encode_episode(tok, ep)


def test_audit_reports_match_and_mismatch(tok):
    rows = make_episodes(3, 9)
    rep = T.audit_rows(tok, rows)
    assert rep["ok"] and rep["n_failures"] == 0
    mod = rep["by_category"]["modify"]
    assert mod["prefix_ok"] == mod["prefix_ok_checked"] == mod["turns"] and mod["target_ok"] == mod["turns"] and mod["joint_ok"] == mod["turns"]
    assert mod["turn_2_whole_ok"] == 0 and mod["turn_4_whole_ok"] == mod["turn_4_n"] == 3 and mod["whole_mismatch"] == 3  # 途中のターンだけ全体レンダリングと食い違う
    assert "<think>" in mod["whole_mismatch_examples"][0]["infer_tail"] and "<think>" not in mod["whole_mismatch_examples"][0]["whole_tail"]
    for cat in ("single", "repair", "context"):
        assert rep["by_category"][cat].get("whole_mismatch", 0) == 0


# --------------------------------------------------------------------------------------------------
# 層化サブサンプル・長さ・バッチ化
# --------------------------------------------------------------------------------------------------
def test_stratified_by_category_and_fraction():
    rows = make_episodes(40, 3)
    pick = T.stratified_pick(rows, 10, 7)
    assert {c: sum(1 for r in pick if r["category"] == c) for c in ("single", "modify", "repair", "context")} == {c: 10 for c in ("single", "modify", "repair", "context")}
    assert [r["id"] for r in pick] == [r["id"] for r in T.stratified_pick(rows, 10, 7)] and [r["id"] for r in pick] != [r["id"] for r in T.stratified_pick(rows, 10, 8)]
    # subtype（variant の代わり）の比率が保たれる
    for c in ("single", "modify"):
        sub = [r["metadata"]["subtype"] for r in pick if r["category"] == c]
        assert abs(sub.count(c + "0") - sub.count(c + "1")) <= 1
    fr = T.stratified_fraction(rows, 0.25, 1)
    assert {c: sum(1 for r in fr if r["category"] == c) for c in ("single", "modify")} == {"single": 10, "modify": 10}
    assert T.row_group({"task": "T1"}) == "T1" and T.row_group({"category": "repair", "task": "x"}) == "repair" and T.row_variant({"variant": "a"}) == "a"


def test_pack_by_tokens_budget_and_batch_cap():
    lengths = [random.Random(1).randrange(50, 900) for _ in range(1)] + [100, 300, 300, 700, 700, 700, 1500, 40]
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    for bs, budget in ((4, 1000), (2, 5000), (8, 2000)):
        groups = T.pack_by_tokens(order, lengths, bs, budget)
        assert sorted(i for g in groups for i in g) == sorted(order)
        for g in groups:
            assert len(g) <= bs and (len(g) == 1 or len(g) * max(lengths[i] for i in g) <= budget)
    assert [len(g) for g in T.pack_by_tokens(order, lengths, 4, 0)] == [4, 4, 1]  # budget 0 は従来の固定件数
    plan = T.build_steps(len(lengths), lengths, 3, 8, 4, 2, 64, random.Random(0), max_tokens=1000)
    assert len(plan) == 3 and all(len(s) == 2 for s in plan)
    assert plan == T.build_steps(len(lengths), lengths, 3, 8, 4, 2, 64, random.Random(0), max_tokens=1000)


def test_max_seq_len_skip_and_drop_history(tok):
    ep = make_episode(0, "context", random.Random(4))
    full = T.encode_episode(tok, ep)[0]
    limit = len(full["ids"]) - 5
    st = __import__("collections").Counter()
    assert T.encode_episode(tok, ep, max_seq_len=limit, over_length="skip", stats=st) == [] and st["skipped_samples"] == 1 and st["skipped_episodes"] == 1
    st = __import__("collections").Counter()
    out = T.encode_episode(tok, ep, max_seq_len=limit, over_length="drop-history", stats=st)
    assert len(out) == 1 and len(out[0]["ids"]) <= limit and st["history_dropped_samples"] == 1 and st["history_dropped_pairs"] >= 1
    a, b = out[0]["spans"][0]
    assert tok.decode(out[0]["ids"][a : b - 1]) == ep["messages"][6]["content"]  # 学習対象の本文は同じ
    assert out[0]["ids"][:a] == tok(T.render_prefix(tok, [ep["messages"][0]] + ep["messages"][1 + 2 * st["history_dropped_pairs"] : 6], 99), add_special_tokens=False)["input_ids"]  # 先頭の過去ターンだけ落ちる
    # repair の feedback が先頭になる落とし方はしない
    rep = make_episode(0, "repair", random.Random(4))
    n = len(T.encode_episode(tok, rep)[0]["ids"])
    st = __import__("collections").Counter()
    assert T.encode_episode(tok, rep, max_seq_len=n - 5, over_length="drop-history", stats=st) == [] and st["skipped_samples"] == 1
    # 余裕があれば何もしない
    st = __import__("collections").Counter()
    assert len(T.encode_episode(tok, ep, max_seq_len=10_000, stats=st)) == 1 and not st["over_length_samples"]


# --------------------------------------------------------------------------------------------------
# main(): --count-only / --audit-prefix / サブサンプル / checkpoint / 再開 / preflight
# --------------------------------------------------------------------------------------------------
def run_main(env_, out, *extra, data=None):
    return T.main(["--model", str(env_["model"]), "--data", str(data or env_["data"]), "--out", str(out), "--device", "cpu", "--dtype", "float32",
                   "--rank", "4", "--alpha", "8", "--batch-size", "2", "--grad-accum", "2", "--lr", "1e-3", "--warmup-ratio", "0", "--log-every", "1",
                   "--eval-batch-size", "8", *extra])


def test_count_only_by_category(env, tmp_path, tok):
    assert run_main(env, tmp_path / "c", "--count-only", "--valid-per-task", "2", "--max-tokens-per-batch", "600", "--epochs", "1") == 0
    tc = json.loads((tmp_path / "c" / "token_count.json").read_text())
    assert tc["train_episodes"] == 32 and tc["by_task_episodes"] == {c: 8 for c in ("single", "modify", "repair", "context")}
    assert tc["by_task_samples"]["modify"] == 16 and tc["by_task_samples"]["single"] == 8  # modify の途中ターンは別サンプル
    want = {}
    for r in env["train"]:
        want[r["category"]] = want.get(r["category"], 0) + sum(len(tok(r["messages"][k]["content"] + "<|im_end|>", add_special_tokens=False)["input_ids"]) for k in r["metadata"]["train_turns"])
    assert tc["by_task_loss_tokens"] == want and tc["train_loss_tokens"] == sum(want.values())
    assert tc["train_tokens"] == sum(tc["by_task_tokens"].values()) and tc["encode_stats"]["split_turn_samples"] == 8
    assert tc["valid_rows_by_task"]["modify"] == 4  # valid 2 件 x 2 サンプル
    assert tc["estimate"]["train_hours_excl_eval"] > 0 and tc["estimate"]["max_tokens_per_batch"] == 600


def test_train_subsample_options_record_ids(env, tmp_path):
    run_main(env, tmp_path / "a", "--count-only", "--train-per-category", "3", "--seed", "5")
    run_main(env, tmp_path / "b", "--count-only", "--train-fraction", "0.5", "--seed", "5")
    run_main(env, tmp_path / "a2", "--count-only", "--train-per-task", "3", "--seed", "5")
    ids_a = json.loads((tmp_path / "a" / "train_ids.json").read_text())
    ids_b = json.loads((tmp_path / "b" / "train_ids.json").read_text())
    assert len(ids_a) == 12 and len(ids_b) == 16 and ids_a == json.loads((tmp_path / "a2" / "train_ids.json").read_text())
    assert {i.split("_")[1] for i in ids_a} == {"single", "modify", "repair", "context"}
    with pytest.raises(SystemExit):
        run_main(env, tmp_path / "x", "--count-only", "--train-fraction", "0.5", "--train-per-task", "3")


def test_audit_prefix_cli(env, tmp_path):
    assert run_main(env, tmp_path / "au", "--audit-prefix", "3") == 0
    rep = json.loads((tmp_path / "au" / "prefix_audit.json").read_text())
    assert rep["ok"] and rep["by_category"]["modify"]["turn_2_whole_ok"] == 0


def test_valid_loss_grouped_by_category_with_multi_turn_training(env, tmp_path):
    out = tmp_path / "t"
    assert run_main(env, out, "--iters", "2", "--eval-every", "2", "--valid-per-task", "2", "--max-tokens-per-batch", "700", "--batch-size", "4", "--gradient-checkpointing") == 0
    evals = [json.loads(l) for l in (out / "train_log.jsonl").read_text().splitlines() if '"type": "eval"' in l]
    assert evals and {k for k in evals[-1]["loss_by_group"] if ":" not in k} == {"single", "modify", "repair", "context"}
    assert any(k.startswith("modify:modify") for k in evals[-1]["loss_by_group"])  # (category:subtype)
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["max_tokens_per_batch"] == 700 and cfg["train_loss_tokens"] > 0


def _weights(path):
    from safetensors.torch import load_file

    return load_file(str(path / "adapter_model.safetensors"))


def test_checkpoint_resume_is_exact(env, tmp_path):
    out = tmp_path / "r"
    common = ("--iters", "6", "--eval-every", "3", "--valid-per-task", "2", "--max-tokens-per-batch", "700", "--batch-size", "4", "--dropout", "0.1", "--seed", "3")
    assert run_main(env, out, *common, "--save-every", "2", "--keep-last", "1") == 0
    cks = sorted(p.name for p in out.glob("ckpt_step*"))
    assert cks == ["ckpt_step00004"]  # 2 と 4 を保存し keep-last 1 で 4 だけ残る（最終 step の 6 は final に任せる）
    assert not list(out.glob(".tmp_*"))
    assert (out / "ckpt_step00004" / "adapter" / "adapter_config.json").is_file() and (out / "ckpt_step00004" / "trainer_state.pt").is_file()
    ref = _weights(out / "final")
    import shutil

    shutil.copytree(out / "final", tmp_path / "final_ref")
    assert run_main(env, out, *common, "--resume-from", "latest") == 0  # step 4 から 6 まで
    got = _weights(out / "final")
    assert set(ref) == set(got) and all(torch.equal(ref[k], got[k]) for k in ref)
    log = [json.loads(l) for l in (out / "train_log.jsonl").read_text().splitlines()]
    assert any(r["type"] == "resume" and r["step"] == 4 for r in log)
    # データ順が違えば拒否される
    with pytest.raises(RuntimeError):
        run_main(env, out, *common, "--resume-from", str(out / "ckpt_step00004"), "--seed", "4")
    with pytest.raises(FileNotFoundError):
        run_main(env, tmp_path / "empty", *common, "--resume-from", "latest")


def test_preflight_longest(env, tmp_path, capsys):
    assert run_main(env, tmp_path / "p", "--iters", "2", "--preflight-only", "--max-tokens-per-batch", "700", "--batch-size", "4", "--valid-per-task", "2") == 0
    cap = capsys.readouterr().out
    assert "[preflight] longest_sample" in cap and "[preflight] largest_padded_batch" in cap and "[preflight] largest_eval_batch" in cap
    assert not (tmp_path / "p" / "final").exists()
    assert run_main(env, tmp_path / "p2", "--iters", "1", "--preflight-longest", "--valid-per-task", "2", "--eval-every", "1") == 0
    assert (tmp_path / "p2" / "final").is_dir()


# --------------------------------------------------------------------------------------------------
# 推論: --multi-turn-eval
# --------------------------------------------------------------------------------------------------
def test_multi_turn_eval_prompt_is_teacher_forced_and_matches_training_prefix(env, tok):
    rows = make_episodes(3, 11)
    items = INF.select_multi_turn_items(rows)
    assert len(items) == len(rows)
    by_id = {r["id"]: r for r in rows}
    for it in items:
        ep = by_id[it["id"]]
        k = max(ep["metadata"]["train_turns"])
        assert it["turn_index"] == k and it["messages"] == ep["messages"][:k] and it["messages"][-1]["role"] == "user"
        prompt = INF.prompt_text(tok, {"messages": it["messages"]})
        assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
        # 正解ターンより前の assistant は gold が文脈に入り、正解ターン自身は入らない
        for m in ep["messages"][2:k:2]:
            assert m["content"] in prompt
        assert ep["messages"][k]["content"] not in prompt
        # 学習側の prefix と同一
        sm = [s for s in T.encode_episode(tok, ep) if k in s["turns"]][0]
        a = sm["spans"][sm["turns"].index(k)][0]
        assert tok(prompt, add_special_tokens=False)["input_ids"] == sm["ids"][:a]
    # modify は最後の学習対象ターン（4）、1 ターン目の gold assistant が文脈に入る
    mod = next(i for i in items if i["category"] == "modify")
    assert mod["turn_index"] == 4 and [m["role"] for m in mod["messages"]] == ["system", "user", "assistant", "user"]


def test_select_multi_turn_items_filters(env):
    rows = make_episodes(20, 12)
    assert {i["category"] for i in INF.select_multi_turn_items(rows, categories={"repair", "context"})} == {"repair", "context"}
    per = INF.select_multi_turn_items(rows, per_category=5, seed=1)
    assert len(per) == 20 and [i["id"] for i in per] == [i["id"] for i in INF.select_multi_turn_items(rows, per_category=5, seed=1)]
    assert len(INF.select_multi_turn_items(rows, limit=3)) == 12
    ids = {rows[0]["id"], rows[41]["id"]}
    assert {i["id"] for i in INF.select_multi_turn_items(rows, ids=ids)} == ids
    no_tt = dict(rows[25])
    no_tt["metadata"] = {}
    assert INF.last_train_turn(no_tt) == len(no_tt["messages"]) - 1  # train_turns が無ければ最後の assistant


def test_multi_turn_eval_cli_writes_prediction_lines(env, tmp_path, monkeypatch):
    out = tmp_path / "preds.jsonl"
    argv = ["shipknow_infer_hf.py", "--model", str(env["model"]), "--test", str(env["data"] / "test.jsonl"), "--out", str(out), "--multi-turn-eval", "--device", "cpu",
            "--dtype", "float32", "--batch-size", "4", "--max-new-tokens", "6", "--per-category", "2", "--categories", "modify,context"]
    monkeypatch.setattr(sys, "argv", argv)
    assert INF.main() == 0
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == 4 and {r["category"] for r in rows} == {"modify", "context"}
    assert all(set(r) == {"id", "category", "turn_index", "text", "seed"} and r["seed"] is None and isinstance(r["text"], str) for r in rows)
    assert {r["turn_index"] for r in rows if r["category"] == "modify"} == {4} and {r["turn_index"] for r in rows if r["category"] == "context"} == {6}
    meta = json.loads((tmp_path / "preds_meta.json").read_text())
    assert meta["mode"] == "multi_turn_eval" and meta["n"] == 4
    monkeypatch.setattr(sys, "argv", argv + ["--sample-seed", "7"])
    assert INF.main() == 0
    assert all(json.loads(l)["seed"] == 7 for l in out.read_text().splitlines())
