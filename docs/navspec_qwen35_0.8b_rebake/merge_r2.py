import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
base = "Qwen/Qwen3.5-0.8B"
tok = AutoTokenizer.from_pretrained(base)
m = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
m = PeftModel.from_pretrained(m, "out/r2/best")
m = m.merge_and_unload()
print(type(m))
m.save_pretrained("merged/r2", safe_serialization=True)
tok.save_pretrained("merged/r2")
