import json
import os
import re
import time
from datetime import date, timedelta

import requests

DATA_DIR = os.environ.get("FUND_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
NAV_DIR = os.path.join(DATA_DIR, "nav")
os.makedirs(NAV_DIR, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Referer": "https://fund.eastmoney.com/"})

RANK_FIELDS = [
    "code", "name", "pinyin", "date", "nav", "acc_nav", "r_1d", "r_1w", "r_1m", "r_3m", "r_6m",
    "r_1y", "r_2y", "r_3y", "r_ytd", "r_since", "established",
]


def _get(url, params=None, referer=None, timeout=20, retries=3):
    headers = {"Referer": referer} if referer else None
    last = None
    for i in range(retries):
        try:
            r = session.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + i)
    raise last


def _num(v):
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return None


def fetch_rank_page(fund_type="all", sort="1nzf", order="desc", page=1, size=200):
    """fund_type: all/gp/hh/zq/zs/qdii/fof.
    sort: rzdf(日) zzf(周) 1yzf(月) 3yzf 6yzf 1nzf(1年) 2nzf 3nzf jnzf(今年) lnzf(成立以来)."""
    ed = date.today()
    sd = ed - timedelta(days=365)
    text = _get(
        "https://fund.eastmoney.com/data/rankhandler.aspx",
        params={
            "op": "ph", "dt": "kf", "ft": fund_type, "rs": "", "gs": "0", "sc": sort, "st": order,
            "sd": sd.isoformat(), "ed": ed.isoformat(), "qdii": "", "tabSubtype": ",,,,,",
            "pi": str(page), "pn": str(size), "dx": "1",
        },
        referer="https://fund.eastmoney.com/data/fundranking.html",
    )
    m = re.search(r"datas:\[(.*?)\],allRecords:(\d+).*?allPages:(\d+)", text, re.S)
    if not m:
        return {"rows": [], "total": 0, "pages": 0}
    rows = json.loads("[" + m.group(1) + "]") if m.group(1) else []
    out = []
    for row in rows:
        parts = row.split(",")
        item = dict(zip(RANK_FIELDS, parts))
        for k in RANK_FIELDS[4:16]:
            item[k] = _num(item.get(k))
        item["fee_rate"] = parts[20] if len(parts) > 20 else ""
        out.append(item)
    return {"rows": out, "total": int(m.group(2)), "pages": int(m.group(3))}


def fetch_rank(fund_type="all", sort="1nzf", size=200):
    return fetch_rank_page(fund_type, sort, "desc", 1, size)["rows"]


CATEGORY_PREFIX = [("债券", "债券"), ("QDII", "QDII"), ("指数", "指数"), ("股票", "股票"), ("混合", "混合"), ("FOF", "FOF")]


def fund_types():
    out = {}
    for f in fetch_fund_list():
        cat = next((c for p, c in CATEGORY_PREFIX if f["type"].startswith(p)), "其他")
        out[f["code"]] = (cat, f["type"])
    return out


def _js_var(text, name):
    m = re.search(r"var\s+%s\s*=\s*(.*?);\s*(?:/\*|var\s|$)" % re.escape(name), text, re.S)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    try:
        return json.loads(raw)
    except ValueError:
        return None


def fetch_fund_detail(code, max_age=6 * 3600):
    """Full NAV history + profile from pingzhongdata, cached on disk."""
    path = os.path.join(NAV_DIR, f"{code}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    text = _get(f"https://fund.eastmoney.com/pingzhongdata/{code}.js")
    if "fS_code" not in text:
        raise ValueError(f"基金 {code} 不存在或无数据")
    trend = _js_var(text, "Data_netWorthTrend") or []
    scale = _js_var(text, "Data_fluctuationScale") or {}
    managers = _js_var(text, "Data_currentFundManager") or []
    series = scale.get("series") or []
    detail = {
        "code": code,
        "name": _js_var(text, "fS_name") or code,
        "fee_rate": _js_var(text, "fund_Rate") or "",
        "min_buy": _js_var(text, "fund_minsg") or "",
        "scale": series[-1]["y"] if series else None,
        "manager": managers[0]["name"] if managers else "",
        "nav": [[p["x"], p["y"], p.get("equityReturn") or 0] for p in trend],
        "fetched_at": time.time(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False)
    return detail


def fetch_status(code):
    """Latest purchase / redemption status, e.g. 开放申购 / 限大额 / 暂停申购."""
    try:
        text = _get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            params={"fundCode": code, "pageIndex": 1, "pageSize": 1},
            referer="https://fundf10.eastmoney.com/",
        )
        rows = json.loads(text)["Data"]["LSJZList"]
        if rows:
            return {"buy": rows[0]["SGZT"], "sell": rows[0]["SHZT"]}
    except Exception:  # noqa: BLE001
        pass
    return {"buy": "未知", "sell": "未知"}


def fetch_fund_list():
    path = os.path.join(DATA_DIR, "fund_list.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    text = _get("https://fund.eastmoney.com/js/fundcode_search.js")
    m = re.search(r"var r = (\[.*\]);", text, re.S)
    rows = json.loads(m.group(1)) if m else []
    out = [{"code": r[0], "name": r[2], "type": r[3]} for r in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out
