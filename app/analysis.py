import math

import numpy as np
import pandas as pd

TRADING_DAYS = 250
RISK_FREE = 1.5  # % per year
MIN_HOLD_DAYS = 7


def to_frame(detail):
    """Adjusted NAV (dividends reinvested, from daily equityReturn) + unit NAV, indexed by Beijing date."""
    rows = detail["nav"]
    if not rows:
        return pd.DataFrame(columns=["unit", "adj"])
    df = pd.DataFrame(rows, columns=["ts", "unit", "ret"])
    df["date"] = pd.to_datetime(df["ts"] + 8 * 3600 * 1000, unit="ms").dt.normalize()
    df = df.drop_duplicates("date", keep="last").set_index("date").sort_index()
    df["ret"] = pd.to_numeric(df["ret"], errors="coerce").fillna(0.0)
    df["adj"] = (1 + df["ret"] / 100).cumprod()
    return df[["unit", "adj"]]


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def _max_drawdown(s):
    if len(s) < 2:
        return 0.0
    return float(((s / s.cummax()) - 1).min() * -100)


def _streaks(up):
    best = cur = 0
    for u in up:
        cur = cur + 1 if u else 0
        best = max(best, cur)
    return best, cur


def steady_stats(s, n=63):
    """How smoothly the fund rose over the last n trading days."""
    w = s.iloc[-n - 1:]
    if len(w) < n // 2:
        return {}
    d = w.pct_change(fill_method=None).dropna()
    up = (d > 0).tolist()
    best, cur = _streaks(up)
    y = np.log(w.values)
    t = np.arange(len(y))
    slope, icpt = np.polyfit(t, y, 1)
    ss = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ((y - (slope * t + icpt)) ** 2).sum() / ss if ss > 0 else 0.0
    return {
        "up_days_3m": int(sum(up)),
        "days_3m": int(len(up)),
        "up_ratio_3m": float(sum(up) / len(up) * 100),
        "max_streak_3m": best,
        "cur_streak": cur,
        "smooth_3m": float(max(r2, 0) * 100 * (1 if slope > 0 else -1)),
        "r_3m": float((w.iloc[-1] / w.iloc[0] - 1) * 100),
        "mdd_3m": _max_drawdown(w),
    }


def _ret(s, days):
    if len(s) <= days:
        return None
    return float((s.iloc[-1] / s.iloc[-1 - days] - 1) * 100)


def _r(v, n=2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), n)


def jump_mask(adj):
    """One-off abnormal daily moves (e.g. NAV windfall from mass redemptions) that will not repeat."""
    r = adj.pct_change(fill_method=None).fillna(0.0)
    tail = r.iloc[-750:]
    sigma = 1.4826 * float((tail - tail.median()).abs().median())
    return r.abs() > max(12 * sigma, 0.03)


def metrics(df):
    s = df["adj"]
    end = s.index[-1]
    years = (end - s.index[0]).days / 365.25
    s1 = s[s.index > end - pd.DateOffset(years=1)]
    s3 = s[s.index > end - pd.DateOffset(years=3)]
    monthly_all = s.resample("ME").last().pct_change(fill_method=None).dropna() * 100
    full_months = monthly_all[monthly_all.index < end.to_period("M").to_timestamp()]
    m36 = full_months.iloc[-36:]
    m12 = full_months.iloc[-12:]
    daily = s1.pct_change(fill_method=None).dropna()
    vol = float(daily.std() * math.sqrt(TRADING_DAYS) * 100) if len(daily) > 20 else None
    ann1 = _ret(s, len(s1) - 1) if len(s1) > 1 else None
    ann3 = None
    if years >= 1 and len(s3) > 1:
        span = max((s3.index[-1] - s3.index[0]).days / 365.25, 0.5)
        ann3 = float(((s3.iloc[-1] / s3.iloc[0]) ** (1 / span) - 1) * 100)
    mdd1 = _max_drawdown(s1)
    mdd3 = _max_drawdown(s3)
    rsi = _rsi(s)
    ma20 = s.rolling(20).mean()
    ma60 = s.rolling(60).mean()
    sigma20 = float(daily.std() * math.sqrt(20) * 100) if len(daily) > 20 else None
    mu20 = float(daily.mean() * 20 * 100) if len(daily) > 20 else 0.0
    u3 = df["unit"][df.index > end - pd.DateOffset(years=3)].dropna()
    jm = jump_mask(s)
    jr = s.pct_change(fill_method=None)[jm & (s.index > end - pd.DateOffset(years=3))]
    return {
        "jump": float(jr.abs().max() * 100) if len(jr) else 0.0,
        "years": _r(years, 1),
        "nav": float(df["unit"].iloc[-1]),
        "nav_date": end.strftime("%Y-%m-%d"),
        "r_1d": _ret(s, 1),
        "r_20d": _ret(s, 20),
        "r_60d": _ret(s, 60),
        "r_120d": _ret(s, 120),
        "ann_1y": ann1,
        "ann_3y": ann3,
        "vol": vol,
        "sigma20": sigma20,
        "mu20": mu20,
        "mdd_1y": mdd1,
        "mdd_3y": mdd3,
        "cur_dd": float((1 - s.iloc[-1] / s3.max()) * 100) if len(s3) else 0.0,
        "mdd_all": _max_drawdown(s),
        "min_nav_3y": float(u3.min()) if len(u3) else None,
        "max_nav_3y": float(u3.max()) if len(u3) else None,
        "min_nav_date": u3.idxmin().strftime("%Y-%m-%d") if len(u3) else None,
        "sharpe": (ann1 - RISK_FREE) / vol if ann1 is not None and vol else None,
        "calmar": ann3 / max(mdd3, 0.1) if ann3 is not None else None,
        "pos_ratio_36": float((m36 > 0).mean() * 100) if len(m36) else None,
        "pos_ratio_12": float((m12 > 0).mean() * 100) if len(m12) else None,
        "months": int(len(m36)),
        "worst_month": float(m36.min()) if len(m36) else None,
        "avg_month": float(m36.mean()) if len(m36) else None,
        "rsi": float(rsi.iloc[-1]),
        "ma20": float(ma20.iloc[-1]) if not math.isnan(ma20.iloc[-1]) else None,
        "ma60": float(ma60.iloc[-1]) if not math.isnan(ma60.iloc[-1]) else None,
        "adj": float(s.iloc[-1]),
        **steady_stats(s),
    }


def overheated(rsi, r_20d, sigma20, vol, mu20=0.0):
    """20-day gain far above the fund's normal pace, or RSI>75 for volatile funds
    (RSI is meaningless for smooth low-volatility bond NAVs)."""
    sigma20 = sigma20 or 1.0
    mu20 = 0.0 if mu20 is None or mu20 != mu20 else mu20
    if (r_20d or 0) > mu20 + 3 * sigma20:
        return True
    return (vol or 0) > 5 and rsi > 75


def risk_levels(m):
    """Stop-loss / take-profit percentages scaled to the fund's own volatility."""
    sigma = m.get("sigma20") or 1.0
    stop = min(max(3 * sigma, 1.0), 15.0)
    take = min(max(4 * sigma, 1.5), 30.0)
    return round(stop, 2), round(take, 2)


def signal(m):
    price, ma20, ma60, rsi = m["adj"], m["ma20"], m["ma60"], m["rsi"]
    stop, take = risk_levels(m)
    nav = m["nav"]
    out = {
        "stop_pct": stop,
        "take_pct": take,
        "stop_nav": round(nav * (1 - stop / 100), 4),
        "take_nav": round(nav * (1 + take / 100), 4),
        "reasons": [],
    }
    if ma20 is None or ma60 is None:
        out.update(action="观望", level="neutral", reasons=["历史数据不足 60 个交易日"])
        return out
    sigma = m.get("sigma20") or 1.0
    trend_up = price > ma60 and ma20 > ma60
    trend_down = price < ma60 and ma20 < ma60
    overheat = overheated(rsi, m["r_20d"], sigma, m["vol"], m.get("mu20"))
    r = out["reasons"]
    r.append(f"净值{'高于' if price > ma60 else '低于'}60日均线，20日均线{'在上' if ma20 > ma60 else '在下'}")
    r.append(f"RSI14={rsi:.0f}（>75 偏热，<30 偏冷）")
    if trend_down:
        out.update(action="卖出/回避", level="sell")
        r.append("中期趋势向下：持有的考虑卖出，没买的先别买")
    elif trend_up and overheat:
        out.update(action="持有，不追高", level="hold")
        r.append("趋势向上但短期涨得过快：已持有继续拿，没买的等回调到20日均线附近再买")
    elif trend_up:
        out.update(action="买入", level="buy")
        r.append("趋势向上且不过热：可以买入，严格设置止损")
    elif price > ma20:
        out.update(action="小额分批", level="watch")
        r.append("趋势正在修复：可以先买计划仓位的 1/3，站稳 60 日均线后再加")
    else:
        out.update(action="观望", level="neutral")
        r.append("方向不明：先观望")
    return out


def indicators(df):
    s = df["adj"]
    out = pd.DataFrame({"adj": s, "unit": df["unit"]})
    out["ma20"] = s.rolling(20).mean()
    out["ma60"] = s.rolling(60).mean()
    out["rsi"] = _rsi(s)
    daily = s.pct_change(fill_method=None)
    out["sigma20"] = daily.rolling(250, min_periods=60).std() * math.sqrt(20) * 100
    out["vol"] = daily.rolling(250, min_periods=60).std() * math.sqrt(TRADING_DAYS) * 100
    out["mu20"] = daily.rolling(250, min_periods=60).mean() * 20 * 100
    out["r20"] = (s / s.shift(20) - 1) * 100
    return out


def backtest(df, is_bond=False, buy_fee=0.15, years=3):
    """Trend strategy: signal on day t close, trade at t+1 NAV. Compared to buy & hold."""
    ind = indicators(df)
    start = ind.index[-1] - pd.DateOffset(years=years)
    ind = ind[ind.index > start].dropna(subset=["ma60"])
    if len(ind) < 30:
        return None
    s = ind["adj"].values
    dates = ind.index
    cash, units = 1.0, 0.0
    pos = False
    entry_i = entry_px = peak = 0
    pending = None
    trades, equity = [], []
    took_half = False
    for i in range(len(ind)):
        px = s[i]
        if pending is not None:
            act, frac = pending
            pending = None
            if act == "buy" and not pos:
                units = cash * (1 - buy_fee / 100) / px
                cash = 0.0
                pos, entry_i, entry_px, peak, took_half = True, i, px, px, False
                trades.append({"date": dates[i].strftime("%Y-%m-%d"), "type": "buy", "adj": px})
            elif act == "sell" and pos:
                held = (dates[i] - dates[entry_i]).days
                fee = 1.5 if held < MIN_HOLD_DAYS else (0 if is_bond else (0.5 if held < 365 else 0))
                sell_units = units * frac
                cash += sell_units * px * (1 - fee / 100)
                units -= sell_units
                trades.append({
                    "date": dates[i].strftime("%Y-%m-%d"), "type": "sell" if frac == 1 else "take",
                    "adj": px, "pnl": round((px / entry_px - 1) * 100, 2),
                })
                if frac == 1:
                    pos, units = False, 0.0
                else:
                    took_half = True
        equity.append(cash + units * px)
        row = ind.iloc[i]
        sigma = row["sigma20"] if not math.isnan(row["sigma20"]) else 1.0
        stop = min(max(3 * sigma, 1.0), 15.0)
        take = min(max(4 * sigma, 1.5), 30.0)
        if not pos:
            if px > row["ma60"] and row["ma20"] > row["ma60"] and not overheated(row["rsi"], row["r20"], sigma, row["vol"], row["mu20"]):
                pending = ("buy", 1)
        else:
            peak = max(peak, px)
            held = (dates[i] - dates[entry_i]).days
            if held + 1 < MIN_HOLD_DAYS:
                continue
            trend_down = px < row["ma60"] and row["ma20"] < row["ma60"]
            trailing = (1 - px / peak) * 100 >= stop
            if trend_down or trailing:
                pending = ("sell", 1)
            elif not took_half and (px / entry_px - 1) * 100 >= take:
                pending = ("sell", 0.5)
    eq = pd.Series(equity, index=dates)
    bh = pd.Series(s / s[0], index=dates)
    closed = [t for t in trades if t["type"] == "sell"]
    wins = [t for t in closed if t["pnl"] > 0]
    span = max((dates[-1] - dates[0]).days / 365.25, 0.25)
    return {
        "start": dates[0].strftime("%Y-%m-%d"),
        "end": dates[-1].strftime("%Y-%m-%d"),
        "strategy_return": _r((eq.iloc[-1] - 1) * 100),
        "strategy_ann": _r(((eq.iloc[-1]) ** (1 / span) - 1) * 100),
        "strategy_mdd": _r(_max_drawdown(eq)),
        "hold_return": _r((bh.iloc[-1] - 1) * 100),
        "hold_ann": _r(((bh.iloc[-1]) ** (1 / span) - 1) * 100),
        "hold_mdd": _r(_max_drawdown(bh)),
        "trades": trades[-40:],
        "n_trades": len(closed),
        "win_rate": _r(len(wins) / len(closed) * 100 if closed else None, 0),
        "in_position": pos,
    }


def chart(df, years=3):
    ind = indicators(df)
    ind = ind[ind.index > ind.index[-1] - pd.DateOffset(years=years)]
    s = df["adj"]
    monthly = (s.resample("ME").last().pct_change(fill_method=None) * 100).dropna().iloc[-36:]

    def col(c):
        return [None if math.isnan(v) else round(float(v), 5) for v in ind[c]]

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in ind.index],
        "adj": col("adj"),
        "ma20": col("ma20"),
        "ma60": col("ma60"),
        "months": [d.strftime("%Y-%m") for d in monthly.index],
        "monthly": [round(float(v), 2) for v in monthly],
    }


def clean(d):
    return {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


HORIZONS = [("1个月", 21), ("3个月", 63), ("6个月", 126), ("1年", 250)]


def expected(adj, years=3):
    """Distribution of past returns over each holding horizon (every start day in the window)."""
    adj = adj.dropna()
    if adj.empty:
        return []
    r = adj.pct_change(fill_method=None).fillna(0.0).mask(jump_mask(adj), 0.0)
    adj = (1 + r).cumprod()
    s = adj[adj.index >= adj.index[-1] - pd.DateOffset(years=years)]
    out = []
    for label, n in HORIZONS:
        if len(s) < n + 40:
            continue
        r = ((s / s.shift(n) - 1).dropna() * 100).values
        out.append({
            "label": label, "days": n,
            "bad": _r(np.percentile(r, 10)), "mid": _r(np.percentile(r, 50)), "good": _r(np.percentile(r, 90)),
            "loss_prob": _r((r < 0).mean() * 100, 0), "worst": _r(r.min()),
        })
    return out


def blend(adjs, weights):
    """Daily-rebalanced portfolio NAV from several adjusted NAV series."""
    rets = pd.concat([a.pct_change() for a in adjs], axis=1).dropna(how="all").fillna(0.0)
    w = np.array(weights, dtype=float)
    return (1 + rets.values @ (w / w.sum())).cumprod() if len(rets) else np.array([]), rets.index


def portfolio_expected(adjs, weights, years=3):
    vals, idx = blend(adjs, weights)
    if not len(vals):
        return []
    return expected(pd.Series(vals, index=idx), years)


def follow_signals(df, flows, is_bond=False, buy_fee=0.15):
    """Same buy dates/amounts as the user, but sell and re-buy strictly by the signals (trade at next NAV)."""
    ind = indicators(df).dropna(subset=["ma60"])
    if not flows or ind.empty:
        return None
    ind = ind[ind.index >= pd.Timestamp(min(d for d, _ in flows))]
    if ind.empty:
        return None
    dates, s = ind.index, ind["adj"].values
    inflow = {}
    for d, a in flows:
        i = min(int(dates.searchsorted(pd.Timestamp(d))), len(dates) - 1)
        inflow[i] = inflow.get(i, 0.0) + a
    cash = units = 0.0
    pos, took_half, pending = True, False, None
    entry_i, entry_px, peak, n = 0, s[0], s[0], 0
    for i in range(len(ind)):
        px = s[i]
        if pending is not None:
            act, frac = pending
            pending = None
            if act == "buy" and not pos and cash > 0:
                units, cash = cash * (1 - buy_fee / 100) / px, 0.0
                pos, entry_i, entry_px, peak, took_half = True, i, px, px, False
                n += 1
            elif act == "sell" and pos:
                held = (dates[i] - dates[entry_i]).days
                fee = 1.5 if held < MIN_HOLD_DAYS else (0 if is_bond else (0.5 if held < 365 else 0))
                sell = units * frac
                cash += sell * px * (1 - fee / 100)
                units -= sell
                n += 1
                if frac == 1:
                    pos, units = False, 0.0
                else:
                    took_half = True
        a = inflow.get(i, 0.0)
        if a:
            if pos:
                new = a * (1 - buy_fee / 100) / px
                entry_px = (entry_px * units + px * new) / (units + new) if units else px
                if not units:
                    entry_i, peak = i, px
                units += new
            else:
                cash += a
        row = ind.iloc[i]
        sigma = row["sigma20"] if not math.isnan(row["sigma20"]) else 1.0
        stop = min(max(3 * sigma, 1.0), 15.0)
        take = min(max(4 * sigma, 1.5), 30.0)
        if not pos:
            if px > row["ma60"] and row["ma20"] > row["ma60"] and not overheated(row["rsi"], row["r20"], sigma, row["vol"], row["mu20"]):
                pending = ("buy", 1)
        elif units:
            peak = max(peak, px)
            if (dates[i] - dates[entry_i]).days + 1 < MIN_HOLD_DAYS:
                continue
            if (px < row["ma60"] and row["ma20"] < row["ma60"]) or (1 - px / peak) * 100 >= stop:
                pending = ("sell", 1)
            elif not took_half and (px / entry_px - 1) * 100 >= take:
                pending = ("sell", 0.5)
    return {"value": cash + units * s[-1], "n_trades": n, "in_position": pos}
