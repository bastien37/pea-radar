import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type EtfStat, eur, pct, type Plan } from "../api";
import Tuto from "../Tuto";

const fr = (n: number) => n.toLocaleString("fr-FR");
const perf = (n: number | null | undefined) =>
  n === null || n === undefined ? <td className="muted">—</td> : <td className={n >= 0 ? "pos" : "neg"}>{pct(n)}</td>;

export default function Etf() {
  const [etfs, setEtfs] = useState<EtfStat[] | null>(null);
  const [ticker, setTicker] = useState("");
  const [monthly, setMonthly] = useState(200);
  const [initial, setInitial] = useState(0);
  const [years, setYears] = useState(10);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<EtfStat[]>("/etfs").then((rows) => {
      setEtfs(rows);
      setTicker(rows.find((r) => r.available)?.ticker ?? "");
    }).catch((e: Error) => setError(e.message));
  }, []);

  const simulate = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setPlan(await api.post<Plan>("/etfs/simulate", { ticker, monthly, initial, years }));
    } catch (err) {
      setError((err as Error).message);
      setPlan(null);
    } finally {
      setBusy(false);
    }
  };

  const duration = (months: number) =>
    months % 12 === 0 ? `${months / 12} an${months >= 24 ? "s" : ""}` : `${Math.floor(months / 12)} ans et ${months % 12} mois`;

  return (
    <>
      <section className="toolbar">
        <div>
          <h1>ETF éligibles PEA</h1>
          <p className="muted">Fonds indiciels : non notés par le scan, comparés sur leur historique réel.</p>
        </div>
        <Tuto topic="etf" />
      </section>
      {error && <p className="error">{error}</p>}
      {!etfs && !error && <p className="muted">Chargement…</p>}
      {etfs && (
        <div className="card scroll">
          <table>
            <thead>
              <tr><th>ETF</th><th>Indice</th><th>Cours</th><th>Par an sur 1 an</th><th>3 ans</th><th>5 ans</th><th>10 ans</th>
                <th>Volatilité</th><th>Perte maximale</th><th>Historique depuis</th></tr>
            </thead>
            <tbody>
              {etfs.map((e) => e.available ? (
                <tr key={e.ticker}>
                  <td><Link to={`/titre/${e.ticker}`}>{e.name}</Link> <span className="muted small">{e.ticker}</span></td>
                  <td>{e.index}</td><td>{eur(e.price!)}</td>
                  {perf(e.perf_1y_pct)}{perf(e.perf_3y_pct)}{perf(e.perf_5y_pct)}{perf(e.perf_10y_pct)}
                  <td>{fr(e.volatility_pct!)} %</td><td className="neg">−{fr(e.max_drawdown_pct!)} %</td>
                  <td className="muted small">{e.since}</td>
                </tr>
              ) : (
                <tr key={e.ticker}><td>{e.name} <span className="muted small">{e.ticker}</span></td><td>{e.index}</td>
                  <td colSpan={8} className="muted">Cours indisponibles</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Simuler des versements programmés</h2>
      <p className="muted small">
        Le plan est rejoué sur l'historique réel de l'ETF : un achat le premier jour de bourse de chaque mois,
        jusqu'au dernier cours connu.
      </p>
      <form className="card params" onSubmit={simulate}>
        <label>ETF
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {etfs?.filter((e) => e.available).map((e) => <option key={e.ticker} value={e.ticker}>{e.name}</option>)}
          </select>
        </label>
        <label>Versement mensuel (€)
          <input type="number" min={10} max={12500} step={10} value={monthly}
                 onChange={(e) => setMonthly(Number(e.target.value))} />
        </label>
        <label>Versement initial (€)
          <input type="number" min={0} max={150000} step={100} value={initial}
                 onChange={(e) => setInitial(Number(e.target.value))} />
        </label>
        <label>Durée (années)
          <input type="number" min={1} max={15} step={1} value={years}
                 onChange={(e) => setYears(Number(e.target.value))} />
        </label>
        <button disabled={busy || !ticker}>{busy ? "Calcul…" : "Simuler"}</button>
      </form>

      {plan && (
        <>
          {plan.months < plan.requested_months && (
            <p className="card warn">
              Cet ETF n'a que {duration(plan.months)} d'historique exploitable : le plan est simulé sur cette
              durée, pas sur les {duration(plan.requested_months)} demandés.
            </p>
          )}
          <div className="stats">
            <div className="card"><span className="muted small">Total versé</span><strong>{eur(plan.invested)}</strong>
              <span className="muted small">sur {duration(plan.months)}, dont {eur(plan.fees)} de frais</span></div>
            <div className="card"><span className="muted small">Valeur finale</span><strong>{eur(plan.final_value)}</strong>
              <span className={plan.gain >= 0 ? "pos" : "neg"}>{eur(plan.gain)} ({pct(plan.gain_pct)})</span></div>
            <div className="card"><span className="muted small">Rendement annuel</span>
              <strong className={(plan.annual_return_pct ?? 0) >= 0 ? "pos" : "neg"}>
                {plan.annual_return_pct === null ? "—" : pct(plan.annual_return_pct, 2)}</strong>
              <span className="muted small">pondéré par les dates de versement</span></div>
            <div className="card"><span className="muted small">Net d'impôt si retrait à la fin</span>
              <strong>{eur(plan.net_value)}</strong>
              <span className="muted small">{eur(plan.tax)} prélevés ({fr(plan.tax_rate_pct)} % des gains)</span></div>
            <div className="card"><span className="muted small">Pire moment</span>
              <strong className={plan.worst_point.gap_pct >= 0 ? "pos" : "neg"}>{pct(plan.worst_point.gap_pct)}</strong>
              <span className="muted small">par rapport aux sommes versées, le {plan.worst_point.date}</span></div>
          </div>
          <div className="card">
            <h3>{plan.name} : du {plan.start} au {plan.end}</h3>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={plan.curve}>
                <CartesianGrid stroke="#232838" vertical={false} />
                <XAxis dataKey="date" stroke="#8b93a7" fontSize={11} minTickGap={60} />
                <YAxis stroke="#8b93a7" fontSize={11} width={60} />
                <Tooltip contentStyle={{ background: "#171b26", border: "1px solid #2a3040" }}
                         formatter={(v) => eur(Number(v))} />
                <Legend />
                <Area dataKey="value" name="Valeur du plan" stroke="#3ecf8e" fill="#3ecf8e" fillOpacity={0.15} strokeWidth={2} />
                <Area dataKey="invested" name="Sommes versées" stroke="#8b93a7" fill="#8b93a7" fillOpacity={0.1} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          {plan.windows ? (
            <div className="card">
              <h3>Et en démarrant à un autre moment ?</h3>
              <p className="muted small">
                Le même plan de {duration(plan.months)}, démarré à chacun des {plan.windows.count} mois possibles
                de l'historique. Gain ou perte à l'arrivée, par rapport aux sommes versées :
              </p>
              <div className="stats">
                <div><span className="muted small">Pire départ</span>
                  <strong className={plan.windows.worst_pct >= 0 ? "pos" : "neg"}>{pct(plan.windows.worst_pct)}</strong></div>
                <div><span className="muted small">Départ médian</span>
                  <strong className={plan.windows.median_pct >= 0 ? "pos" : "neg"}>{pct(plan.windows.median_pct)}</strong></div>
                <div><span className="muted small">Meilleur départ</span>
                  <strong className="pos">{pct(plan.windows.best_pct)}</strong></div>
                <div><span className="muted small">Plans terminés en perte</span>
                  <strong>{fr(Math.round(plan.windows.loss_share * 100))} %</strong></div>
              </div>
            </div>
          ) : (
            <p className="card muted small">
              Historique trop court pour rejouer ce plan à d'autres dates de départ : le résultat ci-dessus
              repose sur une seule période.
            </p>
          )}
          <p className="muted small">
            Ce calcul décrit le passé de cet ETF. Il ne prédit pas son rendement futur et ne constitue pas un
            conseil en investissement.
          </p>
        </>
      )}
    </>
  );
}
