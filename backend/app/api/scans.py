from datetime import timedelta

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.market.provider import get_provider
from app.models import Opportunity, Scan, User, utcnow
from app.security import get_current_user
from app.services.scanner import run_scan

router = APIRouter(tags=["scans"])
STALE = timedelta(minutes=20)


def _opp_out(o: Opportunity) -> dict:
    return {
        "id": o.id, "rank": o.rank, "ticker": o.ticker, "name": o.name, "price": o.price,
        "price_date": o.price_date, "score": o.score, "adjusted_score": o.adjusted_score,
        "risk_level": o.risk_level, "risk_label": o.details.get("risk_label"),
        "expected_gain_pct": o.expected_gain_pct, "gain_low_pct": o.gain_low_pct,
        "gain_high_pct": o.gain_high_pct, "win_rate": o.win_rate,
        "holding_days": o.holding_days, "stop_loss": o.stop_loss,
        "details": o.details, "news": o.news,
    }


def _scan_out(scan: Scan, full: bool = True) -> dict:
    out = {"id": scan.id, "status": scan.status, "progress": scan.progress, "error": scan.error,
           "started_at": scan.started_at, "finished_at": scan.finished_at}
    if full:
        out["opportunities"] = [_opp_out(o) for o in scan.opportunities]
        out["ranking"] = scan.ranking
    return out


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)


@router.post("/scans", status_code=status.HTTP_202_ACCEPTED)
def start_scan(tasks: BackgroundTasks, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)) -> dict:
    active = db.scalars(select(Scan).where(Scan.user_id == user.id,
                                           Scan.status.in_(("pending", "running")))).all()
    for scan in active:
        if utcnow() - _as_utc(scan.started_at) < STALE:
            raise HTTPException(status.HTTP_409_CONFLICT, "Un scan est déjà en cours")
        scan.status, scan.error = "failed", "Scan interrompu"
    scan = Scan(user_id=user.id)
    db.add(scan)
    db.commit()
    tasks.add_task(run_scan, scan.id)
    return _scan_out(scan, full=False)


@router.get("/scans/latest")
def latest_scan(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Dernier scan (pour suivre la progression) et dernier scan terminé (pour l'affichage)."""
    q = select(Scan).where(Scan.user_id == user.id).order_by(Scan.id.desc())
    last = db.scalar(q)
    done = db.scalar(q.where(Scan.status == "done"))
    return {"current": _scan_out(last, full=False) if last else None,
            "result": _scan_out(done) if done else None}


@router.get("/opportunities/track")
def track(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Confronte chaque recommandation passée à ce que le cours a réellement fait ensuite."""
    opps = db.scalars(
        select(Opportunity).join(Scan).where(Scan.user_id == user.id)
        .order_by(Opportunity.id.desc()).limit(200)
    ).all()
    histories = get_provider().history(sorted({o.ticker for o in opps}))
    rows, seen = [], set()
    for o in opps:
        key = (o.ticker, o.price_date)
        df = histories.get(o.ticker)
        if key in seen or df is None:
            continue
        seen.add(key)
        after = df[df.index > o.price_date]
        elapsed = len(after)
        matured = elapsed >= o.holding_days
        window = after.iloc[: o.holding_days]
        ref = window["Close"].iloc[-1] if elapsed else o.price
        outcome = (ref / o.price - 1) * 100
        stopped = bool(elapsed and window["Low"].min() <= o.stop_loss)
        verdict = "stop touché" if stopped else (
            "en cours" if not matured else ("gagnante" if outcome > 0 else "perdante"))
        rows.append({
            "ticker": o.ticker, "name": o.name, "date": o.price_date, "entry_price": o.price,
            "score": o.adjusted_score, "expected_gain_pct": o.expected_gain_pct,
            "holding_days": o.holding_days, "elapsed_days": min(elapsed, o.holding_days),
            "outcome_pct": round(float(outcome), 2), "matured": matured, "verdict": verdict,
        })
    closed = [r for r in rows if r["matured"] or r["verdict"] == "stop touché"]
    summary = {
        "total": len(rows), "closed": len(closed),
        "win_rate": round(float(np.mean([r["outcome_pct"] > 0 for r in closed])), 3)
        if closed else None,
        "avg_outcome_pct": round(float(np.mean([r["outcome_pct"] for r in closed])), 2)
        if closed else None,
        "avg_expected_pct": round(float(np.mean([r["expected_gain_pct"] for r in closed])), 2)
        if closed else None,
    }
    return {"summary": summary, "items": rows}
