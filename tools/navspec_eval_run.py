"""再開可能な NavSpec 統一評価 run のオーケストレータ。

このモジュールはモデル固有の推論器を持たず、評価入力の正規化、hash と provenance の
記録、結果 JSONL の追記・再開を担当する。既定 runner は gold 出力をそのまま返す
決定的 smoke 用で、実験時は ``runner_command`` を設定して標準入力/標準出力の JSON
アダプターを差し替えられる。

出力:

* ``manifest.json``: 入力・コード・モデル・adapter・contract の provenance と進捗
* ``predictions.jsonl``: 1行1 item の入力、期待値、予測値、raw output
* ``run_metadata.json``: manifest と同じ provenance の機械可読コピー
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_end_to_end_eval_contract_v1.json"
PREDICTIONS_NAME = "predictions.jsonl"
MANIFEST_NAME = "manifest.json"

Runner = Callable[[dict[str, Any], dict[str, Any]], Any]


def canonical_json(value: Any) -> str:
    """比較・hash 用の決定的 JSON 表現を返す。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_path(path: Path | None) -> str | None:
    """ファイルまたはディレクトリを相対パス込みでhashする。

    存在しない任意のモデル名・adapter名は ``None`` とし、宣言値は provenance 側に残す。
    """

    if path is None or not path.exists():
        return None
    if path.is_file():
        return sha256_bytes(path.read_bytes())
    digest = hashlib.sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        with child.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _resolve_repo_path(value: str | Path | None, *, base: Path = ROOT) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    repo_path = ROOT / path
    if repo_path.exists() or base == ROOT:
        return repo_path
    return base / path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: JSONLを解釈できません: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: 評価行はobjectである必要があります")
            rows.append(value)
    return rows


def load_eval_rows(path: Path) -> list[dict[str, Any]]:
    """JSONL、JSON配列、``{cases|rows|results: [...]}`` を評価行へ展開する。"""

    if path.is_dir():
        candidates = [path / "cases.jsonl", path / "eval.jsonl", path / "rows.jsonl"]
        path = next((candidate for candidate in candidates if candidate.exists()), path)
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)
    value = _read_json(path)
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = next((value[key] for key in ("cases", "rows", "results") if isinstance(value.get(key), list)), None)
        if rows is None:
            raise ValueError(f"{path}: cases/rows/results の配列がありません")
    else:
        raise ValueError(f"{path}: 評価入力はJSON配列またはobjectである必要があります")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path}: 評価行はすべてobjectである必要があります")
    return rows


def evaluation_input(row: dict[str, Any]) -> dict[str, Any]:
    """期待出力や学習用 messages を除いた評価入力を返す。"""

    if isinstance(row.get("input"), dict):
        return row["input"]
    excluded = {
        "expected_output", "expected_status", "expected_reason_codes", "expected_warning_codes",
        "output", "prediction", "predicted_output", "messages", "text", "split",
    }
    preferred = {
        key: row[key]
        for key in (
            "id", "task", "output_schema", "response_format", "nation", "year", "ship_type",
            "role", "constraints", "request", "candidate", "context",
        )
        if key in row
    }
    if preferred:
        return preferred
    return {key: value for key, value in row.items() if key not in excluded}


def _expected_output(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("expected_output")
    if not isinstance(value, dict):
        value = row.get("output")
    return value if isinstance(value, dict) else {}


def _status_from_output(output: Any) -> str | None:
    if not isinstance(output, dict):
        return None
    if isinstance(output.get("pipeline_result"), dict):
        return _status_from_output(output["pipeline_result"])
    value = output.get("status")
    return value if isinstance(value, str) else None


def _stable_id(row: dict[str, Any], input_value: dict[str, Any]) -> str:
    explicit = row.get("id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    return f"case_{sha256_text(canonical_json(input_value))[:16]}"


def case_group_id(row: dict[str, Any], input_value: dict[str, Any]) -> str:
    """同じ設計条件を同じ group へ寄せる canonical group ID を作る。"""

    source = input_value
    constraints = source.get("constraints") or {}
    candidate = source.get("candidate") or row.get("candidate") or {}
    if isinstance(candidate, dict) and isinstance(candidate.get("main_batteries"), list):
        batteries = candidate["main_batteries"]
        candidate = {**candidate, **(batteries[0] if batteries and isinstance(batteries[0], dict) else {})}
    elif isinstance(source.get("armament"), dict):
        armament = source["armament"]
        batteries = armament.get("main_batteries")
        candidate = {**armament, **(batteries[0] if batteries and isinstance(batteries[0], dict) else {})}
    group_value = {
        "nation": source.get("nation"),
        "year": source.get("year"),
        "ship_type": source.get("ship_type"),
        "role": source.get("role"),
        "constraints": constraints,
        "weapon_id": candidate.get("weapon_id") if isinstance(candidate, dict) else None,
        "mount_id": candidate.get("mount_id") if isinstance(candidate, dict) else None,
        "fire_control_id": candidate.get("fire_control_id") if isinstance(candidate, dict) else None,
    }
    return f"case_group_v1_{sha256_text(canonical_json(group_value))[:16]}"


def normalize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for row in rows:
        input_value = evaluation_input(row)
        item_id = _stable_id(row, input_value)
        input_hash = sha256_text(canonical_json(input_value))
        prior_hash = seen.get(item_id)
        if prior_hash is not None and prior_hash != input_hash:
            raise ValueError(f"重複 id {item_id!r} に異なる入力があります")
        if prior_hash is not None:
            raise ValueError(f"評価入力内で id {item_id!r} が重複しています")
        seen[item_id] = input_hash
        expected = _expected_output(row)
        normalized.append({
            "id": item_id,
            "case_group_id": case_group_id(row, input_value),
            "input": input_value,
            "input_hash": input_hash,
            "expected_output": expected,
            "expected_status": row.get("expected_status") or _status_from_output(expected),
            "expected_reason_codes": row.get("expected_reason_codes"),
            "expected_warning_codes": row.get("expected_warning_codes"),
            "source_category": row.get("category"),
        })
    return normalized


def parse_ids(values: Iterable[str] | None) -> set[str] | None:
    if not values:
        return None
    ids: set[str] = set()
    for value in values:
        # シェル展開で複数IDが1引数にまとまる場合もあるため、両形式を許容する。
        ids.update(item for item in value.replace(",", " ").split() if item)
    return ids


def _git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _config_value(config: dict[str, Any], key: str, default: Any = None) -> Any:
    value = config.get(key, default)
    if value is not None:
        return value
    return default


def load_config(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if path is None:
        config: dict[str, Any] = {}
        return config, None
    config = _read_json(path)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: config はobjectである必要があります")
    return config, sha256_path(path)


def _declared_path_hash(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    path = _resolve_repo_path(value) if isinstance(value, str) else None
    return {"declared": value, "sha256": sha256_path(path)}


def build_metadata(
    *,
    config: dict[str, Any],
    config_hash: str | None,
    eval_set: Path,
    input_hash: str,
    selected_count: int,
    requested_limit: int | None,
    requested_ids: set[str] | None,
) -> dict[str, Any]:
    contract_value = config.get("contract")
    contract_path = _resolve_repo_path(contract_value) if isinstance(contract_value, str) else DEFAULT_CONTRACT
    git_status = _git_value(["status", "--porcelain"])
    runner_command = config.get("runner_command")
    if isinstance(runner_command, str):
        runner_command = shlex.split(runner_command)
    return {
        "metadata_version": "navspec.eval_run_metadata.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_value(["rev-parse", "HEAD"]),
        "git_dirty": bool(git_status),
        "git_status_sha256": sha256_text(git_status or ""),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "mlx_version": _version("mlx"),
        "eval_set": str(eval_set),
        "data_sha256": sha256_path(eval_set),
        "input_sha256": input_hash,
        "selected_cases": selected_count,
        "limit": requested_limit,
        "ids": sorted(requested_ids) if requested_ids else None,
        "seed": int(_config_value(config, "seed", 0)),
        "temperature": float(_config_value(config, "temperature", 0.0)),
        "max_tokens": _config_value(config, "max_tokens"),
        "runner": config.get("runner", "golden"),
        "config": config.get("name", config.get("id", "default")),
        "runner_command": runner_command,
        "config_sha256": config_hash,
        "contract": {
            "declared": str(contract_value) if contract_value is not None else str(DEFAULT_CONTRACT),
            "sha256": sha256_path(contract_path),
        },
        "model": _declared_path_hash(config, "model"),
        "adapter": _declared_path_hash(config, "adapter"),
        "code": {
            "runner_module": str(Path(__file__).relative_to(ROOT)),
            "runner_module_sha256": sha256_path(Path(__file__)),
        },
    }


def _atomic_write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_existing_predictions(path: Path) -> tuple[set[str], int]:
    if not path.exists():
        return set(), 0
    processed: set[str] = set()
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: 破損した途中結果です") from exc
            item_id = row.get("id") if isinstance(row, dict) else None
            if not isinstance(item_id, str):
                raise ValueError(f"{path}:{line_number}: id がありません")
            if item_id in processed:
                raise ValueError(f"{path}:{line_number}: id {item_id!r} が重複しています")
            processed.add(item_id)
            count += 1
    return processed, count


def _run_command(row: dict[str, Any], config: dict[str, Any]) -> tuple[Any, str]:
    command = config.get("runner_command")
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError("runner=command には runner_command の文字列または配列が必要です")
    env = dict(os.environ)
    env.update({
        "NAVSPEC_EVAL_ID": row["id"],
        "NAVSPEC_EVAL_INPUT_JSON": canonical_json(row["input"]),
        "NAVSPEC_EVAL_SEED": str(config.get("seed", 0)),
        "NAVSPEC_EVAL_TEMPERATURE": str(config.get("temperature", 0.0)),
    })
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=canonical_json(row["input"]) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=float(config.get("timeout_seconds", 300)),
        check=True,
    )
    stdout = completed.stdout.strip()
    if not stdout:
        raise ValueError(f"runner command が {row['id']} のJSONを返しませんでした")
    try:
        prediction = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"runner command のstdoutがJSONではありません ({row['id']})") from exc
    return prediction, completed.stdout


def default_runner(row: dict[str, Any], config: dict[str, Any]) -> tuple[Any, str | None]:
    runner = config.get("runner", "golden")
    if runner in {"golden", "identity", "expected"}:
        prediction = row["expected_output"]
        return prediction, canonical_json(prediction)
    if runner in {"field", "prediction_field"}:
        field = config.get("prediction_field", "prediction")
        prediction = row.get(field)
        return prediction, canonical_json(prediction)
    if runner == "command":
        return _run_command(row, config)
    raise ValueError(f"未対応の評価runner: {runner!r}")


def _unpack_runner_result(result: Any) -> tuple[Any, str | None]:
    if isinstance(result, tuple) and len(result) == 2:
        return result[0], result[1]
    if isinstance(result, dict) and "prediction" in result and (
        "raw_output" in result or "raw_text" in result
    ):
        return result["prediction"], result.get("raw_output", result.get("raw_text"))
    return result, canonical_json(result)


def run_evaluation(
    eval_set: Path,
    run_dir: Path,
    *,
    config: dict[str, Any] | None = None,
    limit: int | None = None,
    ids: Iterable[str] | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """評価を実行し、途中結果を残しながら再開する。

    ``runner`` はテストおよび既存pipeline adapter用の注入点である。runner未指定時は
    config の ``runner`` を使用する。
    """

    config = dict(config or {})
    eval_set = eval_set.expanduser().resolve()
    run_dir = run_dir.expanduser().resolve()
    source_rows = normalize_rows(load_eval_rows(eval_set))
    requested_ids = set(ids) if ids else None
    if requested_ids is not None:
        unknown = requested_ids - {row["id"] for row in source_rows}
        if unknown:
            raise ValueError(f"評価入力に存在しないid: {sorted(unknown)}")
        source_rows = [row for row in source_rows if row["id"] in requested_ids]
    if limit is not None:
        if limit < 0:
            raise ValueError("limit は0以上である必要があります")
        source_rows = source_rows[:limit]

    input_hash = sha256_text(canonical_json([
        {"id": row["id"], "case_group_id": row["case_group_id"], "input": row["input"]}
        for row in source_rows
    ]))
    config_hash = sha256_text(canonical_json(config))
    metadata = build_metadata(
        config=config,
        config_hash=config_hash,
        eval_set=eval_set,
        input_hash=input_hash,
        selected_count=len(source_rows),
        requested_limit=limit,
        requested_ids=requested_ids,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / MANIFEST_NAME
    predictions_path = run_dir / PREDICTIONS_NAME
    if manifest_path.exists():
        existing_manifest = _read_json(manifest_path)
        if existing_manifest.get("input_sha256") != input_hash:
            raise ValueError("既存runの入力hashが今回の評価入力と一致しません")
        if existing_manifest.get("config_sha256") != config_hash:
            raise ValueError("既存runのconfig hashが今回のconfigと一致しません")

    processed_ids, existing_count = _load_existing_predictions(predictions_path)
    source_ids = {row["id"] for row in source_rows}
    unexpected = processed_ids - source_ids
    if unexpected:
        raise ValueError(f"既存runに今回の入力にないidがあります: {sorted(unexpected)}")

    metadata.update({
        "run_id": run_dir.name,
        "status": "running",
        "processed_cases": existing_count,
        "remaining_cases": len(source_rows) - existing_count,
    })
    _atomic_write_json(manifest_path, metadata)
    _atomic_write_json(run_dir / "run_metadata.json", metadata)

    selected_runner = runner or default_runner
    started = time.perf_counter()
    with predictions_path.open("a", encoding="utf-8") as handle:
        for row in source_rows:
            if row["id"] in processed_ids:
                continue
            item_started = time.perf_counter()
            prediction, raw_output = _unpack_runner_result(selected_runner(row, config))
            record = {
                "id": row["id"],
                "case_group_id": row["case_group_id"],
                "input": row["input"],
                "input_hash": row["input_hash"],
                "expected_output": row["expected_output"],
                "expected_status": row["expected_status"],
                "expected_reason_codes": row["expected_reason_codes"],
                "expected_warning_codes": row["expected_warning_codes"],
                "prediction": prediction,
                "raw_output": raw_output,
                "source_category": row["source_category"],
                "latency_s": round(time.perf_counter() - item_started, 6),
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            processed_ids.add(row["id"])
            metadata["processed_cases"] = len(processed_ids)
            metadata["remaining_cases"] = len(source_rows) - len(processed_ids)
            _atomic_write_json(manifest_path, metadata)
            _atomic_write_json(run_dir / "run_metadata.json", metadata)

    metadata["status"] = "complete"
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    metadata["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    _atomic_write_json(manifest_path, metadata)
    _atomic_write_json(run_dir / "run_metadata.json", metadata)
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="評価runner/config JSON")
    parser.add_argument("--eval-set", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="+", help="idの空白区切りまたはカンマ区切り一覧")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    config, _ = load_config(args.config)
    metadata = run_evaluation(
        args.eval_set,
        args.run_dir,
        config=config,
        limit=args.limit,
        ids=parse_ids(args.ids),
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
