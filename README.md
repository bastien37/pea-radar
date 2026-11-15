# PEA Radar

Application web qui classe les actions du SBF 120 éligibles au PEA (indicateurs techniques,
fondamentaux, actualité analysée par un modèle gratuit d'OpenRouter), teste la stratégie sur le
passé et la suit dans un portefeuille fictif.

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

Relancer `npm install` après chaque `git pull` qui ajoute une dépendance, sinon le serveur de
développement refuse de démarrer.

Sous Windows PowerShell : une commande par ligne (`&&` n'existe pas en 5.1), `Copy-Item` à la
place de `cp`, et `npm.cmd` si la stratégie d'exécution bloque `npm`.

Le premier compte créé depuis l'écran de connexion est le seul autorisé
(`ALLOW_REGISTRATION=true` pour en ouvrir d'autres).
Pour essayer sans réseau : `DATA_PROVIDER=synthetic` (cours fictifs).

Le schéma de la base est créé et mis à jour automatiquement au démarrage (migrations Alembic).
Le premier scan télécharge 15 ans de cours et prend quelques minutes ; les suivants relisent la base.

Production : `npm run build` puis lancer uniquement le back-end, qui sert `frontend/dist`.
Définir `ENVIRONMENT=prod`, `SECRET_KEY`, `COOKIE_SECURE=true` et placer le tout derrière HTTPS.

Docker : créer un `.env` à la racine (`SECRET_KEY`, `OPENROUTER_API_KEY`), puis
`docker compose up -d --build` et ouvrir http://localhost:8000. Voir `docker-compose.yml` pour
l'option PostgreSQL.

En développement, si `SECRET_KEY` est vide, une clé est créée une fois dans `backend/.secret_key`
(non versionné) : les sessions survivent aux redémarrages du serveur.

Tests : `cd backend && uv run pytest && uv run ruff check .` et `cd frontend && npm test`.
GitHub Actions lance les deux, compile le front, construit l'image Docker et vérifie qu'elle démarre.

## Architecture

```
frontend/  React + TypeScript (Vite, Recharts) — ne parle qu'à l'API REST /api
backend/   FastAPI, SQLAlchemy, Alembic, environnement uv
  app/api/        routes REST : auth, scans, stocks, portfolio, backtests, alerts
  app/analysis/   indicators.py, scoring.py (score, risque, estimation), backtest.py
  app/market/     provider.py (sources de cours, repli, stockage), fundamentals.py
  app/news/       flux RSS Google Actualités + analyse OpenRouter
  app/services/   scanner, simulation, alerts (alertes + suivi auto), scheduler, backtester
  app/data/       sbf120.csv (univers, secteurs, éligibilité), etfs.csv (ETF éligibles)
  migrations/     migrations Alembic
```

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/api/auth/register`, `/login`, `/logout` | session par cookie httpOnly |
| POST | `/api/scans` | lance un scan en tâche de fond |
| GET | `/api/scans/latest` | progression + dernier résultat (top, classement, marché, ETF) |
| GET | `/api/stocks/{ticker}` | séries cours / indicateurs pour les graphiques |
| GET | `/api/opportunities/track` | pertinence des recommandations passées |
| GET/POST | `/api/portfolio`, `/orders`, `/trades`, `/reset`, `/settings` | simulation |
| POST/GET | `/api/backtests`, `/api/backtests/latest` | backtest |
| POST/GET | `/api/backtests/walkforward`, `/walkforward/latest` | validation walk-forward |
| GET/POST | `/api/etfs`, `/api/etfs/simulate`, `/api/etfs/goal` | ETF et indices, plans, mode objectif |
| POST | `/api/auth/password` | changement de mot de passe (révoque les autres sessions) |
| GET/POST | `/api/alerts/email`, `/api/alerts/email/test` | configuration d'envoi, e-mail de test |
| GET/POST | `/api/alerts`, `/api/alerts/read` | alertes |

## Méthode d'analyse

**Indicateurs** (calculés en pandas) : moyennes mobiles 20/50/200, RSI 14, MACD 12/26/9,
stochastique lent 14/3/3, bandes de Bollinger (%B), ATR 14, ADX 14, performance 3 et 6 mois,
ratio de volumes, volatilité annualisée 60 séances, repli maximal sur un an.

**Score technique 0–100** = tendance 30 % + momentum 25 % + point d'entrée 25 % + volumes 10 % +
stabilité 10 %. Il favorise les titres en tendance haussière pris sur un repli. Pondérations
dans `scoring.py` (`WEIGHTS`).

**Fondamentaux** : pour les 40 meilleurs scores techniques, PER, croissance des bénéfices,
dette / fonds propres et ROE donnent un score fondamental 0–100. Le score global vaut
`technique + 0,2 × (fondamental − 50)`, soit ±10 points ; des données absentes ne changent rien.

**Diversification** : le top 20 (`TOP_N`) est plafonné à 4 titres par secteur (`MAX_PER_SECTOR`).

**Filtre de marché** : quand le CAC 40 est sous sa moyenne 200 séances, un bandeau le signale,
le risque de chaque titre monte d'un cran et le suivi automatique n'achète plus.

**Dates de résultats** : une publication dans les 10 jours est signalée sur la carte, et le
suivi automatique n'achète pas le titre.

**Évolution passée et horizon observé** (affichés sur les cartes, sans valeur de prévision : voir
le résultat du backtest) : le score est recalculé pour chaque jour de l'historique. On
relève ce que le cours a fait 10, 20, 40, 60 et 90 séances après chaque jour où le score était
comparable (≥ score actuel − 5), sur le titre lui-même s'il y a au moins 30 cas, sinon sur tout
l'univers. Gain affiché = médiane ; fourchette = 1ᵉʳ au 3ᵉ quartile ; horizon retenu = celui qui
maximise médiane / √durée.

**Risque 1–5** : volatilité annualisée (seuils 18 / 25 / 33 / 45 %), +1 si le repli sur un an
dépasse 35 %, ±1 selon l'actualité, +1 en marché défavorable. Stop suggéré : cours − 2 × ATR.

**Actualités** : pour le top, les titres de presse des 21 derniers jours sont envoyés au modèle
`openrouter/free` (`OPENROUTER_MODEL`), qui renvoie sentiment, confiance, synthèse, catalyseurs et
risques. Le score est ajusté de ±10 points au plus.

**ETF** : huit ETF éligibles PEA (`etfs.csv`) ont leur propre écran, séparé des actions ; ils
ne sont pas notés par le scan (voir « Écran ETF »).

## Écran ETF

- **Tableaux comparatifs** : rendement annualisé sur 1, 3, 5 et 10 ans, volatilité, perte maximale
  et début d'historique de chaque ETF ; et, à part, quatre indices (CAC 40, Euro Stoxx 50,
  S&P 500, Nasdaq-100) dont l'historique complet couvre les crises de 2000 et de 2008. Les
  indices ne sont pas achetables, ignorent les dividendes, et les indices américains sont en dollars.
- **Versements programmés** (`services/etf.py`) : rejoue sur l'historique réel un plan mensuel
  (premier jour de bourse du mois) sur 1 à 30 ans, avec versement initial facultatif.
  Résultat : total versé, valeur finale, rendement annuel pondéré par les dates de versement,
  net d'impôt selon l'âge du plan, pire moment par rapport aux sommes versées.
- **Répartition** : jusqu'à quatre supports avec leurs pourcentages, rééquilibrage annuel en option.
- **Frais** (`fees.py`) : pourcentage, minimum par ordre et forfait, combinables ; préréglages
  dans l'écran, valeurs par défaut dans `.env` (`ORDER_FEE_RATE`, `ORDER_FEE_MIN`, `ORDER_FEE_FIXED`).
- **Résultat selon la date de départ** : le même plan démarré chaque mois de l'historique, en
  courbe, avec le pire, le médian, le meilleur résultat et la part des plans terminés en perte.
- **Mode objectif** : pour une somme visée nette d'impôt et en euros d'aujourd'hui, calcule le
  versement mensuel qui aurait suffi dans 9 périodes passées sur 10, une sur deux, ou seulement
  les meilleures.
- Dans la simulation, les positions en actions et en ETF sont listées séparément.

## Backtest

`analysis/backtest.py` rejoue la stratégie sur 1 à 15 ans : à chaque revue, achat à l'ouverture
suivante des titres les mieux notés (score minimal, plafond sectoriel, filtre de marché
optionnel), à parts égales ; sortie au stop (N × ATR) ou à l'échéance ; frais sur chaque ordre.
Le résultat est comparé à l'univers équipondéré et à l'ETF CAC 40 : rendement annualisé, perte
maximale, volatilité, Sharpe, détail par année.

Limites : biais du survivant (seules les sociétés aujourd'hui dans l'indice sont testées),
fondamentaux et actualités non rejoués, exécution sans décalage de cours.

## Simulation et automatisation

- **Portefeuille fictif** : 10 000 € par défaut (plafond 150 000 € comme un PEA), ordres au
  dernier cours de clôture, frais de 0,5 %, pas de vente à découvert. Un achat reprend le stop,
  l'objectif et l'échéance de la dernière recommandation sur le titre.
- **Suivi automatique** (à activer dans l'écran Simulation) : après chaque scan, vend les
  positions arrivées au stop, à l'objectif ou à l'échéance, puis achète les meilleures
  opportunités à parts égales (`AUTO_FOLLOW_POSITIONS`).
- **Scan planifié** : `SCAN_SCHEDULE=18:30` lance un scan chaque jour ouvré (heure de Paris),
  tant que le serveur tourne.
- **Alertes** : nouvelles entrées dans le top, positions au stop / à l'objectif / à l'échéance,
  opérations du suivi automatique. Visibles dans l'application, et envoyées à l'adresse du compte
  si `SMTP_*` est renseigné (STARTTLS exigé). L'écran Compte indique la configuration détectée et
  envoie un e-mail de test. Une adresse Outlook, Hotmail ou Live ne peut pas servir d'expéditeur :
  Microsoft n'accepte plus l'authentification par mot de passe pour ces comptes.
- **Fiscalité** : gain net si tout était retiré aujourd'hui — 31,4 % avant 5 ans (12,8 % d'impôt
  sur le revenu + 18,6 % de prélèvements sociaux), 18,6 % après (taux 2026, modifiables dans `.env`).
- **Pertinence** : chaque recommandation est comparée au gain annoncé une fois sa durée écoulée.

## Données et base

- Les cours sont conservés dans la table `prices` et rafraîchis toutes les 6 heures. Si la
  source ne répond pas, les derniers cours en base sont utilisés.
- Sources : `yfinance`, puis en secours l'API « chart » de Yahoo interrogée directement. Les
  deux dépendent de Yahoo : le secours couvre une panne de la bibliothèque, pas de Yahoo.
- PostgreSQL : `uv sync --extra postgres` puis `DATABASE_URL` dans `.env` (ou le profil
  `postgres` de `docker-compose.yml`).
- Les indices (tickers commençant par `^`) sont téléchargés sur tout leur historique.
- Les titres que le scan n'a pas pu noter (ticker périmé, cotation arrêtée) sont listés sous
  le top, pour corriger `sbf120.csv`. La mise à jour de la liste reste manuelle.
- Nouvelle migration après modification de `models.py` :
  `uv run alembic revision --autogenerate -m "description"`.

## Sécurité

- Mots de passe hachés en Argon2id, 12 caractères minimum, temps de réponse constant au login.
- JWT court dans un cookie `HttpOnly`, `SameSite=Strict`, `Secure` en production ; rien dans le
  `localStorage`. La déconnexion révoque tous les jetons émis.
- Anti-CSRF : en-tête `X-Requested-With` exigé sur les requêtes modifiantes, CORS limité aux
  origines déclarées.
- Limitation des tentatives de connexion par IP et par e-mail.
- Toutes les entrées validées par Pydantic ; tickers restreints à l'univers ; requêtes SQL via l'ORM.
- En-têtes : CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS en HTTPS, `no-store` sur l'API.
- Clés (OpenRouter, SMTP, signature) uniquement côté serveur, dans `.env` non versionné, jamais
  journalisées ; e-mails envoyés uniquement après STARTTLS.
- Titres de presse traités comme non fiables : XML analysé par `defusedxml`, consigne anti-injection,
  sortie du modèle validée par un schéma strict et bornée avant d'influencer le score.
- OpenAPI/Swagger désactivés en production.

## Résultat du backtest (novembre 2026, cours réels)

Premier backtest sur 10 ans (2016-2026, 111 titres, réglages par défaut : 5 lignes,
40 séances de détention, stop 2 × ATR, revue toutes les 5 séances, frais 0,5 %) :

| | Rendement annualisé | Perte maximale | Sharpe |
|---|---|---|---|
| Stratégie | **−2,6 %** | 46,7 % | −0,18 |
| Univers équipondéré | +10,4 % | 40,4 % | 0,66 |
| ETF CAC 40 | +9,2 % | 38,5 % | 0,60 |

**Le score ne bat pas l'achat de l'indice.** Positions gagnantes : 34 %, 62 % sorties au stop,
résultat moyen par position −0,3 %.

Deux variantes pour comprendre :

- **Sans stop** (2 × ATR → 10 × ATR) : −2,6 % par an, inchangé. Le stop n'est pas la cause ;
  les positions gagnantes passent à 49 %, soit un pile ou face.
- **Rotation lente** (détention 120 séances, revue toutes les 20 séances, sans stop) : +4,3 %
  par an, 75 ordres au lieu de 349. Les frais et la rotation coûtaient environ 7 points par an,
  mais même corrigé, le résultat reste sous l'indice.

Conclusion : le score technique n'a pas d'avantage démontré sur cette période. Les estimations
de gain affichées sur les cartes restent valables comme statistiques descriptives du passé,
mais rien ne montre qu'elles se réalisent en stratégie.

## Validation walk-forward

`analysis/walkforward.py` teste quatre stratégies à faible rotation — momentum 12 mois, faible
volatilité, leur combinaison, et le score PEA Radar — sous forme de portefeuilles équipondérés
des 10 ou 20 meilleurs titres, rééquilibrés chaque mois ou chaque trimestre.

Le réglage de chaque stratégie est choisi sur 3 ans (meilleur ratio de Sharpe), puis appliqué à
l'année suivante ; on avance d'un an et on recommence. Seules les années de test sont jugées.
« Sélection automatique » suit chaque année la stratégie la mieux classée sur les 3 années
précédentes.

Règle de verdict, fixée à l'avance : une stratégie n'est retenue que si son rendement annualisé
dépasse à la fois celui de l'ETF CAC 40 et celui de l'univers équipondéré, et qu'elle bat l'ETF
au moins 60 % des années de test. L'univers équipondéré profite du même biais du survivant que
les stratégies, d'où l'obligation de le battre lui aussi.

L'écran Opportunités affiche un bandeau qui rappelle le résultat du dernier backtest : tant que
le classement ne bat pas ses références, les cartes ne sont pas des recommandations d'achat.

## Limites connues

- Les cours et fondamentaux viennent de Yahoo Finance (non officiel) ; la couverture des
  fondamentaux et des dates de résultats est inégale sur les valeurs françaises.
- La composition du SBF 120, les secteurs, l'éligibilité PEA et la liste d'ETF (`app/data/`)
  sont à vérifier et à tenir à jour.
- L'estimation de gain est mesurée sur le même historique que celui qui sert au classement :
  elle est optimiste par construction. Le backtest et la simulation servent à la confronter à la réalité.
- Limiteur de débit, cache mémoire et planificateur : prévus pour une seule instance.
