# daily-trader

A股量化投资研究与交易系统。**研究员**每周筛选行业和个股，**交易员**每天对股票池进行技术分析并给出买卖建议。

## 架构

```
研究员（周度）                  交易员（每日）
    │                              │
    ├─ 行业评分排名                ├─ 技术指标计算 (MA/MACD/RSI/BB/ATR)
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
│   │   ├── industry_analysis.py # 行业评分排名
│   │   └── stock_selection.py   # 个股筛选打分
│   ├── trading/
│   │   ├── technical.py        # 技术指标计算
│   │   └── signals.py          # 买卖信号引擎
│   ├── reports/
│   │   └── generator.py        # Markdown / JSON 报告生成
│   ├── main.py                 # CLI 入口
│   └── track.py                # 收益率每日跟踪
├── data/
│   ├── stock_pool.json         # 当前股票池
│   ├── baseline.json           # 跟踪基准价
│   └── tracking.json           # 每日收益率记录
├── output/
│   ├── weekly/                 # 周度研报
│   └── daily/                  # 每日交易建议
├── config.py                   # 全局配置
└── requirements.txt
```

## 安装

```bash
pip install -r requirements.txt
```

依赖：`pandas`, `numpy`, `click`, `diskcache`, `baostock`，技术指标使用纯 numpy 实现（TA-Lib 可选）。

## 使用

### 周度研究（研究员）

```bash
# 全行业分析，选出最优3个行业各5只个股
python -m src.main research

# 指定行业，自定义数量
python -m src.main research --industry "计算机、通信和其他电子设备制造业" --top-stocks 3
```

输出：
- `output/weekly/YYYYMMDD-Wxx-report.md` — 周度研报
- `output/weekly/YYYYMMDD-Wxx-pool.json` — 完整股票池数据
- `data/stock_pool.json` — 当前股票池（交易员读取）

### 每日交易分析（交易员）

```bash
# 对当前股票池全部标的生成交易建议
python -m src.main trade

# 单只股票分析
python -m src.main trade --stock 002558
```

输出：
- `output/daily/YYYYMMDD-trading.md` — 每日交易建议
- `output/daily/YYYYMMDD-signals.json` — 结构化信号数据

### 收益率跟踪

```bash
python src/track.py
```

对比 `baseline.json` 基准价，计算每只股票的累计收益率，记录到 `tracking.json`。

### 查看股票池

```bash
python -m src.main status
```

## 评分模型

### 行业评分（100分制）

| 维度 | 分值 | 说明 |
|------|------|------|
| 动量 | 60 | 行业指数 20/60 日涨跌幅、量能趋势 |
| 基本面 | 40 | 成分股市盈率中位数、ROE 中位数 |
| 政策催化 | +10 | 定性评估，研究员人工追加 |

### 个股评分（100分制，5:2:3 权重）

| 维度 | 分值 | 说明 |
|------|------|------|
| 价值 | 50 | PE(15) + PB(10) + ROE(15) + 资产规模(10) |
| 成长 | 20 | 营收增速(10) + 利润增速(10) |
| 技术 | 30 | 20日动量(12) + 60日动量(8) + 均线(5) + 流动性(5) |

硬过滤：ST、上市不足1年

### 交易信号（100分制）

| 维度 | 分值 |
|------|------|
| 趋势（MA多头排列） | 30 |
| 动量（MACD/RSI） | 20 |
| 量能（量比/涨跌量比） | 15 |
| 支撑阻力（距支撑/阻力距离、布林带位置） | 20 |
| K线形态（吞没/锤子线/十字星） | 15 |

操作判定：≥75 STRONG BUY / 60-74 BUY / 45-59 HOLD / 30-44 REDUCE / <30 STRONG SELL

## 数据源

- **baostock**：日线K线、财务数据、行业分类
- **新浪实时API**：盘中实时价格
- **Web Search**：政策催化、个股风险排查（定性审查用）

## 免责声明

本系统由量化模型自动生成分析，不构成投资建议。投资有风险，入市须谨慎。
