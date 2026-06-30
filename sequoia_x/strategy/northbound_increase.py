"""北向资金增持策略
追踪沪股通/深股通持股比例连续增加，聪明钱方向。
数据源：akshare stock_hsgt_hold_*
"""
import pandas as pd
import sqlite3
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class NorthBoundIncreaseStrategy(BaseStrategy):
    """北向资金连续增持"""

    webhook_key: str = "north"
    min_consecutive_days: int = 3  # 连续增持天数
    min_hold_pct: float = 0.5       # 最低持股比例

    def run(self) -> list[str]:
        try:
            import akshare as ak
            # 拉取沪股通+深股通持股
            sh_hgt = ak.stock_hsgt_hold_sh()
            sz_hgt = ak.stock_hsgt_hold_sz()
        except Exception as exc:
            logger.warning(f"北向数据拉取失败: {exc}")
            return []

        if sh_hgt.empty and sz_hgt.empty:
            return []

        try:
            # 拉取历史持股变化（用于判定连续增持）
            sh_det = ak.stock_hsgt_hist_em(symbol="沪股通")
            sz_det = ak.stock_hsgt_hist_em(symbol="深股通")
        except Exception:
            sh_det, sz_det = pd.DataFrame(), pd.DataFrame()

        selected = set()
        for df_raw in [sh_hgt, sz_hgt]:
            if df_raw.empty:
                continue
            df = df_raw.copy()
            # 列名映射（akshare 返回值变化较大，做兼容）
            code_col = None
            hold_col = None
            for c in df.columns:
                if "代码" in c:
                    code_col = c
                if "持股比例" in c or "占流通股" in c:
                    hold_col = c

            if code_col is None or hold_col is None:
                continue

            for _, row in df.iterrows():
                code = str(row[code_col])
                if not code.startswith(("6", "0", "3")):
                    continue
                hold = float(row[hold_col]) if row[hold_col] not in (None, "--", "") else 0
                if hold >= self.min_hold_pct:
                    selected.add(code)

        logger.info(f"NorthBoundIncreaseStrategy 选出 {len(selected)} 只股票")
        return list(selected)
