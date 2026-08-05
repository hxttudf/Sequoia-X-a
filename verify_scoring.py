#!/usr/bin/env python3
"""Deep-dive: structural issues in current scoring logic"""
import sqlite3, math
from collections import defaultdict

conn = sqlite3.connect('data/sequoia_v2.db')
today = '2026-07-02'

# ── compute weights (same logic) ──
weights = {}
for row in conn.execute("""
    SELECT strategy, win_rate, avg_return, total_picks,
           win_rate_3d, avg_return_3d, win_rate_5d, avg_return_5d, win_rate_10d, avg_return_10d
    FROM strategy_stats WHERE lookback_days=10 ORDER BY date DESC""").fetchall():
    sname = row[0]; cnt = row[3] or 0
    def dim_score(wr, ar):
        wr = wr or 0; ar = ar or 0
        if wr > 0 and ar > 0: return wr * (1 + ar/100)
        elif wr > 0: return wr * 0.5
        else: return wr * 0.1
    dims = {"1d": (row[1],row[2]), "3d": (row[4],row[5]), "5d": (row[6],row[7]), "10d": (row[8],row[9])}
    tw = {"1d":0.40, "3d":0.30, "5d":0.20, "10d":0.10}
    active = {d: tw[d] for d in dims if dims[d][0] is not None or dims[d][1] is not None}
    raw = sum((active[d]/sum(active.values()))*dim_score(*dims[d]) for d in active) if active else 0.01
    if cnt < 5: raw *= cnt/5
    weights[sname] = max(raw, 0.01)
total = sum(weights.values())
w = {k: v/total for k,v in weights.items()}

# ── picks ──
picks = defaultdict(list)
for row in conn.execute("SELECT strategy, symbol FROM strategy_picks WHERE date=?", (today,)).fetchall():
    picks[row[1]].append(row[0])

# ── illustrate the fundamental problem with real examples ──
print("=" * 75)
print("问题1: score = max(weight) 使共振信号被系统性低估")
print("=" * 75)
print(f"\n{'情景':<45} {'单策略权重':<12} {'max(W)分':<10} {'sum(W)分':<10}")
print("-" * 75)

# Scenario A: 3-strat resonance vs 1-strong-strat (today's real data)
rps_w = w.get("RpsBreakoutStrategy", 0)
turtle_w = w.get("TurtleTradeStrategy", 0)
fifty_w = w.get("FiftyTwoWeekHighStrategy", 0)
bull_w = w.get("BollingerSqueezeStrategy", 0)
uptrend_w = w.get("UptrendLimitDownStrategy", 0)

# Find a real 3-strat stock
three_strat_syms = [s for s in picks if len(picks[s]) >= 3]
single_rps_syms = [s for s in picks if len(picks[s]) == 1 and picks[s][0] == "RpsBreakoutStrategy"]

# 3-strat resonance example
ex_sym = three_strat_syms[0] if three_strat_syms else "688689"
ex_strats = picks[ex_sym]
ex_wts = [w.get(s,0) for s in ex_strats]
ex_maxw = max(ex_wts)
ex_sumw = sum(ex_wts)
ex_extra = len(ex_strats) - 1
ex_cur = ex_maxw * (1 + 0.15*ex_extra) if ex_extra > 0 else ex_maxw
ex_prop = ex_sumw * (1 + 0.15*ex_extra) if ex_extra > 0 else ex_sumw

print(f"\n实例如: {ex_sym} ({','.join(s.replace('Strategy','') for s in ex_strats)})")
print(f"  各策略权重: {', '.join(f'{s.replace(\"Strategy\",\"\")}={w.get(s,0):.3f}' for s in ex_strats)}")
print(f"  max(W)  = {ex_maxw:.3f}, sum(W)  = {ex_sumw:.3f}")
print(f"  共振因子= {1 + 0.15*ex_extra:.2f}x ({ex_extra}个额外策略)")
print(f"  当前分  = max(W) × 共振 = {ex_maxw:.3f} × {1+0.15*ex_extra:.2f} = {ex_cur:.4f}")
print(f"  建议分  = sum(W) × 共振 = {ex_sumw:.3f} × {1+0.15*ex_extra:.2f} = {ex_prop:.4f}")
print(f"\n  → 对比: 单RpsBreakout票(权重{rps_w:.3f})得分为 {rps_w:.4f} (无共振)")
print(f"  当前: 3共振({ex_sym})={ex_cur:.4f} vs 单Rps票={rps_w:.4f} → 差={ex_cur-rps_w:+.4f}")
print(f"  建议: 3共振({ex_sym})={ex_prop:.4f} vs 单Rps票={rps_w:.4f} → 差={ex_prop-rps_w:+.4f}")

print(f"\n{'='*75}")
print("问题2: RpsBreakout 权重异常偏高 (0.315) 导致它在max(W)模式下一票否决其他策略")
print("="*75)
print(f"\n  信号量对比: RpsBreakout 1482票/10天 vs BollingerSqueeze 25票/10天")
print(f"  但 RpsBreakout 的 T+1 胜率 45.4% 只比随机略好，且均收益为负(-0.23%)")
print(f"  dim_score 给负收益×0.5惩罚后 Rps T+1=22.7")
print(f"  而 BollingerSqueeze T+1胜率52% 均收益+0.35% → dim_score=52.2")
print(f"  但归一化后 Rps=0.315 > Bollinger=0.062")
print(f"  原因: Rps 的 T+3 胜率 49.9%+均收益1.42% → dim_score=50.6 拉高了整体")
print(f"  但 50.6 vs Bollinger的 T+3=66.7, 为什么 Rps 反而归一化后权重更高?")
print(f"  因为所有策略归一化到总和1.0, Rps的原始raw分数可能不低于Bollinger")

# Check raw scores
print(f"\n  {'策略':<30} {'raw分数':<10} {'归一化权重':<12}")
print("  " + "-"*50)
for sname, raw_w in sorted(weights.items(), key=lambda x: -x[1]):
    print(f"  {sname.replace('Strategy',''):<30} {raw_w:<10.4f} {w.get(sname,0):<12.3f}")

print(f"\n{'='*75}")
print("问题3: 权重10天窗口 + 策略产出量差异大 → 置信度差异被忽略")
print("="*75)
print(f"\n  例: BollingerSqueeze 25票/10天, T+5胜率66.7%, 标准差较大")
print(f"      95%置信区间 ≈ ±19% (实际真值在47-86%之间)")
print(f"  RpsBreakout 1482票/10天, T+1胜率45.4%, 标准差较小")
print(f"      95%置信区间 ≈ ±2.5% (实际真值在43-48%之间)")
print(f"  → 当前权重系统平等对待两者，没有根据样本量调整置信度")
print(f"  → 小样本策略偶然高胜率会获得过高权重")
print(f"  → cnt<5惩罚不够 (25->完全无惩罚, 但25票仍有很大不确定性)")

print(f"\n{'='*75}")
print("问题4: 成交额破平上限0.005, 在主要分数差(>0.01)面前基本没用")
print("="*75)
print(f"\n  当前榜: top1=0.4154, top10=0.3663, top11=0.3658")
print(f"  相邻差距: 第10-11名差={0.3663-0.3658:.4f}")
print(f"  成交额bonus最大=0.005, 但相邻差距常小于0.01")
print(f"  实际上次排序靠的是共振数量(第二排序键), 成交额bonus很少真正破平")

conn.close()
