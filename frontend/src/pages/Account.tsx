import { type FormEvent, useState } from "react";
import { api } from "../api";

export default function Account() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== confirm) {
      setMessage({ ok: false, text: "Les deux saisies du nouveau mot de passe diffèrent." });
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/password", { current_password: current, new_password: next });
      setMessage({ ok: true, text: "Mot de passe modifié. Les autres sessions ont été déconnectées." });
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Compte</h1>
      <form className="card account" onSubmit={submit}>
        <h3>Changer de mot de passe</h3>
        <label>Mot de passe actuel
          <input type="password" autoComplete="current-password" required value={current}
                 onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>Nouveau mot de passe <span className="muted">(12 caractères min.)</span>
          <input type="password" autoComplete="new-password" required minLength={12} maxLength={128}
                 value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>Confirmer le nouveau mot de passe
          <input type="password" autoComplete="new-password" required minLength={12} maxLength={128}
                 value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </label>
        {message && <p className={message.ok ? "card ok" : "error"}>{message.text}</p>}
        <button disabled={busy}>Enregistrer</button>
      </form>
    </>
  );
}
