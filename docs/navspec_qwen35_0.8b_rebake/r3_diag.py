import json, sys, random
sys.path.insert(0, 'repo/tools')
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
from navspec_armament_verdict_eval_hf import build_prompts, generate_all, read_jsonl
from pathlib import Path
tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-0.8B", padding_side="left")
if tok.pad_token is None: tok.pad_token = tok.eos_token
tok.padding_side = "left"
D = Path("data/stage4a_armament_verdict_v3")
test = read_jsonl(D / "test.jsonl")
train = read_jsonl(D / "train.jsonl")
random.Random(0).shuffle(train)
train = train[:150]
out = {}
for s in (0, 1, 2):
    base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.5-0.8B", dtype=torch.bfloat16)
    m = PeftModel.from_pretrained(base, f"out/r3_s{s}/best").cuda().eval()
    out[f"s{s}"] = {
        "test": generate_all(m, tok, build_prompts(tok, test), 64, 8),
        "train150": generate_all(m, tok, build_prompts(tok, train), 64, 8),
    }
    del m, base; torch.cuda.empty_cache()
json.dump(out, open("out/r3_diag_gens.json", "w"), ensure_ascii=False)
