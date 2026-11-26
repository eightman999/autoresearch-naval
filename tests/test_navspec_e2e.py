from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _run_process(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_armament_cli_normal_case_is_a_real_process(tmp_path):
    output_path = tmp_path / "armament.json"
    process = _run_process(
        "tools/navspec_armament_pipeline.py",
        "--nation",
        "Japan",
        "--year",
        "1941",
        "--ship-type",
        "battleship",
        "--displacement-t",
        "30000",
        "--beam-m",
        "38",
        "--output",
        str(output_path),
    )

    assert process.returncode == 0, process.stderr
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["schema_version"] == "navspec.pipeline_result.v1"
    assert result["status"] == "ok"
    assert result["detailed_armament"] is not None
    assert result["fallback"]["used"] is False
    assert result["validation_trace"]


def test_two_stage_cli_emits_pipeline_result_from_cached_phases(tmp_path):
    case_id = "e2e_two_stage_normal"
    case = {
        "id": case_id,
        "input": {
            "nation": "Japan",
            "year": 1941,
            "ship_type": "battleship",
            "role": "fleet_battle",
            "constraints": {},
            "displacement_t": 30000,
            "beam_m": 38,
            "request": "",
        },
        "expected_status": "ok",
        "expected_output": {},
    }
    cases_path = tmp_path / "cases.jsonl"
    stage2_cache = tmp_path / "stage2.jsonl"
    numeric_cache = tmp_path / "numeric.jsonl"
    report_path = tmp_path / "report.json"
    trace_path = tmp_path / "pipeline.jsonl"
    cases_path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    stage2_cache.write_text(
        json.dumps(
            {
                "id": case_id,
                "raw_text": "{\"status\":\"ok\"}",
                "parsed": {"status": "ok", "design_concept": "cached stage2"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    numeric_cache.write_text(
        json.dumps(
            {"id": case_id, "raw_text": "", "parsed": {"spec": {}}},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    process = _run_process(
        "tools/navspec_two_stage_pipeline.py",
        "--cases",
        str(cases_path),
        "--stage2-cache",
        str(stage2_cache),
        "--numeric-cache",
        str(numeric_cache),
        "--with-armament",
        "--output-format",
        "pipeline-result",
        "--json-report",
        str(report_path),
        "--pipeline-result-output",
        str(trace_path),
    )

    assert process.returncode == 0, process.stderr + process.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["armament_enabled"] is True
    assert len(report["results"]) == 1
    assert report["results"][0]["schema_version"] == "navspec.pipeline_result.v1"
    assert report["results"][0]["status"] == "ok"
    trace_rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(trace_rows) == 1
    assert trace_rows[0]["schema_version"] == "navspec.pipeline_result.v1"


def test_gui_smoke_runs_normal_impossible_and_warning_in_a_real_process():
    process = _run_process("tools/navspec_gui.py", "--smoke", "all")

    assert process.returncode == 0, process.stderr
    results = {item["case"]: item for item in json.loads(process.stdout)}
    assert set(results) == {"normal", "impossible", "warning"}
    assert results["normal"]["status"] == "ok"
    assert results["normal"]["contract_errors"] == []
    assert results["impossible"]["status"] == "impossible"
    assert results["impossible"]["reason_codes"]
    assert results["impossible"]["trace_count"] >= 1
    assert results["warning"]["status"] == "ok"
    assert results["warning"]["warning_codes"]
    assert all(item["contract_errors"] == [] for item in results.values())
