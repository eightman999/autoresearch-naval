"""Leak-free train/valid/test split for the shipknow records (80/10/10 by group).

Rows that describe the same design are merged into one group with union-find, and
whole groups are assigned to a split (stratified by the group's majority coarse
ship type, fixed seed).  Union rules (all recorded in splits.json):

  R1  identical normalised source_url
  R2  same country + same normalised class base name        ("Hatsuharu" 1935 / "Hatsuharu" variants)
  R3  same class base name in different countries when the base name is
      distinctive (>=5 chars, not generic) and the specs are compatible
      (length within 12 %, else displacement within 25 %)   (transferred / licence-built classes)
  R4  identical (length, beam) to 0.1 m for hulls >= 15 m   (same hull under another name)
  R5  identical (displacement, power, speed) triple         (same design, different page)
  R6  same coarse type and displacement within 1.5 %, length within 1.5 %,
      beam within 3 % (all three present)                    (near-twin sister classes / re-issued specs)

``run()`` also verifies, and writes into splits.json, that train/valid/test do not
share class base names, source URLs or R4/R5 fingerprints.

``run_inherit()`` (v0_2): classes that existed in an earlier version (matched by normalised source_url) keep their
earlier split, so that the test sets stay comparable.  Groups are rebuilt with the same union-find rules over the new
records; then

  * a group whose earlier members all sat in one split goes to that split;
  * a group with no earlier member (new classes) is assigned 80/10/10 with the same seeded, type-stratified procedure
    (``assign(..., fixed=...)``: the inherited groups count towards the per-type totals);
  * a group whose earlier members sat in different splits (a new class bridges them) is a *conflict*: it goes to the split
    holding most of its earlier records (ties: test > valid > train), and the minority records are reported as moved.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "navspec" / "shipknow_v0"
RATIOS = (("train", 0.8), ("valid", 0.1), ("test", 0.1))
GENERIC_BASES = {"type", "class", "group", "boats", "craft", "series", "project", "unnamed", "wwi", "wwii", "ship", "ships", "motor", "small", "large"}


class DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[max(ra, rb)] = min(ra, rb)
        return True


def _disp(r: dict[str, Any]) -> float | None:
    return r.get("displacement_normal_t") or r.get("displacement_full_t")


def _compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    la, lb = a.get("length_m"), b.get("length_m")
    if la and lb:
        return abs(la / lb - 1) <= 0.12
    da, db = _disp(a), _disp(b)
    if da and db:
        return abs(da / db - 1) <= 0.25
    return True


def build_groups(records: list[dict[str, Any]]) -> tuple[list[int], dict[str, int]]:
    n = len(records)
    dsu = DSU(n)
    stats: collections.Counter[str] = collections.Counter()

    def bucket(keyfn):
        d: dict[Any, list[int]] = collections.defaultdict(list)
        for i, r in enumerate(records):
            k = keyfn(r)
            if k is not None:
                d[k].append(i)
        return d

    for idxs in bucket(lambda r: r.get("source_url")).values():  # R1
        for j in idxs[1:]:
            stats["R1_url"] += dsu.union(idxs[0], j)
    for idxs in bucket(lambda r: (r["country"], r["class_base"]) if r.get("class_base") else None).values():  # R2
        for j in idxs[1:]:
            stats["R2_country_name"] += dsu.union(idxs[0], j)
    for base, idxs in bucket(lambda r: r["class_base"] if len(r.get("class_base") or "") >= 5 and r["class_base"] not in GENERIC_BASES else None).items():  # R3
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                if records[i]["country"] != records[j]["country"] and _compatible(records[i], records[j]):
                    stats["R3_cross_country_name"] += dsu.union(i, j)
    for idxs in bucket(lambda r: (round(r["length_m"], 1), round(r["beam_m"], 1)) if r.get("length_m") and r.get("beam_m") and r["length_m"] >= 15 else None).values():  # R4
        for j in idxs[1:]:
            stats["R4_length_beam"] += dsu.union(idxs[0], j)
    for idxs in bucket(lambda r: (r["displacement_normal_t"], r["power_hp"], r["speed_kn"]) if r.get("displacement_normal_t") and r.get("power_hp") and r.get("speed_kn") else None).values():  # R5
        for j in idxs[1:]:
            stats["R5_disp_power_speed"] += dsu.union(idxs[0], j)
    by_type: dict[str, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(records):
        if _disp(r) and r.get("length_m") and r.get("beam_m"):
            by_type[r["ship_type"]].append(i)
    for idxs in by_type.values():  # R6 (sliding window over length)
        idxs.sort(key=lambda i: records[i]["length_m"])
        for a, i in enumerate(idxs):
            li = records[i]["length_m"]
            for j in idxs[a + 1:]:
                lj = records[j]["length_m"]
                if lj > li * 1.015:
                    break
                if abs(_disp(records[i]) / _disp(records[j]) - 1) <= 0.015 and abs(records[i]["beam_m"] / records[j]["beam_m"] - 1) <= 0.03:
                    stats["R6_near_twin"] += dsu.union(i, j)
    roots = [dsu.find(i) for i in range(n)]
    return roots, dict(stats)


def assign(records: list[dict[str, Any]], roots: list[int], seed: int, fixed: dict[int, str] | None = None) -> dict[int, str]:
    """Group -> split.  ``fixed`` = {group root: split} for groups whose split is already decided (inherited); they are
    placed first and counted in the per-type totals, and only the remaining groups are drawn."""
    fixed = fixed or {}
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(roots):
        groups[r].append(i)
    by_key: dict[str, list[int]] = collections.defaultdict(list)
    for root, idxs in groups.items():
        cnt = collections.Counter(records[i]["ship_type"] for i in idxs)
        key = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        by_key[key].append(root)
    out: dict[int, str] = {}
    for key in sorted(by_key):
        all_gids = sorted(by_key[key])
        gids = [g for g in all_gids if g not in fixed]
        rng = random.Random(int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()[:12], 16))
        rng.shuffle(gids)
        # biggest groups first so that small ones can fill the deficits precisely
        gids.sort(key=lambda g: -len(groups[g]) if len(groups[g]) > 25 else 0)
        total = sum(len(groups[g]) for g in all_gids)
        have = {s: 0 for s, _ in RATIOS}
        for g in all_gids:
            if g in fixed:
                have[fixed[g]] += len(groups[g])
                for i in groups[g]:
                    out[i] = fixed[g]
        for g in gids:
            sz = len(groups[g])
            deficits = {s: total * ratio - have[s] for s, ratio in RATIOS}
            # prefer the split with the largest deficit; ties -> RATIOS order
            best = max(RATIOS, key=lambda sr: (deficits[sr[0]] - 0.0))[0]
            have[best] += sz
            for i in groups[g]:
                out[i] = best
    return out


def verify(records: list[dict[str, Any]], split_of: dict[int, str]) -> dict[str, Any]:
    by_split: dict[str, list[int]] = collections.defaultdict(list)
    for i, s in split_of.items():
        by_split[s].append(i)

    def keys(idxs, fn):
        return {fn(records[i]) for i in idxs if fn(records[i]) is not None}

    name_cty = lambda r: (r["country"], r["class_base"]) if r.get("class_base") else None  # noqa: E731
    name_only = lambda r: r["class_base"] if len(r.get("class_base") or "") >= 5 and r["class_base"] not in GENERIC_BASES else None  # noqa: E731
    lb = lambda r: (round(r["length_m"], 1), round(r["beam_m"], 1)) if r.get("length_m") and r.get("beam_m") and r["length_m"] >= 15 else None  # noqa: E731
    dps = lambda r: (r["displacement_normal_t"], r["power_hp"], r["speed_kn"]) if r.get("displacement_normal_t") and r.get("power_hp") and r.get("speed_kn") else None  # noqa: E731
    url = lambda r: r.get("source_url")  # noqa: E731
    res: dict[str, Any] = {}
    train = by_split["train"]
    for other in ("valid", "test"):
        o = by_split[other]
        res[f"{other}_vs_train"] = {
            "shared_country_class_base": len(keys(o, name_cty) & keys(train, name_cty)),
            "shared_source_url": len(keys(o, url) & keys(train, url)),
            "shared_length_beam_fingerprint(>=15m)": len(keys(o, lb) & keys(train, lb)),
            "shared_disp_power_speed": len(keys(o, dps) & keys(train, dps)),
            "shared_class_base_any_country(>=5ch)": len(keys(o, name_only) & keys(train, name_only)),
        }
    # same distinctive name across splits: list incompatible-spec ones (not unioned on purpose)
    shared = sorted(keys(by_split["test"], name_only) & keys(train, name_only))
    ex = []
    for b in shared[:8]:
        ti = next(i for i in by_split["test"] if name_only(records[i]) == b)
        tr = next(i for i in train if name_only(records[i]) == b)
        ex.append({"base": b, "test": [records[ti]["country"], records[ti]["ship_type"], _disp(records[ti]), records[ti].get("length_m")],
                   "train": [records[tr]["country"], records[tr]["ship_type"], _disp(records[tr]), records[tr].get("length_m")]})
    res["same_name_incompatible_examples(test vs train)"] = ex
    return res


SPLIT_PRIORITY = {"test": 2, "valid": 1, "train": 0}


def inherit_fixed(records: list[dict[str, Any]], roots: list[int], old_split_by_url: dict[str, str]) -> tuple[dict[int, str], dict[str, Any]]:
    """Group root -> inherited split (majority of the earlier members; ties test > valid > train) and the conflict report."""
    members: dict[int, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(roots):
        members[r].append(i)
    fixed: dict[int, str] = {}
    conflicts: list[dict[str, Any]] = []
    n_new_groups = 0
    moved = collections.Counter()  # (earlier split, new split) -> records that changed split
    for root, idxs in members.items():
        votes = collections.Counter(old_split_by_url[records[i]["source_url"]] for i in idxs if records[i].get("source_url") in old_split_by_url)
        if not votes:
            n_new_groups += 1
            continue
        winner = max(votes, key=lambda sp: (votes[sp], SPLIT_PRIORITY[sp]))
        fixed[root] = winner
        if len(votes) > 1:
            conflicts.append({"group": records[root]["id"], "size": len(idxs), "earlier_votes": dict(votes), "assigned": winner,
                              "example_classes": sorted({f"{records[i]['class_name']} ({records[i]['country']})" for i in idxs})[:6]})
            for sp, n in votes.items():
                if sp != winner:
                    moved[f"{sp}->{winner}"] += n
    rep = {"groups_with_earlier_members": len(fixed), "groups_without_earlier_members(new)": n_new_groups,
           "conflict_groups": len(conflicts), "records_in_conflict_groups": sum(c["size"] for c in conflicts),
           "earlier_records_moved": dict(moved), "conflicts": sorted(conflicts, key=lambda c: -c["size"])}
    return fixed, rep


def run_inherit(out_dir: Path, prev_dir: Path, seed: int = 20261006) -> dict[str, Any]:
    """Split ``out_dir/records.jsonl`` keeping the earlier split (``prev_dir``) of every class that already existed."""
    records = [json.loads(line) for line in (out_dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    prev_recs = [json.loads(line) for line in (prev_dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    prev_assign = json.loads((prev_dir / "splits.json").read_text(encoding="utf-8"))["assignments"]
    old_by_url: dict[str, str] = {}
    for r in prev_recs:
        if r.get("source_url"):
            old_by_url.setdefault(r["source_url"], prev_assign[r["id"]])
    roots, _ = build_groups(records)
    fixed, inh = inherit_fixed(records, roots, old_by_url)
    return run(out_dir, seed, roots=roots, fixed=fixed, inherit_report=inh, prev_assign_by_url=old_by_url, prev_name=prev_dir.name)


def run(out_dir: Path = DEFAULT_DIR, seed: int = 20261006, roots: list[int] | None = None, fixed: dict[int, str] | None = None,
        inherit_report: dict[str, Any] | None = None, prev_assign_by_url: dict[str, str] | None = None, prev_name: str | None = None) -> dict[str, Any]:
    records = [json.loads(line) for line in (out_dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if roots is None:
        roots, rule_stats = build_groups(records)
    else:
        _, rule_stats = build_groups(records)
    split_of = assign(records, roots, seed, fixed=fixed)
    ver = verify(records, split_of)
    gid = {i: f"g{records[roots[i]]['id'][1:]}" for i in range(len(records))}
    sizes = collections.Counter(gid.values())
    per_split = collections.Counter(split_of.values())
    per_split_type: dict[str, dict[str, int]] = {s: dict(collections.Counter(records[i]["ship_type"] for i, sp in split_of.items() if sp == s).most_common()) for s, _ in RATIOS}
    doc = {
        "seed": seed,
        "ratios": dict(RATIOS),
        "unit": "group (union-find over R1..R5, see tools/shipknow_split.py)",
        "n_records": len(records),
        "n_groups": len(sizes),
        "union_counts_by_rule": rule_stats,
        "group_size_top10": sizes.most_common(10),
        "group_size_hist": dict(sorted(collections.Counter(min(v, 10) for v in sizes.values()).items())),
        "rows_per_split": dict(per_split),
        "ship_type_per_split": per_split_type,
        "groups_per_split": {s: len({gid[i] for i, sp in split_of.items() if sp == s}) for s, _ in RATIOS},
        "verification": ver,
        "assignments": {records[i]["id"]: split_of[i] for i in range(len(records))},
        "group_of": {records[i]["id"]: gid[i] for i in range(len(records))},
    }
    if inherit_report is not None:
        by_url = prev_assign_by_url or {}
        carried = collections.Counter()
        for i, r in enumerate(records):
            prev = by_url.get(r.get("source_url") or "")
            carried["new_class" if prev is None else ("same_split" if prev == split_of[i] else "changed_split")] += 1
        doc["inherited_from"] = prev_name
        doc["inheritance"] = {**inherit_report, "records_vs_earlier": dict(carried)}
    (out_dir / "splits.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: doc[k] for k in ("n_records", "n_groups", "rows_per_split", "union_counts_by_rule", "group_size_top10")}, ensure_ascii=False))
    print(json.dumps(ver["test_vs_train"], ensure_ascii=False))
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--inherit-from", type=Path, default=None, help="earlier version dir (records.jsonl + splits.json): keep its split for matching classes")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if args.inherit_from:
        run_inherit(args.dir, args.inherit_from, args.seed)
    else:
        run(args.dir, args.seed)


if __name__ == "__main__":
    main()
