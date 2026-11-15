"""Univers d'investissement : SBF 120, filtré sur l'éligibilité PEA.

Le fichier data/sbf120.csv est modifiable : la composition de l'indice évolue et
l'éligibilité PEA (colonne `pea`) doit être vérifiée auprès de son courtier.
Les foncières SIIC sont marquées non éligibles.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_CSV = Path(__file__).parent / "data" / "sbf120.csv"


@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    pea: bool


@lru_cache
def load_universe() -> dict[str, Stock]:
    with _CSV.open(encoding="utf-8", newline="") as f:
        rows = [Stock(r["ticker"].strip(), r["name"].strip(), r["pea"].strip() == "1")
                for r in csv.DictReader(f)]
    return {s.ticker: s for s in rows if s.pea}
