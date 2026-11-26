import os
import sys
import json
import numpy as np
from safetensors.numpy import load_file, save_file

def convert_mlx_to_peft(mlx_adapter_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    
    with open(os.path.join(mlx_adapter_dir, "adapter_config.json"), "r") as f:
        mlx_config = json.load(f)
        
    rank = mlx_config["lora_parameters"]["rank"]
    scale = mlx_config["lora_parameters"].get("scale", 1.0)
    lora_alpha = float(scale * rank)
    dropout = mlx_config["lora_parameters"]["dropout"]
    base_model = mlx_config.get("model", "Qwen/Qwen2.5-0.5B-Instruct")
    
    mlx_tensors = load_file(os.path.join(mlx_adapter_dir, "adapters.safetensors"))
    peft_tensors = {}
    target_modules = set()
    layers = set()
    
    for key, tensor in mlx_tensors.items():
        if not key.endswith(".lora_a") and not key.endswith(".lora_b"):
            peft_tensors[key] = tensor
            continue
            
        parts = key.split(".")
        # parts: ['model', 'layers', '8', 'self_attn', 'q_proj', 'lora_a']
        layer_idx = int(parts[2])
        layers.add(layer_idx)
        proj_name = parts[-2]
        target_modules.add(proj_name)
        
        lora_type = "lora_A" if parts[-1] == "lora_a" else "lora_B"
        new_key = f"base_model.model.{'.'.join(parts[:-1])}.{lora_type}.default.weight"
        
        # Transpose MLX weights to match PyTorch (out_features, in_features)
        if len(tensor.shape) == 2:
            tensor = np.transpose(tensor)
            
        peft_tensors[new_key] = tensor
        
    save_file(peft_tensors, os.path.join(output_dir, "adapter_model.safetensors"))
    
    peft_config = {
        "alpha_pattern": {},
        "auto_mapping": None,
        "base_model_name_or_path": base_model,
        "bias": "none",
        "fan_in_fan_out": False,
        "inference_mode": True,
        "init_lora_weights": True,
        "layer_replication": None,
        "layers_pattern": None,
        "layers_to_transform": sorted(list(layers)),
        "loftq_config": {},
        "lora_alpha": lora_alpha,
        "lora_bias": False,
        "lora_dropout": dropout,
        "megatron_config": None,
        "megatron_core": "megatron.core",
        "modules_to_save": None,
        "peft_type": "LORA",
        "r": rank,
        "rank_pattern": {},
        "revision": None,
        "target_modules": sorted(list(target_modules)),
        "task_type": "CAUSAL_LM"
    }
    
    # Fix python None to None in dict
    peft_config["auto_mapping"] = None
    peft_config["layer_replication"] = None
    peft_config["layers_pattern"] = None
    peft_config["megatron_config"] = None
    peft_config["modules_to_save"] = None
    peft_config["revision"] = None

    with open(os.path.join(output_dir, "adapter_config.json"), "w") as f:
        json.dump(peft_config, f, indent=2)
        
    print(f"Converted MLX adapter to PEFT format at {output_dir}")
    print(f"Layers: {sorted(list(layers))}")
    print(f"Target modules: {sorted(list(target_modules))}")

if __name__ == "__main__":
    convert_mlx_to_peft(sys.argv[1], sys.argv[2])
