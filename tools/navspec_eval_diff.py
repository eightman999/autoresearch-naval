"""評価run間のitem単位diffを生成する。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from tools.navspec_eval_run import canonical_json, read_jsonl
except ImportError:  # direct execution from tools/
    from navspec_eval_run import canonical_json, read_jsonl

PER_ITEM_NAME = "per_item.jsonl"


def load_items(source: Path) -> dict[str, dict[str, Any]]:
    path = source / PER_ITEM_NAME if source.is_dir() else source
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value.get("items", value.get("results", [])) if isinstance(value, dict) else value
    else:
        rows = read_jsonl(path)
    if not isinstance(rows, list):
        raise ValueError(f"{path}: item結果の配列がありません")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"{path}: id付きのitem結果が必要です")
        if row["id"] in result:
            raise ValueError(f"{path}: id {row['id']!r} が重複しています")
        result[row["id"]] = row
    return result


def _metric_vector(item: dict[str, Any]) -> tuple[int, ...]:
    numeric = item.get("numeric")
    armament = item.get("armament")
    values: list[int] = []
    for value in (
        item.get("schema_ok"),
        item.get("status_match"),
        (item.get("reason") or {}).get("exact"),
        (item.get("warning") or {}).get("exact"),
        numeric.get("tolerant") if isinstance(numeric, dict) else None,
        armament.get("contract_ok") if isinstance(armament, dict) else None,
        armament.get("exact") if isinstance(armament, dict) else None,
        not bool(item.get("unknown_codes")) if item.get("unknown_codes") is not None else None,
        not bool(item.get("fallback")) if item.get("fallback") is not None else None,
    ):
        if value is not None:
            values.append(int(bool(value)))
    return tuple(values)


def item_quality(item: dict[str, Any]) -> float:
    vector = _metric_vector(item)
    return sum(vector) / len(vector) if vector else 0.0


def _changed_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    if before is None:
        return ["added"]
    if after is None:
        return ["removed"]
    fields = set(before) | set(after)
    return sorted(field for field in fields if before.get(field) != after.get(field))


def diff_items(
    baseline: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """baseline/candidateをIDで突合し、improved/regressed/unchangedを返す。"""

    output: list[dict[str, Any]] = []
    for item_id in sorted(set(baseline) | set(candidate)):
        before = baseline.get(item_id)
        after = candidate.get(item_id)
        before_vector = _metric_vector(before) if before else tuple()
        after_vector = _metric_vector(after) if after else tuple()
        if before is None:
            classification = "improved"
        elif after is None:
            classification = "regressed"
        elif after_vector > before_vector:
            classification = "improved"
        elif after_vector < before_vector:
            classification = "regressed"
        else:
            classification = "unchanged"
        output.append({
            "id": item_id,
            "case_group_id": (after or before or {}).get("case_group_id"),
            "classification": classification,
            "baseline_quality": round(item_quality(before), 6) if before else None,
            "candidate_quality": round(item_quality(after), 6) if after else None,
            "changed_fields": _changed_fields(before, after),
            "baseline": before,
            "candidate": after,
        })
    return output


def summarize_diff(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["classification"] for item in items)
    return {
        "diff_version": "navspec.eval_diff.v1",
        "total": len(items),
        "improved": counts.get("improved", 0),
        "regressed": counts.get("regressed", 0),
        "unchanged": counts.get("unchanged", 0),
        "regression_free": counts.get("regressed", 0) == 0,
    }


def write_diff(output_dir: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_diff(items)
    (output_dir / "diff.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items),
        encoding="utf-8",
    )
    (output_dir / "diff.json").write_text(
        json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# NavSpec evaluation diff",
        "",
        f"- total: {summary['total']}",
        f"- improved: {summary['improved']}",
        f"- regressed: {summary['regressed']}",
        f"- unchanged: {summary['unchanged']}",
        f"- regression_free: {summary['regression_free']}",
        "",
        "| id | classification | changed fields |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {item['id']} | {item['classification']} | {', '.join(item['changed_fields']) or '-'} |"
        for item in items
    )
    (output_dir / "diff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = write_diff(args.output_dir, diff_items(load_items(args.baseline), load_items(args.candidate)))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
