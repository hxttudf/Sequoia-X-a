"""涨停回调策略
涨停后缩量回调至 5/10 日均线附近企稳，买点信号。
与 LimitUpShakeout 互补：Shakeout 是震仓洗盘，这个是自然回调。
"""
import pandas as pd
import sqlite3
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class LimitUpPullbackStrategy(BaseStrategy):
    """涨停后缩量回调企稳"""

    webhook_key: str = "lu_pullback"
    lu_window: int = 5   # N日前涨停
    pullback_days: int = 3  # 回调天数
    ma_short: int = 5
    ma_long: int = 10

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql("SELECT symbol, date, open, close, volume FROM stock_daily", conn)
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])

        # 计算涨跌幅
        df["pct_chg"] = df.groupby("symbol")["close"].pct_change() * 100
        latest_date = df["date"].max()

        selected = []
        for sym, grp in df.groupby("symbol"):
            if len(grp) < 30:
                continue
            grp = grp.tail(30).copy()
            grp["ma5"] = grp["close"].rolling(5).mean()
            grp["ma10"] = grp["close"].rolling(10).mean()
            grp["vol_ma5"] = grp["volume"].rolling(5).mean()

            # 找最近是否有涨停 (>=9.5%)
            recent_grp = grp.tail(self.lu_window + self.pullback_days + 1)
            lu_idx = None
            for i in range(len(recent_grp) - self.pullback_days - 1, -1, -1):
                if recent_grp["pct_chg"].iloc[i] >= 9.5:
                    lu_idx = i
                    break
            if lu_idx is None:
                continue

            # 涨停后是否有回调
            post_lu = recent_grp.iloc[lu_idx + 1:]
            if len(post_lu) < 2:
                continue
            # 当前价在 MA5 附近 + 缩量
            now = post_lu.iloc[-1]
            if pd.isna(now["ma5"]) or pd.isna(now["ma10"]):
                continue
            near_ma = abs(now["close"] - now["ma5"]) / now["ma5"] < 0.03
            shrink_vol = now["volume"] < now["vol_ma5"] * 0.8
            # ma5 仍 > ma10（趋势还在）
            trend_ok = now["ma5"] > now["ma10"]

            if near_ma and shrink_vol and trend_ok:
                selected.append(sym)

        logger.info(f"LimitUpPullbackStrategy 选出 {len(selected)} 只股票")
        return selected
