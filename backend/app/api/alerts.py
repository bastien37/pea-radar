from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Alert, User
from app.security import client_ip, get_current_user, rate_limiter
from app.services.alerts import email_status, try_send

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Alert).where(Alert.user_id == user.id)
                      .order_by(Alert.read, Alert.id.desc()).limit(50))
    return [{"id": a.id, "kind": a.kind, "ticker": a.ticker, "message": a.message,
             "read": a.read, "created_at": a.created_at} for a in rows]


@router.get("/email")
def email_settings(_: User = Depends(get_current_user)) -> dict:
    return email_status()


@router.post("/email/test")
def test_email(request: Request, user: User = Depends(get_current_user)) -> dict:
    rate_limiter.check(f"mail-test:{client_ip(request)}", 5, 600)
    if not email_status()["configured"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Aucun serveur d'envoi configuré : renseignez SMTP_HOST, SMTP_FROM "
                            "et, si besoin, SMTP_USER et SMTP_PASSWORD dans backend/.env")
    error = try_send(user.email, "PEA Radar : e-mail de test",
                     "Cet e-mail confirme que les alertes de PEA Radar peuvent vous être envoyées.")
    if error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"L'envoi a échoué — {error}")
    return {"sent_to": user.email}


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    db.execute(update(Alert).where(Alert.user_id == user.id, Alert.read.is_(False)).values(read=True))
    db.commit()
