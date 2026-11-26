"""tools/navspec_qwen35_sft_train.py のテスト（CPU・ネットワーク不要）。

Qwen3.5 の text config（linear attention = Gated DeltaNet と full attention の両方を含む）を縮小した
ランダム初期化モデルを float32 / CPU で作り、次を確認する:

  1. 勾配チェックポイント: GC あり/なしで同じ入力・同じ seed の loss と LoRA 勾配が一致する（LoRA dropout あり/なし）。
     実際に再計算が走っていること（decoder layer の forward が 1 step で 2 回）も確認する。
  2. --init-adapter: 保存 -> 読み込みで loss が一致し、LoRA が trainable、保存し直した構成が同じ。
     main() 経由では、継続学習の step 0 の valid loss が元の学習の最終 valid loss と一致する。
  3. 再生データ混合: 件数・タスク内訳・層化・seed 固定・id 記録・タスク名衝突の改名・valid のグループ別 loss・best 選択。

torch / transformers(Qwen3.5) / peft が無い環境（リポジトリ既定の .venv）では skip する。実行例::

    uv run --no-project --with torch --with transformers --with peft --with pytest pytest tests/test_navspec_qwen35_sft_train.py

トークナイザは環境変数 NAVSPEC_QWEN35_TOKENIZER_DIR か HF キャッシュの Qwen/Qwen3.5-0.8B があればそれを使い、
無ければ ChatML 風テンプレート付きの極小 byte-level BPE をその場で作る（fla / causal-conv1d が無いので torch フォールバックで動く）。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")
transformers = pytest.importorskip("transformers")
if not hasattr(transformers, "Qwen3_5TextConfig"):
    pytest.skip("transformers に Qwen3.5 が無い", allow_module_level=True)

from tools import navspec_qwen35_sft_train as T  # noqa: E402

LAYER_TYPES = ["linear_attention", "linear_attention", "full_attention", "linear_attention"]
SYSTEM = "You are a naval assistant."
CHAT_TEMPLATE = (
    "{% for m in messages %}<|im_start|>{{ m.role }}\n{% if m.role == 'assistant' %}<think>\n\n</think>\n\n{% endif %}"
    "{{ m.content }}<|im_end|>\n{% endfor %}{% if add_generation_prompt %}<|im_start|>assistant\n<think>\n\n</think>\n\n{% endif %}"
)


# --------------------------------------------------------------------------------------------------
# 合成データ・モデル・トークナイザ
# --------------------------------------------------------------------------------------------------
def make_rows(prefix: str, tasks: dict[str, list[str]], n_per_variant: int, seed: int) -> list[dict]:
    """tasks: {task: [variant, ...]}。各 (task, variant) に n_per_variant 件。長さはばらつかせる。"""
    rng = random.Random(seed)
    rows = []
    for task, variants in tasks.items():
        for v in variants:
            for i in range(n_per_variant):
                q = " ".join(f"w{rng.randrange(40)}" for _ in range(rng.randrange(3, 14)))
                a = " ".join(f"a{rng.randrange(40)}" for _ in range(rng.randrange(2, 9)))
                rows.append({"id": f"{prefix}:{task}:{v}:{i}", "task": task, "variant": v,
                             "messages": [{"role": "system", "content": SYSTEM},
                                          {"role": "user", "content": f"[{task}] {q}"},
                                          {"role": "assistant", "content": a}]})
    rng.shuffle(rows)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def write_dataset(d: Path, prefix: str, tasks: dict[str, list[str]], n_train: int, n_valid: int, seed: int) -> None:
    write_jsonl(d / "train.jsonl", make_rows(prefix + "tr", tasks, n_train, seed))
    write_jsonl(d / "valid.jsonl", make_rows(prefix + "va", tasks, n_valid, seed + 1))


def find_real_tokenizer() -> Path | None:
    cands = []
    if os.environ.get("NAVSPEC_QWEN35_TOKENIZER_DIR"):
        cands.append(Path(os.environ["NAVSPEC_QWEN35_TOKENIZER_DIR"]))
    cands += sorted(Path.home().glob(".cache/huggingface/hub/models--Qwen--Qwen3.5-0.8B/snapshots/*"))
    for c in cands:
        if (c / "tokenizer.json").is_file() and (c / "tokenizer_config.json").is_file():
            return c
    return None


def build_tokenizer(dst: Path, corpus: list[str]) -> str:
    """dst にトークナイザを保存し、'real' か 'tiny' を返す。"""
    real = find_real_tokenizer()
    if real is not None:
        for f in ("tokenizer.json", "tokenizer_config.json"):
            shutil.copy(real / f, dst / f)
        return "real"
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast

    tk = Tokenizer(models.BPE())
    tk.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tk.decoder = decoders.ByteLevel()
    specials = ["<|endoftext|>", "<|im_start|>", "<|im_end|>"]
    tk.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=320, special_tokens=specials,
                                                       initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    fast = PreTrainedTokenizerFast(tokenizer_object=tk, eos_token="<|im_end|>", pad_token="<|endoftext|>", chat_template=CHAT_TEMPLATE)
    fast.save_pretrained(dst)
    return "tiny"


def build_tiny_model(dst: Path, vocab: int) -> None:
    """Qwen3.5 text config の縮小版（4 層 / hidden 128、linear x3 + full x1）。float32・ランダム初期化で保存する。"""
    from transformers import Qwen3_5ForCausalLM, Qwen3_5TextConfig

    cfg = Qwen3_5TextConfig(
        vocab_size=vocab, hidden_size=128, intermediate_size=256, num_hidden_layers=4, num_attention_heads=4,
        num_key_value_heads=2, head_dim=32, layer_types=LAYER_TYPES, linear_conv_kernel_dim=4,
        linear_key_head_dim=16, linear_value_head_dim=16, linear_num_key_heads=2, linear_num_value_heads=4,
        max_position_embeddings=512, partial_rotary_factor=0.25, tie_word_embeddings=False,
        rope_parameters={"rope_type": "default", "rope_theta": 10000.0, "partial_rotary_factor": 0.25},
    )
    torch.manual_seed(0)
    model = Qwen3_5ForCausalLM(cfg)
    # RMSNorm(1+w) 等の初期値のままだと勾配が小さすぎるので、全パラメータに小さな乱数を足して損失面を非自明にする
    g = torch.Generator().manual_seed(1)
    with torch.no_grad():
        for p in model.parameters():
            p.add_(torch.randn(p.shape, generator=g) * 0.02)
    model.save_pretrained(dst)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("sft")
    main_tasks = {"D": ["v0", "v1"]}
    replay_tasks = {"T1": ["a", "b"], "T2": ["a", "b"], "T3": ["a", "b"], "T4": ["a"]}
    write_dataset(root / "main", "m", main_tasks, n_train=12, n_valid=4, seed=10)  # train 24 / valid 8
    write_dataset(root / "replay", "r", replay_tasks, n_train=10, n_valid=5, seed=20)  # タスクあたり train 20 / valid 10
    write_dataset(root / "clash", "c", {"D": ["x"], "T1": ["a"]}, n_train=6, n_valid=3, seed=30)  # 主データと同名タスク D を含む
    model_dir = root / "model"
    model_dir.mkdir()
    corpus = [json.loads(l)["messages"][i]["content"] for f in root.glob("*/train.jsonl") for l in f.read_text().splitlines() for i in (1, 2)]
    kind = build_tokenizer(model_dir, corpus + [SYSTEM, "日本語 test"])
    vocab = max(len(transformers.AutoTokenizer.from_pretrained(model_dir)), 512)
    build_tiny_model(model_dir, vocab)
    return {"root": root, "main": root / "main", "replay": root / "replay", "clash": root / "clash", "model": model_dir, "tok_kind": kind}


def load_model(env_):
    return transformers.AutoModelForCausalLM.from_pretrained(env_["model"], dtype=torch.float32)


def lora_args(**kw) -> argparse.Namespace:
    d = dict(rank=4, alpha=8, dropout=0.0, target_modules=T.DEFAULT_TARGETS, layers=None, init_adapter=None)
    d.update(kw)
    return argparse.Namespace(**d)


def make_batch_entries(env_, n: int = 4) -> tuple[list[dict], object]:
    tok = transformers.AutoTokenizer.from_pretrained(env_["model"])
    rows = T.read_jsonl(env_["main"] / "train.jsonl")[:n]
    return [T.encode_row(tok, r) for r in rows], tok


def randomize_lora(pm, seed: int = 3, scale: float = 0.05) -> None:
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for n, p in pm.named_parameters():
            if "lora_" in n:
                p.copy_(torch.randn(p.shape, generator=g) * scale)


def step_loss_and_grads(pm, base, tok, batch, seed: int):
    """micro_step を 1 回（train モード・指定 seed）流し、(loss, 勾配ベクトル) を返す。"""
    params = [p for p in pm.parameters() if p.requires_grad]
    for p in params:
        p.grad = None
    n_tok = sum(len(e["ids"]) - e["pl"] for e in batch)
    pm.train()
    torch.manual_seed(seed)
    loss, _ = T.micro_step(base, tok, batch, torch.device("cpu"), n_tok)
    g = torch.cat([p.grad.flatten().clone() for p in params])
    for p in params:
        p.grad = None
    return loss, g


def eval_loss(pm, base, tok, batch) -> float:
    pm.eval()
    with torch.no_grad():
        ids, att = T.make_batch(tok, batch, torch.device("cpu"))
        ce, _, _ = T.completion_loss(base, ids, att, batch)
    pm.train()
    return float(ce.mean())


def run_main(env_, out: Path, *extra: str, data: Path | None = None) -> int:
    return T.main(["--model", str(env_["model"]), "--data", str(data or env_["main"]), "--out", str(out),
                   "--device", "cpu", "--dtype", "float32", "--rank", "4", "--alpha", "8", "--batch-size", "2",
                   "--grad-accum", "2", "--lr", "1e-3", "--warmup-ratio", "0", "--log-every", "1", "--eval-batch-size", "8",
                   *extra])


def read_log(out: Path) -> list[dict]:
    return [json.loads(l) for l in (out / "train_log.jsonl").read_text().splitlines()]


# --------------------------------------------------------------------------------------------------
# 1. 勾配チェックポイント
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("dropout", [0.0, 0.1])
def test_gradient_checkpointing_matches_plain(env, dropout, monkeypatch):
    """GC あり/なしで loss と LoRA 勾配が一致する（float32/CPU）。許容: loss 相対 1e-6、勾配 相対 L2 1e-5。"""
    batch, tok = make_batch_entries(env, 4)
    model = load_model(env)
    enable = T.enable_gradient_checkpointing(model)  # PEFT 構築前に有効化（本番と同じ順序）
    assert enable >= len(LAYER_TYPES)
    pm, info, warns = T.build_peft_model(model, lora_args(dropout=dropout))
    assert info["mode"] == "new" and warns == []
    randomize_lora(pm)
    base = pm.base_model.model

    # 再計算が実際に走っている: decoder layer の forward が 1 micro-step で 2 回（forward + 再計算）。
    # （nn.Module の forward hook は再計算側では発火しないので、クラスの forward 自体を数える）
    layer_cls = type(base.model.layers[0])
    orig_forward = layer_cls.forward
    calls = {"n": 0}

    def counting(self, *a, **k):
        calls["n"] += 1
        return orig_forward(self, *a, **k)

    monkeypatch.setattr(layer_cls, "forward", counting)
    loss_gc, g_gc = step_loss_and_grads(pm, base, tok, batch, seed=7)
    assert calls["n"] == 2 * len(LAYER_TYPES)

    base.gradient_checkpointing_disable()
    assert T.count_checkpointed_layers(base) == 0
    calls["n"] = 0
    loss_plain, g_plain = step_loss_and_grads(pm, base, tok, batch, seed=7)
    assert calls["n"] == len(LAYER_TYPES)
    monkeypatch.undo()

    assert g_plain.norm() > 0 and torch.isfinite(g_gc).all()
    assert abs(loss_gc - loss_plain) <= 1e-6 * abs(loss_plain)
    rel = float((g_gc - g_plain).norm() / g_plain.norm())
    assert rel <= 1e-5, rel
    # 全 LoRA パラメータ（linear attention 層・full attention 層・MLP）に勾配が届いている
    params = {n: p for n, p in pm.named_parameters() if p.requires_grad}
    assert any("linear_attn" in n for n in params) and any("self_attn" in n for n in params)
    assert config_use_cache_off(base)


def config_use_cache_off(base) -> bool:
    return base.config.use_cache is False


def test_gc_selfcheck_helper_and_rng_untouched(env):
    batch, tok = make_batch_entries(env, 4)
    model = load_model(env)
    T.enable_gradient_checkpointing(model)
    pm, _, _ = T.build_peft_model(model, lora_args(dropout=0.1))
    randomize_lora(pm)
    base = pm.base_model.model
    params = [p for p in pm.parameters() if p.requires_grad]
    n_tok = sum(len(e["ids"]) - e["pl"] for e in batch)
    torch.manual_seed(99)
    before = torch.get_rng_state().clone()
    res = T.gc_selfcheck(pm, base, tok, batch, torch.device("cpu"), n_tok, params, tol=1e-5)
    assert torch.equal(before, torch.get_rng_state())  # 学習側の RNG 状態を変えない
    assert res["grad_rel_l2"] <= 1e-5 and res["loss_abs_diff"] <= 1e-6
    assert T.count_checkpointed_layers(base) > 0  # 呼ぶ前の状態（GC あり）に戻す
    assert all(p.grad is None for p in params)


def test_gc_selfcheck_detects_mismatch(env, monkeypatch):
    """再計算が食い違う場合（ここでは GC ありだけ loss を歪める）は RuntimeError になる。"""
    batch, tok = make_batch_entries(env, 2)
    model = load_model(env)
    T.enable_gradient_checkpointing(model)
    pm, _, _ = T.build_peft_model(model, lora_args())
    randomize_lora(pm)
    base = pm.base_model.model
    params = [p for p in pm.parameters() if p.requires_grad]
    orig = T.micro_step

    def skewed(b, tok_, batch_, device, n):
        loss, nt = orig(b, tok_, batch_, device, n)
        if T.count_checkpointed_layers(b) > 0:
            for p in params:
                p.grad = p.grad * 1.5
        return loss, nt

    monkeypatch.setattr(T, "micro_step", skewed)
    with pytest.raises(RuntimeError, match="一致しない"):
        T.gc_selfcheck(pm, base, tok, batch, torch.device("cpu"), 10, params, tol=1e-3)


def test_main_with_gradient_checkpointing(env, tmp_path):
    out = tmp_path / "gc"
    assert run_main(env, out, "--iters", "3", "--eval-every", "3", "--valid-per-task", "4", "--gradient-checkpointing",
                    "--gc-check", "--gc-check-tol", "1e-4") == 0
    log = read_log(out)
    chk = [r for r in log if r["type"] == "gc_check"]
    assert len(chk) == 1 and chk[0]["grad_rel_l2"] <= 1e-4
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["gradient_checkpointing"] is True and cfg["gradient_checkpointing_modules"] >= len(LAYER_TYPES)
    assert json.loads((out / "summary.json").read_text())["gradient_checkpointing"] is True
    # 同じ設定で GC なしの結果と、学習 loss / valid loss が一致する（--gc-check も RNG を乱さない）
    out2 = tmp_path / "nogc"
    run_main(env, out2, "--iters", "3", "--eval-every", "3", "--valid-per-task", "4")
    tr1 = [r["loss"] for r in read_log(out) if r["type"] == "train"]
    tr2 = [r["loss"] for r in read_log(out2) if r["type"] == "train"]
    assert tr1 == pytest.approx(tr2, rel=1e-4)
    ev1 = [r["valid_loss"] for r in read_log(out) if r["type"] == "eval"]
    ev2 = [r["valid_loss"] for r in read_log(out2) if r["type"] == "eval"]
    assert ev1 == pytest.approx(ev2, rel=1e-4)


def test_gc_check_requires_gc_flag(env, tmp_path):
    with pytest.raises(SystemExit):
        run_main(env, tmp_path / "x", "--iters", "1", "--gc-check")


# --------------------------------------------------------------------------------------------------
# 2. --init-adapter
# --------------------------------------------------------------------------------------------------
def test_init_adapter_roundtrip_trainable(env, tmp_path):
    batch, tok = make_batch_entries(env, 4)
    model = load_model(env)
    pm, _, _ = T.build_peft_model(model, lora_args(rank=4, alpha=8, dropout=0.05))
    randomize_lora(pm)
    base = pm.base_model.model
    loss_before = eval_loss(pm, base, tok, batch)
    fresh, _, _ = T.build_peft_model(load_model(env), lora_args())
    assert loss_before != pytest.approx(eval_loss(fresh, fresh.base_model.model, tok, batch), abs=1e-6)  # LoRA を乱数化したので素の初期状態とは違う
    pm.save_pretrained(tmp_path / "adapter")
    n_lora = sum(p.numel() for n, p in pm.named_parameters() if "lora_" in n)

    # 引数は rank 64 / alpha 128 / 別 target のまま（明示指定なし）: アダプタ側を正として読み込む
    args = lora_args(rank=64, alpha=128, dropout=0.05, target_modules="q_proj", layers="0-1", init_adapter=tmp_path / "adapter")
    pm2, info, warns = T.build_peft_model(load_model(env), args, explicit=set())
    base2 = pm2.base_model.model
    assert warns == []
    assert (info["mode"], info["r"], info["lora_alpha"]) == ("init_adapter", 4, 8)
    assert (args.rank, args.alpha) == (4, 8)  # config 記録用に args もアダプタ側へ
    assert eval_loss(pm2, base2, tok, batch) == pytest.approx(loss_before, abs=1e-6)  # 読み込み直後の loss が保存前と一致

    lora2 = {n: p for n, p in pm2.named_parameters() if "lora_" in n}
    assert lora2 and all(p.requires_grad for p in lora2.values())  # LoRA は trainable
    assert sum(p.numel() for p in lora2.values()) == n_lora
    assert not any(p.requires_grad for n, p in pm2.named_parameters() if "lora_" not in n)  # ベースは凍結
    orig = {n: p.detach().clone() for n, p in pm.named_parameters() if "lora_" in n}
    assert all(torch.equal(orig[n], p.detach()) for n, p in lora2.items())

    # そこから 1 step 学習すると重みが動く（trainable で、勾配が届く）
    _, g = step_loss_and_grads(pm2, base2, tok, batch, seed=1)
    assert g.norm() > 0
    opt = torch.optim.AdamW([p for p in lora2.values()], lr=1e-2)
    pm2.train()
    n_tok = sum(len(e["ids"]) - e["pl"] for e in batch)
    T.micro_step(base2, tok, batch, torch.device("cpu"), n_tok)
    opt.step()
    assert any(not torch.equal(orig[n], p.detach()) for n, p in lora2.items())

    # 保存し直したアダプタは同じ構成
    pm2.save_pretrained(tmp_path / "adapter2")
    c1 = json.loads((tmp_path / "adapter" / "adapter_config.json").read_text())
    c2 = json.loads((tmp_path / "adapter2" / "adapter_config.json").read_text())
    for k in ("r", "lora_alpha", "lora_dropout", "layers_to_transform", "peft_type", "task_type"):
        assert c1.get(k) == c2.get(k), k
    assert sorted(c1["target_modules"]) == sorted(c2["target_modules"])  # PEFT は set で保存するので順序は不定


def test_init_adapter_warns_on_conflicting_explicit_args(env, tmp_path):
    model = load_model(env)
    pm, _, _ = T.build_peft_model(model, lora_args(rank=4, alpha=8, dropout=0.05))
    pm.save_pretrained(tmp_path / "adapter")
    args = lora_args(rank=16, alpha=32, dropout=0.2, target_modules="q_proj,v_proj", layers="0-1", init_adapter=tmp_path / "adapter")
    pm2, info, warns = T.build_peft_model(load_model(env), args, explicit={"rank", "alpha", "dropout", "target_modules", "layers"})
    joined = "\n".join(warns)
    for flag in ("--rank", "--alpha", "--target-modules", "--layers", "--dropout"):
        assert flag in joined, flag
    assert (info["r"], info["lora_alpha"]) == (4, 8)  # rank / alpha はアダプタ側が正
    assert info["lora_dropout"] == 0.2 and pm2.peft_config["default"].lora_dropout == 0.2  # dropout は明示指定で上書き
    assert pm2.peft_config["default"].r == 4
    # 一致していれば警告なし
    args_ok = lora_args(rank=4, alpha=8, dropout=0.05, init_adapter=tmp_path / "adapter")
    _, _, warns_ok = T.build_peft_model(load_model(env), args_ok, explicit={"rank", "alpha", "dropout", "target_modules"})
    assert warns_ok == []


def test_init_adapter_missing_path(env, tmp_path):
    with pytest.raises(FileNotFoundError):
        T.build_peft_model(load_model(env), lora_args(init_adapter=tmp_path / "nope"))


def test_parse_args_with_explicit_detects_abbrev_and_defaults():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, default=64)
    ap.add_argument("--alpha", type=float, default=128)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--target-modules", default="q")
    ap.add_argument("--layers", default=None)
    a, ex = T.parse_args_with_explicit(ap, [])
    assert ex == set() and (a.rank, a.alpha, a.dropout, a.target_modules, a.layers) == (64, 128, 0.05, "q", None)
    a, ex = T.parse_args_with_explicit(ap, ["--ran", "8", "--target-modules=k,v"])
    assert ex == {"rank", "target_modules"} and a.rank == 8 and a.target_modules == "k,v"
    assert ap.get_default("rank") == 64  # parser の既定は元に戻る


def test_main_init_adapter_continues_from_final(env, tmp_path):
    out1 = tmp_path / "run1"
    run_main(env, out1, "--iters", "4", "--eval-every", "2", "--valid-per-task", "0", "--dropout", "0.0")
    ev1 = [r for r in read_log(out1) if r["type"] == "eval"]
    last = ev1[-1]
    assert (out1 / "final" / "adapter_model.safetensors").is_file() and (out1 / "best").is_dir()

    out2 = tmp_path / "run2"
    # rank / alpha を矛盾する値で明示しても、アダプタ（r=4/alpha=8）が正
    run_main(env, out2, "--iters", "2", "--eval-every", "2", "--valid-per-task", "0", "--init-adapter", str(out1 / "final"),
             "--rank", "16", "--alpha", "32", "--dropout", "0.0")
    ev2 = [r for r in read_log(out2) if r["type"] == "eval"]
    assert ev2[0]["step"] == 0
    assert ev2[0]["valid_loss"] == pytest.approx(last["valid_loss"], abs=1e-6)  # 読み込み直後の valid loss = 継続前の最終
    assert ev2[0]["valid_loss"] != pytest.approx(ev1[0]["valid_loss"], abs=1e-4)  # 学習前（step 0）とは違う
    cfg = json.loads((out2 / "config.json").read_text())
    assert cfg["adapter"]["mode"] == "init_adapter" and cfg["adapter"]["r"] == 4 and cfg["rank"] == 4 and cfg["alpha"] == 8
    assert any("--rank" in w for w in cfg["adapter_warnings"]) and any("--alpha" in w for w in cfg["adapter_warnings"])
    assert cfg["trainable_params"] == json.loads((out1 / "config.json").read_text())["trainable_params"]
    a1 = json.loads((out1 / "final" / "adapter_config.json").read_text())
    a2 = json.loads((out2 / "final" / "adapter_config.json").read_text())
    assert (a2["r"], a2["lora_alpha"], sorted(a2["target_modules"])) == (a1["r"], a1["lora_alpha"], sorted(a1["target_modules"]))


# --------------------------------------------------------------------------------------------------
# 3. 再生データ混合
# --------------------------------------------------------------------------------------------------
def count_only(env_, out: Path, *extra: str) -> dict:
    run_main(env_, out, "--count-only", *extra)
    return json.loads((out / "token_count.json").read_text())


def by_id(path: Path) -> dict[str, dict]:
    return {r["id"]: r for r in T.read_jsonl(path)}


def test_replay_counts_breakdown_and_id_records(env, tmp_path):
    out = tmp_path / "mix"
    tc = count_only(env, out, "--valid-per-task", "3", "--replay-data", str(env["replay"]), "--replay-per-task", "6",
                    "--replay-tasks", "T1,T3")
    # 件数: 主 24 + 再生 6 x 2 タスク
    assert tc["train_rows"] == 24 + 12 and tc["by_source_rows"] == {"main": 24, "replay": 12}
    assert tc["by_task_rows"] == {"D": 24, "T1": 6, "T3": 6}
    assert tc["valid_rows_by_task"] == {"D": 3, "T1": 3, "T3": 3}  # valid もタスクごと --valid-per-task 件

    rp_train = json.loads((out / "replay_train_ids.json").read_text())
    rp_valid = json.loads((out / "replay_valid_ids.json").read_text())
    pool_tr = by_id(env["replay"] / "train.jsonl")
    pool_va = by_id(env["replay"] / "valid.jsonl")
    assert len(rp_train) == 12 == len(set(rp_train)) and set(rp_train) <= set(pool_tr)
    assert len(rp_valid) == 6 == len(set(rp_valid)) and set(rp_valid) <= set(pool_va)
    assert {pool_tr[i]["task"] for i in rp_train} == {"T1", "T3"}  # T2 / T4 は混ざらない
    for t in ("T1", "T3"):
        rows = [pool_tr[i] for i in rp_train if pool_tr[i]["task"] == t]
        assert len(rows) == 6 and {r["variant"] for r in rows} == {"a", "b"}
        assert sum(r["variant"] == "a" for r in rows) == 3  # (task, variant) 層化で 3:3
        assert [pool_va[i]["task"] for i in rp_valid].count(t) == 3
    # 主データの記録（train_ids.json / valid_subset_ids.json）には再生 id が混ざらない
    main_ids = json.loads((out / "train_ids.json").read_text())
    assert len(main_ids) == 24 and not set(main_ids) & set(rp_train)
    assert not set(json.loads((out / "valid_subset_ids.json").read_text())) & set(rp_valid)

    # seed 固定: 再実行で同一、seed を変えると別の抽出
    out_b = tmp_path / "mix_b"
    count_only(env, out_b, "--valid-per-task", "3", "--replay-data", str(env["replay"]), "--replay-per-task", "6", "--replay-tasks", "T1,T3")
    assert json.loads((out_b / "replay_train_ids.json").read_text()) == rp_train
    assert json.loads((out_b / "replay_valid_ids.json").read_text()) == rp_valid
    out_c = tmp_path / "mix_c"
    count_only(env, out_c, "--valid-per-task", "3", "--seed", "5", "--replay-data", str(env["replay"]), "--replay-per-task", "6",
               "--replay-tasks", "T1,T3")
    assert json.loads((out_c / "replay_train_ids.json").read_text()) != rp_train


def test_replay_default_tasks_all_and_short_pool(env, tmp_path, capsys):
    out = tmp_path / "all"
    tc = count_only(env, out, "--valid-per-task", "0", "--replay-data", str(env["replay"]), "--replay-per-task", "50")
    # --replay-tasks 省略 = 全タスク（T4 は 10 件しかなく全件）。N が足りなければ全件で警告
    assert tc["by_task_rows"] == {"D": 24, "T1": 20, "T2": 20, "T3": 20, "T4": 10}
    assert tc["valid_rows_by_task"] == {"D": 8, "T1": 10, "T2": 10, "T3": 10, "T4": 5}
    assert "[warn]" in capsys.readouterr().out


def test_replay_task_name_clash_is_renamed(env, tmp_path):
    out = tmp_path / "clash"
    tc = count_only(env, out, "--valid-per-task", "2", "--replay-data", str(env["clash"]), "--replay-per-task", "4", "--replay-tasks", "D,T1")
    assert tc["by_task_rows"] == {"D": 24, "replay_D": 4, "T1": 4}  # 主データの D と混ざらない
    assert tc["valid_rows_by_task"] == {"D": 2, "replay_D": 2, "T1": 2}


def test_replay_argument_validation(env, tmp_path):
    base = ("--valid-per-task", "2")
    with pytest.raises(ValueError, match="T9"):
        count_only(env, tmp_path / "a", *base, "--replay-data", str(env["replay"]), "--replay-per-task", "2", "--replay-tasks", "T1,T9")
    with pytest.raises(SystemExit):  # --replay-data には --replay-per-task が必要
        count_only(env, tmp_path / "b", *base, "--replay-data", str(env["replay"]))
    with pytest.raises(SystemExit):  # --replay-data 無しの --replay-tasks
        count_only(env, tmp_path / "c", *base, "--replay-tasks", "T1")
    # 主データと id が衝突する再生データはエラー
    dup = tmp_path / "dup"
    write_jsonl(dup / "train.jsonl", T.read_jsonl(env["main"] / "train.jsonl")[:3])
    write_jsonl(dup / "valid.jsonl", T.read_jsonl(env["main"] / "valid.jsonl")[:3])
    with pytest.raises(ValueError, match="重複"):
        count_only(env, tmp_path / "d", *base, "--replay-data", str(dup), "--replay-per-task", "2")


def test_main_with_replay_logs_group_losses_and_best_on(env, tmp_path):
    results = {}
    for best_on in ("main", "all"):
        out = tmp_path / f"run_{best_on}"
        assert run_main(env, out, "--iters", "4", "--eval-every", "1", "--valid-per-task", "4", "--replay-data", str(env["replay"]),
                        "--replay-per-task", "6", "--replay-tasks", "T1,T2,T3", "--best-on", best_on, "--lr", "5e-3") == 0
        log = read_log(out)
        ev = [r for r in log if r["type"] == "eval"]
        assert [r["step"] for r in ev] == [0, 1, 2, 3, 4]
        for r in ev:
            groups = r["loss_by_group"]
            assert {"D", "T1", "T2", "T3"} <= set(groups) and {"D:v0", "T1:a"} <= set(groups)  # タスク名別・(task:variant) 別
            assert "T4" not in groups
            assert r["n_valid"] == 4 + 12  # 主 D 4 + 再生 3 タスク x 4
            # 主 / 再生 / 全体: トークン加重平均として整合
            tk = r["tokens_by_group"]
            main_t, rep_t = tk["D"], tk["T1"] + tk["T2"] + tk["T3"]
            expect_all = (r["valid_loss_main"] * main_t + r["valid_loss_replay"] * rep_t) / (main_t + rep_t)
            assert r["valid_loss_all"] == pytest.approx(expect_all, rel=1e-4)
            assert r["valid_loss_main"] == pytest.approx(r["loss_by_group"]["D"], rel=1e-4)
            assert r["valid_loss"] == pytest.approx(r["valid_loss_main"] if best_on == "main" else r["valid_loss_all"])
            assert r["best_on"] == best_on
        # best は選択指標の最小 step（step 0 は対象外）
        trained = [r for r in ev if r["step"] > 0]
        want = min(trained, key=lambda r: r["valid_loss"])
        summ = json.loads((out / "summary.json").read_text())
        assert summ["best_step"] == want["step"] and summ["best_valid_loss"] == pytest.approx(want["valid_loss"])
        assert summ["best_on"] == best_on and (out / "best" / "adapter_config.json").is_file()
        cfg = json.loads((out / "config.json").read_text())
        assert cfg["n_train_main"] == 24 and cfg["n_train_replay"] == 18 and cfg["n_train"] == 42
        assert cfg["train_rows_by_task"] == {"D": 24, "T1": 6, "T2": 6, "T3": 6}
        assert len(json.loads((out / "replay_train_ids.json").read_text())) == 18
        assert len(json.loads((out / "replay_valid_ids.json").read_text())) == 12
        results[best_on] = ev
    # 同じ seed なので学習経路は同一（best-on は選択だけ）
    assert [r["valid_loss_all"] for r in results["main"]] == pytest.approx([r["valid_loss_all"] for r in results["all"]], rel=1e-5)


def test_main_without_replay_is_backward_compatible(env, tmp_path):
    out = tmp_path / "plain"
    run_main(env, out, "--iters", "2", "--eval-every", "1", "--valid-per-task", "4")
    log = read_log(out)
    ev = [r for r in log if r["type"] == "eval"]
    for r in ev:  # 再生なし: valid_loss は従来どおり全体 = 主
        assert r["valid_loss"] == r["valid_loss_all"] == r["valid_loss_main"] and "valid_loss_replay" not in r
        assert set(r["loss_by_group"]) == {"D", "D:v0", "D:v1"} and r["n_valid"] == 4
    assert not (out / "replay_train_ids.json").exists()
    assert (out / "train_ids.json").is_file() and (out / "valid_subset_ids.json").is_file()
    assert json.loads((out / "config.json").read_text())["adapter"]["mode"] == "new"

    # 4. 学習ログ: step あたり秒数・tokens/s・ピークメモリ
    tr = [r for r in log if r["type"] == "train"]
    assert [r["step"] for r in tr] == [1, 2]
    for r in tr:
        assert r["sec_per_step"] > 0 and r["tokens_per_s"] > 0 and r["tokens_per_s_window"] > 0
        assert "peak_gib" in r and "peak_train_gib" in r  # CPU では 0
    s = json.loads((out / "summary.json").read_text())
    assert s["sec_per_step_mean"] > 0 and s["tokens_per_s_mean"] > 0 and s["gradient_checkpointing"] is False


def test_build_steps_handles_non_divisible_dataset():
    """n が batch_size で割り切れなくても、全 step が batch_size 揃いの micro-batch で埋まる（端数で step が空にならない）。"""
    plan = T.build_steps(7, [5] * 7, steps=4, per_step=4, batch_size=2, grad_accum=2, chunk=512, rng=random.Random(0))
    assert len(plan) == 4 and all(len(mb) == 2 for step in plan for mb in step) and all(len(step) == 2 for step in plan)
    with pytest.raises(ValueError):
        T.build_steps(1, [5], steps=1, per_step=4, batch_size=2, grad_accum=2, chunk=8, rng=random.Random(0))
