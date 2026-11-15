import { type FormEvent, useEffect, useState } from "react";
import { api, type User } from "../api";

export default function Login({ onAuth }: { onAuth: (u: User) => void }) {
  const [canRegister, setCanRegister] = useState(false);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ registration_open: boolean }>("/auth/status")
      .then((s) => setCanRegister(s.registration_open))
      .catch(() => undefined);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onAuth(await api.post<User>(`/auth/${mode}`, { email, password }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card login" onSubmit={submit}>
      <h1>PEA Radar</h1>
      <p className="muted">{mode === "login" ? "Connexion" : "Création du compte"}</p>
      <label>E-mail
        <input type="email" autoComplete="username" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label>Mot de passe {mode === "register" && <span className="muted">(12 caractères min.)</span>}
        <input type="password" required minLength={mode === "register" ? 12 : 1} maxLength={128}
               autoComplete={mode === "login" ? "current-password" : "new-password"}
               value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy}>{mode === "login" ? "Se connecter" : "Créer le compte"}</button>
      {canRegister && (
        <button type="button" className="ghost"
                onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "Créer un compte" : "J'ai déjà un compte"}
        </button>
      )}
    </form>
  );
}
