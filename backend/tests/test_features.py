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
    from app.fees import FeeModel
    from app.services.etf import simulate_plan
    r = simulate_plan(_monthly_series([100] * 61), monthly=100, initial=0, years=5, fees=FeeModel(0.01))
    assert r["months"] == 60 and r["invested"] == 6000 and r["fees"] == 60
    assert r["final_value"] == 5940 and r["gain"] == -60 and r["tax"] == 0 and r["net_gain"] == -60
    assert r["annual_return_pct"] < 0 and len(r["curve"]) == 61


def test_plan_gain_tax_and_money_weighted_return():
    from app.fees import FeeModel
    from app.services.etf import simulate_plan
    prices = [100 * 1.01 ** i for i in range(73)]  # +1 % par mois, soit 12,68 % par an
    r = simulate_plan(_monthly_series(prices), monthly=200, initial=1000, years=6, fees=FeeModel(0.0))
    assert r["invested"] == 1000 + 200 * 72 and r["after_5_years"] is True and r["tax_rate_pct"] == 18.6
    assert abs(r["annual_return_pct"] - 12.68) < 0.1  # retrouve le rendement de l'actif
    assert abs(r["tax"] - r["gain"] * 0.186) < 0.01 and abs(r["net_value"] - (r["final_value"] - r["tax"])) < 0.01
    short = simulate_plan(_monthly_series(prices), monthly=200, initial=0, years=2, fees=FeeModel(0.0))
    assert short["tax_rate_pct"] == 31.4 and short["windows"]["count"] == 73 - 24
    assert short["windows"]["loss_share"] == 0 and short["windows"]["worst_pct"] > 0


def test_plan_reports_worst_point_and_shortened_history():
    from app.fees import FeeModel
    from app.services.etf import simulate_plan
    prices = [100] * 12 + [50] * 6 + [100] * 19  # krach de 50 % puis retour
    r = simulate_plan(_monthly_series(prices), monthly=100, initial=0, years=10, fees=FeeModel(0.0))
    assert r["months"] == 36 and r["requested_months"] == 120  # historique plus court que demandé
    assert r["worst_point"]["gap_pct"] < -40 and r["gain"] > 0  # acheter pendant la baisse a payé


def test_etf_api(auth_client):
    rows = auth_client.get("/api/etfs").json()
    etfs, indices = [r for r in rows if r["kind"] == "etf"], [r for r in rows if r["kind"] == "indice"]
    assert len(etfs) == 8 and len(indices) == 4 and all(r["available"] for r in rows)
    assert etfs[0]["perf_5y_pct"] is not None and etfs[0]["perf_20y_pct"] is None
    assert indices[0]["history_years"] > 30 and indices[0]["perf_20y_pct"] is not None  # historique long
    plan = {"allocations": [{"ticker": "CW8.PA", "weight": 100}], "monthly": 200, "initial": 1000, "years": 8}
    r = auth_client.post("/api/etfs/simulate", json=plan).json()
    assert r["invested"] == 1000 + 200 * 96 and r["windows"]["count"] > 12 and r["name"] == "Amundi MSCI World Swap"
    assert len(r["windows"]["series"]) == r["windows"]["count"] and r["windows"]["worst_start"]
    stock = {**plan, "allocations": [{"ticker": "AIR.PA", "weight": 100}]}
    assert auth_client.post("/api/etfs/simulate", json=stock).status_code == 404
    too_much = {**plan, "monthly": 5000, "years": 10}
    assert auth_client.post("/api/etfs/simulate", json=too_much).status_code == 422  # plafond du PEA
    auth_client.post("/api/portfolio/orders", json={"ticker": "CW8.PA", "side": "buy", "quantity": 1})
    assert auth_client.get("/api/portfolio").json()["positions"][0]["is_etf"] is True
    index_order = {"ticker": "^FCHI", "side": "buy", "quantity": 1}
    assert auth_client.post("/api/portfolio/orders", json=index_order).status_code == 422  # non achetable


def test_long_plan_on_index_and_allocation_api(auth_client):
    long = {"allocations": [{"ticker": "^FCHI", "weight": 100}], "monthly": 100, "years": 25}
    r = auth_client.post("/api/etfs/simulate", json=long).json()
    assert r["months"] == 300 and r["windows"]["count"] > 100
    mix = {"allocations": [{"ticker": "CW8.PA", "weight": 70}, {"ticker": "PCEU.PA", "weight": 30}],
           "monthly": 300, "years": 8, "rebalance": True, "fees": {"rate": 0.001, "minimum": 1}}
    r = auth_client.post("/api/etfs/simulate", json=mix).json()
    assert r["rebalanced"] is True and r["fee_label"] == "0,1 % (minimum 1 €)"
    assert r["name"] == "70 % Amundi MSCI World Swap + 30 % Amundi PEA MSCI Europe"
    bad = {**mix, "allocations": [{"ticker": "CW8.PA", "weight": 70}, {"ticker": "PCEU.PA", "weight": 20}]}
    assert auth_client.post("/api/etfs/simulate", json=bad).status_code == 422
    twice = {**mix, "allocations": [{"ticker": "CW8.PA", "weight": 50}] * 2}
    assert auth_client.post("/api/etfs/simulate", json=twice).status_code == 422


def test_fee_model():
    from app.fees import FeeModel
    f = FeeModel(rate=0.001, minimum=1.0, fixed=0.5)
    assert f.fee(100) == 1.0 and f.fee(10_000) == 10.5 and f.fee(0) == 0 and f.fee(0.4) == 0.4
    assert FeeModel(0.005).label() == "0,5 %" and FeeModel(0, fixed=2).label() == "2 € fixes"


def test_minimum_fee_hurts_small_orders():
    from app.fees import FeeModel
    from app.services.etf import simulate_plan
    flat = _monthly_series([100] * 61)
    pct = simulate_plan(flat, monthly=50, initial=0, years=5, fees=FeeModel(0.005))
    floor = simulate_plan(flat, monthly=50, initial=0, years=5, fees=FeeModel(0.005, minimum=2))
    assert pct["fees"] == 15.0 and floor["fees"] == 120.0  # 0,25 € contre 2 € par ordre
    assert floor["final_value"] == 3000 - 120


def test_rebalancing_restores_weights_and_costs_fees():
    from app.fees import FeeModel
    from app.services.etf import simulate_plan
    rising, flat = _monthly_series([100 * 1.03 ** i for i in range(37)]), _monthly_series([100] * 37)
    closes = pd.DataFrame({"A": rising, "B": flat})
    drift = simulate_plan(closes, 100, 0, 3, FeeModel(0.0), weights=[50, 50])
    rebal = simulate_plan(closes, 100, 0, 3, FeeModel(0.0), weights=[50, 50], rebalance=True)
    assert rebal["rebalanced"] and not drift["rebalanced"]
    assert rebal["final_value"] < drift["final_value"]  # on a vendu ce qui montait
    costly = simulate_plan(closes, 100, 0, 3, FeeModel(0.01), weights=[50, 50], rebalance=True)
    plain = simulate_plan(closes, 100, 0, 3, FeeModel(0.01), weights=[50, 50])
    assert costly["fees"] > plain["fees"] == 36.0
    single = simulate_plan(rising, 100, 0, 3, FeeModel(0.0), rebalance=True)
    assert single["rebalanced"] is False  # rien à rééquilibrer avec un seul support


def test_goal_monthly_reaches_target_in_most_past_periods():
    import numpy as np

    from app.fees import FeeModel
    from app.services.etf import goal_plan, simulate_plan
    rng = np.random.default_rng(3)
    closes = _monthly_series(100 * np.exp(np.cumsum(rng.normal(0.006, 0.04, 400)))).to_frame("X")
    fees = FeeModel(0.002)
    g = goal_plan(closes, target=50_000, years=10, initial=2_000, fees=fees, inflation_pct=2.0)
    bad, mid, good = (x["monthly"] for x in g["scenarios"])
    assert bad >= mid >= good > 0 and g["worst_case_monthly"] >= bad
    assert abs(g["target_nominal"] - 50_000 * 1.02 ** 10) < 0.01 and g["tax_rate_pct"] == 18.6
    # vérification croisée : avec le versement « défavorable », ~90 % des plans passés atteignent la cible nette
    check = simulate_plan(closes, bad, 2_000, 10, fees)
    invested = check["invested"]
    nets = [invested * (1 + w["pct"] / 100) - max(invested * w["pct"] / 100, 0) * 0.186
            for w in check["windows"]["series"]]
    share = np.mean([n >= g["target_nominal"] - 1 for n in nets])
    assert 0.85 <= share <= 0.95
    import pytest
    with pytest.raises(ValueError):
        goal_plan(closes.iloc[:100], 50_000, 10, 0, fees, 2.0)  # historique trop court


def test_goal_api(auth_client):
    body = {"allocations": [{"ticker": "^GSPC", "weight": 100}], "target": 100000, "years": 15, "initial": 5000}
    g = auth_client.post("/api/etfs/goal", json=body).json()
    assert [s["label"] for s in g["scenarios"]] == ["Défavorable", "Médian", "Favorable"] and g["windows"] > 100
    short = {**body, "allocations": [{"ticker": "CW8.PA", "weight": 100}]}
    assert auth_client.post("/api/etfs/goal", json=short).status_code == 400  # 10 ans d'historique


def test_change_password(auth_client):
    from tests.conftest import CREDS, XHR
    new = "un-nouveau-mot-de-passe"
    wrong = {"current_password": "pas-le-bon-mot-de-passe", "new_password": new}
    assert auth_client.post("/api/auth/password", json=wrong).status_code == 403
    same = {"current_password": CREDS["password"], "new_password": CREDS["password"]}
    assert auth_client.post("/api/auth/password", json=same).status_code == 400
    weak = {"current_password": CREDS["password"], "new_password": "court"}
    assert auth_client.post("/api/auth/password", json=weak).status_code == 422
    old_cookie = auth_client.cookies.get("pea_session")
    ok = {"current_password": CREDS["password"], "new_password": new}
    assert auth_client.post("/api/auth/password", json=ok).status_code == 204
    assert auth_client.get("/api/auth/me").status_code == 200  # la session courante est conservée
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import rate_limiter
    rate_limiter.reset()  # les essais ci-dessus ont consommé le quota de tentatives de ce compte
    with TestClient(app, headers=XHR) as other:
        other.cookies.set("pea_session", old_cookie, path="/api")
        assert other.get("/api/auth/me").status_code == 401  # les autres sessions sont révoquées
        assert other.post("/api/auth/login", json=CREDS).status_code == 401
        assert other.post("/api/auth/login", json={**CREDS, "password": new}).status_code == 200


def test_scan_reports_unusable_tickers(auth_client, monkeypatch):
    real = get_provider().inner.history

    def broken(tickers):
        out = real(tickers)
        out.pop("AIR.PA", None)  # ticker disparu
        if "BNP.PA" in out:
            out["BNP.PA"] = out["BNP.PA"].iloc[:-30]  # cotation arrêtée
        return out

    monkeypatch.setattr(get_provider().inner, "history", broken)
    auth_client.post("/api/scans")
    issues = {i["ticker"]: i["reason"] for i in auth_client.get("/api/scans/latest").json()["result"]["market"]["issues"]}
    assert issues["AIR.PA"] == "aucun cours trouvé" and issues["BNP.PA"].startswith("cours arrêtés au")
    assert len(issues) == 2


def test_dev_secret_persists_across_restarts(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DEV_SECRET_FILE", tmp_path / ".secret_key")
    first = config.Settings(secret_key=None, _env_file=None).signing_key()
    again = config.Settings(secret_key=None, _env_file=None).signing_key()
    assert first == again and len(first) >= 32
    assert (tmp_path / ".secret_key").stat().st_mode & 0o077 == 0  # lisible par le seul propriétaire
    import pytest
    with pytest.raises(RuntimeError):
        config.Settings(secret_key=None, environment="prod", _env_file=None).signing_key()
