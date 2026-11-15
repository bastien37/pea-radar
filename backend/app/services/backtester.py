import logging

from app.analysis.backtest import BacktestParams, run_backtest
from app.db import SessionLocal
from app.market.provider import get_provider
from app.models import Backtest, utcnow
from app.universe import BENCHMARK_ETF, INDEX_TICKER, load_universe

log = logging.getLogger(__name__)


def run_backtest_job(backtest_id: int) -> None:
    with SessionLocal() as db:
        bt = db.get(Backtest, backtest_id)
        bt.status = "running"
        params = BacktestParams(**bt.params)
        db.commit()
    try:
        universe = load_universe()
        histories = get_provider().history([*universe, INDEX_TICKER, BENCHMARK_ETF])
        result = run_backtest(histories, {t: s.sector for t, s in universe.items()},
                              histories.get(INDEX_TICKER), histories.get(BENCHMARK_ETF), params)
        fields = {"status": "done", "result": result}
    except Exception as exc:
        log.exception("Backtest %s en échec", backtest_id)
        fields = {"status": "failed", "error": str(exc)[:500]}
    with SessionLocal() as db:
        bt = db.get(Backtest, backtest_id)
        for k, v in fields.items():
            setattr(bt, k, v)
        bt.finished_at = utcnow()
        db.commit()
