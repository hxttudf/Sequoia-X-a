"""52周新高突破策略
价格突破过去 250 个交易日最高价，经典动量因子。
学术验证最强的技术面因子之一。
"""
import pandas as pd
import sqlite3
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class FiftyTwoWeekHighStrategy(BaseStrategy):
    """52周（250日）新高突破"""

    webhook_key: str = "52whigh"
    window: int = 250

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql("SELECT symbol, date, close, close_qfq FROM stock_daily", conn)
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
            if len(grp) < self.window:
                continue
            grp = grp.tail(self.window).copy()
            # 用前复权价计算
            price_col = "close_qfq" if "close_qfq" in grp.columns and grp["close_qfq"].notna().all() else "close"
            rolling_max = grp[price_col].rolling(window=self.window - 1, min_periods=self.window - 1).max()
            prev_max = rolling_max.iloc[-1]
            current_close = grp[price_col].iloc[-1]

            # 当前收盘达到前250日最高的99.5%以上
            if pd.notna(prev_max) and current_close >= 0.995 * prev_max:
                selected.append(sym)

        logger.info(f"FiftyTwoWeekHighStrategy 选出 {len(selected)} 只股票")
        return selected
