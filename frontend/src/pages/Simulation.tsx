import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, eur, pct, type Portfolio, type Track, type Trade } from "../api";

export default function Simulation() {
  const [pf, setPf] = useState<Portfolio | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [track, setTrack] = useState<Track | null>(null);
  const [stocks, setStocks] = useState<{ ticker: string; name: string }[]>([]);
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [p, t, k] = await Promise.all([
        api.get<Portfolio>("/portfolio"), api.get<Trade[]>("/portfolio/trades"),
        api.get<Track>("/opportunities/track"),
      ]);
      setPf(p); setTrades(t); setTrack(k);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    api.get<{ ticker: string; name: string }[]>("/stocks").then((s) => {
      const sorted = [...s].sort((a, b) => a.name.localeCompare(b.name, "fr"));
      setStocks(sorted);
      setTicker(sorted[0]?.ticker ?? "");
    }).catch(() => undefined);
  }, [refresh]);

  const order = async (side: "buy" | "sell", t = ticker, q = quantity) => {
    setError("");
    try {
      await api.post("/portfolio/orders", { ticker: t, side, quantity: q });
    } catch (e) {
      setError((e as Error).message);
    }
    refresh();
  };

  const reset = async () => {
    const input = window.prompt("Capital fictif de départ (100 à 150 000 €) :", "10000");
    if (input === null) return;
    setError("");
    try {
      await api.post("/portfolio/reset", { initial_cash: Number(input.replace(",", ".")) });
    } catch (e) {
      setError((e as Error).message);
    }
    refresh();
  };

  if (!pf) return <p className="muted">{error || "Chargement…"}</p>;
  const sum = track?.summary;

  return (
    <>
      <section className="toolbar">
        <div>
          <h1>Simulation</h1>
          <p className="muted">Argent fictif · ordres au dernier cours de clôture · frais de {(pf.fee_rate * 100).toLocaleString("fr-FR")} % par ordre</p>
        </div>
        <button className="ghost" onClick={reset}>Réinitialiser</button>
      </section>
      {error && <p className="error">{error}</p>}

      <div className="stats">
        <div className="card"><span className="muted small">Valeur totale</span><strong>{eur(pf.total_value)}</strong>
          <span className={pf.performance_pct >= 0 ? "pos" : "neg"}>{pct(pf.performance_pct, 2)}</span></div>
        <div className="card"><span className="muted small">Liquidités</span><strong>{eur(pf.cash)}</strong>
          <span className="muted small">sur {eur(pf.initial_cash)} au départ</span></div>
        <div className="card"><span className="muted small">Investi</span><strong>{eur(pf.invested)}</strong></div>
        <div className="card"><span className="muted small">Plus-values réalisées</span>
          <strong className={pf.realized_pnl >= 0 ? "pos" : "neg"}>{eur(pf.realized_pnl)}</strong>
          <span className="muted small">dont {eur(pf.fees_paid)} de frais</span></div>
      </div>

      <form className="card order" onSubmit={(e: FormEvent) => { e.preventDefault(); order("buy"); }}>
        <label>Titre
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {stocks.map((s) => <option key={s.ticker} value={s.ticker}>{s.name} ({s.ticker})</option>)}
          </select>
        </label>
        <label>Quantité
          <input type="number" min={1} step={1} value={quantity}
                 onChange={(e) => setQuantity(Math.max(1, Math.floor(Number(e.target.value))))} />
        </label>
        <button>Acheter</button>
        <button type="button" className="ghost" onClick={() => order("sell")}>Vendre</button>
      </form>

      <h2>Positions</h2>
      <div className="card scroll">
        {pf.positions.length === 0 ? <p className="muted">Aucune position.</p> : (
          <table>
            <thead><tr><th>Titre</th><th>Qté</th><th>Prix de revient</th><th>Cours</th><th>Valeur</th><th>+/− latente</th><th /></tr></thead>
            <tbody>
              {pf.positions.map((p) => (
                <tr key={p.ticker}>
                  <td><Link to={`/titre/${p.ticker}`}>{p.name}</Link></td>
                  <td>{p.quantity}</td><td>{eur(p.avg_cost)}</td><td>{eur(p.price)}</td><td>{eur(p.value)}</td>
                  <td className={p.pnl >= 0 ? "pos" : "neg"}>{eur(p.pnl)} ({pct(p.pnl_pct, 2)})</td>
                  <td><button className="ghost" onClick={() => order("sell", p.ticker, p.quantity)}>Tout vendre</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>Pertinence des recommandations</h2>
      <p className="muted small">
        Chaque opportunité signalée par un scan est suivie jusqu'à la durée de détention estimée,
        puis comparée au gain annoncé.
      </p>
      {sum && (
        <div className="stats">
          <div className="card"><span className="muted small">Recommandations suivies</span><strong>{sum.total}</strong>
            <span className="muted small">{sum.closed} arrivées à terme</span></div>
          <div className="card"><span className="muted small">Taux de réussite</span>
            <strong>{sum.win_rate === null ? "—" : `${Math.round(sum.win_rate * 100)} %`}</strong></div>
          <div className="card"><span className="muted small">Résultat moyen / annoncé</span>
            <strong>{sum.avg_outcome_pct === null ? "—" : pct(sum.avg_outcome_pct, 2)}</strong>
            <span className="muted small">{sum.avg_expected_pct === null ? "" : `annoncé ${pct(sum.avg_expected_pct, 2)}`}</span></div>
        </div>
      )}
      <div className="card scroll">
        {!track?.items.length ? <p className="muted">Aucune recommandation à suivre : lancez un scan.</p> : (
          <table>
            <thead><tr><th>Date</th><th>Titre</th><th>Score</th><th>Entrée</th><th>Annoncé</th><th>Constaté</th><th>Avancement</th><th>Verdict</th></tr></thead>
            <tbody>
              {track.items.map((t) => (
                <tr key={t.ticker + t.date}>
                  <td>{t.date}</td><td><Link to={`/titre/${t.ticker}`}>{t.name}</Link></td>
                  <td>{t.score.toFixed(0)}</td><td>{eur(t.entry_price)}</td>
                  <td>{pct(t.expected_gain_pct)}</td>
                  <td className={t.outcome_pct >= 0 ? "pos" : "neg"}>{pct(t.outcome_pct, 2)}</td>
                  <td>{t.elapsed_days}/{t.holding_days} séances</td>
                  <td><span className={`tag ${t.verdict === "gagnante" ? "pos" : t.verdict === "en cours" ? "" : "neg"}`}>{t.verdict}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>Historique des ordres</h2>
      <div className="card scroll">
        {trades.length === 0 ? <p className="muted">Aucun ordre.</p> : (
          <table>
            <thead><tr><th>Date</th><th>Sens</th><th>Titre</th><th>Qté</th><th>Cours</th><th>Frais</th><th>+/− réalisée</th></tr></thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td>{new Date(t.executed_at).toLocaleString("fr-FR")}</td>
                  <td>{t.side === "buy" ? "Achat" : "Vente"}</td><td>{t.ticker}</td><td>{t.quantity}</td>
                  <td>{eur(t.price)}</td><td>{eur(t.fee)}</td>
                  <td className={(t.realized_pnl ?? 0) >= 0 ? "pos" : "neg"}>{t.realized_pnl === null ? "—" : eur(t.realized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
