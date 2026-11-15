import { useRef } from "react";

type Item = [title: string, text: string];

const TOPICS: Record<string, { title: string; intro: string; items: Item[] }> = {
  opportunite: {
    title: "Lire une carte d'opportunité",
    intro: "Chaque carte résume ce que le scan a calculé pour un titre. De haut en bas :",
    items: [
      ["Rang", "Position du titre dans le top du scan, après tous les ajustements."],
      ["Nom, ticker, cours et date", "Le dernier cours de clôture connu et le jour auquel il correspond. Les calculs s'arrêtent à cette date."],
      ["Secteur", "Le secteur d'activité. Le top est plafonné à quelques titres par secteur pour éviter de tout miser sur le même."],
      ["Score sur 100", "La note finale. Elle part du score technique, auquel s'ajoutent un bonus ou malus fondamental (±10 points au plus) et un bonus ou malus d'actualité (±10 points au plus). Au-dessus de 70, la configuration est nettement favorable ; autour de 50, elle est neutre."],
      ["Technique / Fondamentaux / Actualité", "Le détail de la note : la base technique, puis ce que les fondamentaux et l'actualité ont ajouté ou retiré."],
      ["Risque (1 à 5 points)", "Mesure l'ampleur des variations du titre : volatilité sur 60 séances, plus un cran si le titre a perdu plus de 35 % depuis son plus haut d'un an. Un cran de plus si l'actualité est inquiétante ou si le marché est en tendance baissière."],
      ["Gain estimé (médiane)", "Ce que le cours a fait, dans le cas médian, après les situations passées où le score était comparable. Une fois sur deux, le résultat réel a été meilleur ; une fois sur deux, moins bon."],
      ["Fourchette « de … à … »", "La moitié centrale des résultats passés. Un quart des cas a fait pire que la borne basse, un quart mieux que la borne haute. Une borne basse négative veut dire que la perte est un scénario courant."],
      ["Durée de détention", "Le nombre de séances de bourse après lequel le gain a été le meilleur rapporté au temps immobilisé. Cinq séances font une semaine."],
      ["Cas gagnants", "La part des situations passées comparables qui se sont terminées en hausse. À 55 %, c'est à peine mieux que pile ou face."],
      ["Stop suggéré", "Un cours de protection, placé à deux fois l'amplitude quotidienne moyenne (ATR) sous le cours actuel. Si le titre descend jusque-là, le scénario haussier est invalidé et il vaut mieux vendre."],
      ["Les cinq barres", "Les composantes du score technique. Tendance : cours au-dessus de ses moyennes 50 et 200 séances. Momentum : MACD et performance sur 3 et 6 mois. Point d'entrée : titre en repli (stochastique bas, RSI modéré). Volumes : échanges supérieurs à la normale. Stabilité : faible volatilité."],
      ["Fondamentaux", "PER : prix de l'action rapporté au bénéfice (bas = bon marché). Croissance : évolution des bénéfices sur un an. Dette : en pourcentage des fonds propres. ROE : rentabilité des fonds propres. Un tiret signifie que la donnée n'est pas disponible."],
      ["Étiquette « Résultats dans X j »", "La société publie bientôt ses résultats. Le cours peut alors bondir ou chuter d'un coup, au-delà du stop."],
      ["Étiquette « Marché baissier »", "Le CAC 40 est sous sa moyenne 200 séances. Historiquement, les signaux d'achat échouent plus souvent dans ce contexte."],
      ["« Estimation tirée de N configurations »", "Le nombre de cas passés utilisés pour le gain estimé. Plus il est grand, plus l'estimation est solide. « Sur l'ensemble de l'univers » signifie que le titre seul n'avait pas assez d'historique."],
      ["Bloc actualité", "L'avis d'une IA sur les titres de presse récents : sentiment de −1 (très négatif) à +1 (très positif), confiance dans cet avis, synthèse, éléments favorables (+) et défavorables (−). Les titres analysés sont listés en dessous."],
    ],
  },
  simulation: {
    title: "Lire l'écran Simulation",
    intro: "Le portefeuille est fictif : aucun ordre réel n'est passé. Les ordres s'exécutent au dernier cours de clôture.",
    items: [
      ["Valeur totale", "Liquidités plus valeur des titres détenus au dernier cours. Le pourcentage compare cette valeur au capital de départ."],
      ["Liquidités", "L'argent fictif non investi, disponible pour acheter."],
      ["Investi", "La valeur actuelle de l'ensemble des titres détenus."],
      ["Plus-values réalisées", "Le gain ou la perte des ventes déjà effectuées, frais déduits. Les positions encore ouvertes n'y figurent pas."],
      ["Frais", "Chaque achat et chaque vente coûte un pourcentage de l'ordre, comme chez un courtier. C'est pourquoi une position affiche une légère perte dès l'achat."],
      ["Net d'impôt si retrait", "Ce qu'il resterait du gain si vous retiriez tout aujourd'hui. Avant 5 ans de PEA, le gain est taxé à 31,4 % (12,8 % d'impôt sur le revenu et 18,6 % de prélèvements sociaux) et le retrait ferme le plan. Après 5 ans, seuls les prélèvements sociaux de 18,6 % restent dus. Tant que l'argent reste sur le plan, rien n'est prélevé."],
      ["Date d'ouverture du PEA", "Elle fixe le début du délai de 5 ans. Indiquez celle de votre vrai PEA pour un calcul réaliste."],
      ["Suivi automatique", "Quand il est activé, chaque scan vend les positions arrivées au stop, à l'objectif ou à l'échéance, puis achète les meilleures opportunités à parts égales. Il n'achète rien quand le marché est baissier. C'est le moyen de tester la stratégie sans intervenir."],
      ["Prix de revient", "Le prix moyen payé par action, frais d'achat inclus."],
      ["+/− latente", "Le gain ou la perte d'une position encore ouverte, si elle était vendue au dernier cours (hors frais de vente)."],
      ["Stop / Objectif", "Repris de la recommandation au moment de l'achat : le cours de protection et le cours visé (cours d'achat plus gain estimé)."],
      ["Avancement", "Les séances écoulées sur la durée de détention prévue."],
      ["Étiquette stop / objectif / échéance", "La position a atteint l'un de ses trois seuils de sortie. C'est le moment de décider de vendre."],
      ["Pertinence des recommandations", "Chaque opportunité signalée est suivie jusqu'à sa durée de détention, puis son résultat réel (« Constaté ») est comparé au gain annoncé. Le taux de réussite et le résultat moyen ne portent que sur les recommandations arrivées à terme."],
      ["Verdict", "En cours : la durée n'est pas écoulée. Gagnante ou perdante : résultat à l'échéance. Stop touché : le cours est passé sous le stop avant l'échéance."],
    ],
  },
  backtest: {
    title: "Lire un backtest",
    intro: "Le backtest rejoue la stratégie sur le passé : acheter les titres les mieux notés, les revendre au stop ou à l'échéance, frais inclus.",
    items: [
      ["Rendement total", "Le gain cumulé sur toute la période."],
      ["Rendement annualisé", "Le gain moyen par an qui donnerait le même résultat. C'est le chiffre à comparer entre la stratégie et les références."],
      ["Perte maximale", "La plus forte baisse entre un sommet et le creux suivant. Elle indique ce qu'il aurait fallu supporter au pire moment."],
      ["Volatilité", "L'ampleur des variations annuelles. Plus elle est élevée, plus le parcours est heurté."],
      ["Ratio de Sharpe", "Le rendement obtenu par unité de risque. Au-dessus de 1, c'est bon ; sous 0,5, le risque est mal payé."],
      ["Univers équipondéré", "Ce qu'aurait donné l'achat de tous les titres de l'univers à parts égales, sans frais. Si la stratégie ne fait pas mieux, le score n'apporte rien."],
      ["ETF CAC 40", "Ce qu'aurait donné un simple ETF indiciel, la solution la plus facile à mettre en œuvre."],
      ["Taux d'investissement", "La part moyenne du capital réellement placée. S'il est faible, la stratégie est souvent restée en liquidités."],
      ["Sorties au stop", "La part des positions vendues sur leur stop plutôt qu'à l'échéance."],
      ["Limites", "Les sociétés sorties de l'indice ne sont pas testées (résultats embellis), et ni les fondamentaux ni l'actualité ne sont rejoués."],
      ["Validation walk-forward", "Un test plus sévère que le backtest. Le réglage de chaque stratégie est choisi sur 3 ans, puis appliqué à l'année suivante, qu'il n'a jamais vue. On avance d'un an et on recommence. Seules les années de test comptent : un réglage qui n'a marché que par hasard ne tient pas cette épreuve."],
      ["Momentum 12 mois", "Acheter les titres qui ont le plus monté sur un an, en ignorant le dernier mois."],
      ["Faible volatilité", "Acheter les titres dont le cours varie le moins."],
      ["Sélection automatique", "Chaque année, suit la stratégie qui avait le mieux marché sur les 3 années précédentes. C'est ce que donnerait le réflexe de se fier au meilleur backtest."],
      ["Années gagnées", "Le nombre d'années de test où la stratégie a fait mieux que l'ETF."],
      ["Rotation", "La part du portefeuille vendue et rachetée chaque année. À 200 %, tout le portefeuille change deux fois par an, et les frais suivent."],
      ["Verdict", "Une stratégie n'est retenue que si elle bat à la fois l'ETF et l'univers équipondéré en rendement annualisé, et l'ETF au moins 6 années sur 10. La règle est fixée avant de voir les résultats."],
    ],
  },
};

export default function Tuto({ topic }: { topic: keyof typeof TOPICS }) {
  const ref = useRef<HTMLDialogElement>(null);
  const t = TOPICS[topic];
  return (
    <>
      <button type="button" className="ghost" onClick={() => ref.current?.showModal()}>? Tuto</button>
      <dialog ref={ref} className="tuto" onClick={(e) => e.target === ref.current && ref.current?.close()}>
        <header>
          <h2>{t.title}</h2>
          <button type="button" className="ghost" onClick={() => ref.current?.close()} aria-label="Fermer">✕</button>
        </header>
        <p className="muted">{t.intro}</p>
        <dl>
          {t.items.map(([title, text]) => (
            <div key={title}><dt>{title}</dt><dd>{text}</dd></div>
          ))}
        </dl>
      </dialog>
    </>
  );
}
