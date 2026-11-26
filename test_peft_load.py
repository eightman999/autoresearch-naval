import torch
from transformers import AutoModelForCausalLM
from peft import PeftModel
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct", device_map="cpu", torch_dtype=torch.float16)
try:
    model = PeftModel.from_pretrained(model, "navspec-lora-peft")
    print("Loaded successfully")
except Exception as e:
    print("Error:", e)
