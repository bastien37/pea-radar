"""Migrations, cours en base, alertes, suivi automatique, planificateur, fiscalité, backtest."""

from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import create_engine, inspect, select, text

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.market.provider import StoredProvider, SyntheticProvider, get_provider
from app.models import Alert, Position, Scan, User
from app.services import scheduler
from app.services import simulation as sim
from app.services.alerts import after_scan


def test_migrations_match_models():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_legacy_database_is_stamped_then_upgraded(tmp_path, monkeypatch):
    """Une base créée avant Alembic (schéma 0001 sans table de version) doit migrer sans perte."""
    from alembic import command
    from alembic.config import Config

    import app.db as appdb

    url = f"sqlite:///{tmp_path}/legacy.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0001")
    legacy = create_engine(url)
    with legacy.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("INSERT INTO users (email, password_hash, token_version, created_at) "
                          "VALUES ('a@b.fr', 'x', 0, '2026-01-01')"))
    monkeypatch.setattr(appdb, "engine", legacy)
    monkeypatch.setattr(appdb, "_url", url)
    appdb.init_db()
    with legacy.connect() as conn:
        assert conn.execute(text("SELECT email FROM users")).scalar() == "a@b.fr"
        assert "auto_follow" in {c["name"] for c in inspect(conn).get_columns("portfolios")}
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


class _Flaky:
    def __init__(self):
        self.calls, self.down = 0, False

    def history(self, tickers):
        self.calls += 1
        return {} if self.down else SyntheticProvider(days=300).history(tickers)


def test_prices_persist_and_survive_source_outage():
    source = _Flaky()
    first = StoredProvider(source, ttl=3600).history(["AIR.PA"])["AIR.PA"]
    assert source.calls == 1
    # nouvelle instance (redémarrage) : lecture en base, aucune requête à la source
    again = StoredProvider(source, ttl=3600).history(["AIR.PA"])["AIR.PA"]
    assert source.calls == 1
    pd.testing.assert_frame_equal(first, again, check_freq=False)
    # cache expiré et source en panne : les cours en base sont servis quand même
    source.down = True
    stale = StoredProvider(source, ttl=0).history(["AIR.PA"])
    assert source.calls == 2 and len(stale["AIR.PA"]) == 300


def test_etfs_tradable_but_not_ranked(auth_client):
    stocks = auth_client.get("/api/stocks").json()
    assert any(s["ticker"] == "CW8.PA" and s["is_etf"] for s in stocks)
    order = {"ticker": "CW8.PA", "side": "buy", "quantity": 1}
    assert auth_client.post("/api/portfolio/orders", json=order).status_code == 201
    auth_client.post("/api/scans")
    result = auth_client.get("/api/scans/latest").json()["result"]
    assert all(r["ticker"] != "CW8.PA" for r in result["ranking"])
    assert {e["ticker"] for e in result["market"]["etfs"]} >= {"CW8.PA", "CAC.PA"}
    assert result["market"]["regime"]["favorable"] in (True, False)


def test_scan_applies_sector_cap_and_fundamentals(auth_client):
    auth_client.post("/api/scans")
    opps = auth_client.get("/api/scans/latest").json()["result"]["opportunities"]
    sectors = [o["sector"] for o in opps]
    assert max(sectors.count(s) for s in set(sectors)) <= get_settings().max_per_sector
    d = opps[0]["details"]
    assert d["fundamental_score"] is not None and "pe" in d["fundamentals"]
    assert abs(d["global_score"] - opps[0]["score"]) <= 10.05  # bonus fondamental borné


def test_tax_before_and_after_five_years(auth_client):
    tax = auth_client.get("/api/portfolio").json()["tax"]
    assert tax["rate_pct"] == 31.4 and tax["after_5_years"] is False and tax["tax"] == 0
    old = (date.today() - timedelta(days=6 * 366)).isoformat()
    tax = auth_client.post("/api/portfolio/settings", json={"pea_opened_on": old}).json()["tax"]
    assert tax["rate_pct"] == 18.6 and tax["after_5_years"] is True
    future = (date.today() + timedelta(days=3)).isoformat()
    r = auth_client.post("/api/portfolio/settings", json={"pea_opened_on": future})
    assert r.status_code == 422
    with SessionLocal() as db:
        pf = sim.get_portfolio(db, db.scalar(select(User)))
        view = sim.tax_view(pf, pf.initial_cash + 1000)
    assert view["tax"] == 186.0 and view["net_gain"] == 814.0


def test_manual_buy_inherits_plan_and_triggers_alert(auth_client):
    auth_client.post("/api/scans")
    top = auth_client.get("/api/scans/latest").json()["result"]["opportunities"][0]
    order = {"ticker": top["ticker"], "side": "buy", "quantity": 1}
    pos = auth_client.post("/api/portfolio/orders", json=order).json()["positions"][0]
    assert pos["stop_loss"] == top["stop_loss"] and pos["holding_days"] == top["holding_days"]
    assert pos["trigger"] is None and pos["elapsed_days"] == 0
    with SessionLocal() as db:  # vieillit la position : l'échéance est dépassée
        p = db.scalar(select(Position))
        p.opened_on, p.stop_loss, p.target_price = "2020-01-01", None, None
        db.commit()
        scan_id = db.scalar(select(Scan.id).order_by(Scan.id.desc()))
    assert auth_client.get("/api/portfolio").json()["positions"][0]["trigger"] == "échéance"
    assert any("durée de détention" in m for m in after_scan(scan_id))
    assert after_scan(scan_id) == []  # pas de doublon tant que l'alerte n'est pas lue
    alerts = auth_client.get("/api/alerts").json()
    assert alerts[0]["kind"] == "échéance" and alerts[0]["read"] is False
    assert auth_client.post("/api/alerts/read").status_code == 204
    assert auth_client.get("/api/alerts").json()[0]["read"] is True


def test_auto_follow_buys_top_and_sells_triggered(auth_client, monkeypatch):
    assert auth_client.post("/api/portfolio/settings", json={"auto_follow": True}).json()["auto_follow"]
    monkeypatch.setattr("app.services.scanner.market_regime",
                        lambda _: {"favorable": True, "index": "CAC 40"})
    auth_client.post("/api/scans")
    pf = auth_client.get("/api/portfolio").json()
    n = get_settings().auto_follow_positions
    assert len(pf["positions"]) == n and pf["cash"] >= 0
    assert all(abs(p["value"] - 2000) < 2000 * 0.2 + p["price"] for p in pf["positions"])
    sold = pf["positions"][0]["ticker"]
    with SessionLocal() as db:
        p = db.scalar(select(Position).where(Position.ticker == sold))
        p.opened_on, p.holding_days = "2020-01-01", 10
        db.commit()
        scan_id = db.scalar(select(Scan.id).order_by(Scan.id.desc()))
        msgs = after_scan(scan_id)
        kinds = set(db.scalars(select(Alert.kind)))
    assert any("vente de" in m and sold in m for m in msgs) and kinds == {"auto"}
    trades = auth_client.get("/api/portfolio/trades").json()
    assert trades[0]["side"] in ("buy", "sell") and any(t["side"] == "sell" for t in trades)


def test_auto_follow_does_not_buy_in_bear_market(auth_client, monkeypatch):
    auth_client.post("/api/portfolio/settings", json={"auto_follow": True})
    monkeypatch.setattr("app.services.scanner.market_regime",
                        lambda _: {"favorable": False, "index": "CAC 40"})
    auth_client.post("/api/scans")
    assert auth_client.get("/api/portfolio").json()["positions"] == []
    opps = auth_client.get("/api/scans/latest").json()["result"]["opportunities"]
    assert all(o["details"]["market_unfavorable"] for o in opps)


def test_scheduler_runs_once_per_weekday(auth_client, monkeypatch):
    monday = datetime(2026, 11, 16, 19, 0, tzinfo=scheduler.PARIS)
    assert scheduler.due_scans(monday) == []  # planification désactivée par défaut
    monkeypatch.setattr(get_settings(), "scan_schedule", "18:30")
    assert scheduler.due_scans(monday.replace(hour=9)) == []  # trop tôt
    assert scheduler.due_scans(monday + timedelta(days=5)) == []  # samedi
    ids = scheduler.due_scans(monday)
    assert len(ids) == 1
    assert scheduler.due_scans(monday) == []  # déjà créé (en attente)
    with SessionLocal() as db:
        scan = db.get(Scan, ids[0])
        assert scan.trigger == "scheduled"
        scan.status, scan.started_at = "done", monday
        db.commit()
    assert scheduler.due_scans(monday.replace(hour=23)) == []  # une seule fois par jour
    assert len(scheduler.due_scans(monday + timedelta(days=1))) == 1


def test_email_needs_configuration_and_uses_starttls(monkeypatch):
    from app.services import alerts
    assert alerts.send_email("a@b.fr", "s", "b") is False
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(get_settings(), "smtp_from", "radar@example.com")
    log = []

    class FakeSMTP:
        def __init__(self, host, port, timeout): log.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): log.append("starttls")
        def send_message(self, msg): log.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    assert alerts.send_email("a@b.fr", "sujet", "corps") is True
    assert log == [("connect", "smtp.example.com", 587), "starttls", ("send", "a@b.fr", "sujet")]


def test_backtest_api(auth_client):
    assert auth_client.get("/api/backtests/latest").json() is None
    assert auth_client.post("/api/backtests", json={"years": 99}).status_code == 422
    r = auth_client.post("/api/backtests", json={"years": 5, "positions": 4, "market_filter": False})
    assert r.status_code == 202
    bt = auth_client.get("/api/backtests/latest").json()
    assert bt["status"] == "done", bt["error"]
    res = bt["result"]
    assert res["params"]["positions"] == 4 and res["params"]["fee_rate"] == 0.005
    assert "ETF CAC 40 (dividendes réinvestis)" in res["metrics"] and res["trades"]["count"] > 0
    assert len(res["curve"]) > 100 and len(res["yearly"]) >= 5
    assert get_provider().last_price("AIR.PA") is not None
