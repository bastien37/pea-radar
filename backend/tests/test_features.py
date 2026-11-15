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
        from alembic.script import ScriptDirectory
        head = ScriptDirectory.from_config(cfg).get_current_head()
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head


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


def _walk_inputs(days=2400, n=30):
    import numpy as np
    idx = pd.bdate_range(end="2026-11-13", periods=days)
    rng = np.random.default_rng(7)
    hist = {}
    for i in range(n):  # les 10 premiers titres ont une dérive persistante : le momentum doit les trouver
        drift = 0.0012 if i < 10 else -0.0002
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.012, days)))
        hist[f"T{i}.PA"] = pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * 0.995,
                                         "Close": close, "Volume": 1e6}, index=idx)
    return hist


def test_simulate_matches_equal_weight_universe_without_fees():
    import numpy as np

    from app.analysis.walkforward import build_signals, simulate
    close, signals = build_signals(_walk_inputs(days=800, n=8), with_score=False)
    flat = signals["momentum"].notna().astype(float).where(signals["momentum"].notna())
    net, turnover = simulate(flat, close, top_n=8, rebalance=1, fee_rate=0.0)
    start = flat.first_valid_index()
    expected = close.pct_change(fill_method=None).mean(axis=1)
    assert np.allclose(net[net.index > start], expected[expected.index > start])
    costly, _ = simulate(flat, close, top_n=8, rebalance=1, fee_rate=0.01)
    assert (costly <= net + 1e-12).all() and costly.sum() < net.sum()  # les frais ne font que coûter
    assert turnover.loc[start] == 1.0  # premier investissement : tout le portefeuille


def test_walkforward_finds_planted_momentum_and_stays_out_of_sample():
    from app.analysis.walkforward import AUTO, WalkForwardParams, run_walkforward
    hist = _walk_inputs()
    r = run_walkforward(hist, None, "ETF CAC 40", WalkForwardParams(years=9, fee_rate=0.002))
    rows = {s["name"]: s for s in r["strategies"]}
    assert r["reference"] == "Univers équipondéré" and rows["Univers équipondéré"]["reference"]
    assert rows["Momentum 12 mois"]["beats_reference"] is True and "Momentum 12 mois" in r["winners"]
    assert rows["Momentum 12 mois"]["cagr_pct"] > rows["Univers équipondéré"]["cagr_pct"] + 5
    assert rows[AUTO]["years"] == r["period"]["test_years"] == len(r["yearly"]) == len(r["choices"])
    # hors échantillon : la première année de test vient après la fenêtre d'apprentissage
    first_valid = pd.Timestamp("2026-11-13") - pd.DateOffset(years=9)
    assert pd.Timestamp(r["period"]["start"]).year >= first_valid.year + 3
    assert set(r["choices"][0]) == {"year", *rows} - {"Univers équipondéré"}


def test_walkforward_api_is_separate_from_backtests(auth_client):
    assert auth_client.get("/api/backtests/walkforward/latest").json() is None
    assert auth_client.post("/api/backtests/walkforward", json={"years": 3}).status_code == 422
    assert auth_client.post("/api/backtests/walkforward", json={"years": 9}).status_code == 202
    wf = auth_client.get("/api/backtests/walkforward/latest").json()
    assert wf["status"] == "done", wf["error"]
    names = [s["name"] for s in wf["result"]["strategies"]]
    assert names[-2:] == ["Univers équipondéré", "ETF CAC 40"] and "Score PEA Radar" in names
    assert wf["result"]["params"]["fee_rate"] == 0.005
    assert auth_client.get("/api/backtests/latest").json() is None  # pas mélangé aux backtests


def _monthly_series(prices):
    idx = pd.to_datetime([f"{2015 + i // 12}-{i % 12 + 1:02d}-01" for i in range(len(prices))])
    return pd.Series([float(p) for p in prices], index=idx)


def test_plan_on_flat_price_only_loses_fees():
    from app.services.etf import simulate_plan
    r = simulate_plan(_monthly_series([100] * 61), monthly=100, initial=0, years=5, fee_rate=0.01)
    assert r["months"] == 60 and r["invested"] == 6000 and r["fees"] == 60
    assert r["final_value"] == 5940 and r["gain"] == -60 and r["tax"] == 0 and r["net_gain"] == -60
    assert r["annual_return_pct"] < 0 and len(r["curve"]) == 61


def test_plan_gain_tax_and_money_weighted_return():
    from app.services.etf import simulate_plan
    prices = [100 * 1.01 ** i for i in range(73)]  # +1 % par mois, soit 12,68 % par an
    r = simulate_plan(_monthly_series(prices), monthly=200, initial=1000, years=6, fee_rate=0.0)
    assert r["invested"] == 1000 + 200 * 72 and r["after_5_years"] is True and r["tax_rate_pct"] == 18.6
    assert abs(r["annual_return_pct"] - 12.68) < 0.1  # retrouve le rendement de l'actif
    assert abs(r["tax"] - r["gain"] * 0.186) < 0.01 and abs(r["net_value"] - (r["final_value"] - r["tax"])) < 0.01
    short = simulate_plan(_monthly_series(prices), monthly=200, initial=0, years=2, fee_rate=0.0)
    assert short["tax_rate_pct"] == 31.4 and short["windows"]["count"] == 73 - 24
    assert short["windows"]["loss_share"] == 0 and short["windows"]["worst_pct"] > 0


def test_plan_reports_worst_point_and_shortened_history():
    from app.services.etf import simulate_plan
    prices = [100] * 12 + [50] * 6 + [100] * 19  # krach de 50 % puis retour
    r = simulate_plan(_monthly_series(prices), monthly=100, initial=0, years=10, fee_rate=0.0)
    assert r["months"] == 36 and r["requested_months"] == 120  # historique plus court que demandé
    assert r["worst_point"]["gap_pct"] < -40 and r["gain"] > 0  # acheter pendant la baisse a payé


def test_etf_api(auth_client):
    rows = auth_client.get("/api/etfs").json()
    assert len(rows) == 8 and all(r["available"] for r in rows)
    assert rows[0]["perf_5y_pct"] is not None and rows[0]["perf_10y_pct"] is None and rows[0]["max_drawdown_pct"] > 0
    plan = {"ticker": "CW8.PA", "monthly": 200, "initial": 1000, "years": 8}
    r = auth_client.post("/api/etfs/simulate", json=plan).json()
    assert r["invested"] == 1000 + 200 * 96 and r["windows"]["count"] > 12 and r["name"]
    assert auth_client.post("/api/etfs/simulate", json={**plan, "ticker": "AIR.PA"}).status_code == 404
    too_much = {**plan, "monthly": 5000, "years": 10}
    assert auth_client.post("/api/etfs/simulate", json=too_much).status_code == 422  # plafond du PEA
    auth_client.post("/api/portfolio/orders", json={"ticker": "CW8.PA", "side": "buy", "quantity": 1})
    assert auth_client.get("/api/portfolio").json()["positions"][0]["is_etf"] is True
