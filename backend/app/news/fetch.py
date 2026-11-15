"""Récupération des titres d'actualité (flux RSS Google Actualités)."""

import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from defusedxml import ElementTree  # analyse XML durcie (XXE, bombes d'entités)

log = logging.getLogger(__name__)
_RSS = "https://news.google.com/rss/search"
_MAX_BYTES = 2_000_000


def fetch_headlines(name: str, limit: int, max_age_days: int = 21) -> list[dict]:
    params = {"q": f'"{name}" bourse action', "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    try:
        r = httpx.get(_RSS, params=params, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "pea-radar/0.1"})
        r.raise_for_status()
        if len(r.content) > _MAX_BYTES:
            raise ValueError("flux RSS trop volumineux")
        root = ElementTree.fromstring(r.content)
    except Exception as exc:  # noqa: BLE001 — l'actualité est facultative
        log.warning("Actualités indisponibles pour %s : %s", name, exc)
        return []

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    items = []
    for item in root.iter("item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        link = (item.findtext("link") or "").strip()
        if published < cutoff or not link.startswith("https://"):
            continue
        items.append({
            "title": (item.findtext("title") or "").strip()[:300],
            "source": (item.findtext("source") or "").strip()[:80],
            "published": published.astimezone(UTC).strftime("%Y-%m-%d"),
            "link": link[:1000],
        })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:limit]
