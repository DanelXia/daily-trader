# daily-trader

A股量化投资研究与交易系统。研究员每周筛选行业和个股，交易员每天对股票池进行技术分析并给出买卖建议。

## 快速开始

### 1. 安装

```bash
git clone https://github.com/DanelXia/daily-trader.git
cd daily-trader
pip install -r requirements.txt
```

### 2. 周度研究（每周日运行一次）

```bash
python -m src.main research
```

运行后会自动：
1. 识别市场场景（牛/熊/震荡），路由到对应选股模型
2. 对全池流动股票进行价值(50)+成长(20)+技术(30)综合评分（权重随模型变化）
3. 选出得分最高的股票，生成股票池

输出文件：
| 文件 | 说明 |
|------|------|
| `output/weekly/20260708-W28-report.md` | 周度研报，个股评分明细 |
| `output/weekly/20260708-W28-pool.json` | 完整股票池数据 |
| `data/stock_pool.json` | 当前股票池（交易员直接读取） |

可选参数：

```bash
# 指定特定行业，只在该行业内选股
python -m src.main research --industry "计算机、通信和其他电子设备制造业"

# 自定义最优个股数量
python -m src.main research --top-stocks 3
```

### 3. 定性审查（人工）

`research` 命令输出的股票池**需要经过人工定性审查**后才能交易：

- 搜索政策催化事件，为每个行业追加政策催化评分（0-10）
- 逐只排查个股风险：质押比例、大股东减持、财务造假信号、估值泡沫、概念炒作
- 剔除问题股，保留的股票写入 `data/stock_pool.json`

这一步直接在 Claude Code 对话中完成，由 AI 辅助搜索和排查。

### 4. 每日交易分析（每个交易日盘前运行）

```bash
# 对整个股票池运行
python -m src.main trade

# 也可以分析单只股票
python -m src.main trade --stock 002558
```

输出：
| 文件 | 说明 |
|------|------|
| `output/daily/20260710-trading.md` | 每日交易建议（信号+点位+技术指标） |
| `output/daily/20260710-signals.json` | 结构化信号数据 |

交易建议包含：操作信号（STRONG BUY/BUY/HOLD/REDUCE/STRONG SELL）、综合得分、建议入场价、止损价、止盈目标、风险评估、关键信号摘要、完整技术指标快照。

### 5. 每日收益跟踪（每个交易日收盘后运行）

```bash
python src/track.py
```

对比 `data/baseline.json` 中的基准价格，计算每只股票自纳入以来的累计收益率，记录到 `data/tracking.json`。

### 6. 查看当前股票池

```bash
python -m src.main status
```

### 7. 模拟盘交易（Paper Trading，虚拟 10 万本金）

独立于实盘的纸面交易引擎（`src/simulate.py`），用 10 万虚拟本金模拟每日操作，记录每笔交易的盈亏与原因，每周复盘。状态存 `data/paper/`，不影响实盘 `data/positions.json`。

```bash
# 每周日：全池选股（离线秒出），生成 data/paper/watchlist.json
python -m src.simulate weekly

# 每个交易日 15:00 收盘后：估值 + 止盈止损 + 信号买卖 + 记账
python -m src.simulate daily

# 每周末：复盘（量化绩效 + 逐笔盈亏与原因）
python -m src.simulate review

# 查看账户 / 重置（清空历史需 --yes）
python -m src.simulate status
python -m src.simulate reset --yes
```

规则：
- **成交价**：盘后收盘价（`daily` 应在 15:00 后运行，用当日收盘价估值与成交）
- **节奏**：每周全池选股一次，每日仅对持仓 + 股票池（~10 只）做技术信号
- **仓位**：单只 ≤10%，总仓随场景 BULL 80% / BEAR 20% / SIDEWAYS 50%（HIGHVOL 减半）
- **止盈止损**：按场景固定（BEAR -5%/+10%，SIDEWAYS -5%/+15%，BULL -8%/+20%），另加 strong_sell 信号强平
- **成本**：佣金万2.5（双边）+ 印花税千1（卖）+ 滑点0.1%（双边）

输出文件：

| 文件 | 说明 |
|------|------|
| `data/paper/watchlist.json` | 每周选出的股票池（候选池） |
| `data/paper/portfolio.json` | 当前虚拟持仓 + 现金 |
| `data/paper/trade_log.json` | 每笔成交（含原因、盈亏） |
| `data/paper/equity_history.json` | 每日净值曲线 |
| `output/paper/review-YYYY-MM-DD.md` | 周复盘报告 |

## 完整工作流示例

```bash
# === 周日 ===
# 1. 运行周度研究
python -m src.main research

# 2. 在 Claude Code 中对 stock_pool.json 定性审查
#    搜索政策催化、排查个股风险、剔除问题股

# === 每个交易日 ===
# 3. 盘前（08:30）
python -m src.main trade

# 4. 收盘后（15:30）
python src/track.py
```

## 架构

```
研究员（周度）                  交易员（每日）
    │                              │
    ├─ 市场场景识别 + 模型路由      ├─ 技术指标计算 (MA/MACD/RSI/BB/ATR)
    ├─ 个股多维打分                ├─ 综合买卖信号
    └─ 输出股票池 JSON              ├─ 入场/止损/止盈点位
           │                        └─ 每日交易建议报告
           └──────────┬──────────────┘
                      │
              stock_pool.json
              （研究员输出 → 交易员输入）
```

**Python 做量化计算，人工做定性审查**——研究员输出的股票池会经过政策催化搜索和个股风险排查后才能进入交易环节。

## 目录结构

```
daily-trader/
├── src/
│   ├── data/
│   │   ├── fetcher.py          # 数据获取 (baostock + diskcache)
│   │   └── industry.py         # 行业分类映射
│   ├── research/
│   │   └── stock_selection.py   # 个股筛选打分
│   ├── trading/
│   │   ├── technical.py        # 技术指标计算
│   │   └── signals.py          # 买卖信号引擎
│   ├── reports/
│   │   └── generator.py        # Markdown / JSON 报告生成
│   ├── main.py                 # CLI 入口
│   ├── simulate.py             # 模拟盘交易引擎（纸面交易）
│   └── track.py                # 收益率每日跟踪
├── data/
│   ├── stock_pool.json         # 当前股票池
│   ├── baseline.json           # 跟踪基准价
│   ├── paper/                  # 模拟盘状态（持仓/交易/净值/股票池）
│   └── tracking.json           # 每日收益率记录
├── output/
│   ├── weekly/                 # 周度研报
│   ├── daily/                  # 每日交易建议
│   └── paper/                  # 模拟盘周复盘报告
├── config.py                   # 全局配置
└── requirements.txt
```

## 评分模型

### 个股评分（5:2:3）

| 维度 | 分值 | 细分 |
|------|------|------|
| 价值 | 50 | PE(15) + PB(10) + ROE(15) + 资产规模(10) |
| 成长 | 20 | 营收增速(10) + 利润增速(10) |
| 技术 | 30 | 20日动量(12) + 60日动量(8) + 均线(5) + 流动性(5) |

硬过滤：排除 ST、上市不足 250 天。

### 交易信号

| 维度 | 分值 | 说明 |
|------|------|------|
| 趋势 | 30 | MA5/10/20/60 多头排列 |
| 动量 | 20 | MACD 金叉死叉、RSI 位置 |
| 量能 | 15 | 量比、涨跌量比 |
| 支撑阻力 | 20 | 距支撑/阻力距离、布林带位置 |
| K线形态 | 15 | 吞没形态、锤子线、十字星 |

| 得分 | 信号 | 操作 |
|------|------|------|
| ≥75 | STRONG BUY | 强烈买入 |
| 60-74 | BUY | 买入 |
| 45-59 | HOLD | 观望 |
| 30-44 | REDUCE | 减仓 |
| <30 | STRONG SELL | 强烈卖出 |

## 配置

所有参数集中在 `config.py`，包括：

- 选股阈值：最小市值、最大PE、排除ST、最少上市天数
- 选股数量：最优个股数
- 交易参数：账户资金、单只仓位上限、单笔风险上限
- 技术指标参数：MA周期、MACD参数、RSI周期、布林带参数
- 缓存TTL：K线30分钟、财务数据24小时

## 数据源

| 来源 | 用途 |
|------|------|
| baostock | 日线K线、财务数据、行业分类 |
| 新浪实时行情 API | 盘中实时价格 |
| Web Search | 政策催化、个股风险排查（定性审查） |

## 免责声明

本系统由量化模型自动生成分析，不构成投资建议。投资有风险，入市须谨慎。
