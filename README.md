# PEA Radar

Application web qui classe les actions du SBF 120 éligibles au PEA selon des indicateurs
techniques, fait analyser l'actualité des 5 meilleures par un modèle gratuit d'OpenRouter,
et permet de tester les recommandations avec un portefeuille fictif.

> Outil d'aide à la décision personnel. Les estimations sont des statistiques sur le passé,
> pas des promesses de gain, et ne constituent pas un conseil en investissement.

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) et Node.js ≥ 20.

```bash
# Back-end (http://localhost:8000, documentation OpenAPI sur /api/docs en dev)
cd backend
cp .env.example .env          # renseigner OPENROUTER_API_KEY (clé gratuite sur openrouter.ai/keys)
uv sync
uv run uvicorn app.main:app --reload

# Front-end (http://localhost:5173, /api relayé vers le back)
cd frontend
npm install
npm run dev
```

Le premier compte créé depuis l'écran de connexion est le seul autorisé
(`ALLOW_REGISTRATION=true` pour en ouvrir d'autres).
Pour essayer sans réseau : `DATA_PROVIDER=synthetic` (cours fictifs).

Production : `npm run build` puis lancer uniquement le back-end, qui sert `frontend/dist`.
Définir `ENVIRONMENT=prod`, `SECRET_KEY`, `COOKIE_SECURE=true` et placer le tout derrière HTTPS.

Tests : `cd backend && uv run pytest && uv run ruff check .`

## Architecture

```
frontend/  React + TypeScript (Vite, Recharts) — ne parle qu'à l'API REST /api
backend/   FastAPI, SQLAlchemy (SQLite par défaut), environnement uv
  app/api/        routes REST : auth, scans, stocks, portfolio
  app/analysis/   indicators.py (indicateurs), scoring.py (score, risque, estimation)
  app/market/     fournisseur de cours (yfinance ou synthétique) + cache
  app/news/       flux RSS Google Actualités + analyse OpenRouter
  app/services/   scanner.py (pipeline du scan), simulation.py (portefeuille fictif)
  app/data/sbf120.csv   univers, modifiable (colonne pea = éligibilité)
```

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/api/auth/register`, `/login`, `/logout` | session par cookie httpOnly |
| POST | `/api/scans` | lance un scan en tâche de fond |
| GET | `/api/scans/latest` | progression + dernier résultat (top 5 et classement) |
| GET | `/api/stocks/{ticker}` | séries cours / indicateurs pour les graphiques |
| GET | `/api/opportunities/track` | pertinence des recommandations passées |
| GET/POST | `/api/portfolio`, `/orders`, `/trades`, `/reset` | simulation |

## Méthode d'analyse

**Indicateurs** (calculés en pandas) : moyennes mobiles 20/50/200, RSI 14, MACD 12/26/9,
stochastique lent 14/3/3, bandes de Bollinger (%B), ATR 14, ADX 14, performance 3 et 6 mois,
ratio de volumes, volatilité annualisée 60 séances, repli maximal sur un an.

**Score 0–100** = tendance 30 % + momentum 25 % + point d'entrée 25 % + volumes 10 % + stabilité 10 %.
Il favorise les titres en tendance haussière pris sur un repli (stochastique bas qui se retourne,
RSI non suracheté). Les pondérations sont dans `scoring.py` (`WEIGHTS`).

**Gain et durée de détention** : le score est recalculé pour chaque jour de l'historique (3 ans).
On relève ce que le cours a fait 10, 20, 40, 60 et 90 séances après chaque jour où le score était
comparable (≥ score actuel − 5), sur le titre lui-même s'il y a au moins 30 cas, sinon sur tout
l'univers. Le gain affiché est la médiane, la fourchette va du 1ᵉʳ au 3ᵉ quartile, et l'horizon
retenu est celui qui maximise médiane / √durée.

**Risque 1–5** : volatilité annualisée (seuils 18 / 25 / 33 / 45 %), +1 si le repli sur un an
dépasse 35 %, ±1 selon l'analyse de l'actualité. Stop suggéré : cours − 2 × ATR.

**Actualités** : pour le top 5, les titres de presse des 21 derniers jours sont envoyés au modèle
`openrouter/free` (modifiable via `OPENROUTER_MODEL`), qui renvoie sentiment, confiance, synthèse,
catalyseurs et risques. Le score est ajusté de ±10 points au plus.

**Simulation** : capital fictif (10 000 € par défaut, plafonné à 150 000 € comme un PEA), ordres au
dernier cours de clôture, frais de 0,5 %, pas de vente à découvert. L'onglet compare chaque
recommandation passée au gain annoncé une fois la durée de détention écoulée.

## Sécurité

- Mots de passe hachés en Argon2id, 12 caractères minimum, temps de réponse constant au login.
- JWT court dans un cookie `HttpOnly`, `SameSite=Strict`, `Secure` en production ; rien dans le
  `localStorage`. La déconnexion révoque tous les jetons émis.
- Anti-CSRF : en-tête `X-Requested-With` exigé sur les requêtes modifiantes, CORS limité aux
  origines déclarées.
- Limitation des tentatives de connexion par IP et par e-mail.
- Toutes les entrées validées par Pydantic ; tickers restreints à l'univers ; requêtes SQL via l'ORM.
- En-têtes : CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS en HTTPS, `no-store` sur l'API.
- Clés (OpenRouter, signature) uniquement côté serveur, dans `.env` non versionné, jamais journalisées.
- Titres de presse traités comme non fiables : XML analysé par `defusedxml`, consigne anti-injection,
  sortie du modèle validée par un schéma strict et bornée avant d'influencer le score.
- OpenAPI/Swagger désactivés en production.

## Limites connues

- Les cours viennent de Yahoo Finance via `yfinance` (non officiel, clôtures journalières).
- La composition du SBF 120 et l'éligibilité PEA de `sbf120.csv` sont à vérifier et à tenir à jour.
- L'estimation de gain est mesurée sur le même historique que celui qui sert au classement :
  elle est optimiste par construction. L'onglet Simulation sert à la confronter à la réalité.
- Limiteur de débit et cache en mémoire : prévus pour une seule instance.
