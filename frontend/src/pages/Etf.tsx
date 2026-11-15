import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { type Allocation, api, type EtfStat, eur, type Fees, type Goal, pct, type Plan } from "../api";
import Tuto from "../Tuto";

const fr = (n: number) => n.toLocaleString("fr-FR");
const AXIS = { stroke: "#8b93a7", fontSize: 11 };
const TIP = { contentStyle: { background: "#171b26", border: "1px solid #2a3040" } };
const FEE_PRESETS: Record<string, { label: string; fees: Fees | null }> = {
  server: { label: "Réglage du serveur", fees: null },
  pct05: { label: "0,5 % par ordre", fees: { rate: 0.005, minimum: 0, fixed: 0 } },
  pct01: { label: "0,1 % par ordre", fees: { rate: 0.001, minimum: 0, fixed: 0 } },
  min1: { label: "0,1 %, minimum 1 €", fees: { rate: 0.001, minimum: 1, fixed: 0 } },
  flat2: { label: "2 € par ordre", fees: { rate: 0, minimum: 0, fixed: 2 } },
  none: { label: "Sans frais", fees: { rate: 0, minimum: 0, fixed: 0 } },
};

const perf = (n: number | null | undefined) =>
  n === null || n === undefined ? <td className="muted">—</td> : <td className={n >= 0 ? "pos" : "neg"}>{pct(n)}</td>;
const duration = (months: number) =>
  months % 12 === 0 ? `${months / 12} an${months >= 24 ? "s" : ""}` : `${Math.floor(months / 12)} ans et ${months % 12} mois`;

function StatsTable({ rows, long }: { rows: EtfStat[]; long: boolean }) {
  return (
    <div className="card scroll">
      <table>
        <thead>
          <tr><th>{long ? "Indice" : "ETF"}</th><th>{long ? "Devise" : "Indice"}</th><th>Cours</th>
            <th>Par an sur 1 an</th><th>3 ans</th><th>5 ans</th><th>10 ans</th>{long && <th>20 ans</th>}
            <th>Volatilité</th><th>Perte maximale</th><th>Historique depuis</th></tr>
        </thead>
        <tbody>
          {rows.map((e) => e.available ? (
            <tr key={e.ticker}>
              <td>{long ? e.name : <Link to={`/titre/${e.ticker}`}>{e.name}</Link>} <span className="muted small">{e.ticker}</span></td>
              <td>{e.index}</td><td>{long ? fr(e.price!) : eur(e.price!)}</td>
              {perf(e.perf_1y_pct)}{perf(e.perf_3y_pct)}{perf(e.perf_5y_pct)}{perf(e.perf_10y_pct)}
              {long && perf(e.perf_20y_pct)}
              <td>{fr(e.volatility_pct!)} %</td><td className="neg">−{fr(e.max_drawdown_pct!)} %</td>
              <td className="muted small">{e.since}</td>
            </tr>
          ) : (
            <tr key={e.ticker}><td>{e.name} <span className="muted small">{e.ticker}</span></td><td>{e.index}</td>
              <td colSpan={long ? 9 : 8} className="muted">Cours indisponibles</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Etf() {
  const [items, setItems] = useState<EtfStat[] | null>(null);
  const [allocs, setAllocs] = useState<Allocation[]>([]);
  const [rebalance, setRebalance] = useState(true);
  const [feeKey, setFeeKey] = useState("server");
  const [monthly, setMonthly] = useState(200);
  const [initial, setInitial] = useState(0);
  const [years, setYears] = useState(10);
  const [target, setTarget] = useState(100000);
  const [inflation, setInflation] = useState(2);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [goal, setGoal] = useState<Goal | null>(null);
  const [error, setError] = useState("");
  const [goalError, setGoalError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<EtfStat[]>("/etfs").then((rows) => {
      setItems(rows);
      const first = rows.find((r) => r.available && r.kind === "etf") ?? rows.find((r) => r.available);
      if (first) setAllocs([{ ticker: first.ticker, weight: 100 }]);
    }).catch((e: Error) => setError(e.message));
  }, []);

  const usable = items?.filter((e) => e.available) ?? [];
  const etfs = items?.filter((e) => e.kind === "etf") ?? [];
  const indices = items?.filter((e) => e.kind === "indice") ?? [];
  const total = allocs.reduce((s, a) => s + a.weight, 0);
  const support = { allocations: allocs, rebalance, fees: FEE_PRESETS[feeKey].fees ?? undefined };

  const setAlloc = (i: number, patch: Partial<Allocation>) =>
    setAllocs(allocs.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  const addAlloc = () => {
    const free = usable.find((e) => !allocs.some((a) => a.ticker === e.ticker));
    if (!free) return;
    const share = Math.round(100 / (allocs.length + 1));
    setAllocs([...allocs.map((a) => ({ ...a, weight: share })), { ticker: free.ticker, weight: 100 - share * allocs.length }]);
  };
  const removeAlloc = (i: number) => {
    const rest = allocs.filter((_, j) => j !== i);
    const share = Math.round(100 / rest.length);
    setAllocs(rest.map((a, j) => ({ ...a, weight: j === rest.length - 1 ? 100 - share * (rest.length - 1) : share })));
  };

  const call = async (run: () => Promise<void>, report: (message: string) => void) => {
    setBusy(true);
    report("");
    try {
      await run();
    } catch (err) {
      report((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const simulate = (e: FormEvent) => {
    e.preventDefault();
    call(async () => setPlan(await api.post<Plan>("/etfs/simulate", { ...support, monthly, initial, years })),
      setError);
  };
  const solve = () =>
    call(async () => setGoal(await api.post<Goal>("/etfs/goal",
      { ...support, target, initial, years: Math.max(5, years), inflation_pct: inflation })),
    (message) => { setGoalError(message); if (message) setGoal(null); });

  return (
    <>
      <section className="toolbar">
        <div>
          <h1>ETF éligibles PEA</h1>
          <p className="muted">Fonds indiciels : non notés par le scan, comparés sur leur historique réel.</p>
        </div>
        <Tuto topic="etf" />
      </section>
      {!items && !error && <p className="muted">Chargement…</p>}
      {items && (
        <>
          <StatsTable rows={etfs} long={false} />
          <h2>Indices de référence, sur longue durée</h2>
          <p className="muted small">
            Non achetables et hors dividendes, mais leur historique couvre les crises de 2000 et de 2008 :
            utiles pour éprouver un plan. Les indices américains sont exprimés en dollars.
          </p>
          <StatsTable rows={indices} long />
        </>
      )}

      <h2>Simuler des versements programmés</h2>
      <p className="muted small">
        Le plan est rejoué sur l'historique réel : un achat le premier jour de bourse de chaque mois, jusqu'au
        dernier cours connu.
      </p>
      <form className="card plan" onSubmit={simulate}>
        <fieldset>
          <legend>Supports</legend>
          {allocs.map((a, i) => (
            <div className="alloc" key={i}>
              <select value={a.ticker} aria-label="Support" onChange={(e) => setAlloc(i, { ticker: e.target.value })}>
                <optgroup label="ETF">
                  {usable.filter((e) => e.kind === "etf").map((e) => <option key={e.ticker} value={e.ticker}>{e.name}</option>)}
                </optgroup>
                <optgroup label="Indices (longue durée, hors dividendes)">
                  {usable.filter((e) => e.kind === "indice").map((e) => <option key={e.ticker} value={e.ticker}>{e.name}</option>)}
                </optgroup>
              </select>
              <input type="number" min={1} max={100} step={1} value={a.weight} aria-label="Pourcentage"
                     onChange={(e) => setAlloc(i, { weight: Number(e.target.value) })} />
              <span className="muted">%</span>
              {allocs.length > 1 && <button type="button" className="ghost" onClick={() => removeAlloc(i)} aria-label="Retirer ce support">✕</button>}
            </div>
          ))}
          <div className="alloc">
            {allocs.length < 4 && <button type="button" className="ghost" onClick={addAlloc}>+ Ajouter un support</button>}
            {allocs.length > 1 && (
              <label className="check">
                <input type="checkbox" checked={rebalance} onChange={(e) => setRebalance(e.target.checked)} />
                Rééquilibrage annuel
              </label>
            )}
            {total !== 100 && <span className="neg small">Total : {fr(total)} % (attendu : 100 %)</span>}
          </div>
        </fieldset>
        <div className="params">
          <label>Versement mensuel (€)
            <input type="number" min={10} max={12500} step={10} value={monthly} onChange={(e) => setMonthly(Number(e.target.value))} />
          </label>
          <label>Versement initial (€)
            <input type="number" min={0} max={150000} step={100} value={initial} onChange={(e) => setInitial(Number(e.target.value))} />
          </label>
          <label>Durée (années)
            <input type="number" min={1} max={30} step={1} value={years} onChange={(e) => setYears(Number(e.target.value))} />
          </label>
          <label>Frais par ordre
            <select value={feeKey} onChange={(e) => setFeeKey(e.target.value)}>
              {Object.entries(FEE_PRESETS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </label>
          <button disabled={busy || total !== 100 || !allocs.length}>{busy ? "Calcul…" : "Simuler"}</button>
        </div>
      </form>
      {error && <p className="error">{error}</p>}

      {plan && (
        <>
          {plan.months < plan.requested_months && (
            <p className="card warn">
              Ces supports n'ont que {duration(plan.months)} d'historique commun exploitable : le plan est simulé
              sur cette durée, pas sur les {duration(plan.requested_months)} demandés. Choisissez un indice pour
              tester plus long.
            </p>
          )}
          <div className="stats">
            <div className="card"><span className="muted small">Total versé</span><strong>{eur(plan.invested)}</strong>
              <span className="muted small">sur {duration(plan.months)}, dont {eur(plan.fees)} de frais ({plan.fee_label})</span></div>
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
            <h3>{plan.name} : du {plan.start} au {plan.end}{plan.rebalanced && " · rééquilibré chaque année"}</h3>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={plan.curve}>
                <CartesianGrid stroke="#232838" vertical={false} />
                <XAxis dataKey="date" {...AXIS} minTickGap={60} /><YAxis {...AXIS} width={60} />
                <Tooltip {...TIP} formatter={(v) => eur(Number(v))} /><Legend />
                <Area dataKey="value" name="Valeur du plan" stroke="#3ecf8e" fill="#3ecf8e" fillOpacity={0.15} strokeWidth={2} />
                <Area dataKey="invested" name="Sommes versées" stroke="#8b93a7" fill="#8b93a7" fillOpacity={0.1} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          {plan.windows ? (
            <div className="card">
              <h3>Résultat selon la date de départ</h3>
              <p className="muted small">
                Le même plan de {duration(plan.months)}, démarré à chacun des {plan.windows.count} mois possibles
                de l'historique. Gain ou perte à l'arrivée, par rapport aux sommes versées :
              </p>
              <div className="stats">
                <div><span className="muted small">Pire départ ({plan.windows.worst_start})</span>
                  <strong className={plan.windows.worst_pct >= 0 ? "pos" : "neg"}>{pct(plan.windows.worst_pct)}</strong></div>
                <div><span className="muted small">Départ médian</span>
                  <strong className={plan.windows.median_pct >= 0 ? "pos" : "neg"}>{pct(plan.windows.median_pct)}</strong></div>
                <div><span className="muted small">Meilleur départ ({plan.windows.best_start})</span>
                  <strong className="pos">{pct(plan.windows.best_pct)}</strong></div>
                <div><span className="muted small">Plans terminés en perte</span>
                  <strong className={plan.windows.loss_share > 0 ? "neg" : ""}>{fr(Math.round(plan.windows.loss_share * 100))} %</strong></div>
              </div>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={plan.windows.series}>
                  <CartesianGrid stroke="#232838" vertical={false} />
                  <XAxis dataKey="start" {...AXIS} minTickGap={60} /><YAxis {...AXIS} width={50} unit=" %" />
                  <Tooltip {...TIP} formatter={(v) => pct(Number(v))} labelFormatter={(l) => `Départ en ${l}`} />
                  <ReferenceLine y={0} stroke="#f0616d" strokeDasharray="4 4" />
                  <Line dataKey="pct" name="Gain à l'arrivée" stroke="#4da3ff" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="card muted small">
              Historique trop court pour rejouer ce plan à d'autres dates de départ : le résultat ci-dessus
              repose sur une seule période.
            </p>
          )}
        </>
      )}

      <h2>Mode objectif : combien verser pour atteindre une somme ?</h2>
      <p className="muted small">
        Utilise les supports, le versement initial, la durée (5 ans au moins) et les frais choisis ci-dessus.
        La somme visée est nette d'impôt et exprimée en euros d'aujourd'hui.
      </p>
      <div className="card params">
        <label>Somme visée (€)
          <input type="number" min={1000} max={5000000} step={1000} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
        </label>
        <label>Inflation annuelle (%)
          <input type="number" min={0} max={10} step={0.5} value={inflation} onChange={(e) => setInflation(Number(e.target.value))} />
        </label>
        <button type="button" onClick={solve} disabled={busy || total !== 100 || !allocs.length}>Calculer le versement</button>
      </div>
      {goalError && <p className="error">{goalError}</p>}
      {goal && (
        <>
          <div className="stats">
            {goal.scenarios.map((s) => (
              <div className="card" key={s.label}>
                <span className="muted small">{s.label}</span>
                <strong>{eur(s.monthly)} par mois</strong>
                <span className="muted small">{s.note} · {eur(s.invested)} versés au total</span>
              </div>
            ))}
          </div>
          <p className="muted small">
            {goal.name}, sur {goal.years} ans. Pour garder le pouvoir d'achat de {eur(goal.target)} avec{" "}
            {fr(goal.inflation_pct)} % d'inflation par an, il faudra {eur(goal.target_nominal)} à l'échéance, après{" "}
            {fr(goal.tax_rate_pct)} % de prélèvements sur les gains. Calcul sur {goal.windows} périodes passées
            depuis {goal.history_start}, frais : {goal.fee_label}. Dans la pire période de l'historique, il aurait
            fallu {eur(goal.worst_case_monthly)} par mois.
          </p>
        </>
      )}
      <p className="muted small">
        Ces calculs décrivent le passé. Ils ne prédisent pas les rendements futurs et ne constituent pas un
        conseil en investissement.
      </p>
    </>
  );
}
