import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type SeriesPoint } from "../api";

const AXIS = { stroke: "#8b93a7", fontSize: 11 };
const TIP = { contentStyle: { background: "#171b26", border: "1px solid #2a3040" } };

function Panel({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <ResponsiveContainer width="100%" height={220}>{children}</ResponsiveContainer>
    </div>
  );
}

export default function Stock() {
  const { ticker = "" } = useParams();
  const [data, setData] = useState<{ name: string; series: SeriesPoint[] } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ name: string; series: SeriesPoint[] }>(`/stocks/${encodeURIComponent(ticker)}?days=250`)
      .then(setData).catch((e: Error) => setError(e.message));
  }, [ticker]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Chargement…</p>;
  const s = data.series;
  const x = <XAxis dataKey="date" {...AXIS} minTickGap={50} />;
  const grid = <CartesianGrid stroke="#232838" vertical={false} />;

  return (
    <>
      <p><Link to="/">← Opportunités</Link></p>
      <h1>{data.name} <span className="muted small">{ticker}</span></h1>
      <Panel title="Cours et moyennes mobiles (20, 50, 200 séances)">
        <LineChart data={s}>
          {grid}{x}<YAxis {...AXIS} domain={["auto", "auto"]} width={50} /><Tooltip {...TIP} />
          <Line dataKey="close" name="Cours" stroke="#e6e9f0" dot={false} strokeWidth={2} />
          <Line dataKey="sma20" name="MM20" stroke="#4da3ff" dot={false} />
          <Line dataKey="sma50" name="MM50" stroke="#f5a524" dot={false} />
          <Line dataKey="sma200" name="MM200" stroke="#c678dd" dot={false} />
        </LineChart>
      </Panel>
      <Panel title="Score composite (0–100)">
        <LineChart data={s}>
          {grid}{x}<YAxis {...AXIS} domain={[0, 100]} width={50} /><Tooltip {...TIP} />
          <Line dataKey="score" name="Score" stroke="#3ecf8e" dot={false} strokeWidth={2} />
        </LineChart>
      </Panel>
      <Panel title="Stochastique lent (14, 3, 3)">
        <LineChart data={s}>
          {grid}{x}<YAxis {...AXIS} domain={[0, 100]} width={50} /><Tooltip {...TIP} />
          <ReferenceLine y={80} stroke="#f0616d" strokeDasharray="4 4" />
          <ReferenceLine y={20} stroke="#3ecf8e" strokeDasharray="4 4" />
          <Line dataKey="stoch_k" name="%K" stroke="#4da3ff" dot={false} />
          <Line dataKey="stoch_d" name="%D" stroke="#f5a524" dot={false} />
        </LineChart>
      </Panel>
      <Panel title="RSI (14)">
        <LineChart data={s}>
          {grid}{x}<YAxis {...AXIS} domain={[0, 100]} width={50} /><Tooltip {...TIP} />
          <ReferenceLine y={70} stroke="#f0616d" strokeDasharray="4 4" />
          <ReferenceLine y={30} stroke="#3ecf8e" strokeDasharray="4 4" />
          <Line dataKey="rsi" name="RSI" stroke="#c678dd" dot={false} />
        </LineChart>
      </Panel>
      <Panel title="Histogramme MACD (12, 26, 9)">
        <BarChart data={s}>
          {grid}{x}<YAxis {...AXIS} width={50} /><Tooltip {...TIP} />
          <ReferenceLine y={0} stroke="#8b93a7" />
          <Bar dataKey="macd_hist" name="MACD − signal" fill="#4da3ff" />
        </BarChart>
      </Panel>
    </>
  );
}
