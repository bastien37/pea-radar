"""Après chaque scan : alertes (nouvelles entrées, stops, objectifs), suivi automatique, e-mail."""

import logging
import math
import smtplib
from email.message import EmailMessage

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.market.provider import get_provider
from app.models import Alert, Scan, User
from app.services import simulation as sim

log = logging.getLogger(__name__)
TRIGGER_TEXT = {
    "stop": "a touché son stop de protection",
    "objectif": "a atteint son objectif de gain",
    "échéance": "est arrivée au terme de sa durée de détention",
}


def _add(db: Session, user: User, kind: str, ticker: str, message: str, created: list[Alert]) -> None:
    # Une alerte d'état (stop, échéance…) n'est pas répétée tant qu'elle n'est pas lue ;
    # les opérations du suivi automatique sont des événements, toujours consignés.
    duplicate = None if kind == "auto" else db.scalar(select(Alert).where(
        Alert.user_id == user.id, Alert.kind == kind, Alert.ticker == ticker, Alert.read.is_(False)))
    if duplicate is None:
        alert = Alert(user_id=user.id, kind=kind, ticker=ticker, message=message[:300])
        db.add(alert)
        created.append(alert)


def _auto_follow(db: Session, user: User, scan: Scan, created: list[Alert]) -> None:
    """Solde les positions déclenchées, puis achète les meilleures opportunités à parts égales."""
    s = get_settings()
    pf = sim.get_portfolio(db, user)
    just_sold = set()
    for pos in list(pf.positions):
        trigger = sim.position_status(pos)["trigger"]
        if trigger:
            just_sold.add(pos.ticker)  # pas de rachat immédiat : il ne ferait que payer des frais
            trade = sim.place_order(db, user, pos.ticker, "sell", pos.quantity)
            _add(db, user, "auto", pos.ticker,
                 f"Suivi auto : vente de {trade.quantity} {pos.ticker} ({trigger}), "
                 f"résultat {trade.realized_pnl:+.2f} €", created)

    regime = (scan.market or {}).get("regime")
    if regime and not regime["favorable"]:
        return  # même règle que le backtest : pas d'achat sous la moyenne 200 séances
    held = {p.ticker for p in pf.positions}
    budget = sim.portfolio_view(db, user)["total_value"] / s.auto_follow_positions
    for opp in scan.opportunities:
        if len(held) >= s.auto_follow_positions:
            break
        quote = get_provider().last_price(opp.ticker)
        if opp.ticker in held or opp.ticker in just_sold or quote is None \
                or opp.details.get("earnings_warning") or opp.expected_gain_pct <= 0:
            continue
        qty = math.floor(min(budget, pf.cash) / (quote[0] * (1 + s.order_fee_rate)))
        if qty < 1:
            continue
        try:
            sim.place_order(db, user, opp.ticker, "buy", qty,
                            plan=sim.plan_from_opportunity(opp, quote[0]))
        except HTTPException:
            continue
        held.add(opp.ticker)
        _add(db, user, "auto", opp.ticker,
             f"Suivi auto : achat de {qty} {opp.name} à {quote[0]:.2f} € (rang {opp.rank})", created)


def send_email(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not (s.smtp_host and s.smtp_from):
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls()  # refuse d'envoyer identifiants et contenu en clair
            if s.smtp_user and s.smtp_password:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Envoi de l'e-mail d'alerte impossible : %s", type(exc).__name__)
        return False


def after_scan(scan_id: int) -> list[str]:
    created: list[Alert] = []
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        user = db.get(User, scan.user_id)
        previous = db.scalar(select(Scan).where(Scan.user_id == user.id, Scan.status == "done",
                                                Scan.id < scan.id).order_by(Scan.id.desc()))
        if previous is not None:
            before = {o.ticker for o in previous.opportunities}
            entrants = [o for o in scan.opportunities if o.ticker not in before]
            if len(entrants) > 5:  # top largement renouvelé : une alerte de synthèse suffit
                names = ", ".join(o.name for o in entrants[:5])
                _add(db, user, "entrée", "*",
                     f"{len(entrants)} nouveaux titres dans le top, dont {names}", created)
            else:
                for o in entrants:
                    _add(db, user, "entrée", o.ticker,
                         f"{o.name} entre dans le top (rang {o.rank}, score {o.adjusted_score:.0f})",
                         created)
        pf = sim.get_portfolio(db, user)
        if pf.auto_follow:
            _auto_follow(db, user, scan, created)
        else:
            for pos in pf.positions:
                trigger = sim.position_status(pos)["trigger"]
                if trigger:
                    _add(db, user, trigger, pos.ticker,
                         f"Votre position {pos.ticker} {TRIGGER_TEXT[trigger]}", created)
        db.commit()
        messages = [a.message for a in created]
        email = user.email
    if messages:
        send_email(email, f"PEA Radar : {len(messages)} alerte(s)", "\n".join(f"- {m}" for m in messages))
    return messages
