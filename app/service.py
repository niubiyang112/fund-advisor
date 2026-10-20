import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import analysis as A
from . import data as D

log = logging.getLogger("fund")
CN = timezone(timedelta(hours=8))
SNAPSHOT = os.path.join(D.DATA_DIR, "snapshot.json")
EXCLUDE = re.compile(r"后端|持有|定开|定期开放|封闭|滚动|最短|养老|目标日期|ETF联接?[A-Z]?\(QDII")
SHARE_SUFFIX = re.compile(r"(\(人民币\)|\(美元.*?\)|\(QDII.*?\)|人民币|美元现汇|美元现钞|[A-Z]{1,2})$")

_lock = threading.Lock()
_state = {"snapshot": None, "refreshing": False, "progress": "", "error": None}
_status_cache = {}
_all_cache = {}


def cached_status(code):
    hit = _status_cache.get(code)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    st = D.fetch_status(code)
    _status_cache[code] = (time.time(), st)
    return st


def all_funds(ft, sort, order, page):
    key = (ft, sort, order, page)
    hit = _all_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    res = D.fetch_rank_page(ft, sort, order, page, 30)
    if len(_all_cache) > 500:
        _all_cache.clear()
    _all_cache[key] = (time.time(), res)
    return res


def _base(name):
    prev = None
    while prev != name:
        prev, name = name, SHARE_SUFFIX.sub("", name)
    return name


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        if EXCLUDE.search(r["name"]):
            continue
        b = _base(r["name"])
        if b in seen or r["code"] in seen:
            continue
        seen.update([b, r["code"]])
        out.append(r)
    return out


def _fee(v):
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return 0.15


def analyse(code, is_bond=None, with_backtest=True, with_chart=False):
    d = D.fetch_fund_detail(code)
    df = A.to_frame(d)
    if len(df) < 30:
        raise ValueError("历史数据太少")
    if is_bond is None:
        is_bond = "债" in d["name"]
    m = A.metrics(df)
    out = {
        "code": code, "name": d["name"], "scale": d["scale"], "manager": d["manager"],
        "fee_rate": d["fee_rate"], "min_buy": d["min_buy"], "is_bond": is_bond,
        "metrics": A.clean(m), "signal": A.signal(m),
    }
    out["expected"] = A.expected(df["adj"])
    if with_backtest:
        out["backtest"] = A.backtest(df, is_bond=is_bond, buy_fee=_fee(d["fee_rate"]))
    if with_chart:
        out["chart"] = A.chart(df)
    return out, df


def _pct_rank(series, ascending=True):
    return series.rank(pct=True, ascending=ascending).fillna(0)


def _score_stable(items):
    rows = []
    for it in items:
        m = it["metrics"]
        if (m["years"] or 0) < 2 or (it["scale"] or 0) < 2 or m["months"] < 18:
            continue
        if m.get("jump"):
            continue
        if m["mdd_3y"] is None or m["mdd_3y"] > 6 or (m["ann_3y"] or 0) <= 1.5:
            continue
        rows.append(it)
    if not rows:
        return []
    f = pd.DataFrame([{**r["metrics"], "code": r["code"]} for r in rows])
    f["score"] = (
        0.35 * _pct_rank(f["pos_ratio_36"]) + 0.25 * _pct_rank(f["calmar"])
        + 0.2 * _pct_rank(f["sharpe"]) + 0.2 * _pct_rank(f["ann_3y"])
    ) * 100
    score = dict(zip(f["code"], f["score"]))
    for r in rows:
        r["score"] = round(float(score[r["code"]]), 1)
    return sorted(rows, key=lambda r: -r["score"])


def _score_steady(items):
    """Rose most steadily over the last ~3 months: share of up days, straight-line fit, return, shallow dips."""
    rows = [
        it for it in items
        if it["metrics"].get("days_3m", 0) >= 40 and (it["metrics"].get("r_3m") or 0) > 0
        and not it["metrics"].get("jump") and (it.get("scale") or 0) >= 1
    ]
    if not rows:
        return []
    f = pd.DataFrame([{**r["metrics"], "code": r["code"]} for r in rows])
    f["steady"] = (
        0.35 * _pct_rank(f["up_ratio_3m"]) + 0.25 * _pct_rank(f["smooth_3m"])
        + 0.25 * _pct_rank(f["r_3m"]) + 0.15 * _pct_rank(f["mdd_3m"], ascending=False)
    ) * 100
    sc = dict(zip(f["code"], f["steady"]))
    for r in rows:
        r["steady"] = round(float(sc[r["code"]]), 1)
    return sorted(rows, key=lambda r: -r["steady"])


def _score_growth(items):
    rows = []
    for it in items:
        m = it["metrics"]
        if (m["years"] or 0) < 1 or (it["scale"] or 0) < 2 or m["r_60d"] is None or m.get("jump"):
            continue
        rows.append(it)
    if not rows:
        return []
    f = pd.DataFrame([{**r["metrics"], "code": r["code"]} for r in rows])
    trend = ((f["adj"] > f["ma60"]) & (f["ma20"] > f["ma60"])).astype(float)
    f["score"] = (
        0.3 * _pct_rank(f["r_60d"]) + 0.2 * _pct_rank(f["r_20d"]) + 0.2 * _pct_rank(f["r_120d"])
        + 0.15 * _pct_rank(f["sharpe"]) + 0.15 * trend
    ) * 100
    score = dict(zip(f["code"], f["score"]))
    for r in rows:
        r["score"] = round(float(score[r["code"]]), 1)
        m = r["metrics"]
        heat = A.overheated(m["rsi"], m["r_20d"], m["sigma20"], m["vol"], m.get("mu20"))
        r["risk_note"] = "短期过热，追高风险大" if heat else ("波动很大" if (m["vol"] or 0) > 30 else "")
    return sorted(rows, key=lambda r: -r["score"])


def _slim(it):
    return {k: v for k, v in it.items() if k not in ("chart",)} | {
        "backtest": {k: v for k, v in (it.get("backtest") or {}).items() if k != "trades"}
    }


def refresh():
    with _lock:
        if _state["refreshing"]:
            return
        _state.update(refreshing=True, error=None, progress="获取基金排行")
    try:
        stable_pool = _dedupe(D.fetch_rank("zq", "1nzf", 400) + D.fetch_rank("zq", "3nzf", 300))[:220]
        growth_pool = _dedupe(
            D.fetch_rank("all", "3yzf", 250) + D.fetch_rank("all", "6yzf", 200) + D.fetch_rank("qdii", "3yzf", 40)
        )
        stable_codes = {r["code"] for r in stable_pool}
        growth_pool = [r for r in growth_pool if r["code"] not in stable_codes][:200]
        jobs = [(r["code"], True) for r in stable_pool] + [(r["code"], False) for r in growth_pool]
        results, done = {}, [0]

        def work(job):
            code, bond = job
            try:
                it, _ = analyse(code, is_bond=bond)
                results[code] = it
            except Exception as e:  # noqa: BLE001
                log.warning("skip %s: %s", code, e)
            done[0] += 1
            _state["progress"] = f"分析基金 {done[0]}/{len(jobs)}"

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(work, jobs))
        types = D.fund_types()
        for code, it in results.items():
            it["category"], it["type"] = types.get(code, ("债券" if it["is_bond"] else "其他", ""))
        steady_all = _score_steady(list(results.values()))
        stable_all = _score_stable([dict(r) for r in results.values() if r["category"] == "债券"])
        growth_codes = {c for c, b in jobs if not b}
        growth_all = _score_growth([
            dict(r) for c, r in results.items() if r["category"] != "债券" or c in growth_codes
        ])
        _state["progress"] = "检查申购状态"
        stable, growth = stable_all[:40], growth_all[:40]
        steady = [dict(r, score=r["steady"]) for r in steady_all[:40]]
        codes = list({r["code"] for r in stable + growth + steady})
        with ThreadPoolExecutor(8) as ex:
            statuses = dict(zip(codes, ex.map(D.fetch_status, codes)))
        _status_cache.update({c: (time.time(), st) for c, st in statuses.items()})
        for r in stable + growth + steady:
            r["status"] = statuses.get(r["code"], {})
        stable = [r for r in stable if "暂停" not in r["status"].get("buy", "")][:30]
        growth = [r for r in growth if "暂停" not in r["status"].get("buy", "")][:30]
        steady = [r for r in steady if "暂停" not in r["status"].get("buy", "")][:30]
        snap = {
            "updated_at": datetime.now(CN).strftime("%Y-%m-%d %H:%M"),
            "nav_date": max((r["metrics"]["nav_date"] for r in stable + growth), default=""),
            "analysed": len(results),
            "stable": [_slim(r) for r in stable],
            "growth": [_slim(r) for r in growth],
            "steady": [_slim(r) for r in steady],
            "pool_stable": [_slim(r) for r in stable_all],
            "pool_growth": [_slim(r) for r in growth_all],
        }
        tmp = SNAPSHOT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, SNAPSHOT)
        _state["snapshot"] = snap
    except Exception as e:  # noqa: BLE001
        log.exception("refresh failed")
        _state["error"] = str(e)
    finally:
        _state.update(refreshing=False, progress="")


SEED_SNAPSHOT = os.path.join(os.path.dirname(__file__), "seed_snapshot.json")


def snapshot():
    """Latest analysis; falls back to the bundled seed so a fresh server (e.g. free hosting) answers immediately."""
    if _state["snapshot"] is None:
        for path in (SNAPSHOT, SEED_SNAPSHOT):
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    _state["snapshot"] = json.load(f)
                break
    return _state["snapshot"]


def status():
    snap = snapshot()
    return {
        "refreshing": _state["refreshing"], "progress": _state["progress"], "error": _state["error"],
        "updated_at": snap and snap["updated_at"], "nav_date": snap and snap["nav_date"],
    }


RISK_STABLE_SHARE = {"稳健": 0.9, "平衡": 0.7, "进取": 0.5}
HORIZON_DAYS = {"3个月内": (63, "3个月"), "3-12个月": (126, "6个月"), "1年以上": (250, "1年")}
HORIZON_SHIFT = {"3个月内": 0.2, "3-12个月": 0.0, "1年以上": -0.1}
GROWTH_TYPES = ["混合", "股票", "指数", "QDII", "FOF"]


def _pick(cands, k, rng, exclude, max_per_type=None):
    """seed 0 → best scores; otherwise weighted random among the top 4k so “换一批” gives new combos."""
    cands = [c for c in cands if c["code"] not in exclude]
    top = cands[: max(4 * k, k + 4)]
    out, per_type = [], {}
    while top and len(out) < k:
        if rng is None:
            c = top.pop(0)
        else:
            c = rng.choices(top, weights=[max(x.get("pick_w", x["score"]), 1) for x in top])[0]
            top.remove(c)
        if max_per_type and per_type.get(c.get("type"), 0) >= max_per_type:
            continue
        st = cached_status(c["code"])
        if "暂停" in st.get("buy", ""):
            continue
        per_type[c.get("type")] = per_type.get(c.get("type"), 0) + 1
        out.append({**c, "status": st})
    return out


def _by_win(cands, days):
    """Most likely to make money first: share of past start days that were in profit after `days`, then median gain."""
    out = []
    for r in cands:
        e = next((x for x in r.get("expected") or [] if x["days"] == days), None)
        if e:
            win = 100 - e["loss_prob"]
            out.append({**r, "win": win, "hexp": e, "pick_w": max(win - 50, 1) ** 2})
    return sorted(out, key=lambda r: (-round(r["win"] / 5), -r["hexp"]["mid"]))


def _ann(r):
    m = r["metrics"]
    return m["ann_3y"] if m.get("ann_3y") is not None else (m.get("ann_1y") or 0)


def _by_ann(cands):
    """Highest past annualised return first (3y, falling back to 1y)."""
    return sorted(({**r, "pick_w": max(_ann(r), 0.5) ** 2} for r in cands), key=lambda r: -_ann(r))


def _by_steady(cands):
    """Rose most steadily over the last 3 months first."""
    return sorted(
        ({**r, "pick_w": max(r["steady"] - 40, 1) ** 2} for r in cands if r.get("steady") is not None),
        key=lambda r: -r["steady"],
    )


def _loss(m, sig):
    mdd = m["mdd_3y"] if m["mdd_3y"] is not None else (m["mdd_1y"] or 0)
    return min(mdd, sig["stop_pct"] * 1.2) if sig else mdd


def _plan_expected(items, capital, monthly):
    if not items:
        return [], None
    try:
        adjs = [A.to_frame(D.fetch_fund_detail(i["code"], max_age=24 * 3600))["adj"] for i in items]
        exp = A.portfolio_expected(adjs, [i["weight"] for i in items])
    except Exception as e:  # noqa: BLE001
        log.warning("plan expected failed: %s", e)
        return [], None
    for e in exp:
        for k in ("bad", "mid", "good", "worst"):
            e[k + "_amt"] = round(capital * e[k] / 100, 0)
    dca = None
    year = next((e for e in exp if e["days"] == 250), None)
    if monthly > 0 and year:
        def gain(pct):
            g = (1 + pct / 100) ** (1 / 12)
            return sum(monthly * (g ** k - 1) for k in range(1, 13))
        dca = {"invested": round(monthly * 12, 0), "bad": round(gain(year["bad"]), 0),
               "mid": round(gain(year["mid"]), 0), "good": round(gain(year["good"]), 0)}
    return exp, dca


def plan(capital, risk="稳健", horizon="3-12个月", max_loss=5.0, types=None, n_funds=4, monthly=0.0, seed=0, exclude=(), goal="win"):
    snap = snapshot()
    if not snap or "pool_stable" not in snap:
        return None
    types = [t for t in (types or ["债券"] + GROWTH_TYPES) if t]
    rng = random.Random(seed) if seed else None
    notes = []
    stable_share = min(max(RISK_STABLE_SHARE.get(risk, 0.9) + HORIZON_SHIFT.get(horizon, 0), 0.3), 1.0)
    stable_c = [r for r in snap["pool_stable"] if r["signal"]["level"] != "sell" and not r["metrics"].get("jump")]
    growth_types = [t for t in types if t in GROWTH_TYPES]
    growth_c = [r for r in snap["pool_growth"] if r["signal"]["level"] == "buy" and r.get("category") in growth_types and not r["metrics"].get("jump")]
    hdays, hlabel = HORIZON_DAYS.get(horizon, (126, "6个月"))
    stable_c, growth_c = _by_win(stable_c, hdays), _by_win(growth_c, hdays)
    if goal == "ann":
        stable_c, growth_c = _by_ann(stable_c), _by_ann(growth_c)
    elif goal == "steady":
        stable_c, growth_c = _by_steady(stable_c), _by_steady(growth_c)
    if "债券" not in types:
        notes.append("你没选债券基金，但为了控制亏损，稳健仓位仍然只用债券基金。")
    if not growth_types:
        stable_share = 1.0
    n_funds = int(min(max(n_funds, 1), 10))
    est_s = sum(_loss(r["metrics"], None) for r in stable_c[:5]) / max(len(stable_c[:5]), 1)
    est_g = sum(_loss(r["metrics"], r["signal"]) for r in growth_c[:5]) / max(len(growth_c[:5]), 1)
    growth_share = 1 - stable_share
    if growth_share > 0 and growth_c and est_g > est_s:
        cap = max((max_loss - est_s) / (est_g - est_s), 0)
        if cap < growth_share:
            growth_share = round(cap, 2)
            notes.append(f"按你能接受的最大亏损 {max_loss:g}%，进攻仓位最多只能放 {growth_share * 100:.0f}%。")
    if growth_share > 0 and not growth_c:
        notes.append("你选的类型里今天没有处于“买入”信号的趋势基金，进攻仓位暂时放在债券基金里，等信号出现再买。")
        growth_share = 0
    n_growth = min(max(round(n_funds * growth_share), 1), n_funds - 1) if growth_share > 0 and n_funds > 1 else (1 if growth_share >= 0.5 else 0)
    if growth_share > 0 and n_growth == 0:
        growth_share = 0
    n_stable = n_funds - n_growth
    if n_stable == 0:
        growth_share = 1.0
    stable_pick = _pick(stable_c, n_stable, rng, set(exclude)) if n_stable else []
    growth_pick = _pick(growth_c, n_growth, rng, set(exclude), max_per_type=max(1, (n_growth + 1) // 2)) if n_growth else []
    if not growth_pick:
        growth_share = 0
    if not stable_pick:
        growth_share = 1.0 if growth_pick else 0
        if not growth_pick:
            notes.append("今天没有合适的基金，建议先放货币基金（余额宝）。")
    items = []
    for group, share, role in ((stable_pick, 1 - growth_share, "稳健底仓"), (growth_pick, growth_share, "进攻仓位（高风险）")):
        for r in group:
            w = share / len(group)
            amt = capital * w
            level = r["signal"]["level"]
            if role == "稳健底仓":
                if level == "buy":
                    how, first = "今天 15:00 前一次性买入（按今日净值成交）", amt
                elif level == "hold":
                    how, first = "最近涨得偏快：今天先买一半，剩下一半等信号变成“买入”再买（最多等 2 周，到期也买入）", amt / 2
                else:
                    how, first = "趋势还没走好：今天先买 1/3，信号变成“买入”后再买剩下的", amt / 3
            else:
                how, first = "分 3 次买：今天买 1/3；之后每周一信号仍为“买入”再各买 1/3；信号变“卖出”就全部卖出", amt / 3
            mon = monthly * w
            if mon >= 1:
                how += f"；每月固定一天定投 {mon:.0f} 元，信号为“卖出”的月份暂停"
            items.append({
                "code": r["code"], "name": r["name"], "role": role, "category": r.get("category"),
                "weight": round(w * 100, 1), "amount": round(amt, 0), "first_buy": round(first, 0),
                "monthly": round(mon, 0), "how": how, "status": r.get("status"),
                "signal": r["signal"], "metrics": r["metrics"], "score": r["score"], "win": r["win"], "hexp": r["hexp"],
            })
    hist_ann = sum(i["weight"] * (i["metrics"]["ann_3y"] or i["metrics"]["ann_1y"] or 0) for i in items) / 100
    hist_mdd = sum(i["weight"] * (i["metrics"]["mdd_3y"] or i["metrics"]["mdd_1y"] or 0) for i in items) / 100
    worst = sum(i["weight"] * _loss(i["metrics"], i["signal"] if i["role"] != "稳健底仓" else None) for i in items) / 100
    expected, dca = _plan_expected(items, capital, monthly)
    hp = next((e for e in expected if e["days"] == hdays), None)
    return {
        "horizon_days": hdays, "horizon_label": hlabel, "goal": goal, "win": 100 - hp["loss_prob"] if hp else None,
        "expected": expected, "dca": dca,
        "capital": capital, "monthly": monthly, "risk": risk, "horizon": horizon, "max_loss": max_loss,
        "types": types, "n_funds": n_funds, "seed": seed, "items": items, "notes": notes,
        "stable_pct": round((1 - growth_share) * 100) if items else 0, "growth_pct": round(growth_share * 100) if items else 0,
        "hist_ann": round(hist_ann, 2), "hist_mdd": round(hist_mdd, 2), "est_loss": round(worst, 2),
        "expected_year": round(capital * hist_ann / 100, 0),
        "updated_at": snap["updated_at"], "nav_date": snap["nav_date"],
    }


def holding_advice(h):
    code = h["code"]
    it, df = analyse(code, with_backtest=False)
    m, sig = it["metrics"], it["signal"]
    buy_date = pd.Timestamp(h["buy_date"])
    after = df[df.index >= buy_date]
    if after.empty:
        after = df.iloc[-1:]
    buy_adj = float(after["adj"].iloc[0])
    buy_unit = float(after["unit"].iloc[0])
    peak = float(after["adj"].max())
    profit = (m["adj"] / buy_adj - 1) * 100
    from_peak = (1 - m["adj"] / peak) * 100
    held = (pd.Timestamp(datetime.now(CN).date()) - buy_date).days
    amount = float(h.get("amount") or 0)
    stop, take = sig["stop_pct"], sig["take_pct"]
    took_half = bool(h.get("took_half"))
    reasons = []
    if held < A.MIN_HOLD_DAYS:
        action, level = "持有（未满7天）", "hold"
        reasons.append(f"才持有 {held} 天，7 天内卖出要交 1.5% 赎回费，除非暴跌否则先拿着")
    elif from_peak >= stop:
        action, level = "卖出", "sell"
        reasons.append(f"从买入后的最高点回撤 {from_peak:.2f}%，超过止损线 {stop}%")
    elif sig["level"] == "sell":
        action, level = "卖出", "sell"
        reasons.append("中期趋势转弱（跌破 60 日均线且 20 日均线下穿）")
    elif not took_half and profit >= take:
        action, level = "止盈一半", "take"
        reasons.append(f"盈利 {profit:.2f}% 已达到止盈线 {take}%：先卖一半锁定利润，剩下的继续拿")
    elif sig["level"] == "buy":
        action, level = "持有（可加仓）", "buy"
        reasons.append("趋势向上，继续持有；还没买满计划金额的可以加仓")
    else:
        action, level = "持有", "hold"
        reasons.append("没有触发止盈或止损，继续持有")
    stop_adj = peak * (1 - stop / 100)
    ratio = m["nav"] / m["adj"]
    return {
        "code": code, "name": it["name"], "buy_date": h["buy_date"], "held_days": held,
        "buy_nav": round(buy_unit, 4), "nav": m["nav"], "nav_date": m["nav_date"],
        "profit_pct": round(profit, 2), "profit": round(amount * profit / 100, 2), "amount": amount,
        "from_peak": round(from_peak, 2), "action": action, "level": level, "reasons": reasons,
        "stop_nav": round(stop_adj * ratio, 4), "take_nav": round(buy_adj * (1 + take / 100) * ratio, 4),
        "stop_pct": stop, "take_pct": take, "signal": sig, "expected": it["expected"],
    }


def _decide(held, newest_days, from_peak, profit, sig, took_half):
    stop, take = sig["stop_pct"], sig["take_pct"]
    reasons = []
    if held < A.MIN_HOLD_DAYS:
        action, level = "持有（未满7天）", "hold"
        reasons.append(f"才持有 {held} 天，7 天内卖出要交 1.5% 赎回费，除非暴跌否则先拿着")
    elif from_peak >= stop:
        action, level = "卖出", "sell"
        reasons.append(f"从买入后的最高点回撤 {from_peak:.2f}%，超过止损线 {stop}%")
    elif sig["level"] == "sell":
        action, level = "卖出", "sell"
        reasons.append("中期趋势转弱（跌破 60 日均线且 20 日均线下穿）")
    elif not took_half and profit >= take:
        action, level = "止盈一半", "take"
        reasons.append(f"持仓盈利 {profit:.2f}% 已达到止盈线 {take}%：先卖一半锁定利润，剩下的继续拿")
    elif sig["level"] == "buy":
        action, level = "持有（可加仓）", "buy"
        reasons.append("趋势向上，继续持有；还没买满计划金额的可以加仓")
    else:
        action, level = "持有", "hold"
        reasons.append("没有触发止盈或止损，继续持有")
    if level == "sell" and newest_days < A.MIN_HOLD_DAYS:
        reasons.append(f"最近一笔加仓才 {newest_days} 天，这部分卖出要交 1.5% 赎回费")
    return action, level, reasons


def position(p):
    """Share-based ledger: each buy gets shares at that day's NAV, sells remove shares FIFO at the sell day's NAV."""
    code = p["code"]
    it, df = analyse(code, with_backtest=False)
    fee = _fee(D.fetch_fund_detail(code)["fee_rate"])
    m, sig = it["metrics"], it["signal"]
    adj_now, nav_now = m["adj"], m["nav"]
    ratio = nav_now / adj_now
    trades = sorted(p["trades"], key=lambda t: (t["date"], 0 if t["type"] == "buy" else 1))
    no_amount = not any(t["type"] == "buy" and (t.get("amount") or 0) > 0 for t in trades)
    lots, flows, rows = [], [], []
    invested = proceeds = realized = bought_shares = 0.0
    last_buy = last_sell = None
    for t in trades:
        ts = pd.Timestamp(t["date"])
        i = int(df.index.searchsorted(ts))
        est = i >= len(df)
        i = min(i, len(df) - 1)
        adj, unit = float(df["adj"].iloc[i]), float(df["unit"].iloc[i])
        row = {"id": t.get("id"), "date": t["date"], "type": t["type"], "nav": round(unit, 4),
               "nav_date": df.index[i].strftime("%Y-%m-%d"), "estimated": est}
        if t["type"] == "buy":
            amt = float(t.get("amount") or 0) or (1000.0 if no_amount else 0)
            if amt <= 0:
                continue
            sh = amt / (1 + fee / 100) / adj
            lots.append([ts, sh, amt])
            invested += amt
            bought_shares += sh
            flows.append((ts, amt))
            last_buy = ts
            row.update(amount=round(amt, 2), shares=round(sh * adj / unit, 2))
        else:
            total = sum(x[1] for x in lots)
            if total <= 0:
                continue
            sh = total * min(float(t.get("frac") or 0), 1) if t.get("frac") else min(float(t.get("amount") or 0) / adj, total)
            gross = cost = 0.0
            left = sh
            for lot in lots:
                if left <= 1e-12:
                    break
                take = min(lot[1], left)
                c = lot[2] * take / lot[1]
                f = 1.5 if (ts - lot[0]).days < A.MIN_HOLD_DAYS else 0
                gross += take * adj * (1 - f / 100)
                cost += c
                lot[1] -= take
                lot[2] -= c
                left -= take
            lots = [x for x in lots if x[1] > 1e-9]
            proceeds += gross
            realized += gross - cost
            last_sell = ts
            row.update(amount=round(gross, 2), shares=round(sh * adj / unit, 2), profit=round(gross - cost, 2))
        rows.append(row)
    shares = sum(x[1] for x in lots)
    cost = sum(x[2] for x in lots)
    value = shares * adj_now
    total = value + proceeds - invested
    today = pd.Timestamp(datetime.now(CN).date())
    out = {
        "code": code, "name": it["name"], "nav": nav_now, "nav_date": m["nav_date"], "no_amount": no_amount,
        "invested": round(invested, 2), "cost": round(cost, 2), "value": round(value, 2),
        "shares": round(value / nav_now, 2) if nav_now else 0, "proceeds": round(proceeds, 2),
        "floating": round(value - cost, 2), "realized": round(realized, 2), "total": round(total, 2),
        "total_pct": round(total / invested * 100, 2) if invested else 0,
        "profit_pct": round((value / cost - 1) * 100, 2) if cost else 0,
        "cost_nav": round(cost / shares * ratio, 4) if shares else None,
        "trades": rows, "signal": sig, "expected": it["expected"],
    }
    if invested:
        hold_value = bought_shares * adj_now
        out["cmp_hold"] = round(hold_value - invested, 2)
        fs = A.follow_signals(df, flows, is_bond=it["is_bond"], buy_fee=fee)
        if fs:
            out["cmp_signal"] = round(fs["value"] - invested, 2)
            out["signal_trades"] = fs["n_trades"]
            out["signal_in"] = fs["in_position"]
    if shares <= 1e-9:
        return out | {"level": "closed", "action": "已清仓", "reasons": ["已经全部卖出，收益已锁定"]}
    first = min(x[0] for x in lots)
    newest = max(x[0] for x in lots)
    peak = float(df.loc[df.index >= first, "adj"].max()) if (df.index >= first).any() else adj_now
    from_peak = max((1 - adj_now / peak) * 100, 0)
    took_half = bool(last_sell is not None and (last_buy is None or last_sell >= last_buy))
    action, level, reasons = _decide((today - first).days, (today - newest).days, from_peak, out["profit_pct"], sig, took_half)
    return out | {
        "action": action, "level": level, "reasons": reasons, "held_days": (today - first).days,
        "from_peak": round(from_peak, 2), "stop_nav": round(peak * (1 - sig["stop_pct"] / 100) * ratio, 4),
        "take_nav": round(cost / shares * (1 + sig["take_pct"] / 100) * ratio, 4),
    }


def scheduler():
    """Refresh at startup if stale, then every evening after NAVs are published (21:30 Beijing)."""
    while True:
        try:
            snap = snapshot()
            now = datetime.now(CN)
            last = datetime.strptime(snap["updated_at"], "%Y-%m-%d %H:%M").replace(tzinfo=CN) if snap else None
            due = last is None or now - last > timedelta(hours=24)
            evening = now.replace(hour=21, minute=30, second=0, microsecond=0)
            if now >= evening and (last is None or last < evening):
                due = True
            if due:
                refresh()
        except Exception:  # noqa: BLE001
            log.exception("scheduler")
        time.sleep(600)
