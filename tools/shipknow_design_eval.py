"""設計能力の学習前後比較の採点器（shipknow_design_eval_v1）。shipknow_design_score.py と validator を再利用し、問題×seed 単位の行と集計・モデル比較表を出す。

    # 1 モデル（または複数モデル）を採点。--pred NAME=PATH を繰り返す（同じ NAME は合算）。予測は {"id", "seed"(省略可), "text" か "output"}
    .venv/bin/python tools/shipknow_design_eval.py score --problems data/navspec/shipknow_design_eval_v1/problems.jsonl \\
        --pairs data/navspec/shipknow_design_eval_v1/pairs.jsonl --pred v1_2=preds/v1_2.jsonl --pred design_best=preds/best.jsonl --out-dir out/eval
    # 保存済みの summary_<model>.json から比較表だけ作る
    .venv/bin/python tools/shipknow_design_eval.py compare --summary out/eval/summary_v1_2.json --summary out/eval/summary_design_best.json --out out/eval/compare.md
    # T1〜T3 の忘却評価: 採点用の test を忘却サブセットに絞る（採点は tools/shipknow_score.py）
    .venv/bin/python tools/shipknow_design_eval.py forget-test --test <v1_2 の test.jsonl> --out-dir out/forget_test
    # 非 LLM ベースライン（近傍の top1 / 多数決）の予測と採点
    .venv/bin/python tools/shipknow_design_eval.py baselines --out-dir data/navspec/shipknow_design_eval_v1/baselines
    # 規則の誤検出率の確認: v0 test の正解設計（実艦）を予測として採点する
    .venv/bin/python tools/shipknow_design_eval.py sanity --test data/navspec/shipknow_design_v0/test.jsonl

指標（行 = 問題×seed）
  Format          JSON parse 成功率、スキーマ合格率（validator の schema 検査）、フィールド別の欠損率
  Requirement     req の各制約（艦種・排水量・速力・乗員・航続・主砲口径・砲身数・センサー・武装・艦載機数・魚雷管数・推進）を個別に判定。
                  問題ごとの 満たした数/判定可能数 の平均、種別ごとの充足率（判定可能分母と、欠落を未充足に数える厳密分母の両方）
  Consistency     validator の物理比・砲・年代・艦種検査（hard 違反）+ 追加規則（armament_excess、weapon_count_excess、type_displacement_mismatch、
                  displacement_order、air_without_entry、heli_hull_too_short、nuclear_small_hull、beam_ge_length、sub_many_big_guns）。警告数・合格率
  Plausibility    主要諸元（排水量・全長・速力・出力・主砲口径・乗員）が、train の 同国（n<8 なら同地域、更に世界）×同年代 ±10 年×同艦種 の
                  5-95 パーセンタイルに入る割合
  Copy            出力の排水量・全長・速力（・全幅）が全 records の実艦と ±1% で全て一致する率、入力の近傍艦と一致する率
  Diversity       同一問題の seed 間の出力一致率・数値のばらつき、異なる問題間で同一出力が出る率
  E               矛盾の認識（infeasible 系の応答）、緩和（要求を満たさない設計）、数字で埋めた率（要求を全部満たすが整合性違反）
  Pair            期待方向に動いた率（組×seed）、変更種別ごとの正答率、A/B が完全に同じ出力だった率
判定規則の詳細は各関数の docstring と docs/shipknow_design_eval_v1/README.md を参照。
"""

from __future__ import annotations

import argparse
import collections
import functools
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_score as DS  # noqa: E402
import shipknow_design_validator as V  # noqa: E402
import shipknow_score as S  # noqa: E402

EVAL_DIR = C.ROOT / "data" / "navspec" / "shipknow_design_eval_v1"
DESIGN_FIELDS = ["ship_type", "ship_type_fine", "displacement", "length_m", "beam_m", "draught_m", "propulsion", "shafts", "power_hp", "speed_kn", "endurance_nm", "complement",
                 "armour", "weapons", "sensors", "air"]  # "displacement" = 常備・満載・基準のどれか 1 つ（正解は「あるものだけ」を出すので個別の欠損は数えない）
REQUIRED_FIELDS = ["ship_type", "displacement", "length_m", "speed_kn"]  # validator schema: ship_type, any displacement, length_m, speed_kn
PLAUS_FIELDS = ["displacement", "length_m", "speed_kn", "power_hp", "main_gun_cal", "complement"]
PLAUS_Q = (0.05, 0.95)
PLAUS_MIN_N = 8
COPY_TOL = 0.01
NEAR_COPY_TOL = 0.03
MARGIN = V.MARGIN

# --------------------------------------------------------------------------------------------------
# regions (plausibility falls back from the same country to the same region)
# --------------------------------------------------------------------------------------------------
_REGION_LISTS = {
    "west_europe": "United-Kingdom France Germany German-respublic Netherlands Belgium Ireland Austria Austria-Hungary Switzerland",
    "nordic": "Sweden Norway Denmark Finland Iceland Faeroe",
    "mediterranean": "Italy Spain Portugal Greece Malta Cyprus Albania Yugoslavia Croatia Slovenia Montenegro Macedonia Turkey",
    "east_europe_ussr": "Russian Ukraine Poland Romania Bulgaria Hungary Czech-Republic Georgia Azerbaijan Kazakhstan Turkmenistan Uzbekistan Estonia Latvia Lithuania Abkhazia",
    "east_asia": "Japan China Taiwan North-Korea South-Korea Hong-Kong Manchukuo",
    "southeast_asia": "Thailand Vietnam Vietnam2 Indonesia Malaysia Philippines Singapore Cambodia Laos Myanmar Brunei East-timor",
    "south_asia": "India Pakistan Bangladesh Sri-Lanka Maldive",
    "middle_east_n_africa": "Egypt Iran Iraq Israel Syria Lebanon Jordan Saudi-Arabia Kuwait Qatar Bahrain Oman UnitedArab Yemen Yemen2 Libya Tunisia Algeria Morocco Sudan",
    "sub_saharan_africa": ("Nigeria Ghana Kenya Tanzania Angola Mozambique Madagascar Cameroon Congo Zaire Gabon Senegal Guinea Guinea-bissau SierraLeone Liberia Togo Benin "
                           "Cote-dIvoire EquatorialGuinea Gambia Mauritania Mauritius Seychelles Comoros Djibouti Somalia Ethiopia Namibia South-Africa Zambia Zimbabwe Malawi Cape Saotome"),
    "north_america": "United-States Canada Mexico",
    "latin_america": ("Argentina Brazil Chile Colombia Peru Venezuela Uruguay Paraguay Bolivia Ecuador Guyana Suriname Cuba Haiti Dominican-Republic Jamaica Trinidad-and-Tobago "
                      "Barbados Bahamas Belize Panama Costa-Rica Nicaragua Honduras El-Salvador Guatemala Grenada Dominica SaintLucia SaintVincent saintkittsnevis Antigua Anguilla "
                      "Bermuda Cayman-islands Montserrat Turks-and-Caicos-Islands Virgin Falkland"),
    "oceania": "Australia NewZealand Fiji Papua Solomon Vanuatu Tonga Samoa Kiribati Tuvalu Palau Marshall Micronesia Cook-Islands",
}
REGION_OF = {c: r for r, cs in _REGION_LISTS.items() for c in cs.split()}


def region_of(country: str | None) -> str | None:
    return REGION_OF.get(country or "")


# --------------------------------------------------------------------------------------------------
# reference data from the real ships (train for distributions, all records for the copy check)
# --------------------------------------------------------------------------------------------------
def _num(x: Any) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else float("nan")


def gun_index(design: dict[str, Any]) -> float:
    """主砲力の指標 Σ(口径^3 × 砲身数)（口径 mm、砲身数 = n × per）。砲弾重量はほぼ口径の 3 乗に比例するので、舷側投射重量の代理。"""
    tot = 0.0
    for w in X.weapon_items(design):
        if w.get("kind") == "gun" and X._isnum(w.get("cal")) and w["cal"] > 0:
            tot += (w["cal"] / 100.0) ** 3 * X._n(w) * X._per(w)
    return tot


def _band(d: float) -> int:
    return int(np.searchsorted(np.array(V.DISP_EDGES), d, side="right") - 1)


COUNT_FAMILIES = {"ssm": "ssm", "sam": "sam", "ciws": "ciws", "torpedo_tube": "torpedo_tube", "aircraft": "aircraft_helicopter"}


def family_count(design: dict[str, Any], fam: str) -> float:
    if fam == "aircraft":
        return float(X.aircraft_count(design))
    return float(X.kind_count(design, COUNT_FAMILIES[fam]))


class Ref:
    """実艦から作る参照分布。train の設計（plausibility / consistency の分布）と、全 records（copy 判定）。"""

    def __init__(self, records_path: Path | None = None, splits_path: Path | None = None):
        records_path = records_path or (C.V0_DIR / "records.jsonl")
        splits_path = splits_path or (C.V0_DIR / "splits.json")
        recs = [json.loads(l) for l in records_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assign = json.loads(splits_path.read_text(encoding="utf-8"))["assignments"]
        self.all_recs = recs
        train = [r for r in recs if assign.get(r["id"]) == "train" and C.design_eligible(r)]
        self.train = train
        designs = [C.design_from_record(r) for r in train]
        self.designs = designs
        self.country = np.array([r["country"] for r in train])
        self.region = np.array([region_of(r["country"]) or "?" for r in train])
        self.year = np.array([C.request_year(r) for r in train], dtype=float)
        self.type = np.array([d["ship_type"] for d in designs])
        self.vals: dict[str, np.ndarray] = {
            "displacement_normal_t": np.array([_num(d.get("displacement_normal_t")) for d in designs]),
            "displacement_full_t": np.array([_num(d.get("displacement_full_t")) for d in designs]),
            "displacement_standard_t": np.array([_num(d.get("displacement_standard_t")) for d in designs]),
            "length_m": np.array([_num(d.get("length_m")) for d in designs]),
            "speed_kn": np.array([_num(d.get("speed_kn")) for d in designs]),
            "power_hp": np.array([_num(d.get("power_hp")) for d in designs]),
            "complement": np.array([_num(d.get("complement")) for d in designs]),
            "main_gun_cal": np.array([_num(C.main_gun_mm(d)) for d in designs]),
        }
        self._build_consistency(designs)
        self._build_copy(recs, {r["id"] for r in train})

    # ---------------- consistency reference
    def _build_consistency(self, designs: list[dict[str, Any]]) -> None:
        gi: dict[str, list[float]] = collections.defaultdict(list)
        td: dict[str, list[float]] = collections.defaultdict(list)
        fam_pts: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
        heli_len: list[float] = []
        nuc: list[float] = []
        for r, d in zip(self.train, designs):
            dref, _ = C.ref_displacement(d)
            era = C.era_bin(C.request_year(r))
            idx = gun_index(d)
            if dref and idx > 0:
                x = math.log(idx / dref)
                for k in (f"{d['ship_type']}|{era}", f"{d['ship_type']}|*", f"*|{era}", "*|*"):
                    gi[k].append(x)
            if dref:
                td[d["ship_type"]].append(math.log(dref))
                for fam in COUNT_FAMILIES:
                    c = family_count(d, fam)
                    if c > 0:
                        fam_pts[fam].append((dref, c))
            if "air:helicopter" in C.caps_of_design(d) and X._isnum(d.get("length_m")):
                heli_len.append(d["length_m"])
            if "prop:nuclear" in C.caps_of_design(d) and dref:
                nuc.append(dref)
        qs = (0.005, 0.02, 0.98, 0.995)
        self.gun_idx_q = {k: {"n": len(v), **{f"{q:g}": V.quantile(v, q) for q in qs}} for k, v in gi.items() if len(v) >= 30}
        self.type_disp_q = {k: {"n": len(v), **{f"{q:g}": V.quantile(v, q) for q in qs}} for k, v in td.items() if len(v) >= 15}
        # 数の包絡線: 排水量 D 以下の実艦の（その装備の）最大数。weapon_count_excess は 1.5 * 包絡線(1.5 * D) + 1 を超える数を弾く
        self.count_env = {}
        for fam, pts in fam_pts.items():
            pts.sort()
            self.count_env[fam] = (np.array([p[0] for p in pts]), np.maximum.accumulate(np.array([p[1] for p in pts])))
        self.heli_len_min = V.quantile(heli_len, 0.01) if heli_len else 0.0
        self.nuclear_min_disp = min(nuc) if nuc else 0.0

    def _gun_q(self, ship_type: str, era: str | None) -> tuple[str, dict[str, float]] | None:
        for k in (f"{ship_type}|{era}", f"{ship_type}|*", f"*|{era}", "*|*"):
            if k in self.gun_idx_q:
                return k, self.gun_idx_q[k]
        return None

    def count_envelope(self, fam: str, disp: float) -> float:
        """排水量 disp 以下の train 実艦の、その装備（ssm / sam / ciws / torpedo_tube / aircraft）の最大数。該当する実艦が無ければ 0。"""
        env = self.count_env.get(fam)
        if env is None:
            return 0.0
        i = int(np.searchsorted(env[0], disp, side="right")) - 1
        return float(env[1][i]) if i >= 0 else 0.0

    # ---------------- plausibility
    def group(self, country: str | None, ship_type: str, year: float) -> tuple[np.ndarray, str]:
        """(index, level)。level = country（同国 ±10 年 n>=8）→ region（同地域 ±10 年）→ region20（±20 年）→ world（全世界 ±10 年）→ world20。"""
        ok_t = self.type == ship_type
        near10 = np.abs(self.year - year) <= 10
        near20 = np.abs(self.year - year) <= 20
        cands: list[tuple[str, np.ndarray]] = []
        reg = region_of(country)
        cands.append(("country", ok_t & near10 & (self.country == (country or "?"))))
        if reg:
            cands.append(("region", ok_t & near10 & (self.region == reg)))
            cands.append(("region20", ok_t & near20 & (self.region == reg)))
        cands.append(("world", ok_t & near10))
        cands.append(("world20", ok_t & near20))
        for lvl, m in cands:
            idx = np.where(m)[0]
            if len(idx) >= PLAUS_MIN_N:
                return idx, lvl
        return np.array([], dtype=int), "none"

    def plausibility(self, design: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        """主要諸元が (同国→同地域→世界)×±10 年×同艦種の実艦の 5-95 パーセンタイルに入るか。値が無い / 参照が 8 艦未満の項目は None。"""
        idx, level = self.group(req.get("country"), req["ship_type"], req["year"])
        basis = req.get("displacement_basis")
        dkey = f"displacement_{basis}_t" if basis and X._isnum(design.get(f"displacement_{basis}_t")) else None
        if dkey is None:
            for b in ("normal", "standard", "full"):
                if X._isnum(design.get(f"displacement_{b}_t")):
                    dkey = f"displacement_{b}_t"
                    break
        out: dict[str, bool | None] = {}
        for f in PLAUS_FIELDS:
            if f == "displacement":
                x, arr = (design.get(dkey) if dkey else None), (self.vals[dkey] if dkey else None)
            elif f == "main_gun_cal":
                x, arr = C.main_gun_mm(design), self.vals["main_gun_cal"]
            else:
                x, arr = design.get(f), self.vals[f]
            if not X._isnum(x) or arr is None or level == "none":
                out[f] = None
                continue
            v = arr[idx]
            v = v[~np.isnan(v)]
            if len(v) < PLAUS_MIN_N:
                out[f] = None
                continue
            out[f] = bool(np.quantile(v, PLAUS_Q[0]) <= x <= np.quantile(v, PLAUS_Q[1]))
        det = [b for b in out.values() if b is not None]
        return {"fields": out, "level": level, "n_group": int(len(idx)), "all_in_range": (all(det) if det else None), "frac_in_range": (sum(det) / len(det) if det else None)}

    # ---------------- copy
    def _build_copy(self, recs: list[dict[str, Any]], train_ids: set[str]) -> None:
        self.copy_ids = [r["id"] for r in recs]
        self.copy_is_train = np.array([r["id"] in train_ids for r in recs])
        g = lambda k: np.array([_num(r.get(k)) for r in recs])  # noqa: E731
        self.copy_arr = {"dn": g("displacement_normal_t"), "df": g("displacement_full_t"), "ds": g("displacement_standard_t"), "L": g("length_m"), "B": g("beam_m"), "v": g("speed_kn")}

    def copy_match(self, design: dict[str, Any], tol: float = COPY_TOL) -> np.ndarray:
        """主要諸元（排水量のうち出力にある基準のどれか 1 つ以上が同じ基準で一致、全長・速力が一致、全幅は両方にあれば一致）が全て ±tol 以内の実艦の bool 配列。"""
        n = len(self.copy_ids)
        none = np.zeros(n, dtype=bool)
        L, v = design.get("length_m"), design.get("speed_kn")
        disp = {k: design.get(f) for k, f in (("dn", "displacement_normal_t"), ("df", "displacement_full_t"), ("ds", "displacement_standard_t")) if X._isnum(design.get(f))}
        if not (X._isnum(L) and X._isnum(v) and disp):
            return none

        def close(arr: np.ndarray, x: float) -> np.ndarray:
            with np.errstate(invalid="ignore"):
                return np.abs(arr - x) <= tol * abs(x) + 1e-9

        m_disp = np.zeros(n, dtype=bool)
        for k, x in disp.items():
            m_disp |= close(self.copy_arr[k], x)
        m = m_disp & close(self.copy_arr["L"], L) & close(self.copy_arr["v"], v)
        b = design.get("beam_m")
        if X._isnum(b):
            ba = self.copy_arr["B"]
            m &= np.isnan(ba) | close(ba, b)
        return m


@functools.lru_cache(maxsize=1)
def default_ref() -> Ref:
    return Ref()


def nbr_copy(design: dict[str, Any], nbr: list[dict[str, Any]], tol: float = COPY_TOL) -> bool:
    """入力の近傍艦（短縮キー dn/df/ds/L/B/v）のどれかと主要諸元が全て一致するか。"""
    for p in nbr or []:
        L, v = design.get("length_m"), design.get("speed_kn")
        if not (X._isnum(L) and X._isnum(v) and X._isnum(p.get("L")) and X._isnum(p.get("v"))):
            continue

        def close(a: Any, x: Any) -> bool:
            return X._isnum(a) and X._isnum(x) and abs(a - x) <= tol * abs(x) + 1e-9

        d_ok = any(close(design.get(f), p.get(k)) for k, f in (("dn", "displacement_normal_t"), ("df", "displacement_full_t"), ("ds", "displacement_standard_t")) if k in p)
        b_ok = (not X._isnum(design.get("beam_m"))) or (not X._isnum(p.get("B"))) or close(design["beam_m"], p["B"])
        if d_ok and close(L, p["L"]) and close(v, p["v"]) and b_ok:
            return True
    return False


# --------------------------------------------------------------------------------------------------
# consistency
# --------------------------------------------------------------------------------------------------
def consistency(design: dict[str, Any], req: dict[str, Any], ref: Ref, th: dict[str, Any], waivers: list[str] | None = None) -> tuple[list[str], list[str]]:
    """内部整合性の hard 警告コードと soft 警告コード。

    1. validator（物理比 ratio_*、砲 gun_*、年代 era_early、艦種 type_*。要求層・スキーマ層は除く）の違反 = hard、warnings = soft。waivers に挙げた能力語彙の era_early は数えない（架空前提）。
    2. armament_excess: log(Σ(口径^3×砲身数)/排水量) が train の (艦種×年代) 層の [p0.5, p99.5] を幅の 25% 広げた上限を超える（上限だけ）。層は validator と同じフォールバック。
    3. weapon_count_excess:<kind>: ssm / sam / ciws / 魚雷管 / 搭載機の数が、排水量 1.5 倍以下の train 実艦のその装備の最大数（包絡線）の 1.5 倍 + 1 を超える
       （その大きさまでの実艦に装備が無ければ 2 以上で超過。小型艇に SAM 96 基など）。
    4. type_displacement_mismatch: log 排水量が train の艦種ごとの [p0.5, p99.5] を幅の 25% 広げた範囲の外（戦艦が 200 t、哨戒艇が 50,000 t など）。
    5. displacement_order: 満載 < 常備 × 0.97、または 基準 > 常備 × 1.03 / 満載 < 基準 × 0.97。
    6. air_without_entry / air_entry_without_air: air に helicopter / aircraft があるのに weapons に aircraft_helicopter / aviation_facility が無い（または逆）。
    7. heli_hull_too_short: ヘリ搭載で全長が train のヘリ搭載艦の最短（p1）の 0.8 倍未満。
    8. nuclear_small_hull: 原子力推進で排水量が train の原子力艦の最小の 0.5 倍未満。
    9. beam_ge_length: 全幅 >= 全長。
    10. sub_many_big_guns: 潜水艦に口径 100mm 以上の砲が 3 門より多い。
    """
    hard: list[str] = []
    soft: list[str] = []
    year = req.get("year")
    waive = set(waivers or [])
    res = V.validate(None, {**design, "year": year, "country": req.get("country")}, th, layers=("ratio", "gun", "era", "type"))
    for x in res["violations"]:
        if x["code"] == "era_early" and x["field"] in waive:
            continue
        hard.append(f"{x['code']}:{x['field']}")
    soft += [f"{x['code']}:{x['field']}" for x in res["warnings"]]
    st = design.get("ship_type")
    dref, _b = C.ref_displacement(design)
    era = C.era_bin(year) if X._isnum(year) else None
    if dref:
        idx = gun_index(design)
        if idx > 0:
            look = ref._gun_q(st, era)
            if look:
                _k, q = look
                lo, hi = q["0.005"], q["0.995"]
                spread = max(hi - lo, V.MIN_SPREAD)
                x = math.log(idx / dref)
                if x > hi + MARGIN * spread:
                    hard.append("armament_excess")
                elif x > q["0.98"]:
                    soft.append("armament_heavy")
        for fam in COUNT_FAMILIES:
            c = family_count(design, fam)
            if c > 0 and c > 1.5 * ref.count_envelope(fam, 1.5 * dref) + 1:
                hard.append(f"weapon_count_excess:{fam}")
        tq = ref.type_disp_q.get(st)
        if tq:
            spread = max(tq["0.995"] - tq["0.005"], V.MIN_SPREAD)
            lx = math.log(dref)
            if lx < tq["0.005"] - MARGIN * spread or lx > tq["0.995"] + MARGIN * spread:
                hard.append("type_displacement_mismatch")
    dn, df, ds = (design.get(f"displacement_{k}_t") for k in ("normal", "full", "standard"))
    if (X._isnum(dn) and X._isnum(df) and df < 0.97 * dn) or (X._isnum(ds) and X._isnum(dn) and ds > 1.03 * dn) or (X._isnum(ds) and X._isnum(df) and df < 0.97 * ds):
        hard.append("displacement_order")
    air = set(design.get("air") or []) if isinstance(design.get("air"), list) else set()
    kinds = {w.get("kind") for w in X.weapon_items(design)}
    entry = bool(kinds & {"aircraft_helicopter", "aviation_facility"})
    if air and not entry:
        hard.append("air_without_entry")
    if entry and not air:
        hard.append("air_entry_without_air")
    L = design.get("length_m")
    if "helicopter" in air and X._isnum(L) and ref.heli_len_min and L < 0.8 * ref.heli_len_min:
        hard.append("heli_hull_too_short")
    if dref and "prop:nuclear" in C.caps_of_design(design) and ref.nuclear_min_disp and dref < 0.5 * ref.nuclear_min_disp:
        hard.append("nuclear_small_hull")
    B_ = design.get("beam_m")
    if X._isnum(L) and X._isnum(B_) and B_ >= L:
        hard.append("beam_ge_length")
    if st == "submarine":
        big = sum(X._n(w) * X._per(w) for w in X.weapon_items(design) if w.get("kind") == "gun" and X._isnum(w.get("cal")) and w["cal"] >= 100)
        if big > 3:
            hard.append("sub_many_big_guns")
    return hard, soft


# --------------------------------------------------------------------------------------------------
# E (impossible requests): recognition of the contradiction
# --------------------------------------------------------------------------------------------------
REFUSAL_RE = re.compile(r"\b(cannot|can't|can not|unable|impossible|infeasible|not (?:feasible|possible|achievable|attainable|realistic)|unattainable|unrealistic|incompatible|"
                        r"contradict\w*|conflict\w*|no such|did not exist|does not exist|not exist|anachronis\w*|unsatisf\w*|cannot be)\b", re.I)
KEY_STATUS = re.compile(r"^(status|result|verdict|feasibility|outcome)$", re.I)
KEY_TEXT = re.compile(r"^(notes?|reasons?|reasoning|warnings?|comments?|explanation|remarks?|caveats?|message|rationale|analysis)$", re.I)
KEY_LIST = re.compile(r"^(conflicts?|unmet\w*|violations?|relax\w*|infeasible\w*|impossible\w*|unsatisfied\w*|errors?)$", re.I)
STATUS_BAD_RE = re.compile(r"infeasible|impossible|cannot|can't|unable|reject|fail|not[ _-]?(feasible|possible)|unsatisf|conflict|error", re.I)
SHIP_TYPE_BAD_RE = re.compile(r"impossib|infeasib|invalid|n/?a|none|unknown|error", re.I)


def infeasible_marker(text: str | None, obj: Any) -> tuple[bool, str]:
    """E の「矛盾を認識した」とみなす応答の検出規則（現状の学習データに拒否応答は無いので規則で定義して記録する）。次のどれか:
      structured_key   status / result / verdict 系のキーが infeasible・impossible・cannot・fail 等、feasible = false、infeasible / impossible = true、
                       conflicts / unmet* / violations / relaxed* / errors 系のキーが空でない
      note_text        notes / reason / warning / comment / explanation / remarks / caveats 系のキーの文字列が REFUSAL_RE（cannot / impossible / infeasible / conflict /
                       did not exist / anachronism ...）に合う
      ship_type        ship_type が impossible / infeasible / invalid / none / unknown / n/a など
      text             JSON の外の文章（または JSON が無い応答）が REFUSAL_RE に合う
    戻り値 (認識したか, 根拠の種類)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if KEY_STATUS.match(ks) and isinstance(v, str) and STATUS_BAD_RE.search(v):
                return True, "structured_key"
            if re.fullmatch(r"feasible|is_feasible|possible|achievable", ks, re.I) and v is False:
                return True, "structured_key"
            if re.fullmatch(r"infeasible|impossible|is_infeasible|is_impossible", ks, re.I) and v is True:
                return True, "structured_key"
            if KEY_LIST.match(ks) and v not in (None, "", [], {}, False):
                return True, "structured_key"
            if KEY_TEXT.match(ks) and isinstance(v, (str, list)) and REFUSAL_RE.search(json.dumps(v, ensure_ascii=False)):
                return True, "note_text"
        st = obj.get("ship_type")
        if isinstance(st, str) and SHIP_TYPE_BAD_RE.search(st):
            return True, "ship_type"
    if isinstance(text, str):
        outside = text
        j = S.extract_json(text)
        if j is not None:
            s0 = text.find("{")
            e0 = text.rfind("}")
            outside = text[:s0] + " " + text[e0 + 1:] if s0 >= 0 and e0 > s0 else ""
        if REFUSAL_RE.search(outside):
            return True, "text"
    return False, ""


# --------------------------------------------------------------------------------------------------
# row scoring
# --------------------------------------------------------------------------------------------------
def canon_design(d: Any) -> str | None:
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")) if isinstance(d, dict) else None


def template_key(d: dict[str, Any]) -> tuple:
    return (d.get("ship_type_fine"), d.get("displacement_normal_t") or d.get("displacement_standard_t") or d.get("displacement_full_t"), d.get("length_m"), d.get("speed_kn"), d.get("power_hp"))


def score_row(prob: dict[str, Any], pred: dict[str, Any] | None, ref: Ref, th: dict[str, Any], skip_copy: bool = False) -> dict[str, Any]:
    req = prob["req"]
    obj = S.parse_output(pred) if pred is not None else None
    raw = None
    if pred is not None:
        raw = pred.get("text") if pred.get("text") is not None else (pred.get("output") if isinstance(pred.get("output"), str) else json.dumps(pred.get("output"), ensure_ascii=False))
    row: dict[str, Any] = {"id": prob["id"], "category": prob["category"], "seed": (pred or {}).get("seed", 0) or 0, "json_ok": isinstance(obj, dict), "design_present": pred is not None}
    pm = prob.get("meta", {}).get("pair")
    if pm:
        row.update({"pair_id": pm["id"], "role": pm["role"]})
    row["marker"], row["marker_kind"] = infeasible_marker(raw, obj if isinstance(obj, dict) else None)
    zero = X.req_checks(req, {})
    row["req_total"] = len(zero)
    if not isinstance(obj, dict):
        row.update({"schema_ok": False, "missing": list(DESIGN_FIELDS), "missing_required": True, "req_det": 0, "req_sat": 0, "req_by_type": {}, "req_all_sat": False,
                    "warn": [], "soft": [], "cons_pass": None, "plaus": None, "copy_any": None, "copy_nbr": None, "canon": None})
        row["e_class"] = ("refused" if row["marker"] else "invalid") if prob["category"] == "E" else None
        return row
    sch = V._schema(obj)
    row["schema_ok"] = not sch
    row["schema_codes"] = sorted({x["code"] for x in sch})
    row["missing"] = [f for f in DESIGN_FIELDS if not has_field(obj, f)]
    row["missing_required"] = any(not has_field(obj, f) for f in REQUIRED_FIELDS)
    checks = X.req_checks(req, obj)
    by: dict[str, list[int]] = {}
    for c in checks:
        t = by.setdefault(c["type"], [0, 0, 0])
        t[0] += 1
        t[1] += c["ok"] is not None
        t[2] += c["ok"] is True
    row["req_by_type"] = by
    row["req_det"] = sum(1 for c in checks if c["ok"] is not None)
    row["req_sat"] = sum(1 for c in checks if c["ok"] is True)
    row["req_all_sat"] = bool(checks) and all(c["ok"] is True for c in checks)
    row["req_failed"] = [c["key"] for c in checks if c["ok"] is not True]
    waivers = prob.get("meta", {}).get("era_waivers") or []
    hard, soft = consistency(obj, req, ref, th, waivers)
    row["warn"], row["soft"], row["cons_pass"] = hard, soft, not hard
    row["plaus"] = ref.plausibility(obj, req)
    if skip_copy:
        row["copy_any"] = row["copy_near_any"] = row["copy_nbr"] = None
    else:
        m = ref.copy_match(obj)
        mn = ref.copy_match(obj, NEAR_COPY_TOL)
        row["copy_any"] = bool(m.any())
        row["copy_train"] = bool((m & ref.copy_is_train).any())
        row["copy_near_any"] = bool(mn.any())
        row["copy_ids"] = [ref.copy_ids[i] for i in np.where(m)[0][:3]]
        row["copy_nbr"] = nbr_copy(obj, prob.get("nbr") or [])
    row["canon"] = canon_design(obj)
    row["tkey"] = json.dumps(template_key(obj), default=str)
    if prob["category"] in ("P",):
        row["metrics"] = X.design_metrics(obj, req)
    if prob["category"] == "E":
        if row["marker"]:
            row["e_class"] = "refused"
        elif not row["req_all_sat"]:
            row["e_class"] = "relaxed"
        elif hard:  # 要求は全て満たしたが、整合性（物理・年代・艦種・砲・追加規則）の hard 違反がある = 無理な要求を数字で埋めた
            row["e_class"] = "filled"
        else:
            row["e_class"] = "ok"
    return row


def has_field(obj: dict[str, Any], f: str) -> bool:
    if f == "displacement":
        return any(X._isnum(obj.get(f"displacement_{b}_t")) for b in ("normal", "full", "standard"))
    v = obj.get(f)
    return v not in (None, [], {}) and not (isinstance(v, str) and not v)


# --------------------------------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------------------------------
def _rate(a: int | float, b: int | float, nd: int = 4) -> float | None:
    return round(a / b, nd) if b else None


def _mean(xs: list[float], nd: int = 4) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), nd) if xs else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """問題×seed の行の集合を集計する（モデル・カテゴリ・seed の各単位で呼ぶ）。"""
    n = len(rows)
    ok = [r for r in rows if r["json_ok"]]
    res: dict[str, Any] = {"n": n, "n_json_ok": len(ok)}
    if not n:
        return res
    res["json_ok_rate"] = _rate(len(ok), n)
    res["schema_ok_rate"] = _rate(sum(r["schema_ok"] for r in ok), n)  # 分母は全行（JSON が壊れた行は不合格）
    res["schema_ok_rate_among_json_ok"] = _rate(sum(r["schema_ok"] for r in ok), len(ok))
    res["missing_required_rate"] = _rate(sum(r["missing_required"] for r in ok), len(ok))
    miss = collections.Counter(f for r in ok for f in r["missing"])
    res["missing_rate_by_field"] = {f: _rate(miss[f], len(ok)) for f in DESIGN_FIELDS}
    # requirement
    per_prob_det = [r["req_sat"] / r["req_det"] for r in ok if r["req_det"]]
    per_prob_strict = [(r["req_sat"] / r["req_total"]) if (r["json_ok"] and r["req_total"]) else 0.0 for r in rows]  # 不正な行は 0（厳密）
    res["req_sat_mean_determinable"] = _mean(per_prob_det)
    res["req_sat_mean_strict"] = _mean(per_prob_strict)
    res["all_req_satisfied_rate"] = _rate(sum(bool(r.get("req_all_sat")) for r in rows), n)
    tt: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r["json_ok"]:
            for t, (a, b, c) in r["req_by_type"].items():
                tt[t][0] += a
                tt[t][1] += b
                tt[t][2] += c
    res["req_by_type"] = {t: {"n": v[0], "n_determinable": v[1], "rate_determinable": _rate(v[2], v[1]), "rate_strict": _rate(v[2], v[0])} for t, v in sorted(tt.items())}
    # consistency
    res["consistency_pass_rate"] = _rate(sum(r["cons_pass"] for r in ok), len(ok))
    res["mean_warnings"] = _mean([len(r["warn"]) for r in ok])
    res["mean_soft_warnings"] = _mean([len(r["soft"]) for r in ok])
    wc = collections.Counter(w for r in ok for w in r["warn"])
    res["warning_codes"] = dict(wc.most_common(15))
    # plausibility
    pl = [r["plaus"] for r in ok if r.get("plaus")]
    res["plausible_all_rate"] = _rate(sum(1 for p in pl if p["all_in_range"]), sum(1 for p in pl if p["all_in_range"] is not None))
    res["plausible_frac_mean"] = _mean([p["frac_in_range"] for p in pl])
    res["plausible_by_field"] = {f: _rate(sum(1 for p in pl if p["fields"].get(f) is True), sum(1 for p in pl if p["fields"].get(f) is not None)) for f in PLAUS_FIELDS}
    res["plausible_group_level"] = dict(collections.Counter(p["level"] for p in pl))
    # copy
    cp = [r for r in ok if r.get("copy_any") is not None]
    if cp:
        res["copy_any_rate"] = _rate(sum(r["copy_any"] for r in cp), len(cp))
        res["copy_near_any_rate"] = _rate(sum(r["copy_near_any"] for r in cp), len(cp))
        res["copy_nbr_rate"] = _rate(sum(r["copy_nbr"] for r in cp), len(cp))
        res["copy_nbr_share_of_copy"] = _rate(sum(1 for r in cp if r["copy_any"] and r["copy_nbr"]), sum(r["copy_any"] for r in cp))
    # E
    es = [r for r in rows if r.get("e_class")]
    if es:
        c = collections.Counter(r["e_class"] for r in es)
        res["e"] = {"n": len(es), "recognized_infeasible_rate": _rate(c["refused"], len(es)), "relaxed_rate": _rate(c["relaxed"], len(es)), "filled_with_numbers_rate": _rate(c["filled"], len(es)),
                    "satisfied_and_consistent_rate": _rate(c["ok"], len(es)), "invalid_rate": _rate(c["invalid"], len(es)),
                    "marker_kinds": dict(collections.Counter(r["marker_kind"] for r in es if r["marker"])),
                    "all_req_satisfied_rate": _rate(sum(bool(r.get("req_all_sat")) for r in es), len(es))}
    return res


def diversity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """同一問題の seed 間の一致・ばらつきと、異なる問題間での同一出力（テンプレート化）。rows は 1 モデル分。"""
    by_id: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        if r.get("canon"):
            by_id[r["id"]].append(r)
    pairs = dup = 0
    jac: list[float] = []
    cvs: list[float] = []
    type_agree = []
    for rid, rs in by_id.items():
        if len(rs) < 2:
            continue
        ds = [json.loads(r["canon"]) for r in rs]
        for i in range(len(ds)):
            for j in range(i + 1, len(ds)):
                pairs += 1
                dup += rs[i]["canon"] == rs[j]["canon"]
                wa = {json.dumps(w, sort_keys=True) for w in X.weapon_items(ds[i])}
                wb = {json.dumps(w, sort_keys=True) for w in X.weapon_items(ds[j])}
                jac.append(1.0 if not wa and not wb else len(wa & wb) / len(wa | wb))
                type_agree.append(ds[i].get("ship_type_fine") == ds[j].get("ship_type_fine"))
        pc = []
        for f in ("displacement_normal_t", "length_m", "speed_kn", "power_hp"):
            v = [d[f] for d in ds if X._isnum(d.get(f))]
            if len(v) >= 2 and statistics.mean(v) > 0:
                pc.append(statistics.pstdev(v) / statistics.mean(v))
        if pc:
            cvs.append(statistics.mean(pc))  # 問題ごとに 4 項目の変動係数の平均 -> 問題間の中央値
    res: dict[str, Any] = {"n_problems_multi_seed": sum(1 for rs in by_id.values() if len(rs) >= 2), "n_seed_pairs": pairs,
                           "seed_identical_rate": _rate(dup, pairs), "seed_weapon_jaccard_mean": _mean(jac), "seed_fine_type_agree_rate": _rate(sum(type_agree), len(type_agree)),
                           "seed_numeric_cv_median": round(statistics.median(cvs), 4) if cvs else None}
    allr = [r for r in rows if r.get("canon")]
    cnt = collections.Counter(r["canon"] for r in allr)
    ids_of: dict[str, set[str]] = collections.defaultdict(set)
    tk: dict[str, set[str]] = collections.defaultdict(set)
    for r in allr:
        ids_of[r["canon"]].add(r["id"])
        tk[r["tkey"]].add(r["id"])
    res["cross_problem_identical_rate"] = _rate(sum(1 for r in allr if len(ids_of[r["canon"]]) > 1), len(allr))
    res["cross_problem_template_rate"] = _rate(sum(1 for r in allr if len(tk[r["tkey"]]) > 1), len(allr))
    res["distinct_output_ratio"] = _rate(len(cnt), len(allr))
    return res


def pair_eval(rows: list[dict[str, Any]], pair_problems: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """組×seed の期待方向の判定。rows は 1 モデル分の pairs の行。A / B の両方が JSON として読めた組だけ判定（読めない組は不正解）。
    組が正解 = meta.pair.expected の全項目が True（値が無く判定できない項目は不正解）。"""
    by: dict[tuple[str, int], dict[str, dict[str, Any]]] = collections.defaultdict(dict)
    for r in rows:
        if r.get("pair_id"):
            by[(r["pair_id"], r["seed"])][r["role"]] = r
    items: list[dict[str, Any]] = []
    for (pid, seed), ab in sorted(by.items()):
        a, b = ab.get("A"), ab.get("B")
        pa = pair_problems.get(f"{pid}_A")
        pb = pair_problems.get(f"{pid}_B")
        if not (a and b and pa and pb):
            continue
        pm = pb["meta"]["pair"]
        item: dict[str, Any] = {"pair": pid, "seed": seed, "kind": pm["kind"], "valid": bool(a["json_ok"] and b["json_ok"]), "identical": bool(a.get("canon") and a.get("canon") == b.get("canon")),
                                "results": []}
        if item["valid"]:
            for exp in pm["expected"]:
                item["results"].append({"metric": exp["metric"], "op": exp["op"], "ok": X.eval_direction(exp, a["metrics"], b["metrics"])})
        item["correct"] = bool(item["valid"] and item["results"] and all(x["ok"] is True for x in item["results"]))
        items.append(item)
    res: dict[str, Any] = {"n_pair_seed": len(items), "accuracy": _rate(sum(i["correct"] for i in items), len(items)), "both_valid_rate": _rate(sum(i["valid"] for i in items), len(items)),
                           "identical_output_rate": _rate(sum(i["identical"] for i in items), len(items))}
    kinds = sorted({i["kind"] for i in items})
    res["by_kind"] = {k: {"n": sum(1 for i in items if i["kind"] == k), "accuracy": _rate(sum(i["correct"] for i in items if i["kind"] == k), sum(1 for i in items if i["kind"] == k)),
                          "identical_rate": _rate(sum(i["identical"] for i in items if i["kind"] == k), sum(1 for i in items if i["kind"] == k))} for k in kinds}
    seeds = sorted({i["seed"] for i in items})
    res["by_seed"] = {str(s): _rate(sum(i["correct"] for i in items if i["seed"] == s), sum(1 for i in items if i["seed"] == s)) for s in seeds}
    mm: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for i in items:
        for x in i["results"]:
            key = f"{x['metric']}:{x['op']}"
            mm[key][0] += 1
            mm[key][1] += x["ok"] is not None
            mm[key][2] += x["ok"] is True
    res["by_metric"] = {k: {"n": v[0], "n_determinable": v[1], "rate": _rate(v[2], v[0])} for k, v in sorted(mm.items())}
    # accuracy over pair x seed with majority vote across seeds (a pair counts as correct when a majority of its seeds is correct)
    per_pair: dict[str, list[bool]] = collections.defaultdict(list)
    for i in items:
        per_pair[i["pair"]].append(i["correct"])
    res["accuracy_majority_over_seeds"] = _rate(sum(1 for v in per_pair.values() if sum(v) * 2 > len(v)), len(per_pair))
    return res


def summarize(rows: list[dict[str, Any]], problems: dict[str, dict[str, Any]]) -> dict[str, Any]:
    main_rows = [r for r in rows if r["category"] != "P"]
    pair_rows = [r for r in rows if r["category"] == "P"]
    seeds = sorted({r["seed"] for r in rows})
    out: dict[str, Any] = {"seeds": seeds, "n_rows": len(rows), "overall": aggregate(main_rows),
                           "by_category": {c: aggregate([r for r in main_rows if r["category"] == c]) for c in sorted({r["category"] for r in main_rows})},
                           "by_seed": {str(s): aggregate([r for r in main_rows if r["seed"] == s]) for s in seeds}}
    if pair_rows:
        out["pair_rows"] = aggregate(pair_rows)
    out["diversity"] = diversity(main_rows)
    out["diversity_by_category"] = {c: diversity([r for r in main_rows if r["category"] == c]) for c in sorted({r["category"] for r in main_rows})}
    if pair_rows:
        out["pairs"] = pair_eval(pair_rows, {k: v for k, v in problems.items() if k.startswith("pair_")})
        out["pair_diversity"] = diversity(pair_rows)
    return out


# --------------------------------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------------------------------
def load_problems(paths: list[Path]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            u = json.loads(r["messages"][1]["content"])
            r["req"], r["nbr"] = u["req"], u.get("nbr", [])
            r.setdefault("category", "D")
            out[r["id"]] = r
    return out


def load_pred_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def score_model(problems: dict[str, dict[str, Any]], preds: list[dict[str, Any]], ref: Ref | None = None, th: dict[str, Any] | None = None, skip_copy: bool = False) -> list[dict[str, Any]]:
    """問題 × seed の行を返す。モデルの予測にある seed の全てについて、全問題に行を作る（予測が無い問題は JSON 不正）。"""
    ref = ref or default_ref()
    th = th if th is not None else V.load_thresholds()
    by: dict[tuple[str, int], dict[str, Any]] = {}
    for p in preds:
        if p.get("id") in problems:
            by[(p["id"], p.get("seed", 0) or 0)] = p
    seeds = sorted({s for _, s in by}) or [0]
    rows = []
    for pid, prob in problems.items():
        for s in seeds:
            pred = by.get((pid, s))
            if pred is not None and "seed" not in pred:
                pred = {**pred, "seed": s}
            r = score_row(prob, pred, ref, th, skip_copy)
            r["seed"] = s
            rows.append(r)
    return rows


# --------------------------------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------------------------------
def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}"


def _f(x: float | None, nd: int = 3) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def summary_markdown(name: str, s: dict[str, Any]) -> str:
    o = s["overall"]
    L = [f"# {name}", "", f"行数 {s['n_rows']}（seed {s['seeds']}）。率は % 表記（特記以外）。", "", "## 全体（A〜E、問題×seed）", "",
         "| 指標 | 値 |", "|---|---:|",
         f"| JSON parse 成功率 | {_pct(o['json_ok_rate'])} |", f"| スキーマ合格率（全行基準） | {_pct(o['schema_ok_rate'])} |",
         f"| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | {_pct(o['req_sat_mean_determinable'])} |", f"| 要求充足（厳密: 欠落・不正は未充足） | {_pct(o['req_sat_mean_strict'])} |",
         f"| 要求を全て満たした行 | {_pct(o['all_req_satisfied_rate'])} |", f"| 内部整合性の合格率（JSON 有効行） | {_pct(o['consistency_pass_rate'])} |",
         f"| 警告数 平均 | {_f(o['mean_warnings'])} |", f"| 5-95% に入る率（全項目、判定可能な行） | {_pct(o['plausible_all_rate'])} |",
         f"| 5-95% に入る項目の割合 平均 | {_pct(o['plausible_frac_mean'])} |", f"| 実艦の丸写し（±1%） | {_pct(o.get('copy_any_rate'))} |",
         f"| 　うち入力の近傍艦と一致 | {_pct(o.get('copy_nbr_share_of_copy'))} |", f"| 同一問題の seed 間で完全一致 | {_pct(s['diversity']['seed_identical_rate'])} |",
         f"| 異なる問題間で同一出力 | {_pct(s['diversity']['cross_problem_identical_rate'])} |", ""]
    L += ["## カテゴリ別", "", "| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c, a in s["by_category"].items():
        L.append(f"| {c} | {a['n']} | {_pct(a['json_ok_rate'])} | {_pct(a['schema_ok_rate'])} | {_pct(a['req_sat_mean_determinable'])} | {_pct(a['all_req_satisfied_rate'])} | "
                 f"{_pct(a['consistency_pass_rate'])} | {_f(a['mean_warnings'])} | {_pct(a['plausible_all_rate'])} | {_pct(a.get('copy_any_rate'))} |")
    L += ["", "## seed 別", "", "| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |", "|---|---:|---:|---:|---:|---:|"]
    for sd, a in s["by_seed"].items():
        L.append(f"| {sd} | {a['n']} | {_pct(a['json_ok_rate'])} | {_pct(a['req_sat_mean_determinable'])} | {_pct(a['all_req_satisfied_rate'])} | {_pct(a['consistency_pass_rate'])} |")
    L += ["", "## 制約種別ごとの充足率", "", "| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |", "|---|---:|---:|---:|---:|"]
    for t, a in o["req_by_type"].items():
        L.append(f"| {t} | {a['n']} | {a['n_determinable']} | {_pct(a['rate_determinable'])} | {_pct(a['rate_strict'])} |")
    L += ["", "## フィールド別の欠損率（JSON 有効行）", "", "| フィールド | 欠損率 |", "|---|---:|"]
    L += [f"| {f} | {_pct(v)} |" for f, v in o["missing_rate_by_field"].items()]
    L += ["", f"警告コード上位: {json.dumps(o['warning_codes'], ensure_ascii=False)}", ""]
    if "E" in s["by_category"] and s["by_category"]["E"].get("e"):
        e = s["by_category"]["E"]["e"]
        L += ["## E（不可能な要求）", "", "| 指標 | 値 |", "|---|---:|", f"| 矛盾を認識（infeasible 系の応答） | {_pct(e['recognized_infeasible_rate'])} |", f"| 緩和（要求を満たさない設計を返した） | {_pct(e['relaxed_rate'])} |",
              f"| 数字で埋めた（要求を全部満たすが整合性違反） | {_pct(e['filled_with_numbers_rate'])} |", f"| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | {_pct(e['satisfied_and_consistent_rate'])} |",
              f"| JSON 不正 | {_pct(e['invalid_rate'])} |", ""]
    if "pairs" in s:
        p = s["pairs"]
        L += ["## ペアテスト", "", f"組×seed {p['n_pair_seed']}、正答率 {_pct(p['accuracy'])}%（A/B の両方が JSON として読めた率 {_pct(p['both_valid_rate'])}%、A/B が完全に同じ出力 {_pct(p['identical_output_rate'])}%、"
              f"seed 多数決の正答率 {_pct(p['accuracy_majority_over_seeds'])}%）", "", "| 変更種別 | n | 正答率 | A/B 同一出力 |", "|---|---:|---:|---:|"]
        L += [f"| {k} | {v['n']} | {_pct(v['accuracy'])} | {_pct(v['identical_rate'])} |" for k, v in p["by_kind"].items()]
        L += ["", "| 期待方向の項目 | n | 正答率 |", "|---|---:|---:|"] + [f"| {k} | {v['n']} | {_pct(v['rate'])} |" for k, v in p["by_metric"].items()] + [""]
    d = s["diversity"]
    L += ["## 多様性", "", f"seed 間 完全一致 {_pct(d['seed_identical_rate'])}%、weapons Jaccard 平均 {_f(d['seed_weapon_jaccard_mean'])}、数値 CV 中央値 {_f(d['seed_numeric_cv_median'])}、"
          f"問題間で同一出力 {_pct(d['cross_problem_identical_rate'])}%、主要諸元が同じ（テンプレート） {_pct(d['cross_problem_template_rate'])}%、出力の種類数/行数 {_pct(d['distinct_output_ratio'])}%", ""]
    return "\n".join(L)


COMPARE_METRICS = [("json_ok_rate", "JSON parse 成功率"), ("schema_ok_rate", "スキーマ合格率"), ("req_sat_mean_determinable", "要求充足（判定可能）"), ("req_sat_mean_strict", "要求充足（厳密）"),
                   ("all_req_satisfied_rate", "要求を全て満たした行"), ("consistency_pass_rate", "内部整合性の合格率"), ("mean_warnings", "警告数 平均"),
                   ("plausible_all_rate", "5-95% 全項目"), ("plausible_frac_mean", "5-95% 項目割合"), ("copy_any_rate", "実艦の丸写し（±1%）"), ("copy_nbr_rate", "近傍艦との一致")]


def _get(s: dict[str, Any], path: str) -> Any:
    cur: Any = s
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def compare_models(summaries: dict[str, dict[str, Any]], baseline: str | None = None) -> str:
    """複数モデルの summary（summarize() の戻り値）を並べ、baseline（既定は最初のモデル）との差分を出す Markdown。率は % 、差は pp（警告数のみ実数差）。"""
    names = list(summaries)
    base = baseline or names[0]
    others = [n for n in names if n != base]

    def cell(v: float | None, key: str) -> str:
        return _f(v) if key == "mean_warnings" else _pct(v)

    def table(title: str, rows: list[tuple[str, str, Any]]) -> list[str]:
        head = "| 指標 | " + " | ".join([base] + [f"{n}" for n in others] + [f"Δ {n}" for n in others]) + " |"
        L = [f"### {title}", "", head, "|---|" + "---:|" * (1 + 2 * len(others))]
        for label, key, getter in rows:
            vals = {n: getter(summaries[n]) for n in names}
            ds = []
            for n in others:
                a, b = vals[base], vals[n]
                ds.append("-" if a is None or b is None else (f"{b - a:+.3f}" if key == "mean_warnings" else f"{100 * (b - a):+.1f}"))
            L.append(f"| {label} | " + " | ".join([cell(vals[base], key)] + [cell(vals[n], key) for n in others] + ds) + " |")
        return L + [""]

    L = [f"# モデル比較（基準: {base}。率は %、Δ は pp、警告数のみ実数）", ""]
    L += table("全体（A〜E）", [(lab, k, (lambda s, k=k: _get(s, f"overall.{k}"))) for k, lab in COMPARE_METRICS])
    cats = sorted({c for s in summaries.values() for c in s["by_category"]})
    for k, lab in (("req_sat_mean_determinable", "要求充足（判定可能）"), ("all_req_satisfied_rate", "要求を全て満たした行"), ("consistency_pass_rate", "内部整合性の合格率"),
                   ("plausible_all_rate", "5-95% 全項目"), ("copy_any_rate", "実艦の丸写し")):
        L += table(f"カテゴリ別: {lab}", [(c, k, (lambda s, c=c, k=k: _get(s, f"by_category.{c}.{k}"))) for c in cats])
    seeds = sorted({sd for s in summaries.values() for sd in s["by_seed"]})
    if len(seeds) > 1:
        L += table("seed 別: 要求を全て満たした行", [(f"seed {sd}", "all", (lambda s, sd=sd: _get(s, f"by_seed.{sd}.all_req_satisfied_rate"))) for sd in seeds])
        L += table("seed 別: 内部整合性の合格率", [(f"seed {sd}", "cons", (lambda s, sd=sd: _get(s, f"by_seed.{sd}.consistency_pass_rate"))) for sd in seeds])
    L += table("多様性", [("同一問題の seed 間で完全一致", "x", lambda s: _get(s, "diversity.seed_identical_rate")), ("異なる問題間で同一出力", "x", lambda s: _get(s, "diversity.cross_problem_identical_rate")),
                         ("主要諸元が同じ（テンプレート）", "x", lambda s: _get(s, "diversity.cross_problem_template_rate")), ("出力の種類数/行数", "x", lambda s: _get(s, "diversity.distinct_output_ratio"))])
    if any("E" in s["by_category"] and s["by_category"]["E"].get("e") for s in summaries.values()):
        L += table("E（不可能な要求）", [("矛盾を認識", "x", lambda s: _get(s, "by_category.E.e.recognized_infeasible_rate")), ("緩和（要求を満たさない設計）", "x", lambda s: _get(s, "by_category.E.e.relaxed_rate")),
                                       ("数字で埋めた", "x", lambda s: _get(s, "by_category.E.e.filled_with_numbers_rate")), ("要求を満たし整合性も合格", "x", lambda s: _get(s, "by_category.E.e.satisfied_and_consistent_rate")),
                                       ("JSON 不正", "x", lambda s: _get(s, "by_category.E.e.invalid_rate"))])
    if any("pairs" in s for s in summaries.values()):
        L += table("ペアテスト", [("組×seed 正答率", "x", lambda s: _get(s, "pairs.accuracy")), ("seed 多数決の正答率", "x", lambda s: _get(s, "pairs.accuracy_majority_over_seeds")),
                                 ("A/B が完全に同じ出力", "x", lambda s: _get(s, "pairs.identical_output_rate"))])
        kinds = sorted({k for s in summaries.values() for k in _get(s, "pairs.by_kind") or {}})
        L += table("ペアテスト: 変更種別ごとの正答率", [(k, "x", (lambda s, k=k: _get(s, f"pairs.by_kind.{k}.accuracy"))) for k in kinds])
    types = sorted({t for s in summaries.values() for t in s["overall"]["req_by_type"]})
    L += table("制約種別ごとの充足率（判定可能分母）", [(t, "x", (lambda s, t=t: _get(s, f"overall.req_by_type.{t}.rate_determinable"))) for t in types])
    return "\n".join(L)


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def cmd_score(args: argparse.Namespace) -> None:
    paths = [Path(args.problems)] + ([Path(args.pairs)] if args.pairs else [])
    problems = load_problems(paths)
    ref = default_ref()
    th = V.load_thresholds(Path(args.thresholds)) if args.thresholds else V.load_thresholds()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_model: dict[str, list[Path]] = collections.defaultdict(list)
    for spec in args.pred:
        name, _, path = spec.partition("=")
        by_model[name].append(Path(path))
    summaries: dict[str, dict[str, Any]] = {}
    for name, ps in by_model.items():
        rows = score_model(problems, load_pred_rows(ps), ref, th)
        for r in rows:
            r["model"] = name
        with (out_dir / f"rows_{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        s = summarize(rows, problems)
        summaries[name] = s
        (out_dir / f"summary_{name}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / f"summary_{name}.md").write_text(summary_markdown(name, s), encoding="utf-8")
        o = s["overall"]
        print(f"{name:<18} rows={s['n_rows']} json={o['json_ok_rate']} req_sat={o['req_sat_mean_determinable']} all_req={o['all_req_satisfied_rate']} cons_pass={o['consistency_pass_rate']} "
              f"plaus={o['plausible_all_rate']} copy={o.get('copy_any_rate')}" + (f" pair_acc={s['pairs']['accuracy']}" if "pairs" in s else ""))
    if len(summaries) > 1:
        md = compare_models(summaries)
        (out_dir / "compare.md").write_text(md, encoding="utf-8")
        print(f"wrote {out_dir / 'compare.md'}")


def cmd_compare(args: argparse.Namespace) -> None:
    summaries = {}
    for p in args.summary:
        name = Path(p).stem.removeprefix("summary_")
        summaries[name] = json.loads(Path(p).read_text(encoding="utf-8"))
    md = compare_models(summaries, args.baseline)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    print(md)


def cmd_sanity(args: argparse.Namespace) -> None:
    """v0 の test（実艦の正解設計）を予測として採点し、整合性・plausibility の規則が実艦をどれだけ誤って弾くかを見る（copy は自分自身に一致するので見ない）。"""
    rows = DS.load_rows(Path(args.test))
    if args.limit:
        rows = rows[: args.limit]
    ref = default_ref()
    th = V.load_thresholds()
    scored = []
    for r in rows:
        prob = {"id": r["id"], "category": "G", "req": r["req"], "nbr": r["nbr"], "meta": {}}
        scored.append(score_row(prob, {"id": r["id"], "output": r["gold"]}, ref, th, skip_copy=True))
    a = aggregate(scored)
    codes = collections.Counter(w.split(":")[0] if not w.startswith(("ratio", "weapon_count", "era", "type_equip", "gun")) else w for r in scored for w in r["warn"])
    rep = {"n": len(scored), "consistency_pass_rate": a["consistency_pass_rate"], "false_positive_rate": round(1 - a["consistency_pass_rate"], 4), "hard_warning_codes": dict(codes.most_common()),
           "plausible_all_rate": a["plausible_all_rate"], "plausible_frac_mean": a["plausible_frac_mean"], "plausible_by_field": a["plausible_by_field"],
           "plausible_group_level": a["plausible_group_level"], "schema_ok_rate": a["schema_ok_rate"]}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


def make_forget_test(test_path: Path, ids_path: Path, out_dir: Path) -> int:
    """T1〜T3 の忘却評価用: v1_2 の test.jsonl を forget_ids.json の id だけに絞り、manifest.json と一緒に out_dir に書く
    （tools/shipknow_score.py は test と同じ場所の manifest.json から T1 のラベルを読むので、同じ場所に置く）。戻り値は書いた行数。"""
    ids = set(json.loads(Path(ids_path).read_text(encoding="utf-8"))["all_ids"])
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with (out_dir / "test.jsonl").open("w", encoding="utf-8") as fh:
        for line in Path(test_path).read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line)["id"] in ids:
                fh.write(line + "\n")
                n += 1
    man = Path(test_path).parent / "manifest.json"
    if man.exists():
        (out_dir / "manifest.json").write_text(man.read_text(encoding="utf-8"), encoding="utf-8")
    return n


def cmd_forget_test(args: argparse.Namespace) -> None:
    n = make_forget_test(Path(args.test), Path(args.ids), Path(args.out_dir))
    print(f"wrote {n} rows to {Path(args.out_dir) / 'test.jsonl'}")


def cmd_baselines(args: argparse.Namespace) -> None:
    """近傍だけから作る非 LLM ベースライン（top1 / median_vote）の予測を作って採点する（学習済みモデルが超えるべき基準線。seed は 0 の 1 本）。"""
    paths = [Path(args.problems)] + ([Path(args.pairs)] if args.pairs else [])
    problems = load_problems(paths)
    out_dir = Path(args.out_dir)
    (out_dir / "preds").mkdir(parents=True, exist_ok=True)
    summaries = {}
    for name, fn in (("top1", DS.baseline_top1), ("median_vote", DS.baseline_median_vote)):
        preds = [{"id": pid, "output": fn({"nbr": p["nbr"], "req": p["req"]})} for pid, p in problems.items()]
        with (out_dir / "preds" / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for p in preds:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        rows = score_model(problems, preds)
        s = summarize(rows, problems)
        summaries[name] = s
        (out_dir / f"summary_{name}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / f"summary_{name}.md").write_text(summary_markdown(name, s), encoding="utf-8")
        o = s["overall"]
        print(f"{name:<12} req_sat={o['req_sat_mean_determinable']} all_req={o['all_req_satisfied_rate']} cons_pass={o['consistency_pass_rate']} plaus={o['plausible_all_rate']} copy={o.get('copy_any_rate')}"
              + (f" pair_acc={s['pairs']['accuracy']}" if "pairs" in s else ""))
    (out_dir / "compare.md").write_text(compare_models(summaries), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--problems", default=str(EVAL_DIR / "problems.jsonl"))
    s.add_argument("--pairs", default=str(EVAL_DIR / "pairs.jsonl"))
    s.add_argument("--pred", action="append", required=True, help="NAME=PATH（繰り返し可。同じ NAME は合算）")
    s.add_argument("--thresholds", default=None)
    s.add_argument("--out-dir", required=True)
    s.set_defaults(fn=cmd_score)
    c = sub.add_parser("compare")
    c.add_argument("--summary", action="append", required=True)
    c.add_argument("--baseline", default=None)
    c.add_argument("--out", default=None)
    c.set_defaults(fn=cmd_compare)
    b = sub.add_parser("baselines")
    b.add_argument("--problems", default=str(EVAL_DIR / "problems.jsonl"))
    b.add_argument("--pairs", default=str(EVAL_DIR / "pairs.jsonl"))
    b.add_argument("--out-dir", required=True)
    b.set_defaults(fn=cmd_baselines)
    f = sub.add_parser("forget-test", help="v1_2 の test を forget_ids.json の id に絞った採点用 test を作る")
    f.add_argument("--test", required=True)
    f.add_argument("--ids", default=str(EVAL_DIR / "forget_ids.json"))
    f.add_argument("--out-dir", required=True)
    f.set_defaults(fn=cmd_forget_test)
    z = sub.add_parser("sanity")
    z.add_argument("--test", default=str(C.OUT_DIR / "test.jsonl"))
    z.add_argument("--limit", type=int, default=0)
    z.add_argument("--out", default=None)
    z.set_defaults(fn=cmd_sanity)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
