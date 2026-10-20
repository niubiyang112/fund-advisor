import logging
import os
import re
import threading
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import data as D
from . import service as S

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC = os.path.join(os.path.dirname(__file__), "static")
app = FastAPI(title="基金分析助手")


@app.on_event("startup")
def _start():
    if os.environ.get("FUND_SCHEDULER", "1") == "1":
        threading.Thread(target=S.scheduler, daemon=True).start()


def _code(code):
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(400, "基金代码应为 6 位数字")
    return code


@app.get("/api/status")
def status():
    return S.status()


@app.post("/api/refresh")
def refresh():
    if not S.status()["refreshing"]:
        threading.Thread(target=S.refresh, daemon=True).start()
    return S.status()


@app.get("/api/rankings")
def rankings():
    snap = S.snapshot()
    if not snap:
        raise HTTPException(503, "数据正在首次生成，请稍候几分钟")
    return snap


@app.get("/api/plan")
def plan(
    capital: float = 10000, risk: str = "稳健", horizon: str = "3-12个月", max_loss: float = 5,
    types: str = "债券,混合,股票,指数,QDII", n_funds: int = 4, monthly: float = 0, seed: int = 0, goal: str = "win",
):
    if capital <= 0 or monthly < 0:
        raise HTTPException(400, "金额必须大于 0")
    p = S.plan(capital, risk, horizon, max_loss, types.split(","), n_funds, monthly, seed, goal=goal if goal in ("win", "ann", "steady") else "win")
    if not p:
        raise HTTPException(503, "数据正在生成，请稍候几分钟")
    return p


SORTS = {"rzdf", "zzf", "1yzf", "3yzf", "6yzf", "1nzf", "2nzf", "3nzf", "jnzf", "lnzf"}
FTS = {"all", "gp", "hh", "zq", "zs", "qdii", "fof"}


@app.get("/api/all")
def all_funds(ft: str = "all", sort: str = "rzdf", order: str = "desc", page: int = 1):
    if ft not in FTS or sort not in SORTS or order not in ("asc", "desc") or not 1 <= page <= 1000:
        raise HTTPException(400, "参数错误")
    try:
        return S.all_funds(ft, sort, order, page)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"获取基金列表失败：{e}") from e


@app.get("/api/fund/{code}")
def fund(code: str):
    try:
        it, _ = S.analyse(_code(code), with_chart=True)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"获取失败：{e}") from e
    it["status"] = D.fetch_status(code)
    return it


@app.get("/api/search")
def search(q: str):
    q = q.strip()
    if not q:
        return []
    return [f for f in D.fetch_fund_list() if q in f["code"] or q in f["name"]][:20]


class Holding(BaseModel):
    code: str
    buy_date: date
    amount: float = 0
    took_half: bool = False


@app.post("/api/holdings")
def holdings(items: list[Holding]):
    out = []
    for h in items[:50]:
        try:
            out.append(S.holding_advice({**h.model_dump(), "code": _code(h.code), "buy_date": h.buy_date.isoformat()}))
        except Exception as e:  # noqa: BLE001
            out.append({"code": h.code, "error": str(e)})
    return out


class Trade(BaseModel):
    id: str | None = None
    date: date
    type: str
    amount: float = 0
    frac: float = 0


class Position(BaseModel):
    code: str
    trades: list[Trade]


@app.post("/api/positions")
def positions(items: list[Position]):
    out = []
    for p in items[:50]:
        try:
            trades = [t.model_dump() | {"date": t.date.isoformat()} for t in p.trades if t.type in ("buy", "sell")]
            out.append(S.position({"code": _code(p.code), "trades": trades}))
        except Exception as e:  # noqa: BLE001
            out.append({"code": p.code, "error": str(e)})
    return out


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
