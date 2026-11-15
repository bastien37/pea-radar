import { useCallback, useEffect, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, pct, type WalkForward as Wf } from "../api";

const fr = (n: number) => n.toLocaleString("fr-FR");
const COLORS = ["#3ecf8e", "#4da3ff", "#c678dd", "#f0616d", "#e6e9f0", "#8b93a7", "#f5a524"];

export default function WalkForward() {
  const [wf, setWf] = useState<Wf | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const latest = await api.get<Wf | null>("/backtests/walkforward/latest");
      setWf(latest);
      if (latest && (latest.status === "pending" || latest.status === "running"))
        timer.current = window.setTimeout(load, 2000);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    return () => window.clearTimeout(timer.current);
  }, [load]);

  const start = async () => {
    setError("");
    try {
      await api.post("/backtests/walkforward", { years: 15, train_years: 3 });
    } catch (e) {
      setError((e as Error).message);
    }
    load();
  };

  const running = wf?.status === "pending" || wf?.status === "running";
  const r = wf?.status === "done" ? wf.result : null;
  const names = r ? r.strategies.map((s) => s.name) : [];
  const tested = r ? r.strategies.filter((s) => !s.reference).map((s) => s.name) : [];

  return (
    <>
      <section className="toolbar">
        <div>
          <h2>Validation walk-forward</h2>
          <p className="muted">
            Quatre stratégies simples, réglées sur 3 ans puis jugées sur l'année suivante, qu'elles n'ont jamais vue.
          </p>
        </div>
        <button onClick={start} disabled={running}>{running ? "Calcul en cours…" : "Lancer la validation"}</button>
      </section>
      {error && <p className="error">{error}</p>}
      {wf?.status === "failed" && <p className="error">La validation a échoué : {wf.error}</p>}
      {!wf && <p className="card muted">Aucune validation pour l'instant.</p>}
      {r && (
        <>
          <p className={`card ${r.winners.length ? "ok" : "warn"}`}>
            <strong>
              {r.winners.length === 0 ? "Aucune stratégie ne passe le test."
                : `${r.winners.length} stratégie${r.winners.length > 1 ? "s passent" : " passe"} le test : ${r.winners.join(", ")}.`}
            </strong>{" "}
            {r.period.test_years} années de test, du {r.period.start} au {r.period.end}, sur {r.period.tickers} titres.{" "}
            {r.winners.length === 0
              ? "Sur cette période, aucune n'a fait durablement mieux que l'ETF et l'univers, frais inclus."
              : "Ce résultat reste embelli par le biais du survivant : à confirmer en simulation avant toute décision."}
          </p>
          <p className="muted small">{r.rule}</p>
          <div className="card scroll">
            <table>
              <thead><tr><th>Sur les années de test</th><th>Annualisé</th><th>Perte maximale</th><th>Sharpe</th>
                <th>Années gagnées</th><th>Rotation par an</th><th>Verdict</th></tr></thead>
              <tbody>
                {r.strategies.map((s) => (
                  <tr key={s.name}>
                    <td><strong>{s.name}</strong>{s.reference && <span className="muted small"> (référence)</span>}</td>
                    <td className={s.cagr_pct >= 0 ? "pos" : "neg"}>{pct(s.cagr_pct, 2)}</td>
                    <td className="neg">−{fr(s.max_drawdown_pct)} %</td>
                    <td>{fr(s.sharpe)}</td>
                    <td>{s.years_won === null ? "—" : `${s.years_won} / ${s.years}`}</td>
                    <td>{s.turnover_pct_per_year === null ? "—" : `${fr(s.turnover_pct_per_year)} %`}</td>
                    <td>{s.beats_reference === null ? "—"
                      : <span className={`tag ${s.beats_reference ? "pos" : "neg"}`}>{s.beats_reference ? "retenue" : "rejetée"}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Capital de 100 sur les années de test</h3>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={r.curve}>
                <CartesianGrid stroke="#232838" vertical={false} />
                <XAxis dataKey="date" stroke="#8b93a7" fontSize={11} minTickGap={60} />
                <YAxis stroke="#8b93a7" fontSize={11} width={50} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ background: "#171b26", border: "1px solid #2a3040" }} />
                <Legend />
                {names.map((n, i) => (
                  <Line key={n} dataKey={n} stroke={COLORS[i % COLORS.length]} dot={false}
                        strokeWidth={n === r.reference ? 2.5 : 1.5}
                        strokeDasharray={r.strategies[i].reference ? "5 4" : undefined} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <h3>Par année de test</h3>
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
          <details className="card">
            <summary>Réglage retenu chaque année (choisi sur les 3 années précédentes)</summary>
            <div className="scroll">
              <table>
                <thead><tr><th>Année</th>{tested.map((n) => <th key={n}>{n}</th>)}</tr></thead>
                <tbody>
                  {r.choices.map((c) => (
                    <tr key={c.year}><td>{c.year}</td>{tested.map((n) => <td key={n} className="small">{c[n]}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <ul className="card muted small notes">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </>
      )}
    </>
  );
}
