"""Phase 1 統一評価基盤の決定性・再開・採点・diffテスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.navspec_eval_diff import diff_items
from tools.navspec_eval_run import parse_ids, run_evaluation
from tools.navspec_eval_score import load_contract, score_items, summarize_scores, upsert_summary


def _output(status: str = "ok", reason: list[str] | None = None) -> dict:
    return {
        "schema_version": "navspec.lora_candidate.v1",
        "status": status,
        "spec": {"nation": "Japan"} if status == "ok" else None,
        "reason": reason or [],
        "suggested_revision": {},
        "design_concept": "test",
        "confidence": 0.9,
    }


def _rows(count: int = 3) -> list[dict]:
    return [
        {
            "id": f"case_{index}",
            "nation": "Japan",
            "year": 1938,
            "ship_type": "battleship",
            "role": "fleet_battle",
            "constraints": {},
            "request": f"case {index}",
            "expected_status": "ok",
            "expected_output": _output(),
        }
        for index in range(count)
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_same_inputs_have_same_hash_and_group_id(tmp_path: Path) -> None:
    eval_set = tmp_path / "eval.jsonl"
    _write_jsonl(eval_set, _rows(2))
    first = run_evaluation(eval_set, tmp_path / "run-a", config={"name": "smoke"})
    second = run_evaluation(eval_set, tmp_path / "run-b", config={"name": "smoke"})

    assert first["input_sha256"] == second["input_sha256"]
    first_rows = [json.loads(line) for line in (tmp_path / "run-a" / "predictions.jsonl").read_text().splitlines()]
    second_rows = [json.loads(line) for line in (tmp_path / "run-b" / "predictions.jsonl").read_text().splitlines()]
    assert [row["case_group_id"] for row in first_rows] == [row["case_group_id"] for row in second_rows]


def test_resume_skips_existing_ids_without_duplicate_rows(tmp_path: Path) -> None:
    eval_set = tmp_path / "eval.jsonl"
    _write_jsonl(eval_set, _rows(3))
    calls: list[str] = []

    def fail_after_two(row: dict, _config: dict):
        calls.append(row["id"])
        if len(calls) == 3:
            raise RuntimeError("simulated interruption")
        return row["expected_output"]

    with pytest.raises(RuntimeError, match="simulated interruption"):
        run_evaluation(eval_set, tmp_path / "run", runner=fail_after_two)

    resumed_calls: list[str] = []

    def resume_runner(row: dict, _config: dict):
        resumed_calls.append(row["id"])
        return row["expected_output"]

    metadata = run_evaluation(eval_set, tmp_path / "run", runner=resume_runner)
    rows = (tmp_path / "run" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3
    assert len({json.loads(row)["id"] for row in rows}) == 3
    assert resumed_calls == ["case_2"]
    assert metadata["status"] == "complete"


def test_summary_upsert_keeps_other_runs(tmp_path: Path) -> None:
    path = tmp_path / "summary.csv"
    first = summarize_scores([], {"run_id": "run-a", "config": "baseline"})
    second = summarize_scores([], {"run_id": "run-b", "config": "candidate"})
    upsert_summary(path, first)
    upsert_summary(path, second)
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3
    assert "run-a" in path.read_text(encoding="utf-8")
    assert "run-b" in path.read_text(encoding="utf-8")


def test_item_diff_classifies_improvement_regression_and_unchanged() -> None:
    def item(ok: bool) -> dict:
        return {
            "schema_ok": ok,
            "status_match": ok,
            "reason": {"exact": ok},
            "warning": {"exact": True},
            "unknown_codes": [],
            "fallback": False,
        }

    result = diff_items({"improved": item(False), "regressed": item(True), "same": item(True)},
                        {"improved": item(True), "regressed": item(False), "same": item(True)})
    by_id = {row["id"]: row["classification"] for row in result}
    assert by_id == {"improved": "improved", "regressed": "regressed", "same": "unchanged"}


def test_unknown_code_is_an_independent_fail_axis() -> None:
    contract = load_contract()
    row = {
        "id": "unknown",
        "expected_status": "ok",
        "expected_output": _output(),
        "prediction": _output(reason=["armament.not_a_real_code"]),
    }
    items = score_items([row], contract)
    summary = summarize_scores(items)
    assert items[0]["unknown_codes"] == ["armament.not_a_real_code"]
    assert summary["unknown_codes"]["rate"] == 1.0
    assert summary["gates"]["unknown_codes"] is False
    assert summary["overall_passed"] is False


def test_pipeline_fallback_envelope_uses_used_flag() -> None:
    contract = load_contract()
    pipeline = {
        "schema_version": "navspec.pipeline_result.v1",
        "status": "ok",
        "ship_spec": {},
        "detailed_armament": None,
        "reason": [],
        "warnings": [],
        "validation_trace": [],
        "provenance": {},
        "fallback": {"used": False, "stage": None, "reason": None, "message": None},
    }
    summary = summarize_scores(score_items([
        {"id": "pipeline", "expected_status": "ok", "expected_output": pipeline, "prediction": pipeline},
    ], contract))
    assert summary["fallback"] == {"items": 1, "count": 0, "rate": 0.0}


def test_parse_ids_accepts_comma_and_whitespace_delimiters() -> None:
    assert parse_ids(["case-a case-b,case-c"]) == {"case-a", "case-b", "case-c"}
