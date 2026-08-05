"""底部首放量策略 — 捕捉刚启动/即将启动的股票

核心逻辑：
1. 股价在250日低点附近（离最低<30%），排除半山腰
2. 近期涨幅温和：20日涨幅 5%~20%（刚启动不是已大涨）
3. 成交量放量>1.5倍均量（资金刚进来）
4. 刚刚站上MA60（股价/MA60在1.00~1.12）
5. 之前横盘充分：前60日振幅<35%

所有价格计算使用 close_qfq（前复权）。
参数网格在 optimizer.py 中每周自优化。
"""
import pandas as pd
import numpy as np
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class BottomFirstVolStrategy(BaseStrategy):
    """底部首放量 — 捕捉刚启动信号"""

    webhook_key: str = "bottom_first_vol"

    # 默认参数（偏宽松，确保初期能出信号，后续自适应逐步收紧）
    price_from_low_max: float = 0.35      # 离250日最低的最大比例
    range_20d_min: float = 0.02           # 20日最小涨幅
    range_20d_max: float = 0.25           # 20日最大涨幅
    vol_ratio_min: float = 1.2            # 放量倍数(今日量/20日均量)
    ma60_dist_max: float = 0.15           # 离MA60的最大距离(股价/MA60 - 1)
    consolidation_amp_max: float = 2.0    # 60日振幅上限（设为200%只过滤极端波动股）

    def update_params(self, params: dict) -> None:
        """用优化后的参数更新策略"""
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
        logger.info(f"BottomFirstVol 参数更新: {params}")

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 250:
                    continue

                # 使用前复权价格
                price_col = "close_qfq"
                if price_col not in df.columns:
                    price_col = "close"  # fallback

                closes = df[price_col].values
                volumes = df["volume"].values
                highs = df["high"].values
                lows = df["low"].values

                # --- 条件①：离250日最低 < price_from_low_max ---
                low_250 = np.min(closes[-250:])
                current = closes[-1]
                if current <= low_250:
                    continue
                if (current / low_250 - 1) > self.price_from_low_max:
                    continue

                # --- 条件②：20日涨幅在 [range_20d_min, range_20d_max] ---
                close_20d = closes[-21] if len(closes) >= 21 else closes[0]
                ret_20d = current / close_20d - 1
                if ret_20d < self.range_20d_min or ret_20d > self.range_20d_max:
                    continue

                # --- 条件③：成交量放量 ---
                vol_ma20 = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else np.mean(volumes)
                if vol_ma20 <= 0:
                    continue
                if volumes[-1] < vol_ma20 * self.vol_ratio_min:
                    continue

                # --- 条件④：刚刚站上MA60 ---
                ma60 = np.mean(closes[-60:]) if len(closes) >= 60 else np.mean(closes)
                if ma60 <= 0:
                    continue
                dist_to_ma60 = current / ma60 - 1
                if dist_to_ma60 < 0 or dist_to_ma60 > self.ma60_dist_max:
                    continue

                # --- 条件⑤：前60日横盘（振幅小于 consolidated_amp_max）---
                if len(closes) >= 60:
                    high_60 = np.max(highs[-60:]) if len(highs) >= 60 else current
                    low_60 = np.min(lows[-60:]) if len(lows) >= 60 else current
                else:
                    high_60 = current
                    low_60 = current
                amp_60 = (high_60 / low_60 - 1) if low_60 > 0 else 0
                if amp_60 > self.consolidation_amp_max:
                    continue

                selected.append(symbol)

            except Exception as exc:
                logger.warning(f"[{symbol}] BottomFirstVol 计算失败：{exc}")
                continue

        logger.info(f"BottomFirstVolStrategy 选出 {len(selected)} 只股票")
        return selected
