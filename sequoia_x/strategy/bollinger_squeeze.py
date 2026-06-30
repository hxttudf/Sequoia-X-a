"""布林带收缩突破策略
布林带宽收窄至 N 日低点，价格放量突破上轨。
震荡→趋势转换信号，与动量策略共振效果好。
"""
import pandas as pd
import sqlite3
import numpy as np
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class BollingerSqueezeStrategy(BaseStrategy):
    """布林带收缩后放量突破上轨"""

    webhook_key: str = "boll_sqz"
    bb_period: int = 20
    bb_std: float = 2.0
    squeeze_lookback: int = 50
    squeeze_percentile: int = 15  # 带宽低于15%分位才算收缩

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql("SELECT symbol, date, close, volume FROM stock_daily", conn)
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])

        selected = []
        for sym, grp in df.groupby("symbol"):
            if len(grp) < self.squeeze_lookback + 20:
                continue
            grp = grp.tail(self.squeeze_lookback + 20).copy()
            closes = grp["close"].values

            # 布林带
            rolling = pd.Series(closes).rolling(self.bb_period)
            mid = rolling.mean().values
            std = rolling.std().values
            upper = mid + self.bb_std * std
            lower = mid - self.bb_std * std
            bandwidth = (upper - lower) / mid  # 带宽

            # 收缩判定：当前带宽低于 lookback 内第 squeeze_percentile 分位
            bw_valid = bandwidth[~np.isnan(bandwidth)]
            if len(bw_valid) < self.squeeze_lookback:
                continue
            bw_recent = bw_valid[-self.squeeze_lookback:]
            threshold = np.percentile(bw_recent, self.squeeze_percentile)
            current_bw = bw_valid[-1]

            if pd.isna(current_bw) or current_bw > threshold:
                continue

            # 突破上轨 + 放量
            current_close = closes[-1]
            current_upper = upper[-1]
            current_vol = grp["volume"].iloc[-1]
            avg_vol = grp["volume"].iloc[-6:-1].mean() if len(grp) >= 6 else current_vol

            if pd.notna(current_upper) and current_close > current_upper and current_vol > avg_vol * 1.2:
                selected.append(sym)

        logger.info(f"BollingerSqueezeStrategy 选出 {len(selected)} 只股票")
        return selected
