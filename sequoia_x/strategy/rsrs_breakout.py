"""RSRS 阻力支撑相对强度突破策略
光大证券经典研报 — 计算 (high - low) 回归斜率标准化值。
RSRS 斜率突破阈值时做多，斜率右偏说明买方力量增强。
"""
import pandas as pd
import sqlite3
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class RSRSBreakoutStrategy(BaseStrategy):
    """RSRS 斜率突破选股"""

    webhook_key: str = "rsrs"
    lookback: int = 18       # 回归窗口
    score_threshold: float = 0.5  # RSRS 标准化得分阈值（0.5=偏强，0.7=极强）

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql("SELECT symbol, date, open, high, low, close FROM stock_daily", conn)
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])

        latest_date = df["date"].max()
        latest = df[df["date"] == latest_date].copy()

        selected = []
        for sym, grp in df.groupby("symbol"):
            if len(grp) < self.lookback + 20:
                continue
            grp = grp.tail(self.lookback + 20).copy()
            highs = grp["high"].values
            lows = grp["low"].values

            # 滚动计算 RSRS 斜率
            slopes = []
            for i in range(self.lookback, len(grp)):
                x = lows[i - self.lookback : i]
                y = highs[i - self.lookback : i]
                if len(x) < self.lookback:
                    continue
                x_mean, y_mean = x.mean(), y.mean()
                num = ((x - x_mean) * (y - y_mean)).sum()
                den = ((x - x_mean) ** 2).sum()
                if den == 0:
                    slopes.append(0)
                else:
                    slopes.append(num / den)

            if len(slopes) < 21:
                continue
            # 标准化: (当前斜率 - 均值) / 标准差
            slopes_series = pd.Series(slopes)
            current = slopes_series.iloc[-1]
            mu = slopes_series.iloc[-21:].mean()
            sigma = slopes_series.iloc[-21:].std()
            if sigma == 0:
                continue
            zscore = (current - mu) / sigma
            if zscore > self.score_threshold:
                # 确认当天收阳
                today_row = latest[latest["symbol"] == sym]
                if not today_row.empty and today_row["close"].iloc[0] > today_row["open"].iloc[0]:
                    selected.append(sym)

        logger.info(f"RSRSBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected
