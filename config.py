"""
daily-trader 全局配置
"""

from pathlib import Path

# --- 路径 ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
WEEKLY_DIR = OUTPUT_DIR / "weekly"
DAILY_DIR = OUTPUT_DIR / "daily"
STOCK_POOL_FILE = DATA_DIR / "stock_pool.json"
CACHE_DIR = DATA_DIR / "cache"

# --- 缓存 TTL（秒） ---
CACHE_TTL_SNAPSHOT = 300       # 实时快照 5 分钟
CACHE_TTL_FINANCIAL = 86400    # 财务数据 24 小时
CACHE_TTL_KLINE = 1800         # K 线 30 分钟
CACHE_TTL_FUND_FLOW = 1800     # 资金流向 30 分钟

# --- 个股筛选阈值 ---
STOCK_SCREENING = {
    "min_market_cap_billion": 50,   # 最小市值（亿）
    "max_pe": 200,                  # 最大 PE
    "min_listing_days": 250,        # 最少上市天数（≈1年）
    "exclude_st": True,             # 排除 ST
    "max_pledge_ratio_pct": 50,     # 最大质押比例（%）
}

# --- 选股数量 ---
TOP_INDUSTRIES = 3              # 每期最优行业数
TOP_STOCKS_PER_INDUSTRY = 5     # 每个行业最优个股数

# --- 交易 ---
DEFAULT_ACCOUNT_CAPITAL = 100_000  # 默认账户资金（元）
MAX_POSITION_PCT = 0.10           # 单只股票最大仓位 10%
RISK_PER_TRADE_PCT = 0.02         # 单笔交易风险 2%

# --- 技术指标参数 ---
MA_PERIODS = [5, 10, 20, 60]
MACD_PARAMS = (12, 26, 9)
RSI_PERIODS = [6, 14]
BB_PERIOD = 20
BB_STD = 2
ATR_PERIOD = 14
KLINE_LOOKBACK = 120              # K线回看天数

# --- 请求控制 ---
REQUEST_DELAY_MIN = 2.0          # 最小请求间隔（秒）
REQUEST_DELAY_MAX = 5.0          # 最大请求间隔（秒）
MAX_RETRIES = 3                  # 最大重试次数
