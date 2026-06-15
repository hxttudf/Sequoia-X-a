"""Sequoia-X V2 主程序入口。

运行模式：
  python main.py                  # 日常模式：增量补数据 + 跑策略 + 推送
  python main.py --json           # JSON 输出模式（供 Hermes agent 使用）
  python main.py --backfill       # 回填模式：全市场历史K线
"""

import argparse
import json
import sys
from datetime import date
from dotenv import load_dotenv
load_dotenv()

import socket
socket.setdefaulttimeout(10.0)

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy


def _get_stock_names(symbols: list[str]) -> dict[str, str]:
    """通过 baostock 批量查询股票名称。"""
    import baostock as bs
    bs.login()
    mapping = {}
    for code in symbols:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        rs = bs.query_stock_basic(code=f"{prefix}.{code}")
        while rs.next():
            row = rs.get_row_data()
            mapping[code] = row[1]
    bs.logout()
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：通过 baostock 拉取全市场历史 K 线（约12分钟）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 输出模式：结果以 JSON 格式打印到 stdout（供 Hermes agent 使用）",
    )
    args = parser.parse_args()

    try:
        settings = get_settings()
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")
        engine = DataEngine(settings)

        if args.backfill:
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return

        logger.info("开始拉取最新快照...")
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 只股票")

        strategies: list[BaseStrategy] = [
            MaVolumeStrategy(engine=engine, settings=settings),
            TurtleTradeStrategy(engine=engine, settings=settings),
            HighTightFlagStrategy(engine=engine, settings=settings),
            LimitUpShakeoutStrategy(engine=engine, settings=settings),
            UptrendLimitDownStrategy(engine=engine, settings=settings),
            RpsBreakoutStrategy(engine=engine, settings=settings),
            PrivatePlacementStrategy(engine=engine, settings=settings),
        ]

        results: list[dict] = []

        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")

            result_entry = {
                "strategy": strategy_name,
                "count": len(selected),
                "symbols": selected,
            }

            if selected:
                names = _get_stock_names(selected)
                result_entry["names"] = [names.get(c, c) for c in selected]

            results.append(result_entry)

        if args.json:
            output = {
                "date": date.today().strftime("%Y-%m-%d"),
                "strategies": results,
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
        elif settings.feishu_webhook_url:
            from sequoia_x.notify.feishu import FeishuNotifier
            notifier = FeishuNotifier(settings)
            for entry in results:
                if entry["symbols"]:
                    notifier.send(
                        symbols=entry["symbols"],
                        strategy_name=entry["strategy"],
                        webhook_key=[s.webhook_key for s in strategies if type(s).__name__ == entry["strategy"]][0],
                    )
        else:
            logger.info("FEISHU_WEBHOOK_URL 未配置，跳过推送")

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Sequoia-X V2 运行完成")


if __name__ == "__main__":
    main()
