"""Advisory risk & position-sizing math. Never executes anything -- pure calculation."""

from dataclasses import dataclass

from core.config import risk as risk_config


@dataclass
class RiskPlan:
    stop_price: float
    target_price: float
    risk_pct: float
    position_size: float   # suggested position size, in quote currency (e.g. USDT)
    risk_amount: float     # portfolio currency at risk if the stop is hit


def compute_stop_price(direction: str, entry_price: float, atr: float, invalidation_price: float) -> float:
    """Stop sits at the wider of (an ATR multiple beyond entry) and (the pattern's own invalidation level),
    so the stop never sits inside a level the pattern itself says would prove it wrong."""
    atr_stop = entry_price - atr * risk_config.atr_stop_multiple if direction == "long" else \
        entry_price + atr * risk_config.atr_stop_multiple
    if direction == "long":
        return min(atr_stop, invalidation_price)
    return max(atr_stop, invalidation_price)


def compute_risk_plan(
    direction: str,
    entry_price: float,
    atr: float,
    invalidation_price: float,
    target_price: float,
    portfolio_size: float = None,
    max_risk_pct: float = None,
) -> RiskPlan:
    portfolio_size = portfolio_size if portfolio_size is not None else risk_config.default_portfolio_size
    max_risk_pct = max_risk_pct if max_risk_pct is not None else risk_config.default_max_risk_pct

    stop_price = compute_stop_price(direction, entry_price, atr, invalidation_price)
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        stop_distance = entry_price * 0.005  # guard against a degenerate zero-distance stop

    risk_amount = portfolio_size * (max_risk_pct / 100)
    position_size = min((risk_amount / stop_distance) * entry_price, portfolio_size)

    return RiskPlan(
        stop_price=stop_price,
        target_price=target_price,
        risk_pct=max_risk_pct,
        position_size=position_size,
        risk_amount=risk_amount,
    )
