"""
选股模型定义（三套权重，取代固定权重）。

路由映射（见 src/research/router.py）：
  BULL      -> momentum      （动量突破）
  BEAR      -> defensive     （防守红利）
  SIDEWAYS  -> mean_reversion（均值回归）

每个模型定义两套权重（归一化到各自满分后再加权，权重求和 = 100）：
  industry_weights: {momentum, fundamental}   行业评分
  stock_weights:    {value, growth, technical} 个股评分

除权重外，每个模型还定义 technical_style（技术分口径，修正 DefensiveModel 与基线
技术分相同导致熊市路由失效的缺口）：
  momentum        追强势（20/60日动量 + 均线多头 + 放量）
  defensive       相对抗跌（低回撤 + 低波动 + 不破位 + 流动性）
  mean_reversion  超跌低吸（RSI 超卖 + 回踩支撑 + 不追高）
"""


class SelectionModel:
    """选股模型基类。默认权重等价于原固定权重，保证无路由时行为不变。"""
    name = "base"
    label = "基础固定权重"
    industry_weights = {"momentum": 60, "fundamental": 40}
    stock_weights = {"value": 50, "growth": 20, "technical": 30}
    technical_style = "momentum"
    filter_hint = ""


class MomentumModel(SelectionModel):
    """BULL：强者恒强，追趋势不追估值。"""
    name = "momentum"
    label = "动量突破"
    industry_weights = {"momentum": 70, "fundamental": 30}
    stock_weights = {"technical": 40, "value": 30, "growth": 30}
    technical_style = "momentum"
    filter_hint = "突破60日高点 + 放量确认，剔除纯超跌反弹"


class DefensiveModel(SelectionModel):
    """BEAR：优先活下来，赚股息 + 抗跌，不赚趋势钱。"""
    name = "defensive"
    label = "防守红利"
    industry_weights = {"momentum": 40, "fundamental": 60}
    stock_weights = {"value": 50, "growth": 20, "technical": 30}
    technical_style = "defensive"
    filter_hint = "PE<20、相对抗跌、优先高股息红利低波"


class MeanReversionModel(SelectionModel):
    """SIDEWAYS：低吸回踩支撑，不追高。"""
    name = "mean_reversion"
    label = "均值回归"
    industry_weights = {"momentum": 40, "fundamental": 60}
    stock_weights = {"technical": 40, "value": 40, "growth": 20}
    technical_style = "mean_reversion"
    filter_hint = "RSI<40 或回踩 MA20/前低支撑，不追高"


MODEL_REGISTRY = {
    "momentum": MomentumModel,
    "defensive": DefensiveModel,
    "mean_reversion": MeanReversionModel,
}


def get_model(name: str | None) -> SelectionModel:
    """按名称取模型实例；未知或空名时返回基础模型（等价原固定权重）。"""
    cls = MODEL_REGISTRY.get(name or "")
    return cls() if cls else SelectionModel()
