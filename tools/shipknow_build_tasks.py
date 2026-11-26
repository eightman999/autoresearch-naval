"""records.jsonl + splits.json -> multi-task SFT data (messages format) for shipknow v0.

    .venv/bin/python tools/shipknow_build_tasks.py

Three tasks, one model (all outputs are JSON only):

  T1  ship type          spec (no names / raw TYPE / history)      -> {"ship_type","ship_type_fine"}
  T2  weapons & sensors  type+year+country+hull numbers (no armament) -> {"weapons":[...],"sensors":[...]}
  T3  missing numbers    spec with 1-3 numeric fields hidden        -> {"field": value, ...}

Per ship class: T1 x2 (full / partial inputs), T2 x2 (full / partial), T3 x2 (k=1..3 hidden fields)
=> at most 6 examples.  Names, class names, source_url, Naval_service and Project_history are
never put into any prompt.

Writes data/navspec/shipknow_v0/{train,valid,test}.jsonl and manifest.json.

v0_2 inputs (only when the records carry them, so v0 / v0_1 regenerate unchanged): ``year_in_navy`` (shown only when it differs
from ``year``), ``displacement_standard_t``, ``armour_belt_mm`` / ``armour_deck_mm``.  They are visible inputs of T1/T2/T3;
the T3 targets (``T3_FIELDS``) and the T2 targets (weapons / sensors) are unchanged.  They are dropped from partial views with
their own rng stream (``rng_new``), so the existing fields draw exactly the same random numbers as before.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_types as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "navspec" / "shipknow_v0"
DEFAULT_TOKENIZER = Path.home() / ".cache/huggingface/hub/models--Qwen--Qwen2.5-0.5B-Instruct"

NUMERIC_FIELDS = [
    "displacement_normal_t", "displacement_full_t", "length_m", "beam_m", "draught_m",
    "shafts", "power_hp", "speed_kn", "endurance_nm", "complement",
]
T3_FIELDS = ["displacement_normal_t", "displacement_full_t", "length_m", "beam_m", "draught_m", "speed_kn", "power_hp", "complement"]
KIND_ORDER = ["gun", "ciws", "mg", "torpedo_tube", "ssm", "sam", "ballistic_missile", "asw_rocket", "rocket_launcher",
              "grenade_launcher", "depth_charge", "mine", "minesweeping_gear", "aircraft_helicopter", "aviation_facility", "small_arms"]
WEAPON_KINDS = KIND_ORDER
MAX_WEAPON_ITEMS = 14

# ---------------------------------------------------------------------------
# rounding (also applied to the inputs so the model sees the same resolution it must output)
# ---------------------------------------------------------------------------
ROUNDING_RULES = {
    "displacement_*_t": "1 t (<50 t), 5 t (<500 t), 10 t (>=500 t)",
    "length_m": "0.1 m (<100 m), 0.5 m (>=100 m)",
    "beam_m": "0.1 m",
    "draught_m": "0.05 m (<3 m), 0.1 m (>=3 m)",
    "speed_kn": "0.5 kn",
    "power_hp": "10 hp (<1000), 100 hp (<20000), 500 hp (>=20000)",
    "complement": "1 (<50), 5 (<500), 10 (>=500)",
    "endurance_nm": "50 nm (<1000), 100 nm (>=1000)",
    "shafts": "1",
    "armour_belt_mm / armour_deck_mm": "1 mm (maximum thickness over the belt / deck entries)",
    "year_in_navy": "shown only when it differs from year",
}


def round_step(field: str, v: float) -> float:
    if field.startswith("displacement"):
        return 1 if v < 50 else (5 if v < 500 else 10)
    if field == "length_m":
        return 0.1 if v < 100 else 0.5
    if field == "beam_m":
        return 0.1
    if field == "draught_m":
        return 0.05 if v < 3 else 0.1
    if field == "speed_kn":
        return 0.5
    if field == "power_hp":
        return 10 if v < 1000 else (100 if v < 20000 else 500)
    if field == "complement":
        return 1 if v < 50 else (5 if v < 500 else 10)
    if field == "endurance_nm":
        return 50 if v < 1000 else 100
    return 1


def rnd(field: str, v: float) -> float | int:
    step = round_step(field, v)
    out = max(step, round(v / step) * step)
    out = round(out, 2)
    return int(out) if float(out).is_integer() else out


def num(x: float) -> float | int:
    x = round(float(x), 1)
    return int(x) if x.is_integer() else x


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------
COARSE = T.COARSE_TYPES
WEAPON_NOTE = "weapons: n=mounts, per=barrels/tubes per mount (omitted if 1), cal=caliber in mm."
# fields added in v0_2 (see module docstring) and the sentence that explains them to the model
NEW_KEYS = ("year_in_navy", "displacement_standard_t", "armour_belt_mm", "armour_deck_mm")
DROP_P_NEW = {"year_in_navy": 0.15}
YEAR_NOTE = (
    " year = design/build era; year_in_navy (only if different) = year it entered this navy (acquisition year for transfers); "
    "armour_*_mm = max thickness."
)
SYSTEM = {
    "T1": (
        "Task T1 (ship type). From the partial specification of a warship class, reply with JSON only: "
        "{\"ship_type\": <" + "|".join(COARSE) + ">, \"ship_type_fine\": <singular type slug, e.g. missile-destroyer>}. "
        + WEAPON_NOTE
    ),
    "T2": (
        "Task T2 (armament and sensors). Armament and sensors are hidden: infer them from the type, year, country and hull numbers. "
        "Reply with JSON only: {\"weapons\": [{\"kind\": <" + "|".join(WEAPON_KINDS) + ">, \"n\": mounts, \"per\": barrels per mount (omit if 1), \"cal\": mm (if any)}], "
        "\"sensors\": [<sorted kinds of air_search_radar|surface_radar|fire_control_radar|navigation_radar|radar|sonar|esm_ecm|decoy|eo_director|combat_system>]}. "
        "Order weapons by kind, then descending caliber. Output only the keys listed in \"want\"."
    ),
    "T3": (
        "Task T3 (missing numbers). Estimate the fields listed in \"predict\" from the other fields of the warship class. "
        "Reply with JSON only, exactly those fields as numbers (t, m, hp, kn, persons). " + WEAPON_NOTE
    ),
}


def jdump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------
def compact_weapons(weapons: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if weapons is None:
        return None
    merged: dict[tuple, int] = {}
    for w in weapons:
        if w["kind"] not in KIND_ORDER:
            continue
        per = w.get("per_mount") or 1
        cal = num(w["caliber_mm"]) if w.get("caliber_mm") else None
        n = w.get("count")
        if n is None:  # count unknown / not applicable (small arms, sweep gear, "N guns" without count ...)
            n = 0 if w["kind"] in ("mine", "minesweeping_gear", "small_arms", "aviation_facility") else 1
        key = (w["kind"], per, cal)
        merged[key] = merged.get(key, 0) + int(n)
    items = []
    for (kind, per, cal), n in merged.items():
        d: dict[str, Any] = {"kind": kind}
        if n:
            d["n"] = n
        if per and per > 1:
            d["per"] = per
        if cal is not None:
            d["cal"] = cal
        items.append((KIND_ORDER.index(kind), -(cal or 0), d))
    items.sort(key=lambda t: (t[0], t[1], t[2].get("n", 0)))
    return [d for _, _, d in items]


def system_for(rec: dict[str, Any], task: str) -> str:
    """System prompt of a record's examples: the v0_2 one (explains the new fields) when the record carries ``year_in_navy``."""
    return SYSTEM[task] + (YEAR_NOTE if "year_in_navy" in rec else "")


def full_view(rec: dict[str, Any]) -> dict[str, Any]:
    """All input fields available for a record (rounded, no names / no leaking text)."""
    v: dict[str, Any] = {}
    if rec.get("year"):
        v["year"] = rec["year"]
    if rec.get("country"):
        v["country"] = rec["country"].replace("-", " ")
    for f in NUMERIC_FIELDS:
        x = rec.get(f)
        if x is not None:
            v[f] = rnd(f, x)
    if rec.get("propulsion"):
        v["propulsion"] = rec["propulsion"]
    w = compact_weapons(rec.get("weapons"))
    if w is not None and rec.get("armament_parse_ok") and len(w) <= MAX_WEAPON_ITEMS:
        v["weapons"] = w
    if rec.get("sensors_known") and rec.get("sensor_kinds") is not None:
        v["sensors"] = rec["sensor_kinds"]
    # v0_2 fields (absent in older records)
    if rec.get("year_in_navy") and rec.get("year") and rec["year_in_navy"] != rec["year"]:
        v["year_in_navy"] = rec["year_in_navy"]
    if rec.get("displacement_standard_t") is not None:
        v["displacement_standard_t"] = rnd("displacement_standard_t", rec["displacement_standard_t"])
    for f in ("armour_belt_mm", "armour_deck_mm"):
        if rec.get(f) is not None:
            v[f] = int(round(rec[f]))
    return v


INFORMATIVE = [f for f in NUMERIC_FIELDS] + ["propulsion", "weapons", "sensors"]
DROP_P = {"year": 0.15, "country": 0.10}


def partial_view(view: dict[str, Any], rng: random.Random, p: float = 0.45, protect: set[str] | None = None,
                 rng_new: random.Random | None = None) -> dict[str, Any]:
    """Drop fields at random.  The v0_2 fields (``NEW_KEYS``) are handled afterwards with ``rng_new`` (kept when None) so the
    older fields consume ``rng`` exactly as in v0 / v0_1."""
    new_part = {k: v for k, v in view.items() if k in NEW_KEYS}
    view = {k: v for k, v in view.items() if k not in NEW_KEYS}
    out = _partial_old(view, rng, p, protect or set())
    for k, v in new_part.items():
        if rng_new is None or rng_new.random() >= DROP_P_NEW.get(k, p):
            out[k] = v
    return out


def _partial_old(view: dict[str, Any], rng: random.Random, p: float, protect: set[str]) -> dict[str, Any]:
    out = dict(view)
    dropped = []
    for k in list(view):
        if k in protect:
            continue
        if rng.random() < DROP_P.get(k, p):
            dropped.append(k)
            del out[k]
    inf = [k for k in out if k in INFORMATIVE]
    if len(inf) < 2:  # keep at least two informative fields
        cand = [k for k in dropped if k in INFORMATIVE]
        rng.shuffle(cand)
        for k in cand[: 2 - len(inf)]:
            out[k] = view[k]
    if out == view:
        cand = [k for k in out if k in INFORMATIVE and k not in protect]
        if len(cand) > 2:
            del out[rng.choice(cand)]
    return out


def sub_rng(seed: int, *parts: str) -> random.Random:
    h = hashlib.sha256((str(seed) + ":" + ":".join(parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def n_informative(view: dict[str, Any]) -> int:
    return sum(1 for k in view if k in INFORMATIVE)


# ---------------------------------------------------------------------------
# example builders
# ---------------------------------------------------------------------------
def msgs(task: str, user: dict[str, Any], assistant: dict[str, Any], system: str | None = None) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system or SYSTEM[task]},
        {"role": "user", "content": jdump(user)},
        {"role": "assistant", "content": jdump(assistant)},
    ]


def build_t1(rec: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    if rec["ship_type"] == T.UNKNOWN or not rec.get("ship_type_fine"):
        return []
    fv = full_view(rec)
    if n_informative(fv) < 2:
        return []
    target = {"ship_type": rec["ship_type"], "ship_type_fine": rec["ship_type_fine"]}
    out = []
    pv = partial_view(fv, sub_rng(seed, rec["id"], "T1"), rng_new=sub_rng(seed, rec["id"], "T1", "new"))
    for variant, view in (("full", fv), ("partial", pv)):
        out.append({"id": f"{rec['id']}:T1:{variant}", "task": "T1", "variant": variant, "row": rec["id"],
                    "messages": msgs("T1", view, target, system_for(rec, "T1"))})
    return out


def sensors_target(rec: dict[str, Any]) -> list[str]:
    return list(rec.get("sensor_kinds") or [])


def build_t2(rec: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    if rec["ship_type"] == T.UNKNOWN or not rec.get("ship_type_fine"):
        return []
    weapons = compact_weapons(rec.get("weapons"))
    if weapons is None or not rec.get("armament_parse_ok") or len(weapons) > MAX_WEAPON_ITEMS:
        return []
    base = full_view(rec)
    base.pop("weapons", None)
    base.pop("sensors", None)
    base["ship_type"] = rec["ship_type"]
    base["ship_type_fine"] = rec["ship_type_fine"]
    known = bool(rec.get("sensors_known"))
    want = ["weapons", "sensors"] if known else ["weapons"]
    target: dict[str, Any] = {"weapons": weapons}
    if known:
        target["sensors"] = sensors_target(rec)
    out = []
    rng = sub_rng(seed, rec["id"], "T2")
    rng_new = sub_rng(seed, rec["id"], "T2", "new")
    for variant in ("full", "partial"):
        view = dict(base)
        if variant == "partial":
            view = partial_view(view, rng, p=0.4, protect={"ship_type", "year"}, rng_new=rng_new)
            if rng.random() < 0.5:
                view.pop("ship_type_fine", None)
        view["want"] = want
        out.append({"id": f"{rec['id']}:T2:{variant}", "task": "T2", "variant": variant, "row": rec["id"], "messages": msgs("T2", view, target, system_for(rec, "T2")),
                    "meta": {"sensors_known": known}})
    return out


def build_t3(rec: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    avail = [f for f in T3_FIELDS if rec.get(f) is not None]
    if len(avail) < 3:
        return []
    out = []
    base = full_view(rec)
    if rec["ship_type"] != T.UNKNOWN and rec.get("ship_type_fine"):
        base["ship_type"] = rec["ship_type"]
        base["ship_type_fine"] = rec["ship_type_fine"]
    base.pop("sensors", None)
    rng = sub_rng(seed, rec["id"], "T3")
    used: set[tuple[str, ...]] = set()
    for i in range(2):
        k = rng.choices([1, 2, 3], weights=[0.5, 0.3, 0.2])[0]
        k = min(k, len(avail) - 2)  # keep >= 2 numeric fields visible
        for _ in range(10):
            hidden = tuple(sorted(rng.sample(avail, k)))
            if hidden not in used:
                break
        used.add(hidden)
        view = {kk: vv for kk, vv in base.items() if kk not in hidden}
        # the "full displacement" of a hidden normal displacement stays visible on purpose (other specs are fair game)
        if rng.random() < 0.3:
            view.pop("ship_type_fine", None)
        if rng.random() < 0.3:
            view.pop("weapons", None)
        if sum(1 for kk in view if kk in NUMERIC_FIELDS and kk != "shafts") < 1:
            continue
        view["predict"] = list(hidden)
        target = {f: rnd(f, rec[f]) for f in hidden}
        out.append({"id": f"{rec['id']}:T3:{i}", "task": "T3", "variant": f"k{k}", "row": rec["id"], "messages": msgs("T3", view, target, system_for(rec, "T3")),
                    "meta": {"hidden": list(hidden)}})
    return out


# ---------------------------------------------------------------------------
# tokenizer-based stats
# ---------------------------------------------------------------------------
def load_tokenizer(path: Path | None):
    try:
        from tokenizers import Tokenizer
    except Exception:
        return None, "chars/3.2 estimate (tokenizers not installed)"
    cand: list[Path] = []
    if path:
        if path.is_file():
            cand.append(path)
        else:
            cand += sorted(path.glob("snapshots/*/tokenizer.json")) + sorted(path.glob("tokenizer.json"))
    for c in cand:
        try:
            return Tokenizer.from_file(str(c)), f"tokenizers:{c.parent.parent.parent.name if 'snapshots' in str(c) else c.parent.name + '/' + c.name} (token counts depend on this file: Qwen3.5-0.8B tokenizer.json = exact, Qwen2.5 = proxy)"
        except Exception:
            continue
    return None, "chars/3.2 estimate (no local tokenizer.json)"


def count_tokens(tok, ex: dict[str, Any]) -> tuple[int, int]:
    """(total tokens, assistant tokens) with ChatML overhead (+6 for the empty think block of Qwen3.5 non-thinking)."""
    m = ex["messages"]
    text = "".join(f"<|im_start|>{x['role']}\n{x['content']}<|im_end|>\n" for x in m)
    ans = m[-1]["content"] + "<|im_end|>"
    if tok is None:
        return int(len(text) / 3.2) + 6, int(len(ans) / 3.2)
    return len(tok.encode(text).ids) + 6, len(tok.encode(ans).ids)


def pct(xs: list[int], p: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--tokenizer", type=Path, default=DEFAULT_TOKENIZER, help="tokenizer.json or a HF cache model dir (default: local Qwen2.5 proxy)")
    ap.add_argument("--max-train-per-task", type=int, default=None, help="subsample train examples per task (seeded)")
    ap.add_argument("--tok-per-sec", type=float, default=3500.0, help="training throughput used for the time estimate (RTX 3060: ~3-4k tok/s)")
    args = ap.parse_args()

    recs = [json.loads(line) for line in (args.dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    splits = json.loads((args.dir / "splits.json").read_text(encoding="utf-8"))
    assign, group_of = splits["assignments"], splits["group_of"]
    tok, tok_note = load_tokenizer(args.tokenizer)

    examples: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    for rec in recs:
        sp = assign[rec["id"]]
        for ex in build_t1(rec, args.seed) + build_t2(rec, args.seed) + build_t3(rec, args.seed):
            ex["group"] = group_of[rec["id"]]
            ex.setdefault("meta", {})
            examples[sp].append(ex)

    full_counts = {sp: dict(collections.Counter(e["task"] for e in exs)) for sp, exs in examples.items()}
    if args.max_train_per_task:
        rng = random.Random(args.seed)
        kept = []
        by_task: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for e in examples["train"]:
            by_task[e["task"]].append(e)
        for t in sorted(by_task):
            lst = by_task[t]
            rng.shuffle(lst)
            kept += lst[: args.max_train_per_task]
        examples["train"] = sorted(kept, key=lambda e: e["id"])

    stats: dict[str, Any] = {}
    for sp, exs in examples.items():
        with (args.dir / f"{sp}.jsonl").open("w", encoding="utf-8") as fh:
            for e in exs:
                fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        st: dict[str, Any] = {}
        for task in ("T1", "T2", "T3"):
            sel = [e for e in exs if e["task"] == task]
            toks = [count_tokens(tok, e) for e in sel]
            tt = [a for a, _ in toks]
            at = [b for _, b in toks]
            st[task] = {
                "examples": len(sel),
                "tokens_total": sum(tt),
                "tokens_avg": round(statistics.mean(tt), 1) if tt else 0,
                "tokens_p95": pct(tt, 0.95),
                "tokens_max": max(tt) if tt else 0,
                "assistant_tokens_avg": round(statistics.mean(at), 1) if at else 0,
                "assistant_tokens_max": max(at) if at else 0,
                "variants": dict(collections.Counter(e["variant"] for e in sel)),
            }
        st["all"] = {"examples": len(exs), "tokens_total": sum(st[t]["tokens_total"] for t in ("T1", "T2", "T3"))}
        stats[sp] = st
    train_tokens = stats["train"]["all"]["tokens_total"]
    est = {
        "train_tokens_per_epoch": train_tokens,
        "assumed_tok_per_sec": args.tok_per_sec,
        "epoch_minutes_at_3k": round(train_tokens / 3000 / 60, 1),
        "epoch_minutes_at_3.5k": round(train_tokens / 3500 / 60, 1),
        "epoch_minutes_at_4k": round(train_tokens / 4000 / 60, 1),
        "epoch_minutes_at_assumed_rate": round(train_tokens / args.tok_per_sec / 60, 1),
        "note": "tokens are ChatML + 6 (Qwen3.5 empty think block) counted with the tokenizer named in \"tokenizer\" (v0: Qwen2.5 proxy; v0_1: Qwen3.5-0.8B tokenizer.json, exact).",
    }
    size_mb = round(sum((args.dir / f).stat().st_size for f in ("records.jsonl", "splits.json", "type_map.json", "train.jsonl", "valid.jsonl", "test.jsonl")) / 1e6, 1)
    label_dist = {sp: dict(collections.Counter(json.loads(e["messages"][2]["content"])["ship_type"] for e in exs if e["task"] == "T1" and e["variant"] == "full").most_common())
                  for sp, exs in examples.items()}
    manifest = {
        "name": args.dir.name,
        "seed": args.seed,
        "source": "nsdb/NSDB.db CLASS (read-only), real ships only",
        "files": {"records": "records.jsonl", "splits": "splits.json", "train": "train.jsonl", "valid": "valid.jsonl", "test": "test.jsonl", "type_map": "type_map.json"},
        "records": len(recs),
        "examples_before_subsample": full_counts,
        "max_train_per_task": args.max_train_per_task,
        "examples": {sp: {t: stats[sp][t]["examples"] for t in ("T1", "T2", "T3")} for sp in stats},
        "token_stats": stats,
        "train_time_estimate": est,
        "tokenizer": tok_note,
        "total_size_mb_incl_records": size_mb,
        "rounding_rules": ROUNDING_RULES,
        "coarse_types": COARSE,
        "weapon_kinds": WEAPON_KINDS,
        "t3_fields": T3_FIELDS,
        "t1_label_distribution_full_variant": label_dist,
        "never_in_prompts": ["ship/class names", "raw TYPE", "source_url", "Naval_service", "Project_history", "ship_type_fine (T1 input)"],
        "new_input_fields": [k for k in NEW_KEYS if any(k in r for r in recs[:50])],
        "system_prompts": {t: system_for(recs[0], t) for t in ("T1", "T2", "T3")},
    }
    (args.dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"examples": manifest["examples"], "train_tokens": train_tokens, "epoch_minutes_at_assumed_rate": est["epoch_minutes_at_assumed_rate"], "size_mb": size_mb}, ensure_ascii=False))
    for sp in ("train", "test"):
        for t in ("T1", "T2", "T3"):
            s = stats[sp][t]
            print(sp, t, {k: s[k] for k in ("examples", "tokens_avg", "tokens_p95", "tokens_max", "assistant_tokens_avg", "assistant_tokens_max")})


if __name__ == "__main__":
    main()
