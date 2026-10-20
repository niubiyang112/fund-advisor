# 基金分析助手

每日分析公募基金（数据来自天天基金公开接口），给出稳健榜（月度胜率高、回撤小）、趋势榜（近期强势，高风险）、按风险偏好生成的买入方案，以及持仓的止盈 / 止损提醒。

> 没有任何方法能保证不亏损。本工具仅基于历史数据和固定规则提供参考，不构成投资建议。

## 运行

```bash
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 。首次启动会自动分析约 400 只基金（几分钟），之后每晚 21:30（北京时间）自动更新。

环境变量：`FUND_DATA_DIR` 数据缓存目录（默认 `./data`），`FUND_SCHEDULER=0` 关闭自动更新。

## Docker 部署（长期使用）

```bash
docker build -t fund-advisor .
docker run -d --restart=always -p 8000:8000 -v fund-data:/data fund-advisor
```
