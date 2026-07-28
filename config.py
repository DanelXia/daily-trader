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

# --- 拆股/份额拆分检测 ---
SPLIT_DETECTION_THRESHOLD = 0.20  # 偏差阈值 20%，超过此值判定为拆分

# --- 散户情绪分析 ---
SENTIMENT_MARKET_CLOSE_HOUR = 15     # 只收集收盘后（15:00）的帖子

SENTIMENT_BULLISH_KEYWORDS = [
    # 经典看多
    "抄底", "反弹", "牛市", "满仓", "加仓", "起飞", "暴涨",
    "稳了", "见底", "反转", "利好", "护盘", "低吸", "突破", "金叉",
    # 口语化看多
    "上车", "梭哈", "到底了", "跌够了", "补仓", "建仓", "埋伏",
    "吃肉", "回血", "翻身", "赚了", "起飞了", "冲进去", "抄家伙",
    "机会来了", "跌不动了", "底到了", "抄底机会",
]

SENTIMENT_BEARISH_KEYWORDS = [
    # 经典看空
    "清仓", "割肉", "暴跌", "熊市", "跑路", "崩盘", "套牢",
    "完蛋", "见顶", "踩踏", "利空", "出货", "诱多", "死叉", "阴跌",
    # 口语化看空
    "跑了", "破产", "打工", "进厂", "完了", "没了", "亏麻",
    "腰斩", "大跌", "回本", "拉尾盘", "被套", "没救", "绝望",
    "摆烂", "躺平", "认输", "离场", "退市", "撤退", "死了",
    "亏钱", "被割", "收割", "韭菜", "绿了", "套死", "解套",
    "血亏", "亏惨", "吃相难看", "3000点", "跌回", "没希望",
    "今天吃面", "关灯吃面", "天台", "销户", "不玩了",
    # 疑似看空短语（短词，匹配需谨慎）
    "深套", "爆亏", "巨亏", "亏光", "输光", "亏完",
    # 股市特有看空表达
    "保卫战", "杀下去", "谢幕", "百孔千疮", "无人问津",
    "装牛", "多套", "结束了", "又跌了", "杀跌", "再跌",
    "不拉", "没拉",
]

SENTIMENT_INSTITUTIONAL_KEYWORDS = [
    # 只匹配明显的机构内容，避免误杀散户帖
    "分析师", "研报", "证券之星", "基金持仓", "策略报告",
    "买入评级", "目标价位", "强烈推荐", "机构观点",
]

SENTIMENT_PLATFORMS = {
    "eastmoney": {
        "url": "http://guba.eastmoney.com/interface/GetData.aspx",
        "code": "zssh000001",
    },
    "xueqiu": {
        "homepage": "https://xueqiu.com",
        "api_url": "https://xueqiu.com/v4/statuses/public_timeline_by_category.json",
        "category": "111",
    },
}

SENTIMENT_MAX_POSTS_PER_PLATFORM = 80
SENTIMENT_MIN_POSTS_REQUIRED = 5
SENTIMENT_GUBA_PAGES = 3               # 东方财富股吧翻页数
