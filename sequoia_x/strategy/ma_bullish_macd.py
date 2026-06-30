"""均线多头排列 + MACD 金叉策略
5>10>20>60 日线多头，同时 MACD 柱由负转正或金叉。
经典趋势转强信号，结合多头排列过滤假信号。
"""
import pandas as pd
import sqlite3
import numpy as np
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class MABullishMACDStrategy(BaseStrategy):
    """均线多头+MACD金叉"""

    webhook_key: str = "ma_macd"
    ma_periods: tuple = (5, 10, 20, 60)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql("SELECT symbol, date, close FROM stock_daily", conn)
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])

        latest_date = df["date"].max()

        selected = []
        for sym, grp in df.groupby("symbol"):
            if len(grp) < 120:
                continue
            grp = grp.tail(120).copy()
            closes = grp["close"].values

            # 均线
            mas = {}
            for p in self.ma_periods:
                mas[p] = pd.Series(closes).rolling(p).mean().values

            # 多头排列检查
            if any(pd.isna(mas[p][-1]) for p in self.ma_periods):
                continue
            ma_values = [mas[p][-1] for p in self.ma_periods]
            if not all(ma_values[i] > ma_values[i+1] for i in range(len(ma_values)-1)):
                continue

            # MACD
            ema_fast = pd.Series(closes).ewm(span=self.macd_fast, adjust=False).mean().values
            ema_slow = pd.Series(closes).ewm(span=self.macd_slow, adjust=False).mean().values
            dif = ema_fast - ema_slow
            dea = pd.Series(dif).ewm(span=self.macd_signal, adjust=False).mean().values
            macd_bar = 2 * (dif - dea)

            # MACD金叉: 今天bar转正 或 dif上穿dea
            today_bar = macd_bar[-1]
            yesterday_bar = macd_bar[-2] if len(macd_bar) > 1 else 0
            today_dif, yesterday_dif = dif[-1], dif[-2] if len(dif) > 1 else 0
            today_dea_val = dea[-1]

            golden_cross = (yesterday_bar <= 0 and today_bar > 0) or                            (yesterday_dif <= dea[-2] and today_dif > today_dea_val)

            if golden_cross and today_dif > 0:
                selected.append(sym)

        logger.info(f"MABullishMACDStrategy 选出 {len(selected)} 只股票")
        return selected
