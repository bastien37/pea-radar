import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, eur, type Latest, type News, type Opportunity, pct, weeks } from "../api";

const SUB_LABELS: Record<string, string> = {
  trend: "Tendance", momentum: "Momentum", timing: "Point d'entrée",
  volume: "Volumes", stability: "Stabilité",
};

export function Risk({ level, label }: { level: number; label?: string }) {
  return (
    <span className={`risk risk-${level}`} title={`Risque ${level}/5`}>
      {"●".repeat(level)}{"○".repeat(5 - level)} {label}
    </span>
  );
}

function NewsBlock({ news }: { news: News }) {
  if (news.status === "no_key")
    return <p className="muted small">Analyse IA désactivée : renseignez OPENROUTER_API_KEY côté serveur.</p>;
  if (news.status === "no_news") return <p className="muted small">Aucune actualité récente trouvée.</p>;
  if (news.status === "error")
    return <p className="muted small">Analyse IA indisponible pour ce titre (quota ou réponse invalide).</p>;
  const s = news.sentiment ?? 0;
  return (
    <div className="news">
      <p>
        <strong className={s > 0.15 ? "pos" : s < -0.15 ? "neg" : ""}>
          Actualité {s > 0.15 ? "favorable" : s < -0.15 ? "défavorable" : "neutre"}
        </strong>{" "}
        <span className="muted small">
          (sentiment {s.toFixed(2)}, confiance {Math.round((news.confidence ?? 0) * 100)} %)
        </span>
      </p>
      <p>{news.summary}</p>
      {!!news.catalysts?.length && <p className="small pos">+ {news.catalysts.join(" · ")}</p>}
      {!!news.risks?.length && <p className="small neg">− {news.risks.join(" · ")}</p>}
      <details>
        <summary className="small muted">{news.headlines.length} titres analysés par {news.model}</summary>
        <ul className="small">
          {news.headlines.map((h) => (
            <li key={h.link}>
              <a href={h.link} target="_blank" rel="noopener noreferrer">{h.title}</a>{" "}
              <span className="muted">{h.published}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function OpportunityCard({ o }: { o: Opportunity }) {
  const est = o.details.estimate;
  return (
    <article className="card opp">
      <header>
        <span className="rank">{o.rank}</span>
        <div>
          <h3><Link to={`/titre/${o.ticker}`}>{o.name}</Link></h3>
          <span className="muted small">{o.ticker} · {eur(o.price)} au {o.price_date}</span>
        </div>
        <div className="score" title="Score technique ajusté de l'actualité">
          {o.adjusted_score.toFixed(0)}<span>/100</span>
        </div>
      </header>
      <div className="kpis">
        <div><span className="muted small">Risque</span><Risk level={o.risk_level} label={o.risk_label} /></div>
        <div>
          <span className="muted small">Gain estimé (médiane)</span>
          <strong className={o.expected_gain_pct >= 0 ? "pos" : "neg"}>{pct(o.expected_gain_pct)}</strong>
          <span className="muted small">de {pct(o.gain_low_pct)} à {pct(o.gain_high_pct)}</span>
        </div>
        <div>
          <span className="muted small">Durée de détention</span>
          <strong>{o.holding_days} séances</strong>
          <span className="muted small">{weeks(o.holding_days)}</span>
        </div>
        <div>
          <span className="muted small">Cas gagnants</span>
          <strong>{Math.round(o.win_rate * 100)} %</strong>
          <span className="muted small">stop suggéré {eur(o.stop_loss)}</span>
        </div>
      </div>
      <div className="bars">
        {Object.entries(o.details.subscores).map(([k, v]) => (
          <div key={k} title={`${SUB_LABELS[k]} : ${v}/100`}>
            <span className="small muted">{SUB_LABELS[k]}</span>
            <div className="bar"><i style={{ width: `${v}%` }} /></div>
          </div>
        ))}
      </div>
      <p className="muted small">
        {est.basis === "volatilité"
          ? "Historique insuffisant : estimation fondée sur la seule volatilité du titre."
          : `Estimation tirée de ${est.samples} configurations passées comparables (${
              est.basis === "titre" ? "sur ce titre" : "sur l'ensemble de l'univers"}).`}
      </p>
      <NewsBlock news={o.news} />
    </article>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<Latest | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const latest = await api.get<Latest>("/scans/latest");
      setData(latest);
      const st = latest.current?.status;
      if (st === "pending" || st === "running") timer.current = window.setTimeout(load, 2000);
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
      await api.post("/scans");
    } catch (e) {
      setError((e as Error).message);
    }
    load();
  };

  const cur = data?.current;
  const running = cur?.status === "pending" || cur?.status === "running";
  const result = data?.result;

  return (
    <>
      <section className="toolbar">
        <div>
          <h1>Opportunités PEA</h1>
          <p className="muted">
            SBF 120 éligible PEA
            {result?.finished_at && ` · dernier scan le ${new Date(result.finished_at).toLocaleString("fr-FR")}`}
          </p>
        </div>
        <button onClick={start} disabled={running}>
          {running ? cur?.progress || "Scan en cours…" : "Lancer un scan"}
        </button>
      </section>
      {error && <p className="error">{error}</p>}
      {cur?.status === "failed" && <p className="error">Le dernier scan a échoué : {cur.error}</p>}
      {!result && !running && <p className="card muted">Aucun scan pour l'instant. Lancez-en un pour classer l'univers.</p>}
      {result && (
        <>
          <div className="grid">{result.opportunities.map((o) => <OpportunityCard key={o.id} o={o} />)}</div>
          <h2>Classement complet</h2>
          <div className="card scroll">
            <table>
              <thead>
                <tr><th>#</th><th>Titre</th><th>Cours</th><th>Score</th><th>Risque</th>
                  <th>RSI</th><th>Stoch. %K</th><th>Perf. 3 mois</th><th>Volatilité</th></tr>
              </thead>
              <tbody>
                {result.ranking.map((r) => (
                  <tr key={r.ticker}>
                    <td>{r.rank}</td>
                    <td><Link to={`/titre/${r.ticker}`}>{r.name}</Link> <span className="muted small">{r.ticker}</span></td>
                    <td>{eur(r.price)}</td>
                    <td><strong>{r.score.toFixed(1)}</strong></td>
                    <td><Risk level={r.risk_level} /></td>
                    <td>{r.rsi}</td>
                    <td>{r.stoch_k}</td>
                    <td className={r.perf_3m_pct >= 0 ? "pos" : "neg"}>{pct(r.perf_3m_pct)}</td>
                    <td>{r.volatility_pct} %</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
