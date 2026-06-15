# Hermes Integration — Sequoia-X

Sequoia-X 是一个 A 股量化选股系统。Hermes 通过 CLI 调用获取每日选股结果。

## 调用方式

```bash
# 1. 增量同步数据 + 跑所有策略 + JSON 输出
python main.py --json

# 2. 首次部署：回填全市场历史数据（约12分钟）
python main.py --backfill
```

## 输出格式

`--json` 输出到 stdout：

```json
{
  "date": "2026-06-15",
  "strategies": [
    {
      "strategy": "MaVolumeStrategy",
      "count": 3,
      "symbols": ["600519", "000858", "002594"],
      "names": ["贵州茅台", "五粮液", "比亚迪"]
    },
    {
      "strategy": "TurtleTradeStrategy",
      "count": 0,
      "symbols": []
    }
  ]
}
```

## 策略说明

| 策略 | 逻辑 |
|---|---|
| MaVolumeStrategy | 5日线上穿20日线 + 放量 > 1.5倍 |
| TurtleTradeStrategy | 20日新高 + 成交额过亿 + 阳线 |
| HighTightFlagStrategy | 高而窄的旗形整理（40日涨60%+，10日振幅<15%） |
| LimitUpShakeoutStrategy | 涨停次日阴线洗盘，不破涨停价 |
| UptrendLimitDownStrategy | 上升趋势中跌停反包 |
| RpsBreakoutStrategy | RPS 强度前10% + 接近新高 |
| PrivatePlacementStrategy | 近7日定增公告监控 |

## 首次部署（Docker）

```bash
cp .env.example .env
docker compose up -d    # 自动回填 + 每日15:30 CST 运行
```

## 路径

- 代码: `/app`
- 数据: `/app/data/sequoia_v2.db`
- 日志: `/app/logs/daily.log`
