from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Alert, User
from app.security import get_current_user

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Alert).where(Alert.user_id == user.id)
                      .order_by(Alert.read, Alert.id.desc()).limit(50))
    return [{"id": a.id, "kind": a.kind, "ticker": a.ticker, "message": a.message,
             "read": a.read, "created_at": a.created_at} for a in rows]


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    db.execute(update(Alert).where(Alert.user_id == user.id, Alert.read.is_(False)).values(read=True))
    db.commit()
