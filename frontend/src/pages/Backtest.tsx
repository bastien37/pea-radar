import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Backtest as Bt, type BacktestParams, type BtTrade, pct } from "../api";
import Tuto from "../Tuto";

const fr = (n: number) => n.toLocaleString("fr-FR");
const COLORS = ["#3ecf8e", "#4da3ff", "#f5a524"];
const DEFAULTS: BacktestParams = {
  years: 10, positions: 5, holding_days: 40, stop_atr: 2, min_score: 60,
  rebalance_days: 5, max_per_sector: 2, market_filter: true,
};
const FIELDS: [keyof BacktestParams, string, number, number, number][] = [
  ["years", "Années", 1, 15, 1], ["positions", "Lignes max.", 1, 20, 1],
  ["holding_days", "Détention (séances)", 5, 250, 5], ["stop_atr", "Stop (× ATR)", 0.5, 10, 0.5],
  ["min_score", "Score minimal", 0, 100, 5], ["rebalance_days", "Revue (séances)", 1, 60, 1],
  ["max_per_sector", "Max par secteur", 1, 20, 1],
];

function Trades({ title, rows }: { title: string; rows: BtTrade[] }) {
  return (
    <div className="card scroll">
      <h3>{title}</h3>
      <table>
        <thead><tr><th>Titre</th><th>Entrée</th><th>Sortie</th><th>Séances</th><th>Motif</th><th>Résultat</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.ticker + t.entry}>
              <td>{t.ticker}</td><td>{t.entry}</td><td>{t.exit}</td><td>{t.days}</td><td>{t.reason}</td>
              <td className={t.pnl_pct >= 0 ? "pos" : "neg"}>{pct(t.pnl_pct, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Backtest() {
  const [bt, setBt] = useState<Bt | null>(null);
  const [params, setParams] = useState<BacktestParams>(DEFAULTS);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async (adopt = false) => {
    try {
      const latest = await api.get<Bt | null>("/backtests/latest");
      setBt(latest);
      if (adopt && latest) setParams({ ...DEFAULTS, ...latest.params });
      if (latest && (latest.status === "pending" || latest.status === "running"))
        timer.current = window.setTimeout(() => load(), 2000);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load(true);
    return () => window.clearTimeout(timer.current);
  }, [load]);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/backtests", params);
    } catch (err) {
      setError((err as Error).message);
    }
    load();
  };

  const running = bt?.status === "pending" || bt?.status === "running";
  const r = bt?.status === "done" ? bt.result : null;
  const names = r ? Object.keys(r.metrics) : [];
  const strat = r?.metrics["Stratégie"];
  const universe = r?.metrics["Univers équipondéré"];

  return (
    <>
      <section className="toolbar">
        <div>
          <h1>Backtest</h1>
          <p className="muted">La stratégie du score rejouée sur le passé, frais inclus, face à ses références.</p>
        </div>
        <Tuto topic="backtest" />
      </section>
      <form className="card params" onSubmit={start}>
        {FIELDS.map(([key, label, min, max, step]) => (
          <label key={key}>{label}
            <input type="number" min={min} max={max} step={step} value={params[key] as number}
                   onChange={(e) => setParams({ ...params, [key]: Number(e.target.value) })} />
          </label>
        ))}
        <label className="check">
          <input type="checkbox" checked={params.market_filter}
                 onChange={(e) => setParams({ ...params, market_filter: e.target.checked })} />
          Filtre de marché
        </label>
        <button disabled={running}>{running ? "Calcul en cours…" : "Lancer le backtest"}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {bt?.status === "failed" && <p className="error">Le backtest a échoué : {bt.error}</p>}
      {!bt && <p className="card muted">Aucun backtest pour l'instant. Le premier télécharge l'historique complet et peut prendre une à deux minutes.</p>}
      {r && strat && universe && (
        <>
          <p className={`card ${strat.cagr_pct > universe.cagr_pct ? "ok" : "warn"}`}>
            Du {r.period.start} au {r.period.end}, sur {r.period.tickers} titres : la stratégie a rapporté{" "}
            <strong>{pct(strat.cagr_pct, 2)} par an</strong>, contre {pct(universe.cagr_pct, 2)} pour l'univers
            équipondéré. {strat.cagr_pct > universe.cagr_pct
              ? "Elle fait mieux que l'achat de tout l'univers sur cette période."
              : "Elle ne fait pas mieux que l'achat de tout l'univers : sur cette période, le score n'a pas apporté d'avantage."}
          </p>
          <div className="card">
            <h3>Évolution d'un capital de 100</h3>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={r.curve}>
                <CartesianGrid stroke="#232838" vertical={false} />
                <XAxis dataKey="date" stroke="#8b93a7" fontSize={11} minTickGap={60} />
                <YAxis stroke="#8b93a7" fontSize={11} width={50} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ background: "#171b26", border: "1px solid #2a3040" }} />
                <Legend />
                {names.map((n, i) => (
                  <Line key={n} dataKey={n} stroke={COLORS[i % COLORS.length]} dot={false}
                        strokeWidth={n === "Stratégie" ? 2.5 : 1.5} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="card scroll">
            <table>
              <thead><tr><th /><th>Rendement total</th><th>Annualisé</th><th>Perte maximale</th><th>Volatilité</th><th>Sharpe</th></tr></thead>
              <tbody>
                {names.map((n) => {
                  const m = r.metrics[n];
                  return (
                    <tr key={n}>
                      <td><strong>{n}</strong></td>
                      <td className={m.total_return_pct >= 0 ? "pos" : "neg"}>{pct(m.total_return_pct)}</td>
                      <td className={m.cagr_pct >= 0 ? "pos" : "neg"}>{pct(m.cagr_pct, 2)}</td>
                      <td className="neg">−{fr(m.max_drawdown_pct)} %</td>
                      <td>{fr(m.volatility_pct)} %</td><td>{m.sharpe.toLocaleString("fr-FR")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="stats">
            <div className="card"><span className="muted small">Positions prises</span><strong>{r.trades.count}</strong>
              <span className="muted small">{r.trades.avg_days === null ? "—" : fr(r.trades.avg_days)} séances en moyenne</span></div>
            <div className="card"><span className="muted small">Positions gagnantes</span>
              <strong>{r.trades.win_rate === null ? "—" : `${Math.round(r.trades.win_rate * 100)} %`}</strong>
              <span className="muted small">résultat moyen {r.trades.avg_pnl_pct === null ? "—" : pct(r.trades.avg_pnl_pct, 2)}</span></div>
            <div className="card"><span className="muted small">Sorties au stop</span>
              <strong>{r.trades.stopped_pct === null ? "—" : fr(r.trades.stopped_pct)} %</strong></div>
            <div className="card"><span className="muted small">Taux d'investissement</span>
              <strong>{fr(r.trades.exposure_pct)} %</strong><span className="muted small">du capital, en moyenne</span></div>
          </div>
          <h2>Par année</h2>
          <div className="card scroll">
            <table>
              <thead><tr><th>Année</th>{names.map((n) => <th key={n}>{n}</th>)}</tr></thead>
              <tbody>
                {r.yearly.map((y) => (
                  <tr key={y.year}>
                    <td>{y.year}</td>
                    {names.map((n) => <td key={n} className={y[n] >= 0 ? "pos" : "neg"}>{pct(y[n])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="grid2">
            <Trades title="Meilleures positions" rows={r.trades.best} />
            <Trades title="Pires positions" rows={r.trades.worst} />
          </div>
          <h2>Limites de ce backtest</h2>
          <ul className="card muted small notes">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </>
      )}
    </>
  );
}
