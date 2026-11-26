"""統一評価 run の採点・summary 生成。

モデルの出力を決定的に再推論することはせず、``predictions.jsonl`` に保存された予測を
読み、schema/status/reason/warning/numeric/armament/fallback/latency の各軸を採点する。
未知の reason/warning code は独立したFAIL軸として扱う。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.navspec_eval_run import DEFAULT_CONTRACT, canonical_json, read_jsonl
except ImportError:  # direct execution from tools/
    from navspec_eval_run import DEFAULT_CONTRACT, canonical_json, read_jsonl

ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS_NAME = "predictions.jsonl"


def load_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = path or DEFAULT_CONTRACT
    value = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{contract_path}: contract はobjectである必要があります")
    return value


def load_prediction_rows(source: Path) -> list[dict[str, Any]]:
    path = source
    if path.is_dir():
        path = path / PREDICTIONS_NAME
    if not path.exists():
        raise FileNotFoundError(f"予測結果がありません: {path}")
    return read_jsonl(path)


def _output(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    nested = value.get("final_json")
    if isinstance(nested, dict):
        return nested
    nested = value.get("pipeline_result")
    if isinstance(nested, dict):
        return nested
    return value


def _prediction(row: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("prediction", "predicted_output", "output"):
        if key in row:
            return _output(row.get(key))
    return None


def _expected(row: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("expected_output", "expected"):
        if isinstance(row.get(key), dict):
            return _output(row[key])
    return None


def _status(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and isinstance(item.get("code"), str):
            result.append(item["code"])
    return result


def reason_codes(output: dict[str, Any] | None, row: dict[str, Any], *, expected: bool = False) -> list[str]:
    if expected:
        explicit = row.get("expected_reason_codes")
        if explicit is not None:
            return _codes(explicit)
    if not output:
        return []
    for key in ("reason_codes", "reason"):
        if key in output:
            return _codes(output[key])
    return []


def warning_codes(output: dict[str, Any] | None, row: dict[str, Any], *, expected: bool = False) -> list[str]:
    if expected:
        explicit = row.get("expected_warning_codes")
        if explicit is not None:
            return _codes(explicit)
    if not output:
        return []
    for key in ("warning_codes", "warnings", "warning"):
        if key in output:
            return _codes(output[key])
    return []


def known_codes(contract: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for codes in (contract.get("known_codes") or {}).values():
        if isinstance(codes, list):
            values.update(code for code in codes if isinstance(code, str))
    return values


def _schema_ok(output: dict[str, Any] | None, contract: dict[str, Any]) -> tuple[bool, list[str]]:
    if not isinstance(output, dict):
        return False, ["output_not_object"]
    version = output.get("schema_version")
    allowed = set(contract.get("prediction_schema_versions") or [])
    if version not in allowed:
        return False, ["unknown_schema_version"]
    errors: list[str] = []
    if version.startswith("navspec.lora_candidate"):
        for key in ("status", "spec", "reason", "suggested_revision", "confidence"):
            if key not in output:
                errors.append(f"missing:{key}")
        if output.get("status") not in {"ok", "impossible"}:
            errors.append("invalid:status")
    elif version == "navspec.pipeline_result.v1":
        for key in ("ship_spec", "detailed_armament", "validation_trace", "provenance"):
            if key not in output:
                errors.append(f"missing:{key}")
        if output.get("status") not in {"ok", "impossible"}:
            errors.append("invalid:status")
    return not errors, errors


def _numeric_value(spec: dict[str, Any], field: str, aliases: dict[str, str]) -> float | int | None:
    candidates = [field]
    alias = aliases.get(field)
    if alias:
        candidates.append(alias)
    candidates.extend(source for source, target in aliases.items() if target == field)
    for candidate in candidates:
        value = spec.get(candidate)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return value
    return None


def _spec(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if not output:
        return None
    value = output.get("spec")
    if isinstance(value, dict):
        return value
    value = output.get("ship_spec")
    if isinstance(value, dict):
        # pipeline result may contain a legacy spec under ship_spec.spec
        nested = value.get("spec")
        return nested if isinstance(nested, dict) else value
    return None


def _tolerance(field_meta: dict[str, Any], expected: float) -> float:
    absolute = field_meta.get("minor_abs_tolerance")
    relative = field_meta.get("minor_rel_tolerance")
    values = []
    if isinstance(absolute, (int, float)):
        values.append(abs(float(absolute)))
    if isinstance(relative, (int, float)):
        values.append(abs(expected) * float(relative))
    return max(values, default=0.0)


def compare_numeric(expected: dict[str, Any] | None, predicted: dict[str, Any] | None, contract: dict[str, Any]) -> dict[str, Any] | None:
    expected_spec = _spec(expected)
    predicted_spec = _spec(predicted)
    if not expected_spec or not predicted_spec:
        return None
    numeric_contract_path = ROOT / str((contract.get("schema_refs") or {}).get("numeric_eval_contract", ""))
    numeric_contract: dict[str, Any] = {}
    if numeric_contract_path.exists():
        value = json.loads(numeric_contract_path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            numeric_contract = value
    field_meta = numeric_contract.get("numeric_fields") or {}
    aliases = {
        str(key): str(value)
        for key, value in (numeric_contract.get("field_aliases") or {}).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    fields: dict[str, Any] = {}
    comparable = 0
    exact = 0
    tolerant = 0
    for field, metadata in field_meta.items():
        if not isinstance(metadata, dict):
            continue
        expected_value = _numeric_value(expected_spec, field, aliases)
        predicted_value = _numeric_value(predicted_spec, field, aliases)
        matched = expected_value is not None and predicted_value is not None
        if matched:
            comparable += 1
            delta = abs(float(expected_value) - float(predicted_value))
            is_exact = delta == 0
            is_tolerant = delta <= _tolerance(metadata, float(expected_value))
            exact += int(is_exact)
            tolerant += int(is_tolerant)
        else:
            delta = None
            is_exact = False
            is_tolerant = False
        fields[field] = {
            "expected": expected_value,
            "predicted": predicted_value,
            "absolute_delta": delta,
            "exact": is_exact,
            "tolerant": is_tolerant,
        }
    if not comparable:
        return None
    return {
        "fields": fields,
        "comparable_fields": comparable,
        "exact_rate": round(exact / comparable, 6),
        "tolerant_rate": round(tolerant / comparable, 6),
        "exact": exact == comparable,
        "tolerant": tolerant == comparable,
    }


def _armament(output: dict[str, Any] | None) -> Any:
    if not output:
        return None
    for key in ("detailed_armament", "armament"):
        if key in output:
            return output[key]
    return None


def _fallback_fields(value: Any) -> tuple[bool | None, Any]:
    """legacy bool/string と pipeline result の fallback envelope を同じ軸へ正規化する。"""
    if value is None:
        return None, None
    if isinstance(value, dict) and isinstance(value.get("used"), bool):
        return value["used"], value.get("reason") if value["used"] else None
    return bool(value), value


def compare_armament(expected: dict[str, Any] | None, predicted: dict[str, Any] | None) -> dict[str, Any] | None:
    expected_armament = _armament(expected)
    predicted_armament = _armament(predicted)
    if expected_armament is None and predicted_armament is None:
        return None
    contract_ok = isinstance(predicted_armament, dict) or predicted_armament is None
    exact = canonical_json(expected_armament) == canonical_json(predicted_armament)
    return {
        "contract_ok": contract_ok,
        "exact": exact,
        "expected_present": expected_armament is not None,
        "predicted_present": predicted_armament is not None,
    }


def _multilabel(expected: list[str], predicted: list[str]) -> dict[str, Any]:
    expected_set = set(expected)
    predicted_set = set(predicted)
    tp = len(expected_set & predicted_set)
    fp = len(predicted_set - expected_set)
    fn = len(expected_set - predicted_set)
    precision = tp / (tp + fp) if tp + fp else (1.0 if not expected_set and not predicted_set else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "expected": sorted(expected_set),
        "predicted": sorted(predicted_set),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "exact": expected_set == predicted_set,
    }


def _micro(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
    tp = sum(int(item[key]["tp"]) for item in items)
    fp = sum(int(item[key]["fp"]) for item in items)
    fn = sum(int(item[key]["fn"]) for item in items)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    exact = sum(1 for item in items if item[key]["exact"])
    return {
        "items": len(items),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "exact_rate": round(exact / len(items), 6) if items else None,
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(values[lower], 6)
    weight = position - lower
    return round(values[lower] + (values[upper] - values[lower]) * weight, 6)


def score_items(rows: Iterable[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    known = known_codes(contract)
    output: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        prediction = _prediction(row)
        expected = _expected(row)
        expected_status = _status(row.get("expected_status")) or _status(expected.get("status") if expected else None)
        predicted_status = _status(prediction.get("status") if prediction else None)
        expected_reasons = reason_codes(expected, row, expected=True)
        predicted_reasons = reason_codes(prediction, row)
        expected_warnings = warning_codes(expected, row, expected=True)
        predicted_warnings = warning_codes(prediction, row)
        unknown = sorted((set(predicted_reasons) | set(predicted_warnings)) - known)
        schema_ok, schema_errors = _schema_ok(prediction, contract)
        status_comparable = expected_status is not None and predicted_status is not None
        status_match = status_comparable and expected_status == predicted_status
        numeric = compare_numeric(expected, prediction, contract)
        armament = compare_armament(expected, prediction)
        fallback_raw: Any = row.get("fallback_reason")
        if fallback_raw is None and isinstance(prediction, dict):
            fallback_raw = prediction.get("fallback_reason", prediction.get("fallback"))
        fallback, fallback_value = _fallback_fields(fallback_raw)
        latency = row.get("latency_s")
        if not isinstance(latency, (int, float)) or isinstance(latency, bool):
            latency = None
        output.append({
            "id": row.get("id") or f"row_{index:06d}",
            "case_group_id": row.get("case_group_id"),
            "category": row.get("source_category", row.get("category")),
            "expected_status": expected_status,
            "predicted_status": predicted_status,
            "status_match": status_match if status_comparable else None,
            "schema_ok": schema_ok,
            "schema_errors": schema_errors,
            "reason": _multilabel(expected_reasons, predicted_reasons),
            "warning": _multilabel(expected_warnings, predicted_warnings),
            "unknown_codes": unknown,
            "numeric": numeric,
            "armament": armament,
            "fallback": fallback,
            "fallback_reason": fallback_value,
            "latency_s": float(latency) if latency is not None else None,
        })
    return output


def _status_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [item for item in items if item["status_match"] is not None]
    accuracy = sum(1 for item in comparable if item["status_match"]) / len(comparable) if comparable else None
    labels = sorted({item["expected_status"] for item in comparable} | {item["predicted_status"] for item in comparable})
    recalls = []
    for label in labels:
        actual = [item for item in comparable if item["expected_status"] == label]
        recalls.append(sum(1 for item in actual if item["predicted_status"] == label) / len(actual) if actual else 0.0)
    impossible_tp = sum(1 for item in comparable if item["expected_status"] == "impossible" and item["predicted_status"] == "impossible")
    impossible_fp = sum(1 for item in comparable if item["expected_status"] != "impossible" and item["predicted_status"] == "impossible")
    impossible_fn = sum(1 for item in comparable if item["expected_status"] == "impossible" and item["predicted_status"] != "impossible")
    impossible_precision = impossible_tp / (impossible_tp + impossible_fp) if impossible_tp + impossible_fp else 0.0
    impossible_recall = impossible_tp / (impossible_tp + impossible_fn) if impossible_tp + impossible_fn else 0.0
    impossible_f1 = (
        2 * impossible_precision * impossible_recall / (impossible_precision + impossible_recall)
        if impossible_precision + impossible_recall else 0.0
    )
    return {
        "items": len(comparable),
        "accuracy": round(accuracy, 6) if accuracy is not None else None,
        "balanced_accuracy": round(statistics.mean(recalls), 6) if recalls else None,
        "class_recall": {label: round(recall, 6) for label, recall in zip(labels, recalls)},
        "impossible_f1": round(impossible_f1, 6) if comparable else None,
    }


def summarize_scores(items: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    total = len(items)
    schema_rate = sum(1 for item in items if item["schema_ok"]) / total if total else None
    reason = _micro(items, "reason")
    warning = _micro(items, "warning")
    numeric_items = [item["numeric"] for item in items if item["numeric"] is not None]
    armament_items = [item["armament"] for item in items if item["armament"] is not None]
    fallback_items = [item for item in items if item["fallback"] is not None]
    latencies = [item["latency_s"] for item in items if item["latency_s"] is not None]
    unknown_items = [item for item in items if item["unknown_codes"]]
    status = _status_summary(items)
    numeric = None
    if numeric_items:
        numeric = {
            "items": len(numeric_items),
            "exact_rate": round(statistics.mean(item["exact_rate"] for item in numeric_items), 6),
            "tolerant_rate": round(statistics.mean(item["tolerant_rate"] for item in numeric_items), 6),
        }
    armament = None
    if armament_items:
        armament = {
            "items": len(armament_items),
            "contract_rate": round(sum(1 for item in armament_items if item["contract_ok"]) / len(armament_items), 6),
            "exact_rate": round(sum(1 for item in armament_items if item["exact"]) / len(armament_items), 6),
        }
    fallback = None
    if fallback_items:
        fallback = {
            "items": len(fallback_items),
            "count": sum(1 for item in fallback_items if item["fallback"]),
            "rate": round(sum(1 for item in fallback_items if item["fallback"]) / len(fallback_items), 6),
        }
    latency = None
    if latencies:
        latency = {
            "items": len(latencies),
            "mean_s": round(statistics.mean(latencies), 6),
            "p50_s": _percentile(latencies, 0.50),
            "p95_s": _percentile(latencies, 0.95),
        }
    gates = {
        "schema": schema_rate == 1.0 if schema_rate is not None else None,
        "status": status["accuracy"] == 1.0 if status["accuracy"] is not None else None,
        "reason": reason["f1"] == 1.0 if reason["items"] else None,
        "warning": warning["f1"] == 1.0 if warning["items"] else None,
        "numeric": numeric["tolerant_rate"] == 1.0 if numeric else None,
        "armament": armament["contract_rate"] == 1.0 if armament else None,
        "unknown_codes": not unknown_items,
    }
    applicable_gates = [value for value in gates.values() if value is not None]
    return {
        "score_version": "navspec.eval_score.v1",
        "total": total,
        "schema": {"rate": round(schema_rate, 6) if schema_rate is not None else None},
        "status": status,
        "reason": reason,
        "warning": warning,
        "numeric": numeric,
        "armament": armament,
        "fallback": fallback,
        "latency": latency,
        "unknown_codes": {
            "items": len(unknown_items),
            "rate": round(len(unknown_items) / total, 6) if total else None,
            "codes": sorted({code for item in unknown_items for code in item["unknown_codes"]}),
        },
        "gates": gates,
        "overall_passed": bool(applicable_gates) and all(applicable_gates),
        "metadata": metadata or {},
    }


def _summary_row(summary: dict[str, Any]) -> dict[str, Any]:
    metadata = summary.get("metadata") or {}
    return {
        "run_id": metadata.get("run_id", ""),
        "config": metadata.get("config", "default"),
        "input_sha256": metadata.get("input_sha256", ""),
        "contract_sha256": (metadata.get("contract") or {}).get("sha256", ""),
        "total": summary.get("total", 0),
        "schema_rate": (summary.get("schema") or {}).get("rate"),
        "status_accuracy": (summary.get("status") or {}).get("accuracy"),
        "status_balanced_accuracy": (summary.get("status") or {}).get("balanced_accuracy"),
        "impossible_f1": (summary.get("status") or {}).get("impossible_f1"),
        "reason_micro_f1": (summary.get("reason") or {}).get("f1"),
        "warning_micro_f1": (summary.get("warning") or {}).get("f1"),
        "numeric_tolerant_rate": (summary.get("numeric") or {}).get("tolerant_rate"),
        "armament_contract_rate": (summary.get("armament") or {}).get("contract_rate"),
        "fallback_rate": (summary.get("fallback") or {}).get("rate"),
        "latency_p50_s": (summary.get("latency") or {}).get("p50_s"),
        "latency_p95_s": (summary.get("latency") or {}).get("p95_s"),
        "unknown_code_rate": (summary.get("unknown_codes") or {}).get("rate"),
        "overall_passed": summary.get("overall_passed"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_summary(path: Path, summary: dict[str, Any], keys: tuple[str, ...] = ("run_id", "config")) -> list[dict[str, Any]]:
    """summary.csv をキーでupsertし、他runの行を保持する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = _summary_row(summary)
    rows: list[dict[str, Any]] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    incoming_key = tuple(str(incoming.get(key, "")) for key in keys)
    replaced = False
    for index, row in enumerate(rows):
        if tuple(str(row.get(key, "")) for key in keys) == incoming_key:
            rows[index] = {**row, **{key: str(value) if value is not None else "" for key, value in incoming.items()}}
            replaced = True
            break
    if not replaced:
        rows.append({key: str(value) if value is not None else "" for key, value in incoming.items()})
    fieldnames = list(incoming)
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fieldnames} for row in rows)
    temporary.replace(path)
    return rows


def write_score_artifacts(run_dir: Path, summary: dict[str, Any], items: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "per_item.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items),
        encoding="utf-8",
    )
    (run_dir / "score.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# NavSpec evaluation score",
        "",
        f"- total: {summary['total']}",
        f"- overall_passed: {summary['overall_passed']}",
        f"- schema rate: {(summary['schema'] or {}).get('rate')}",
        f"- status accuracy: {(summary['status'] or {}).get('accuracy')}",
        f"- status balanced accuracy: {(summary['status'] or {}).get('balanced_accuracy')}",
        f"- reason micro F1: {(summary['reason'] or {}).get('f1')}",
        f"- warning micro F1: {(summary['warning'] or {}).get('f1')}",
        f"- unknown code rate: {(summary['unknown_codes'] or {}).get('rate')}",
    ]
    (run_dir / "score.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def score_run(run_dir: Path, contract_path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract = load_contract(contract_path)
    rows = load_prediction_rows(run_dir)
    metadata_path = run_dir / "manifest.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {"run_id": run_dir.name}
    items = score_items(rows, contract)
    summary = summarize_scores(items, metadata)
    write_score_artifacts(run_dir, summary, items)
    return summary, items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--summary", type=Path, default=ROOT / "evaluation" / "scores" / "summary.csv")
    parser.add_argument("--no-fail", action="store_true", help="ゲート不合格でも終了コード0にする")
    args = parser.parse_args(argv)
    summary, _ = score_run(args.run_dir, args.contract)
    upsert_summary(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if args.no_fail or summary["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
