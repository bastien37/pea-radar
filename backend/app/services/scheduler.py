"""Scan planifié : chaque jour ouvré à l'heure configurée (heure de Paris)."""

import asyncio
import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Scan, User
from app.services.scanner import run_scan

log = logging.getLogger(__name__)
PARIS = ZoneInfo("Europe/Paris")


def due_scans(now: datetime | None = None) -> list[int]:
    """Crée les scans planifiés dus et retourne leurs identifiants."""
    schedule = get_settings().scan_schedule
    now = now or datetime.now(PARIS)
    if not schedule or now.weekday() >= 5 or now.strftime("%H:%M") < schedule:
        return []
    day_start = datetime.combine(now.date(), time.min, PARIS).astimezone(UTC)
    ids = []
    with SessionLocal() as db:
        for user in db.scalars(select(User)):
            busy = db.scalar(select(Scan.id).where(
                Scan.user_id == user.id,
                (Scan.status.in_(("pending", "running")))
                | ((Scan.trigger == "scheduled") & (Scan.started_at >= day_start))))
            if busy is None:
                scan = Scan(user_id=user.id, trigger="scheduled")
                db.add(scan)
                db.flush()
                ids.append(scan.id)
        db.commit()
    return ids


def tick() -> None:
    for scan_id in due_scans():
        log.info("Scan planifié %s lancé", scan_id)
        run_scan(scan_id)


async def scheduler_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(tick)
        except Exception:
            log.exception("Planificateur : itération en échec")
