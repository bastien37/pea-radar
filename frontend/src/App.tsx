import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type User } from "./api";
import Account from "./pages/Account";
import Backtest from "./pages/Backtest";
import Dashboard from "./pages/Dashboard";
import Etf from "./pages/Etf";
import Login from "./pages/Login";
import Simulation from "./pages/Simulation";
import Stock from "./pages/Stock";

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    api.get<User>("/auth/me").then(setUser).catch(() => setUser(null));
  }, []);

  if (user === undefined) return <p className="center muted">Chargement…</p>;
  if (user === null) return <Login onAuth={setUser} />;

  const logout = () => api.post("/auth/logout").finally(() => setUser(null));

  return (
    <>
      <header className="topbar">
        <span className="brand">PEA Radar</span>
        <nav>
          <NavLink to="/" end>Actions</NavLink>
          <NavLink to="/etf">ETF</NavLink>
          <NavLink to="/simulation">Simulation</NavLink>
          <NavLink to="/backtest">Backtest</NavLink>
        </nav>
        <NavLink to="/compte" className="muted small">{user.email}</NavLink>
        <button className="ghost" onClick={logout}>Déconnexion</button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/titre/:ticker" element={<Stock />} />
          <Route path="/etf" element={<Etf />} />
          <Route path="/simulation" element={<Simulation />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/compte" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <footer className="muted small">
        Outil d'aide à la décision : les estimations sont des statistiques sur le passé, pas des
        promesses de gain, et ne constituent pas un conseil en investissement.
      </footer>
    </>
  );
}
