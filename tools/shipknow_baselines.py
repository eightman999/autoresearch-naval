"""Non-LLM baselines for the shipknow v0 test set (same examples, same scorer).

    uv run --no-project --with scikit-learn --with numpy python tools/shipknow_baselines.py

(scikit-learn is only needed for the T1 gradient-boosting / kNN models and is pulled in
ad hoc; pyproject.toml is not touched.  Without sklearn the other baselines still run.)

Baselines
  T1  majority | era x country majority | HistGradientBoosting on numeric+weapon+sensor features | kNN
  T2  empty (floor) | nearest train ship of the same type (kNN-1) | medoid of the 5 nearest (kNN-5)
  T3  type x era median | kNN regression (log space; 8 neighbours, samples -> interval)

Everything is fitted on the *train* rows only (records.jsonl rows whose split is "train", or the train
task examples for T1) and evaluated on test.jsonl through tools/shipknow_score.py.  Predictions are also
written to <dir>/baseline_preds/<name>.jsonl (format documented in shipknow_score.py).
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import shipknow_build_tasks as B  # noqa: E402
import shipknow_score as S  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "navspec" / "shipknow_v0"
DEFAULT_RESULT = ROOT / "docs" / "shipknow_v0" / "baselines_test.json"

# v0_2 inputs (year_in_navy, standard displacement, belt / deck armour) are given to the feature-based baselines as well, so that they see the
# same information as the LLM; `--no-new-features` reproduces the v0_1 feature set on v0_2 data (an ablation).
USE_NEW_FEATURES = True
NEW_FEATS = ["year_in_navy", "displacement_standard_t", "armour_belt_mm", "armour_deck_mm"]

ERA_EDGES = [1860, 1880, 1900, 1920, 1940, 1960, 1980, 2000]


def era(year: float | None) -> int:
    if year is None or (isinstance(year, float) and math.isnan(year)):
        return -1
    return sum(1 for e in ERA_EDGES if year >= e)


def read_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_preds(path: Path, preds: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for p in preds:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")


def mode(xs: list[Any], default: Any = None) -> Any:
    xs = [x for x in xs if x is not None]
    return collections.Counter(xs).most_common(1)[0][0] if xs else default


# ---------------------------------------------------------------------------
# features from a prompt view (what the LLM sees)
# ---------------------------------------------------------------------------
NUM = B.NUMERIC_FIELDS
KINDS = B.KIND_ORDER
SENS = ["air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar", "radar", "sonar", "esm_ecm", "decoy", "eo_director", "combat_system"]
PROP = ["nuclear", "gas_turbine", "diesel", "petrol_engine", "steam_turbine", "steam_reciprocating", "electric_motor", "sail", "paddle_wheel"]


def feature_names() -> list[str]:
    n = ["year"] + NUM + ["len_beam", "power_per_t", "disp_per_len3"]
    n += ["gun_max_cal", "gun_main_barrels", "gun_total_barrels", "gun_n_types", "weapons_known", "sensors_known", "n_weapon_items"]
    n += [f"w_{k}" for k in KINDS] + [f"wn_{k}" for k in KINDS]
    n += [f"s_{k}" for k in SENS] + [f"p_{k}" for k in PROP]
    if USE_NEW_FEATURES:
        n += NEW_FEATS
    return n


def feature_vec(view: dict[str, Any]) -> list[float]:
    nan = float("nan")
    v = [float(view["year"]) if view.get("year") else nan]
    for f in NUM:
        v.append(float(view[f]) if view.get(f) is not None else nan)
    d = view.get("displacement_normal_t") or view.get("displacement_full_t")
    L, Bm, P = view.get("length_m"), view.get("beam_m"), view.get("power_hp")
    v += [L / Bm if L and Bm else nan, P / d if P and d else nan, d / L ** 3 if d and L else nan]
    w = view.get("weapons")
    guns = [x for x in (w or []) if x["kind"] == "gun" and x.get("cal")]
    if guns:
        cal = max(g["cal"] for g in guns)
        mb = sum(g.get("n", 1) * g.get("per", 1) for g in guns if g["cal"] == cal)
        tb = sum(g.get("n", 1) * g.get("per", 1) for g in guns)
        v += [cal, mb, tb, len({g["cal"] for g in guns})]
    else:
        v += [0.0 if w is not None else nan] * 4
    v += [1.0 if w is not None else 0.0, 1.0 if "sensors" in view else 0.0, float(len(w)) if w is not None else nan]
    cnt = collections.Counter()
    pres = collections.Counter()
    for x in w or []:
        cnt[x["kind"]] += x.get("n", 1) * x.get("per", 1)
        pres[x["kind"]] += 1
    v += [float(bool(pres[k])) if w is not None else nan for k in KINDS]
    v += [float(cnt[k]) if w is not None else nan for k in KINDS]
    sens = set(view.get("sensors") or [])
    v += [float(k in sens) if "sensors" in view else nan for k in SENS]
    prop = set(view.get("propulsion") or [])
    v += [float(k in prop) for k in PROP]
    if USE_NEW_FEATURES:
        # year_in_navy is shown only when it differs from year: absent = same as year (NaN when year is absent too)
        v.append(float(view["year_in_navy"]) if view.get("year_in_navy") else (float(view["year"]) if view.get("year") else nan))
        v += [float(view[f]) if view.get(f) is not None else nan for f in NEW_FEATS[1:]]
    return v


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------
def run_t1(train_ex: list[dict], valid_ex: list[dict], test_ex: list[dict], labels: list[str], out_dir: Path, seed: int) -> dict[str, Any]:
    def view(e):
        return json.loads(e["messages"][1]["content"])

    def gold(e):
        return json.loads(e["messages"][2]["content"])

    tr_v = [view(e) for e in train_ex]
    tr_g = [gold(e) for e in train_ex]
    te_v = [view(e) for e in test_ex]
    results: dict[str, list[dict[str, Any]]] = {}

    # --- majority
    maj = mode([g["ship_type"] for g in tr_g])
    maj_fine = mode([g["ship_type_fine"] for g in tr_g if g["ship_type"] == maj])
    results["t1_majority"] = [{"id": e["id"], "output": {"ship_type": maj, "ship_type_fine": maj_fine}} for e in test_ex]

    # --- era x country majority with backoff
    cell: dict[tuple, list[int]] = collections.defaultdict(list)
    for i, (v, g) in enumerate(zip(tr_v, tr_g)):
        cell[("ec", era(v.get("year")), v.get("country"))].append(i)
        cell[("e", era(v.get("year")))].append(i)
        cell[("c", v.get("country"))].append(i)
    fine_by_coarse: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for g in tr_g:
        fine_by_coarse[g["ship_type"]][g["ship_type_fine"]] += 1
    preds = []
    for e, v in zip(test_ex, te_v):
        idx = None
        for key in (("ec", era(v.get("year")), v.get("country")), ("e", era(v.get("year"))), ("c", v.get("country"))):
            if len(cell.get(key, [])) >= 5:
                idx = cell[key]
                break
        if idx is None:
            idx = list(range(len(tr_g)))
        c = mode([tr_g[i]["ship_type"] for i in idx])
        fine_in_cell = mode([tr_g[i]["ship_type_fine"] for i in idx if tr_g[i]["ship_type"] == c], fine_by_coarse[c].most_common(1)[0][0])
        preds.append({"id": e["id"], "output": {"ship_type": c, "ship_type_fine": fine_in_cell}})
    results["t1_era_country_majority"] = preds

    # --- sklearn models
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.neighbors import KNeighborsClassifier
    except Exception as exc:  # pragma: no cover
        print("sklearn unavailable, skipping GBM/kNN:", exc)
        return results
    countries = [c for c, _ in collections.Counter(v.get("country") for v in tr_v).most_common(60)]
    cmap = {c: i for i, c in enumerate(countries)}

    def X_of(views):
        X = np.array([feature_vec(v) for v in views], dtype=float)
        c = np.array([[cmap.get(v.get("country"), len(cmap)) if v.get("country") else len(cmap) + 1] for v in views], dtype=float)
        return np.hstack([X, c])

    Xtr, Xte = X_of(tr_v), X_of(te_v)
    ytr = np.array([g["ship_type"] for g in tr_g])
    va_v = [view(e) for e in valid_ex]
    Xva, yva = X_of(va_v), np.array([gold(e)["ship_type"] for e in valid_ex])
    cat = np.zeros(Xtr.shape[1], dtype=bool)
    cat[-1] = True
    gbm = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.08, max_leaf_nodes=24, min_samples_leaf=15, l2_regularization=1.0,
                                         categorical_features=cat, random_state=seed, early_stopping=False)
    gbm.fit(Xtr, ytr)
    # choose the iteration count on the valid split (staged predictions), keep the model otherwise untouched
    best_it, best_ll = 0, 1e9
    classes = list(gbm.classes_)
    yva_i = np.array([classes.index(y) if y in classes else 0 for y in yva])
    for it, proba in enumerate(gbm.staged_predict_proba(Xva), start=1):
        ll = -np.mean(np.log(np.clip(proba[np.arange(len(yva_i)), yva_i], 1e-9, 1)))
        if ll < best_ll:
            best_it, best_ll = it, ll
    gbm_final = HistGradientBoostingClassifier(max_iter=best_it, learning_rate=0.08, max_leaf_nodes=24, min_samples_leaf=15, l2_regularization=1.0,
                                               categorical_features=cat, random_state=seed, early_stopping=False).fit(Xtr, ytr)
    proba = gbm_final.predict_proba(Xte)
    cl = list(gbm_final.classes_)

    # kNN on log-scaled, median-imputed, standardised features (also used to pick the fine label)
    def knn_matrix(X):
        Z = X.copy()
        for j in range(Z.shape[1] - 1):
            col = Z[:, j]
            if np.nanmin(Xtr[:, j]) >= 0 and np.nanmax(Xtr[:, j]) > 50:
                Z[:, j] = np.log1p(np.where(np.isnan(col), np.nan, np.maximum(col, 0)))
        return Z[:, :-1]

    Ztr_raw, Zte_raw = knn_matrix(Xtr), knn_matrix(Xte)
    med = np.nanmedian(Ztr_raw, axis=0)
    mu, sd = np.nanmean(Ztr_raw, axis=0), np.nanstd(Ztr_raw, axis=0) + 1e-6
    miss_tr, miss_te = np.isnan(Ztr_raw).astype(float), np.isnan(Zte_raw).astype(float)

    def prep(Z, miss):
        Zi = np.where(np.isnan(Z), med, Z)
        return np.hstack([(Zi - mu) / sd, 0.7 * miss[:, [0, 1, 2, 3, 4, 6, 7, 8, 9, 10]]])

    Ptr, Pte = prep(Ztr_raw, miss_tr), prep(Zte_raw, miss_te)
    knn = KNeighborsClassifier(n_neighbors=15, weights="distance").fit(Ptr, ytr)
    kp = knn.predict_proba(Pte)
    kcl = list(knn.classes_)
    tr_fine = np.array([g["ship_type_fine"] for g in tr_g])
    nn = knn.kneighbors(Pte, n_neighbors=30, return_distance=False)

    def fine_for(coarse: str, neigh: np.ndarray) -> str:
        cand = [tr_fine[j] for j in neigh if ytr[j] == coarse]
        return mode(cand, fine_by_coarse[coarse].most_common(1)[0][0])

    gb_preds, kn_preds = [], []
    for i, e in enumerate(test_ex):
        c = cl[int(np.argmax(proba[i]))]
        lp = {cl[j]: float(math.log(max(proba[i][j], 1e-9))) for j in range(len(cl))}
        gb_preds.append({"id": e["id"], "output": {"ship_type": c, "ship_type_fine": fine_for(c, nn[i])}, "ranking": lp})
        c2 = kcl[int(np.argmax(kp[i]))]
        lp2 = {kcl[j]: float(math.log(max(kp[i][j], 1e-6))) for j in range(len(kcl))}
        kn_preds.append({"id": e["id"], "output": {"ship_type": c2, "ship_type_fine": fine_for(c2, nn[i])}, "ranking": lp2})
    results["t1_hist_gbm"] = gb_preds
    results["t1_knn15"] = kn_preds
    results["_t1_gbm_best_iter"] = [{"best_iter": best_it, "valid_logloss": float(best_ll)}]
    return results


# ---------------------------------------------------------------------------
# T2 / T3 : retrieval from train rows
# ---------------------------------------------------------------------------
def disp_of(r: dict[str, Any]) -> float | None:
    return r.get("displacement_normal_t") or r.get("displacement_full_t")


def logv(x: float | None) -> float:
    return math.log(x) if x else float("nan")


def run_t2(train_recs: list[dict], test_ex: list[dict], out_dir: Path) -> dict[str, Any]:
    pool = [r for r in train_recs if r["ship_type"] != "unknown" and B.compact_weapons(r.get("weapons")) is not None and r.get("armament_parse_ok")]
    comp = [B.compact_weapons(r["weapons"]) for r in pool]
    # numeric arrays
    year = np.array([r["year"] if r.get("year") else np.nan for r in pool], dtype=float)
    ld = np.array([logv(disp_of(r)) for r in pool])
    ll = np.array([logv(r.get("length_m")) for r in pool])
    lp = np.array([logv(r.get("power_hp")) for r in pool])
    sp = np.array([r.get("speed_kn") or np.nan for r in pool], dtype=float)
    types = np.array([r["ship_type"] for r in pool])
    fines = np.array([r["ship_type_fine"] or "" for r in pool])
    ctry = np.array([(r["country"] or "").replace("-", " ") for r in pool])
    sens_ok = np.array([bool(r.get("sensors_known")) for r in pool])
    preds_knn1, preds_knn5, preds_empty = [], [], []
    for e in test_ex:
        v = json.loads(e["messages"][1]["content"])
        want = v.get("want", ["weapons", "sensors"])
        cand = types == v["ship_type"]
        if "sensors" in want:
            cand = cand & sens_ok
        if not cand.any():
            cand = types == v["ship_type"]
        d = np.zeros(len(pool))
        d += np.where(np.isnan(year) | ("year" not in v), 0.0, np.abs(year - v.get("year", 0)) / 20.0) if v.get("year") else 0.0
        dd = v.get("displacement_normal_t") or v.get("displacement_full_t")
        if not dd and USE_NEW_FEATURES and v.get("displacement_standard_t"):
            dd = v["displacement_standard_t"] * 1.1  # standard displacement stands in (full ~ 1.1 x standard)
        if dd:
            d += np.where(np.isnan(ld), 0.8, 1.5 * np.abs(ld - math.log(dd)))
        if v.get("length_m"):
            d += np.where(np.isnan(ll), 0.4, 1.0 * np.abs(ll - math.log(v["length_m"])))
        if v.get("power_hp"):
            d += np.where(np.isnan(lp), 0.2, 0.3 * np.abs(lp - math.log(v["power_hp"])))
        if v.get("speed_kn"):
            d += np.where(np.isnan(sp), 0.2, np.abs(sp - v["speed_kn"]) / 25.0)
        if v.get("country"):
            d += np.where(ctry == v["country"], 0.0, 0.35)
        if v.get("ship_type_fine"):
            d += np.where(fines == v["ship_type_fine"], 0.0, 0.3)
        d = np.where(cand, d, 1e9)
        order = np.argsort(d)[:5]
        order = [int(j) for j in order if d[j] < 1e8]

        def out_of(j: int) -> dict[str, Any]:
            o = {"weapons": comp[j]}
            if "sensors" in want:
                o["sensors"] = list(pool[j].get("sensor_kinds") or [])
            return o

        if not order:
            o = {"weapons": []}
            if "sensors" in want:
                o["sensors"] = []
            preds_knn1.append({"id": e["id"], "output": o})
            preds_knn5.append({"id": e["id"], "output": o})
        else:
            preds_knn1.append({"id": e["id"], "output": out_of(order[0])})
            # medoid among the 5 nearest by weapon-item F1 agreement
            best, bs = order[0], -1.0
            for a in order:
                sc = 0.0
                for b in order:
                    if a != b:
                        tp, fp, fn = S.match_items(comp[b], comp[a])
                        sc += S.prf(tp, fp, fn)[2]
                if sc > bs:
                    best, bs = a, sc
            preds_knn5.append({"id": e["id"], "output": out_of(best)})
        o = {"weapons": []}
        if "sensors" in want:
            o["sensors"] = []
        preds_empty.append({"id": e["id"], "output": o})
    return {"t2_empty": preds_empty, "t2_knn1": preds_knn1, "t2_knn5_medoid": preds_knn5}


T3_LOG = {f: True for f in B.T3_FIELDS}


def run_t3(train_recs: list[dict], test_ex: list[dict], seed: int) -> dict[str, Any]:
    pool = [r for r in train_recs]
    fields = ["displacement_normal_t", "displacement_full_t", "length_m", "beam_m", "draught_m", "speed_kn", "power_hp", "complement", "endurance_nm", "shafts"]
    if USE_NEW_FEATURES:
        fields.append("displacement_standard_t")  # a visible predictor only (never a T3 target)
    L = np.array([[logv(r.get(f)) for f in fields] for r in pool])
    yr = np.array([r["year"] if r.get("year") else np.nan for r in pool], dtype=float)
    types = np.array([r["ship_type"] for r in pool])
    ctry = np.array([(r["country"] or "").replace("-", " ") for r in pool])
    sd = np.nanstd(L, axis=0)
    eras = np.array([era(None if np.isnan(y) else y) for y in yr])
    # type x era medians (backoff: type -> global)
    med: dict[tuple, dict[str, float]] = {}

    def med_of(mask: np.ndarray) -> dict[str, float]:
        out = {}
        for j, f in enumerate(fields):
            col = L[mask, j]
            col = col[~np.isnan(col)]
            if len(col) >= 5:
                out[f] = float(np.exp(np.median(col)))
        return out

    glob = med_of(np.ones(len(pool), dtype=bool))
    for t in set(types):
        med[(t, None)] = med_of(types == t)
        for e in set(eras[types == t]):
            med[(t, int(e))] = med_of((types == t) & (eras == e))
    preds_med, preds_knn = [], []
    rng = random.Random(seed)
    for e in test_ex:
        v = json.loads(e["messages"][1]["content"])
        hidden = v["predict"]
        t = v.get("ship_type")
        ey = era(v.get("year"))
        out = {}
        for f in hidden:
            val = med.get((t, ey), {}).get(f) or med.get((t, None), {}).get(f) or glob[f]
            out[f] = B.rnd(f, val)
        preds_med.append({"id": e["id"], "output": out})
        # kNN
        x = np.array([logv(v.get(f)) if f not in hidden else float("nan") for f in fields])
        diff = (L - x) / sd
        shared = ~np.isnan(diff)
        cnt = shared.sum(axis=1)
        dist = np.sqrt(np.nansum(diff ** 2, axis=1) / np.maximum(cnt, 1))
        dist = np.where(cnt >= 2, dist, 5.0)
        if v.get("year"):
            dist = dist + np.where(np.isnan(yr), 0.3, np.abs(yr - v["year"]) / 60.0)
        if t:
            dist = dist + np.where(types == t, 0.0, 0.6)
        if v.get("country"):
            dist = dist + np.where(ctry == v["country"], 0.0, 0.1)
        has_all = np.array([all(not np.isnan(L[i, fields.index(f)]) for f in hidden) for i in range(len(pool))])
        dist = np.where(has_all, dist, 1e9)
        idx = np.argsort(dist)[:8]
        out2, samples = {}, []
        for f in hidden:
            vals = np.array([L[i, fields.index(f)] for i in idx])
            w = 1.0 / (dist[idx] + 0.05)
            order = np.argsort(vals)
            cw = np.cumsum(w[order])
            m = vals[order][np.searchsorted(cw, cw[-1] / 2)]
            out2[f] = B.rnd(f, float(math.exp(m)))
        for i in idx:
            samples.append({f: B.rnd(f, float(math.exp(L[i, fields.index(f)]))) for f in hidden})
        preds_knn.append({"id": e["id"], "output": out2, "samples": samples})
    return {"t3_type_era_median": preds_med, "t3_knn8": preds_knn}


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--no-new-features", action="store_true", help="ablation: ignore year_in_navy / standard displacement / armour")
    args = ap.parse_args()
    global USE_NEW_FEATURES
    USE_NEW_FEATURES = not args.no_new_features

    recs = read_jsonl(args.dir / "records.jsonl")
    splits = json.loads((args.dir / "splits.json").read_text(encoding="utf-8"))["assignments"]
    train_recs = [r for r in recs if splits[r["id"]] == "train"]
    train_ex, valid_ex = read_jsonl(args.dir / "train.jsonl"), read_jsonl(args.dir / "valid.jsonl")
    test_rows = S.load_test(args.dir / "test.jsonl")
    labels = S.labels_from_manifest(args.dir / "test.jsonl")
    t1_tr = [e for e in train_ex if e["task"] == "T1"]
    t1_va = [e for e in valid_ex if e["task"] == "T1"]
    t1_te = [e for e in test_rows if e["task"] == "T1"]
    t2_te = [e for e in test_rows if e["task"] == "T2"]
    t3_te = [e for e in test_rows if e["task"] == "T3"]

    all_preds: dict[str, list[dict[str, Any]]] = {}
    all_preds.update(run_t1(t1_tr, t1_va, t1_te, labels, args.dir, args.seed))
    all_preds.update(run_t2(train_recs, t2_te, args.dir))
    all_preds.update(run_t3(train_recs, t3_te, args.seed))

    results: dict[str, Any] = {"n_test": {"T1": len(t1_te), "T2": len(t2_te), "T3": len(t3_te)}, "n_train_rows": len(train_recs), "baselines": {}}
    for name, preds in all_preds.items():
        if name.startswith("_"):
            results[name.strip("_")] = preds[0]
            continue
        write_preds(args.dir / "baseline_preds" / f"{name}.jsonl", preds)
        task = name[:2].upper()
        res = S.score(test_rows, {p["id"]: p for p in preds}, labels, tasks=(task,))
        results["baselines"][name] = res[task]
        print(f"== {name}")
        S.print_summary({task: res[task]})
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", args.result)


if __name__ == "__main__":
    main()
