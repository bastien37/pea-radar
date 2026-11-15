"""Portefeuille fictif : ordres au dernier cours de clôture, frais, plus-values."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.market.provider import get_provider
from app.models import Portfolio, Position, Trade, User
from app.universe import load_universe

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


def place_order(db: Session, user: User, ticker: str, side: str, quantity: int) -> Trade:
    if ticker not in load_universe():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Titre hors de l'univers éligible PEA")
    quote = get_provider().last_price(ticker)
    if quote is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cours indisponible")
    price = quote[0]
    pf = get_portfolio(db, user)
    gross = price * quantity
    fee = round(gross * get_settings().order_fee_rate, 2)
    pos = next((p for p in pf.positions if p.ticker == ticker), None)
    realized = None

    if side == "buy":
        if gross + fee > pf.cash + 1e-9:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Liquidités insuffisantes")
        pf.cash = round(pf.cash - gross - fee, 2)
        if pos is None:
            pf.positions.append(Position(ticker=ticker, quantity=quantity,
                                         avg_cost=(gross + fee) / quantity))
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


def portfolio_view(db: Session, user: User) -> dict:
    pf = get_portfolio(db, user)
    universe, provider = load_universe(), get_provider()
    positions, invested = [], 0.0
    for p in sorted(pf.positions, key=lambda x: x.ticker):
        quote = provider.last_price(p.ticker)
        price = quote[0] if quote else p.avg_cost
        value = price * p.quantity
        invested += value
        positions.append({
            "ticker": p.ticker, "name": universe[p.ticker].name if p.ticker in universe else p.ticker,
            "quantity": p.quantity, "avg_cost": round(p.avg_cost, 4), "price": round(price, 4),
            "value": round(value, 2),
            "pnl": round(value - p.avg_cost * p.quantity, 2),
            "pnl_pct": round((price / p.avg_cost - 1) * 100, 2),
        })
    total = pf.cash + invested
    return {
        "initial_cash": pf.initial_cash, "cash": round(pf.cash, 2),
        "invested": round(invested, 2), "total_value": round(total, 2),
        "performance_pct": round((total / pf.initial_cash - 1) * 100, 2),
        "realized_pnl": round(sum(t.realized_pnl or 0 for t in pf.trades), 2),
        "fees_paid": round(sum(t.fee for t in pf.trades), 2),
        "fee_rate": get_settings().order_fee_rate,
        "positions": positions,
    }
