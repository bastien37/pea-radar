import { type FormEvent, useEffect, useState } from "react";
import { api, type EmailSettings } from "../api";

function EmailAlerts() {
  const [settings, setSettings] = useState<EmailSettings | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<EmailSettings>("/alerts/email").then(setSettings).catch(() => undefined);
  }, []);

  const test = async () => {
    setBusy(true);
    setResult(null);
    try {
      const r = await api.post<{ sent_to: string }>("/alerts/email/test");
      setResult({ ok: true, text: `E-mail envoyé à ${r.sent_to}. Vérifiez votre boîte, et les indésirables.` });
    } catch (err) {
      setResult({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card account">
      <h3>Alertes par e-mail</h3>
      {settings === null ? <p className="muted">Chargement…</p> : settings.configured ? (
        <p className="muted small">
          Serveur d'envoi : {settings.host}:{settings.port}, expéditeur {settings.sender}
          {settings.authenticated ? ", avec authentification" : ", sans authentification"}.
          Les alertes des scans vous sont envoyées en plus d'être affichées dans l'application.
        </p>
      ) : (
        <p className="muted small">
          Aucun serveur d'envoi configuré : les alertes restent visibles dans l'application.
          Renseignez <code>SMTP_HOST</code>, <code>SMTP_FROM</code>, <code>SMTP_USER</code> et{" "}
          <code>SMTP_PASSWORD</code> dans <code>backend/.env</code>, puis relancez le serveur.
          Une adresse Outlook, Hotmail ou Live ne peut pas servir d'expéditeur : Microsoft n'accepte
          plus l'authentification par mot de passe pour ces comptes.
        </p>
      )}
      {result && <p className={result.ok ? "card ok" : "error"}>{result.text}</p>}
      <button type="button" className="ghost" onClick={test} disabled={busy || !settings}>
        {busy ? "Envoi…" : "Envoyer un e-mail de test"}
      </button>
    </div>
  );
}

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
      <EmailAlerts />
    </>
  );
}
