"""navspec 艦艇設計 GUI (Tkinter)

二段pipeline (Stage 2 + classic + numeric specialist) をGUIから操作する。
依存関係追加なし（Python標準のTkinterを使用）。

起動: uv run python tools/navspec_gui.py
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# uv standalone Python は Tcl/Tk ライブラリのパスが通らないことがあるため自動設定
_py_prefix = Path(sys.base_prefix).resolve()
for _env, _sub in [("TCL_LIBRARY", "lib/tcl8.6"), ("TK_LIBRARY", "lib/tk8.6")]:
    _path = _py_prefix / _sub
    if _path.exists() and _env not in os.environ:
        os.environ[_env] = str(_path)

import tkinter as tk
from tkinter import ttk, messagebox

from navspec_nation_tier import NATION_TIERS
from navspec_shiptypes import SHIP_TYPE_MAIN_GUN_CLAMP
from navspec_contradiction import check_contradiction
from navspec_lora_data import make_ok_output, make_impossible_output
from navspec_armament_pipeline import (
    DEFAULT_DATA_DIR as ARMAMENT_DATA_DIR,
    build_stage2_skip_result,
    run_armament_pipeline,
    validate_pipeline_result,
)
from navspec_armament_validator import load_db

NATIONS = sorted(NATION_TIERS.keys())
SHIP_TYPES = sorted(SHIP_TYPE_MAIN_GUN_CLAMP.keys())
ROLES = [
    "fleet_battle", "coastal_defense", "commerce_raiding",
    "carrier_screen", "anti_submarine_escort", "convoy_escort",
    "colonial_patrol", "night_battle", "fleet_air_defense",
    "long_range_scout", "regional_flagship",
]

SPEC_DISPLAY_ORDER = [
    ("nation", "国家", ""),
    ("year", "年代", ""),
    ("ship_type", "艦種", ""),
    ("standard_displacement_t", "基準排水量", "t"),
    ("full_load_displacement_t", "満載排水量", "t"),
    ("length_m", "全長", "m"),
    ("beam_m", "幅", "m"),
    ("draft_m", "吃水", "m"),
    ("lb_ratio", "L/B比", ""),
    ("power_shp", "機関出力", "shp"),
    ("speed_kt", "最大速力", "kt"),
    ("range_nm", "航続距離", "nm"),
    ("main_gun_caliber_mm", "主砲口径", "mm"),
    ("main_gun_count", "主砲門数", "門"),
    ("belt_armor_mm", "舷側装甲", "mm"),
    ("torpedo_tubes", "魚雷発射管", "門"),
    ("radar", "レーダー", ""),
    ("nation_tier", "国家tier", ""),
]

CONSTRAINT_FIELDS = [
    ("main_gun_caliber_mm_min", "主砲口径 min", "mm", 37, 520),
    ("main_gun_caliber_mm_max", "主砲口径 max", "mm", 37, 520),
    ("speed_kt_min", "速力 min", "kt", 5, 45),
    ("speed_kt_max", "速力 max", "kt", 5, 45),
    ("belt_armor_mm_min", "装甲 min", "mm", 0, 600),
    ("range_nm_min", "航続 min", "nm", 200, 25000),
    ("range_nm_max", "航続 max", "nm", 200, 25000),
    ("standard_displacement_t_min", "排水量 min", "t", 100, 150000),
    ("standard_displacement_t_max", "排水量 max", "t", 100, 150000),
    ("main_gun_barrels_min", "主砲門数 min", "門", 1, 80),
]


class PipelineWorker:
    """バックグラウンドでpipelineを実行し、queueでUIに進捗・結果を渡す。"""

    def __init__(self, widget: tk.Misc, case: dict[str, Any], on_progress, on_done, *, deterministic: bool = False):
        self._widget = widget
        self._case = case
        self._q: queue.Queue = queue.Queue()
        self._on_progress = on_progress
        self._on_done = on_done
        self._cancelled = False
        self._thread: threading.Thread | None = None
        self._deterministic = deterministic

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._poll()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self):
        self._cancelled = True

    def _run_deterministic(self):
        """UI表示経路だけを実プロセスで検証するためのモデルなし実行。"""
        case = self._case
        try:
            self._q.put(("progress", "classic検証中..."))
            contradiction = check_contradiction(
                ship_type=case["ship_type"],
                year=int(case["year"]),
                nation=case["nation"],
                constraints=case.get("constraints"),
                raw_prompt=case.get("request", ""),
            )
            if contradiction.get("status", "ok") == "impossible":
                output = make_impossible_output(case, contradiction)
                pipeline_result = build_stage2_skip_result(
                    case,
                    stage2_output=output,
                    seed=0,
                    model_name="gui-smoke",
                    adapter_name="none",
                )
                self._q.put(("done", {
                    "status": "impossible",
                    "final_json": {
                        "schema_version": "navspec.lora_candidate.v2",
                        "status": "impossible",
                        "reason": output.get("reason", []),
                        "suggested_revision": output.get("suggested_revision", {}),
                        "design_concept": output.get("design_concept", ""),
                        "spec": None,
                        "confidence": output.get("confidence", 0.9),
                    },
                    "classic_reasons": contradiction.get("reasons", []),
                    "patch_summary": None,
                    "pipeline_result": pipeline_result,
                    "elapsed": 0,
                }))
                return

            if self._cancelled:
                self._q.put(("done", {"status": "cancelled"}))
                return

            self._q.put(("progress", "詳細兵装を検索・検証中..."))
            canonical_output = make_ok_output(case)
            pipeline_result = run_armament_pipeline(
                case,
                strategy="representative",
                seed=0,
                data_dir=ARMAMENT_DATA_DIR,
                model_name="gui-smoke",
                adapter_name="none",
            )
            pipeline_errors = validate_pipeline_result(pipeline_result)
            if pipeline_errors:
                raise ValueError("pipeline result contract error: " + "; ".join(pipeline_errors[:3]))
            final_json = dict(canonical_output)
            final_json["schema_version"] = "navspec.lora_candidate.v2"
            self._q.put(("done", {
                "status": "ok",
                "final_json": final_json,
                "patch_summary": None,
                "stage2_design_concept": None,
                "numeric_design_concept": None,
                "pipeline_result": pipeline_result,
                "elapsed": 0,
            }))
        except Exception as exc:
            self._q.put(("error", str(exc)))

    def _run(self):
        if self._deterministic:
            self._run_deterministic()
            return
        case = self._case
        try:
            self._q.put(("progress", "classic検証中..."))
            contradiction = check_contradiction(
                ship_type=case["ship_type"],
                year=int(case["year"]),
                nation=case["nation"],
                constraints=case.get("constraints"),
                raw_prompt=case.get("request", ""),
            )
            classic_status = contradiction.get("status", "ok")

            if classic_status == "impossible":
                output = make_impossible_output(case, contradiction)
                pipeline_result = build_stage2_skip_result(
                    case,
                    stage2_output=output,
                    seed=0,
                    model_name="classic-status-validator",
                    adapter_name="none",
                )
                self._q.put(("done", {
                    "status": "impossible",
                    "final_json": {
                        "schema_version": "navspec.lora_candidate.v2",
                        "status": "impossible",
                        "reason": output.get("reason", []),
                        "suggested_revision": output.get("suggested_revision", {}),
                        "design_concept": output.get("design_concept", ""),
                        "spec": None,
                        "confidence": output.get("confidence", 0.9),
                    },
                    "classic_reasons": contradiction.get("reasons", []),
                    "patch_summary": None,
                    "pipeline_result": pipeline_result,
                    "elapsed": 0,
                }))
                return

            canonical_output = make_ok_output(case)
            canonical_spec = canonical_output.get("spec", {})

            if self._cancelled:
                self._q.put(("done", {"status": "cancelled"}))
                return

            self._q.put(("progress", "Stage 2 モデル読込中..."))
            t0 = time.time()
            from qwen_lora_eval import load_mlx_lm, generate_text, prompt_from_row, extract_candidate_json

            stage2_adapter = ROOT / "models" / "navspec-lora-0.5b-v0.1" / "adapter" / "stage2-status-r8"
            model_name = "Qwen/Qwen2.5-0.5B-Instruct"
            schema = json.loads((ROOT / "schemas" / "navspec_lora_candidate.schema.json").read_text(encoding="utf-8"))

            model, tokenizer, generate_fn = load_mlx_lm(model_name, stage2_adapter)
            self._q.put(("progress", "Stage 2 推論中..."))

            messages = [
                {"role": "system", "content": "You are navspec, a naval vessel specification generator. Return only JSON matching navspec.lora_candidate.v1. Do not repeat the user request."},
                {"role": "user", "content": json.dumps(case.get("input", case), ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
            ]
            try:
                prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            except Exception:
                prompt = "\n".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>" for m in messages) + "\n<|im_start|>assistant\n"

            stage2_raw = generate_text(generate_fn, model, tokenizer, prompt, 512, 0.0)
            try:
                stage2_obj = extract_candidate_json(stage2_raw, schema)
            except Exception:
                stage2_obj = {}
            stage2_design_concept = stage2_obj.get("design_concept") if isinstance(stage2_obj, dict) else None

            del model, tokenizer, generate_fn
            import gc
            gc.collect()

            if self._cancelled:
                self._q.put(("done", {"status": "cancelled"}))
                return

            self._q.put(("progress", "numeric specialist 読込中..."))
            numeric_adapter = ROOT / "models" / "navspec-lora-0.5b-v0.1" / "adapter" / "stage3-numeric-quality-r8-v1"
            model, tokenizer, generate_fn = load_mlx_lm(model_name, numeric_adapter)
            self._q.put(("progress", "numeric specialist 推論中..."))
            numeric_raw = generate_text(generate_fn, model, tokenizer, prompt, 512, 0.0)
            try:
                numeric_obj = extract_candidate_json(numeric_raw, schema)
            except Exception:
                numeric_obj = {}
            numeric_spec = numeric_obj.get("spec") if isinstance(numeric_obj, dict) and isinstance(numeric_obj.get("spec"), dict) else {}
            numeric_design_concept = numeric_obj.get("design_concept") if isinstance(numeric_obj, dict) else None

            del model, tokenizer, generate_fn
            gc.collect()

            if self._cancelled:
                self._q.put(("done", {"status": "cancelled"}))
                return

            self._q.put(("progress", "classic numeric patch 適用中..."))

            from navspec_two_stage_pipeline import patch_numeric_spec, compute_pipeline_confidence, DEFAULT_CONTRACT
            contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
            constraints = case.get("constraints") or {}
            patch_result = patch_numeric_spec(numeric_spec, canonical_spec, contract, constraints)

            design_concept = numeric_design_concept or stage2_design_concept or canonical_output.get("design_concept", "")
            required = ["nation", "year", "ship_type", "standard_displacement_t", "full_load_displacement_t",
                        "length_m", "beam_m", "draft_m", "lb_ratio", "power_shp", "speed_kt", "range_nm",
                        "main_gun_caliber_mm", "main_gun_count", "belt_armor_mm", "torpedo_tubes", "radar", "nation_tier"]
            missing_required = sum(1 for f in required if patch_result["final_spec"].get(f) is None)
            constraint_violations = 0
            for ckey, cmeta in contract.get("constraint_fields", {}).items():
                if ckey not in constraints:
                    continue
                limit_v = constraints[ckey]
                try:
                    limit = float(limit_v)
                except (TypeError, ValueError):
                    continue
                final_v = patch_result["final_spec"].get(cmeta["target"])
                if final_v is None:
                    continue
                try:
                    final_val = float(final_v)
                except (TypeError, ValueError):
                    continue
                if cmeta["direction"] == "min" and final_val < limit:
                    constraint_violations += 1
                elif cmeta["direction"] == "max" and final_val > limit:
                    constraint_violations += 1

            confidence = compute_pipeline_confidence(0.88, patch_result, constraint_violations, missing_required)

            elapsed = time.time() - t0
            final_json = {
                "schema_version": "navspec.lora_candidate.v2",
                "status": "ok",
                "reason": [],
                "spec": patch_result["final_spec"],
                "suggested_revision": {},
                "design_concept": design_concept,
                "confidence": confidence,
            }

            self._q.put(("progress", "詳細兵装を検索・検証中..."))
            pipeline_result = run_armament_pipeline(
                case,
                stage2_output=stage2_obj if isinstance(stage2_obj, dict) else None,
                numeric_spec=patch_result["final_spec"],
                strategy="representative",
                seed=0,
                data_dir=ARMAMENT_DATA_DIR,
                model_name=model_name,
                adapter_name=str(stage2_adapter),
            )
            pipeline_errors = validate_pipeline_result(pipeline_result)
            if pipeline_errors:
                raise ValueError("pipeline result contract error: " + "; ".join(pipeline_errors[:3]))

            patched_fields = []
            for p in patch_result.get("patches", []):
                if p["severity"] != "adopted":
                    patched_fields.append({
                        "field": p["field"],
                        "raw": p["raw"],
                        "final": p["final"],
                        "severity": p["severity"],
                        "relative_delta": p.get("relative_delta"),
                    })

            self._q.put(("done", {
                "status": "ok",
                "final_json": final_json,
                "patch_summary": {
                    "exact_adoption": patch_result["exact_adoption"],
                    "tolerant_adoption": patch_result["tolerant_adoption"],
                    "major_patch_rate": patch_result["major_patch_rate"],
                    "patched_fields": patched_fields,
                },
                "stage2_design_concept": stage2_design_concept,
                "numeric_design_concept": numeric_design_concept,
                "pipeline_result": pipeline_result,
                "elapsed": round(elapsed, 1),
            }))

        except Exception as exc:
            self._q.put(("error", str(exc)))

    def _poll(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "progress":
                    self._on_progress(payload)
                elif kind == "done":
                    self._on_done(payload)
                    return
                elif kind == "error":
                    self._on_done({"status": "error", "error": payload})
                    return
        except queue.Empty:
            pass
        self._widget.after(100, self._poll)


class NavspecGUI(tk.Tk):
    def __init__(self, *, deterministic: bool = False):
        super().__init__()
        self.title("navspec 艦艇設計ツール")
        self.geometry("1100x750")
        self.minsize(900, 600)
        self._worker: PipelineWorker | None = None
        self._deterministic = deterministic
        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self, padding=6)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(main, text="設計要件", padding=8)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))

        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_input_panel(left)
        self._build_output_panel(right)

    def _build_input_panel(self, parent):
        row = 0

        ttk.Label(parent, text="年代").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._year = tk.IntVar(value=1937)
        ttk.Spinbox(parent, from_=1850, to=2000, textvariable=self._year, width=8).grid(row=row, column=1, sticky=tk.W, pady=2)
        row += 1

        ttk.Label(parent, text="国家").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._nation = tk.StringVar(value="Japan")
        ttk.Combobox(parent, textvariable=self._nation, values=NATIONS, width=22).grid(row=row, column=1, sticky=tk.W, pady=2)
        row += 1

        ttk.Label(parent, text="艦種").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._ship_type = tk.StringVar(value="heavy_cruiser")
        ttk.Combobox(parent, textvariable=self._ship_type, values=SHIP_TYPES, width=22).grid(row=row, column=1, sticky=tk.W, pady=2)
        row += 1

        ttk.Label(parent, text="ロール").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._role = tk.StringVar(value="fleet_battle")
        ttk.Combobox(parent, textvariable=self._role, values=ROLES, width=22).grid(row=row, column=1, sticky=tk.W, pady=2)
        row += 1

        ttk.Label(parent, text="基準排水量 (t)").grid(row=row, column=0, sticky=tk.W, pady=1)
        self._displacement_t = tk.StringVar()
        ttk.Entry(parent, textvariable=self._displacement_t, width=10).grid(row=row, column=1, sticky=tk.W, pady=1)
        row += 1

        ttk.Label(parent, text="船体幅 (m)").grid(row=row, column=0, sticky=tk.W, pady=1)
        self._beam_m = tk.StringVar()
        ttk.Entry(parent, textvariable=self._beam_m, width=10).grid(row=row, column=1, sticky=tk.W, pady=1)
        row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=6)
        row += 1

        ttk.Label(parent, text="制約（空欄=指定なし）", font=("", 9, "bold")).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(4, 2))
        row += 1

        self._constraint_vars: dict[str, tk.StringVar] = {}
        for key, label, unit, lo, hi in CONSTRAINT_FIELDS:
            ttk.Label(parent, text=f"{label} ({unit})").grid(row=row, column=0, sticky=tk.W, pady=1)
            var = tk.StringVar()
            self._constraint_vars[key] = var
            ttk.Entry(parent, textvariable=var, width=10).grid(row=row, column=1, sticky=tk.W, pady=1)
            row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=6)
        row += 1

        ttk.Label(parent, text="自由記述").grid(row=row, column=0, sticky=tk.NW, pady=2)
        self._request = tk.Text(parent, width=26, height=4, font=("", 9))
        self._request.grid(row=row, column=1, sticky=tk.W, pady=2)
        row += 1

        self._generate_btn = ttk.Button(parent, text="設計生成", command=self._on_generate)
        self._generate_btn.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(8, 2))
        row += 1

        self._cancel_btn = ttk.Button(parent, text="中断", command=self._on_cancel, state=tk.DISABLED)
        self._cancel_btn.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 2))
        row += 1

        self._status_label = ttk.Label(parent, text="待機中", font=("", 9))
        self._status_label.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        row += 1

    def _build_output_panel(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, pady=(0, 4))

        self._result_status = ttk.Label(top, text="—", font=("", 14, "bold"))
        self._result_status.pack(side=tk.LEFT)

        self._result_time = ttk.Label(top, text="", font=("", 9))
        self._result_time.pack(side=tk.LEFT, padx=(12, 0))

        self._result_confidence = ttk.Label(top, text="", font=("", 10))
        self._result_confidence.pack(side=tk.RIGHT)

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        spec_frame = ttk.Frame(notebook, padding=4)
        notebook.add(spec_frame, text="仕様")
        self._build_spec_tab(spec_frame)

        concept_frame = ttk.Frame(notebook, padding=4)
        notebook.add(concept_frame, text="設計コンセプト")
        self._build_concept_tab(concept_frame)

        patch_frame = ttk.Frame(notebook, padding=4)
        notebook.add(patch_frame, text="パッチ詳細")
        self._build_patch_tab(patch_frame)

        armament_frame = ttk.Frame(notebook, padding=4)
        notebook.add(armament_frame, text="詳細兵装")
        self._build_armament_tab(armament_frame)

        trace_frame = ttk.Frame(notebook, padding=4)
        notebook.add(trace_frame, text="検証trace")
        self._build_trace_tab(trace_frame)

        impossible_frame = ttk.Frame(notebook, padding=4)
        notebook.add(impossible_frame, text="不可理由")
        self._build_impossible_tab(impossible_frame)

        json_frame = ttk.Frame(notebook, padding=4)
        notebook.add(json_frame, text="生JSON")
        self._build_json_tab(json_frame)

    def _build_spec_tab(self, parent):
        self._spec_tree = ttk.Treeview(parent, columns=("value", "unit"), show="tree headings", height=20)
        self._spec_tree.heading("#0", text="項目")
        self._spec_tree.heading("value", text="値")
        self._spec_tree.heading("unit", text="単位")
        self._spec_tree.column("#0", width=200)
        self._spec_tree.column("value", width=140, anchor=tk.E)
        self._spec_tree.column("unit", width=50, anchor=tk.W)
        self._spec_tree.pack(fill=tk.BOTH, expand=True)

    def _build_concept_tab(self, parent):
        self._concept_text = tk.Text(parent, wrap=tk.WORD, font=("", 11))
        self._concept_text.pack(fill=tk.BOTH, expand=True)
        self._concept_text.config(state=tk.DISABLED)

    def _build_patch_tab(self, parent):
        self._patch_label = ttk.Label(parent, text="（パッチなし）", font=("", 10))
        self._patch_label.pack(anchor=tk.W, pady=(0, 4))

        self._patch_tree = ttk.Treeview(parent, columns=("raw", "final", "severity", "delta"), show="tree headings", height=12)
        self._patch_tree.heading("#0", text="フィールド")
        self._patch_tree.heading("raw", text="specialist出力")
        self._patch_tree.heading("final", text="最終値")
        self._patch_tree.heading("severity", text="判定")
        self._patch_tree.heading("delta", text="相対差")
        self._patch_tree.column("#0", width=180)
        self._patch_tree.column("raw", width=120, anchor=tk.E)
        self._patch_tree.column("final", width=120, anchor=tk.E)
        self._patch_tree.column("severity", width=70, anchor=tk.CENTER)
        self._patch_tree.column("delta", width=80, anchor=tk.E)
        self._patch_tree.pack(fill=tk.BOTH, expand=True)

    def _build_impossible_tab(self, parent):
        self._impossible_text = tk.Text(parent, wrap=tk.WORD, font=("", 11))
        self._impossible_text.pack(fill=tk.BOTH, expand=True)
        self._impossible_text.config(state=tk.DISABLED)

    def _build_armament_tab(self, parent):
        self._armament_text = tk.Text(parent, wrap=tk.WORD, font=("Courier", 10))
        self._armament_text.pack(fill=tk.BOTH, expand=True)
        self._armament_text.config(state=tk.DISABLED)

    def _build_trace_tab(self, parent):
        self._trace_text = tk.Text(parent, wrap=tk.WORD, font=("Courier", 9))
        self._trace_text.pack(fill=tk.BOTH, expand=True)
        self._trace_text.config(state=tk.DISABLED)

    def _build_json_tab(self, parent):
        self._json_text = tk.Text(parent, wrap=tk.NONE, font=("Courier", 10))
        self._json_text.pack(fill=tk.BOTH, expand=True)
        self._json_text.config(state=tk.DISABLED)

    def _pipeline_spec(self, pipeline_result: dict[str, Any]) -> dict[str, Any]:
        """ShipSpecRecordを既存の仕様TreeView表示項目へ写像する。"""
        record = pipeline_result.get("ship_spec")
        if not isinstance(record, dict):
            return {}
        displacement = record.get("displacement") if isinstance(record.get("displacement"), dict) else {}
        dimensions = record.get("dimensions") if isinstance(record.get("dimensions"), dict) else {}
        propulsion = record.get("propulsion") if isinstance(record.get("propulsion"), dict) else {}
        armor = record.get("armor") if isinstance(record.get("armor"), dict) else {}
        armament = record.get("armament") if isinstance(record.get("armament"), dict) else {}
        equipment = record.get("equipment") if isinstance(record.get("equipment"), dict) else {}
        fire_control = equipment.get("fire_control") if isinstance(equipment.get("fire_control"), dict) else {}
        aggregate = pipeline_result.get("armament_aggregate")
        aggregate = aggregate if isinstance(aggregate, dict) else {}
        caliber = aggregate.get("main_gun_caliber_mm", armament.get("main_caliber_mm"))
        barrels = aggregate.get("main_gun_count", armament.get("main_barrels"))
        normal_t = displacement.get("normal_t")
        full_t = displacement.get("full_t")
        breadth = dimensions.get("breadth_m")
        length = dimensions.get("length_m")
        lb_ratio = round(float(length) / float(breadth), 2) if length and breadth else None
        return {
            "nation": record.get("country"),
            "year": record.get("year"),
            "ship_type": record.get("type"),
            "standard_displacement_t": normal_t,
            "full_load_displacement_t": full_t,
            "length_m": length,
            "beam_m": breadth,
            "draft_m": dimensions.get("draught_m"),
            "lb_ratio": lb_ratio,
            "power_shp": propulsion.get("power_shp"),
            "speed_kt": propulsion.get("speed_kn"),
            "range_nm": None,
            "main_gun_caliber_mm": caliber,
            "main_gun_count": barrels,
            "belt_armor_mm": armor.get("belt_mm"),
            "torpedo_tubes": None,
            "radar": bool(fire_control.get("radar")),
            "nation_tier": None,
        }

    def _display_armament(self, pipeline_result: dict[str, Any] | None):
        self._armament_text.config(state=tk.NORMAL)
        self._armament_text.delete("1.0", tk.END)
        if not isinstance(pipeline_result, dict):
            self._armament_text.insert("1.0", "詳細兵装なし（旧出力モード）")
            self._armament_text.config(state=tk.DISABLED)
            return

        candidate = pipeline_result.get("detailed_armament")
        if not isinstance(candidate, dict):
            fallback = pipeline_result.get("fallback") or {}
            message = fallback.get("message") or "詳細兵装候補なし"
            self._armament_text.insert("1.0", f"{message}\n")
            if fallback.get("used"):
                self._armament_text.insert(tk.END, f"fallback reason: {fallback.get('reason')}\n")
            self._armament_text.config(state=tk.DISABLED)
            return

        try:
            db = load_db(ARMAMENT_DATA_DIR)
        except Exception:
            db = {"weapons": {}, "mounts": {}, "ammunition": {}, "fire_control": {}}
        confidence = candidate.get("confidence")
        self._armament_text.insert(tk.END, f"confidence: {confidence if confidence is not None else '—'}\n")
        aggregate = pipeline_result.get("armament_aggregate")
        if aggregate:
            self._armament_text.insert(tk.END, f"aggregate: {json.dumps(aggregate, ensure_ascii=False, sort_keys=True)}\n")
        self._armament_text.insert(tk.END, "\n主砲バッテリー\n")
        for battery in candidate.get("main_batteries", []):
            weapon_id = battery.get("weapon_id")
            mount_id = battery.get("mount_id")
            fc_id = battery.get("fire_control_id")
            weapon = db.get("weapons", {}).get(weapon_id, {})
            mount = db.get("mounts", {}).get(mount_id, {})
            ammo = next(
                (item for item in db.get("ammunition", {}).values() if item.get("weapon_id") == weapon_id),
                {},
            )
            fire_control = db.get("fire_control", {}).get(fc_id, {}) if fc_id else {}
            self._armament_text.insert(
                tk.END,
                "  " + json.dumps({
                    "battery_id": battery.get("battery_id"),
                    "weapon": {"id": weapon_id, "name": weapon.get("name"), "source": weapon.get("source")},
                    "mount": {"id": mount_id, "name": mount.get("name"), "source": mount.get("source")},
                    "ammunition": {"id": ammo.get("ammo_id"), "name": ammo.get("name"), "source": ammo.get("source")},
                    "fire_control": {"id": fc_id, "name": fire_control.get("name"), "source": fire_control.get("source")},
                    "mount_count": battery.get("mount_count"),
                    "barrels_per_mount": battery.get("barrels_per_mount"),
                    "total_barrels": battery.get("total_barrels"),
                    "positions": battery.get("positions"),
                    "ammo_per_gun": battery.get("ammo_per_gun"),
                }, ensure_ascii=False, sort_keys=True) + "\n",
            )
        provenance = pipeline_result.get("provenance") or {}
        self._armament_text.insert(tk.END, "\n出典・provenance\n")
        self._armament_text.insert(tk.END, f"  DB: {provenance.get('db_version')} / {provenance.get('db_hash')}\n")
        self._armament_text.insert(tk.END, f"  contract: {provenance.get('contract_version')} / {provenance.get('contract_hash')}\n")
        self._armament_text.insert(tk.END, f"  sources: {', '.join(provenance.get('source_files') or [])}\n")
        self._armament_text.config(state=tk.DISABLED)

    def _display_trace(self, pipeline_result: dict[str, Any] | None):
        self._trace_text.config(state=tk.NORMAL)
        self._trace_text.delete("1.0", tk.END)
        if not isinstance(pipeline_result, dict):
            self._trace_text.insert("1.0", "検証traceなし（旧出力モード）")
            self._trace_text.config(state=tk.DISABLED)
            return

        errors = pipeline_result.get("reason") or []
        warnings = pipeline_result.get("warnings") or []
        self._trace_text.insert(tk.END, "[error / reason]\n")
        if errors:
            for item in errors:
                self._trace_text.insert(tk.END, f"  {item.get('code')}: {item.get('message')}\n")
        else:
            self._trace_text.insert(tk.END, "  なし\n")
        self._trace_text.insert(tk.END, "[warning]\n")
        if warnings:
            for item in warnings:
                self._trace_text.insert(tk.END, f"  {item.get('code')}: {item.get('message')}\n")
        else:
            self._trace_text.insert(tk.END, "  なし\n")

        fallback = pipeline_result.get("fallback") or {}
        if fallback.get("used"):
            self._trace_text.insert(tk.END, f"[fallback] {fallback.get('reason')}: {fallback.get('message')}\n")
        self._trace_text.insert(tk.END, "\n[適用ルール / before -> after]\n")
        for item in pipeline_result.get("validation_trace", []):
            severity = item.get("severity", "info")
            source = item.get("source", "")
            self._trace_text.insert(
                tk.END,
                f"#{item.get('sequence')} [{severity}] {item.get('phase')} "
                f"{item.get('rule')} / {item.get('action')} / source={source}\n"
                f"  before={json.dumps(item.get('before'), ensure_ascii=False, sort_keys=True)}\n"
                f"  after ={json.dumps(item.get('after'), ensure_ascii=False, sort_keys=True)}\n",
            )
        self._trace_text.config(state=tk.DISABLED)

    def _collect_constraints(self) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        for key, var in self._constraint_vars.items():
            val = var.get().strip()
            if val:
                try:
                    constraints[key] = int(val) if "." not in val else float(val)
                except ValueError:
                    pass
        return constraints

    def _build_case(self) -> dict[str, Any]:
        year = int(self._year.get())
        nation = self._nation.get()
        ship_type = self._ship_type.get()
        role = self._role.get()
        constraints = self._collect_constraints()
        request_text = self._request.get("1.0", tk.END).strip()
        if not request_text:
            request_text = f"{year}年の{nation}向けに、{role}を重視した{ship_type}を設計せよ。"

        case = {
            "id": f"gui_{int(time.time())}",
            "ship_type": ship_type,
            "year": year,
            "nation": nation,
            "role": role,
            "constraints": constraints,
            "request": request_text,
        }
        for key, variable in (("displacement_t", self._displacement_t), ("beam_m", self._beam_m)):
            raw_value = variable.get().strip()
            if raw_value:
                try:
                    case[key] = float(raw_value)
                except ValueError:
                    pass
        case["input"] = {
            "nation": nation,
            "year": year,
            "ship_type": ship_type,
            "role": role,
            "constraints": constraints,
            "request": request_text,
            "task": "generate_navspec_lora_candidate",
            "output_schema": "navspec.lora_candidate.v1",
        }
        for key in ("displacement_t", "beam_m"):
            if key in case:
                case["input"][key] = case[key]
        return case

    def _on_generate(self):
        if self._worker is not None and self._worker.is_alive():
            self._status_label.config(text="前の処理が終了するまで待ってください")
            return
        self._generate_btn.config(state=tk.DISABLED)
        self._cancel_btn.config(state=tk.NORMAL)
        self._status_label.config(text="開始中...")
        self._result_status.config(text="生成中...")

        case = self._build_case()
        self._worker = PipelineWorker(
            self,
            case,
            self._on_progress,
            self._on_done,
            deterministic=self._deterministic,
        )
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
        self._status_label.config(text="中断処理中...")
        self._cancel_btn.config(state=tk.DISABLED)

    def _on_progress(self, msg: str):
        self._status_label.config(text=msg)

    def _on_done(self, result: dict[str, Any]):
        self._generate_btn.config(state=tk.NORMAL)
        self._cancel_btn.config(state=tk.DISABLED)
        self._worker = None

        status = result.get("status")
        if status == "cancelled":
            self._status_label.config(text="中断されました")
            return
        if status == "error":
            self._status_label.config(text=f"エラー: {result.get('error', '')[:80]}")
            messagebox.showerror("エラー", result.get("error", "不明なエラー"))
            return

        final_json = result.get("final_json", {})
        pipeline_result = result.get("pipeline_result")
        elapsed = result.get("elapsed", 0)
        combined_status = (
            pipeline_result.get("status")
            if isinstance(pipeline_result, dict)
            else final_json.get("status")
        )

        if combined_status == "impossible":
            label = "impossible（設計不可）"
            if isinstance(pipeline_result, dict) and pipeline_result.get("status") == "impossible" and final_json.get("status") == "ok":
                label = "impossible（兵装検証）"
            self._result_status.config(text=label, foreground="red")
            self._display_impossible(result, final_json, pipeline_result)
            if isinstance(pipeline_result, dict) and pipeline_result.get("ship_spec"):
                self._display_spec(self._pipeline_spec(pipeline_result))
            else:
                self._clear_spec()
        else:
            self._result_status.config(text="ok（設計成功）", foreground="green")
            if isinstance(pipeline_result, dict) and pipeline_result.get("ship_spec"):
                self._display_spec(self._pipeline_spec(pipeline_result))
            else:
                self._display_spec(final_json.get("spec", {}))
            self._display_patch(result.get("patch_summary"))
            self._clear_impossible()

        concept = final_json.get("design_concept", "")
        if isinstance(pipeline_result, dict) and pipeline_result.get("design_rationale"):
            concept = f"{concept}\n\n[詳細兵装の根拠]\n{pipeline_result['design_rationale']}"
        self._display_concept(concept)
        self._display_armament(pipeline_result)
        self._display_trace(pipeline_result)
        self._display_json(pipeline_result if isinstance(pipeline_result, dict) else final_json)
        self._result_time.config(text=f"処理時間: {elapsed:.1f}s")
        conf = (
            pipeline_result.get("confidence", 0)
            if isinstance(pipeline_result, dict)
            else final_json.get("confidence", 0)
        )
        self._result_confidence.config(text=f"confidence: {conf:.2f}")
        self._status_label.config(text="完了")

    def _display_spec(self, spec: dict[str, Any]):
        self._spec_tree.delete(*self._spec_tree.get_children())
        for key, label, unit in SPEC_DISPLAY_ORDER:
            val = spec.get(key)
            if val is None:
                val_str = "—"
            elif isinstance(val, bool):
                val_str = "有" if val else "無"
            elif isinstance(val, float):
                val_str = f"{val:,.1f}" if val == int(val) else f"{val:,.1f}"
            else:
                val_str = str(val)
            self._spec_tree.insert("", tk.END, text=label, values=(val_str, unit))

    def _clear_spec(self):
        self._spec_tree.delete(*self._spec_tree.get_children())

    def _display_concept(self, concept: str):
        self._concept_text.config(state=tk.NORMAL)
        self._concept_text.delete("1.0", tk.END)
        self._concept_text.insert("1.0", concept)
        self._concept_text.config(state=tk.DISABLED)

    def _display_patch(self, patch_summary: dict[str, Any] | None):
        self._patch_tree.delete(*self._patch_tree.get_children())
        if not patch_summary:
            self._patch_label.config(text="（パッチ情報なし）")
            return

        ea = patch_summary.get("exact_adoption", 0)
        ta = patch_summary.get("tolerant_adoption", 0)
        mp = patch_summary.get("major_patch_rate", 0)
        self._patch_label.config(text=f"exact {ea:.0%} / tolerant {ta:.0%} / major patch {mp:.0%}")

        for p in patch_summary.get("patched_fields", []):
            raw = p.get("raw")
            final = p.get("final")
            raw_s = f"{raw:,.0f}" if isinstance(raw, (int, float)) else str(raw)
            final_s = f"{final:,.0f}" if isinstance(final, (int, float)) else str(final)
            delta = p.get("relative_delta")
            delta_s = f"{delta:.1%}" if delta is not None else "—"
            sev = p.get("severity", "")
            self._patch_tree.insert("", tk.END, text=p.get("field", ""), values=(raw_s, final_s, sev, delta_s))

    def _display_impossible(
        self,
        result: dict,
        final_json: dict,
        pipeline_result: dict[str, Any] | None = None,
    ):
        self._impossible_text.config(state=tk.NORMAL)
        self._impossible_text.delete("1.0", tk.END)

        reasons = (
            pipeline_result.get("reason")
            if isinstance(pipeline_result, dict) and pipeline_result.get("reason")
            else result.get("classic_reasons") or final_json.get("reason", [])
        )
        if isinstance(reasons, list):
            for i, r in enumerate(reasons, 1):
                if isinstance(r, dict):
                    self._impossible_text.insert(tk.END, f"  {i}. [{r.get('code','')}] {r.get('message','')}\n")
                else:
                    self._impossible_text.insert(tk.END, f"  {i}. {r}\n")
        elif isinstance(reasons, str):
            self._impossible_text.insert(tk.END, reasons)

        rev = final_json.get("suggested_revision", {})
        if isinstance(pipeline_result, dict):
            rev = pipeline_result.get("suggested_revision") or rev
            warnings = pipeline_result.get("warnings") or []
            if warnings:
                self._impossible_text.insert(tk.END, "\n--- 警告（warning-only） ---\n")
                for warning in warnings:
                    self._impossible_text.insert(
                        tk.END,
                        f"  [{warning.get('code')}] {warning.get('message')}\n",
                    )
            fallback = pipeline_result.get("fallback") or {}
            if fallback.get("used"):
                self._impossible_text.insert(
                    tk.END,
                    f"\n--- fallback ---\n  {fallback.get('reason')}: {fallback.get('message')}\n",
                )
        if rev:
            self._impossible_text.insert(tk.END, "\n--- 修正提案 ---\n")
            for k, v in rev.items():
                self._impossible_text.insert(tk.END, f"  {k}: {v}\n")

        self._impossible_text.config(state=tk.DISABLED)

    def _clear_impossible(self):
        self._impossible_text.config(state=tk.NORMAL)
        self._impossible_text.delete("1.0", tk.END)
        self._impossible_text.config(state=tk.DISABLED)

    def _display_json(self, final_json: dict):
        self._json_text.config(state=tk.NORMAL)
        self._json_text.delete("1.0", tk.END)
        self._json_text.insert("1.0", json.dumps(final_json, ensure_ascii=False, indent=2, sort_keys=True))
        self._json_text.config(state=tk.DISABLED)


def _smoke_cases() -> dict[str, dict[str, Any]]:
    return {
        "normal": {
            "id": "gui_smoke_normal",
            "nation": "Japan",
            "year": 1941,
            "ship_type": "battleship",
            "role": "fleet_battle",
            "constraints": {},
            "displacement_t": 30000,
            "beam_m": 38,
            "request": "",
        },
        "impossible": {
            "id": "gui_smoke_impossible",
            "nation": "United States",
            "year": 1944,
            "ship_type": "destroyer",
            "role": "fleet_battle",
            "constraints": {
                "main_gun_caliber_mm_min": 203,
                "main_gun_barrels_min": 6,
                "speed_kt_min": 40,
                "belt_armor_mm_min": 150,
                "range_nm_min": 10000,
            },
            "request": "",
        },
        "warning": {
            "id": "gui_smoke_warning",
            "nation": "Japan",
            "year": 1941,
            "ship_type": "battleship",
            "role": "fleet_battle",
            "constraints": {},
            "displacement_t": 30000,
            "beam_m": 1,
            "request": "",
        },
    }


def _run_smoke(name: str) -> int:
    cases = _smoke_cases()
    selected = cases if name == "all" else {name: cases[name]}
    results = []
    for case_name, case in selected.items():
        result = run_armament_pipeline(case, seed=0, model_name="gui-smoke", adapter_name="none")
        errors = validate_pipeline_result(result)
        results.append({
            "case": case_name,
            "status": result.get("status"),
            "warning_codes": [item.get("code") for item in result.get("warnings", [])],
            "reason_codes": [item.get("code") for item in result.get("reason", [])],
            "fallback": result.get("fallback"),
            "trace_count": len(result.get("validation_trace", [])),
            "contract_errors": errors,
        })
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(not item["contract_errors"] for item in results) else 2


def _run_ui_smoke(name: str) -> int:
    """Tk画面を作り、入力欄から3ケースを順に実行する実プロセス検証。"""
    cases = _smoke_cases()
    selected = cases if name == "all" else {name: cases[name]}
    try:
        app = NavspecGUI(deterministic=True)
    except tk.TclError as exc:
        print(f"GUI smokeを開始できません: {exc}", file=sys.stderr)
        return 2

    state: dict[str, Any] = {"index": 0, "records": [], "error": None}
    selected_items = list(selected.items())
    original_done = app._on_done

    def run_case() -> None:
        if state["index"] >= len(selected_items):
            app.after(20, app.destroy)
            return
        _, case = selected_items[state["index"]]
        app._year.set(case["year"])
        app._nation.set(case["nation"])
        app._ship_type.set(case["ship_type"])
        app._role.set(case["role"])
        app._displacement_t.set(str(case.get("displacement_t", "")))
        app._beam_m.set(str(case.get("beam_m", "")))
        for variable in app._constraint_vars.values():
            variable.set("")
        for key, value in (case.get("constraints") or {}).items():
            if key in app._constraint_vars:
                app._constraint_vars[key].set(str(value))
        app._request.delete("1.0", tk.END)
        app._request.insert("1.0", case.get("request", ""))
        app._on_generate()

    def on_done(result: dict[str, Any]) -> None:
        case_name = selected_items[state["index"]][0]
        if result.get("status") == "error":
            state["error"] = result.get("error", "GUI pipeline error")
            app.destroy()
            return

        original_done(result)
        pipeline_result = result.get("pipeline_result")
        raw_text = app._json_text.get("1.0", tk.END).strip()
        displayed_trace = app._trace_text.get("1.0", tk.END)
        displayed_armament = app._armament_text.get("1.0", tk.END)
        displayed_impossible = app._impossible_text.get("1.0", tk.END)
        try:
            raw_json = json.loads(raw_text)
        except json.JSONDecodeError:
            raw_json = None

        contract_errors = validate_pipeline_result(raw_json) if isinstance(raw_json, dict) else ["生JSONがobjectではありません"]
        reason_codes = [item.get("code") for item in (pipeline_result or {}).get("reason", [])]
        warning_codes = [item.get("code") for item in (pipeline_result or {}).get("warnings", [])]
        fallback = (pipeline_result or {}).get("fallback") or {}
        ui_label = str(app._result_status.cget("text"))
        raw_status = raw_json.get("status") if isinstance(raw_json, dict) else None
        status_visible = (
            (raw_status == "ok" and "ok" in ui_label)
            or (raw_status == "impossible" and "impossible" in ui_label)
        )
        reason_visible = all(
            code in displayed_trace or code in displayed_impossible for code in reason_codes
        )
        warning_visible = all(code in displayed_trace for code in warning_codes)
        fallback_visible = (
            not fallback.get("used")
            or str(fallback.get("reason")) in displayed_trace
            or str(fallback.get("reason")) in displayed_armament
        )
        candidate = (pipeline_result or {}).get("detailed_armament")
        battery_ids = [item.get("battery_id") for item in (candidate or {}).get("main_batteries", [])]
        armament_visible = not battery_ids or all(battery_id in displayed_armament for battery_id in battery_ids)
        record = {
            "case": case_name,
            "raw_status": raw_status,
            "ui_label": ui_label,
            "reason_codes": reason_codes,
            "warning_codes": warning_codes,
            "fallback": fallback,
            "contract_errors": contract_errors,
            "raw_json_equals_displayed_json": isinstance(raw_json, dict) and raw_json == pipeline_result,
            "status_visible": status_visible,
            "reason_visible": reason_visible,
            "warning_visible": warning_visible,
            "fallback_visible": fallback_visible,
            "armament_visible": armament_visible,
        }
        record["passed"] = all([
            not contract_errors,
            record["raw_json_equals_displayed_json"],
            status_visible,
            reason_visible,
            warning_visible,
            fallback_visible,
            armament_visible,
        ])
        state["records"].append(record)
        state["index"] += 1
        app.after(30, run_case)

    app._on_done = on_done
    app.after(50, run_case)
    app.after(60000, lambda: (state.update(error="GUI smoke timeout"), app.destroy()))
    app.mainloop()

    output = state["records"]
    if state["error"]:
        output.append({"error": state["error"]})
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if output and not state["error"] and all(item.get("passed") for item in output) else 2


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="navspec 艦艇設計GUI")
    parser.add_argument(
        "--smoke",
        choices=["normal", "impossible", "warning", "all"],
        help="Tk画面を開かず、GUIと同じpipeline facadeを実プロセスで検証する",
    )
    parser.add_argument(
        "--smoke-ui",
        choices=["normal", "impossible", "warning", "all"],
        help="Tk画面を実際に作成し、入力欄からpipelineを操作して検証する",
    )
    args = parser.parse_args(argv)
    if args.smoke:
        return _run_smoke(args.smoke)
    if args.smoke_ui:
        return _run_ui_smoke(args.smoke_ui)
    app = NavspecGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
