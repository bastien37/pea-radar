"""Portefeuille fictif : ordres au dernier cours de clôture, frais, plus-values, fiscalité PEA."""

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.fees import default_fees
from app.market.provider import get_provider
from app.models import Opportunity, Portfolio, Position, Scan, Trade, User
from app.universe import load_tradable

PEA_CEILING = 150_000.0  # plafond de versements d'un PEA classique


def get_portfolio(db: Session, user: User) -> Portfolio:
    pf = db.scalar(select(Portfolio).where(Portfolio.user_id == user.id))
    if pf is None:
        cash = get_settings().default_initial_cash
        pf = Portfolio(user_id=user.id, initial_cash=cash, cash=cash)
        db.add(pf)
        db.commit()
    return pf


def reset_portfolio(db: Session, user: User, initial_cash: float) -> Portfolio:
    pf = get_portfolio(db, user)
    pf.positions.clear()
    pf.trades.clear()
    pf.initial_cash = pf.cash = round(initial_cash, 2)
    db.commit()
    return pf


def _plan_from_latest_scan(db: Session, user: User, ticker: str, price: float) -> dict:
    """Stop, objectif et échéance repris de la dernière recommandation sur ce titre."""
    opp = db.scalar(
        select(Opportunity).join(Scan)
        .where(Scan.user_id == user.id, Scan.status == "done", Opportunity.ticker == ticker)
        .order_by(Opportunity.id.desc())
    )
    if opp is None:
        return {}
    return plan_from_opportunity(opp, price)


def plan_from_opportunity(opp: Opportunity, price: float) -> dict:
    return {
        "holding_days": opp.holding_days, "stop_loss": opp.stop_loss,
        "target_price": round(price * (1 + opp.expected_gain_pct / 100), 4)
        if opp.expected_gain_pct > 0 else None,
    }


def place_order(db: Session, user: User, ticker: str, side: str, quantity: int,
                plan: dict | None = None) -> Trade:
    if ticker not in load_tradable():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Titre hors de l'univers éligible PEA")
    quote = get_provider().last_price(ticker)
    if quote is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cours indisponible")
    price, price_date = quote
    pf = get_portfolio(db, user)
    gross = price * quantity
    fee = round(default_fees().fee(gross), 2)
    pos = next((p for p in pf.positions if p.ticker == ticker), None)
    realized = None

    if side == "buy":
        if gross + fee > pf.cash + 1e-9:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Liquidités insuffisantes")
        pf.cash = round(pf.cash - gross - fee, 2)
        if pos is None:
            plan = plan if plan is not None else _plan_from_latest_scan(db, user, ticker, price)
            pf.positions.append(Position(ticker=ticker, quantity=quantity, opened_on=price_date,
                                         avg_cost=(gross + fee) / quantity, **plan))
        else:
            total = pos.avg_cost * pos.quantity + gross + fee
            pos.quantity += quantity
            pos.avg_cost = total / pos.quantity
    else:
        if pos is None or pos.quantity < quantity:  # pas de vente à découvert sur un PEA
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Quantité détenue insuffisante")
        realized = round(gross - fee - pos.avg_cost * quantity, 2)
        pf.cash = round(pf.cash + gross - fee, 2)
        pos.quantity -= quantity
        if pos.quantity == 0:
            pf.positions.remove(pos)

    trade = Trade(ticker=ticker, side=side, quantity=quantity, price=round(price, 4),
                  fee=fee, realized_pnl=realized)
    pf.trades.append(trade)
    db.commit()
    return trade


def position_status(pos: Position) -> dict:
    """Séances écoulées et éventuel déclencheur (stop, objectif, échéance) d'une position."""
    df = get_provider().history([pos.ticker]).get(pos.ticker)
    if df is None or df.empty:
        return {"price": pos.avg_cost, "elapsed_days": None, "trigger": None}
    price = float(df["Close"].iloc[-1])
    since = df[df.index > pos.opened_on] if pos.opened_on else df.iloc[:0]
    elapsed = len(since) if pos.opened_on else None
    trigger = None
    if pos.stop_loss is not None and len(since) and float(since["Low"].min()) <= pos.stop_loss:
        trigger = "stop"
    elif pos.target_price is not None and price >= pos.target_price:
        trigger = "objectif"
    elif pos.holding_days and elapsed is not None and elapsed >= pos.holding_days:
        trigger = "échéance"
    return {"price": price, "elapsed_days": elapsed, "trigger": trigger}


def tax_view(pf: Portfolio, total_value: float) -> dict:
    """Ce qu'il resterait après impôts si tout le plan était retiré aujourd'hui."""
    s = get_settings()
    opened = date.fromisoformat(pf.pea_opened_on) if pf.pea_opened_on else pf.created_at.date()
    years = (date.today() - opened).days / 365.25
    rate = s.pea_social_rate + (s.pea_income_tax_rate if years < 5 else 0.0)
    gain = total_value - pf.initial_cash
    tax = max(gain, 0.0) * rate
    return {
        "pea_opened_on": opened.isoformat(), "years": round(years, 1), "after_5_years": years >= 5,
        "rate_pct": round(rate * 100, 1), "social_rate_pct": round(s.pea_social_rate * 100, 1),
        "income_tax_rate_pct": round(s.pea_income_tax_rate * 100, 1),
        "gross_gain": round(gain, 2), "tax": round(tax, 2), "net_gain": round(gain - tax, 2),
        "net_value": round(total_value - tax, 2),
    }


def portfolio_view(db: Session, user: User) -> dict:
    pf = get_portfolio(db, user)
    tradable = load_tradable()
    positions, invested = [], 0.0
    for p in sorted(pf.positions, key=lambda x: x.ticker):
        st = position_status(p)
        price = st["price"]
        value = price * p.quantity
        invested += value
        positions.append({
            "ticker": p.ticker, "name": tradable[p.ticker].name if p.ticker in tradable else p.ticker,
            "is_etf": p.ticker in tradable and tradable[p.ticker].is_etf,
            "quantity": p.quantity, "avg_cost": round(p.avg_cost, 4), "price": round(price, 4),
            "value": round(value, 2),
            "pnl": round(value - p.avg_cost * p.quantity, 2),
            "pnl_pct": round((price / p.avg_cost - 1) * 100, 2),
            "opened_on": p.opened_on, "holding_days": p.holding_days,
            "elapsed_days": st["elapsed_days"], "stop_loss": p.stop_loss,
            "target_price": p.target_price, "trigger": st["trigger"],
        })
    total = pf.cash + invested
    return {
        "initial_cash": pf.initial_cash, "cash": round(pf.cash, 2),
        "invested": round(invested, 2), "total_value": round(total, 2),
        "performance_pct": round((total / pf.initial_cash - 1) * 100, 2),
        "realized_pnl": round(sum(t.realized_pnl or 0 for t in pf.trades), 2),
        "fees_paid": round(sum(t.fee for t in pf.trades), 2),
        "fee_rate": get_settings().order_fee_rate, "fee_label": default_fees().label(),
        "auto_follow": pf.auto_follow,
        "auto_follow_positions": get_settings().auto_follow_positions,
        "tax": tax_view(pf, total),
        "positions": positions,
    }
