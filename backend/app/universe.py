"""Univers d'investissement : SBF 120 filtré sur l'éligibilité PEA, et ETF de référence.

Les fichiers de data/ sont modifiables : la composition de l'indice évolue, et
l'éligibilité PEA (colonne `pea`) doit être vérifiée auprès de son courtier.
Les foncières SIIC sont marquées non éligibles.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data"
INDEX_TICKER = "^FCHI"  # CAC 40, pour le filtre de tendance du marché
BENCHMARK_ETF = "CAC.PA"


@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    sector: str
    pea: bool = True
    is_etf: bool = False


def _read(name: str) -> list[dict]:
    with (_DATA / name).open(encoding="utf-8", newline="") as f:
        return [{k: v.strip() for k, v in row.items()} for row in csv.DictReader(f)]


@lru_cache
def load_universe() -> dict[str, Stock]:
    """Actions notées par le scan."""
    rows = [Stock(r["ticker"], r["name"], r["sector"], r["pea"] == "1") for r in _read("sbf120.csv")]
    return {s.ticker: s for s in rows if s.pea}


@lru_cache
def load_etfs() -> dict[str, Stock]:
    """ETF éligibles PEA : points de comparaison, achetables en simulation, non notés."""
    return {r["ticker"]: Stock(r["ticker"], r["name"], f"ETF {r['index']}", is_etf=True)
            for r in _read("etfs.csv")}


@lru_cache
def load_indices() -> dict[str, Stock]:
    """Indices : historique long pour éprouver un plan sur des crises, non achetables."""
    return {r["ticker"]: Stock(r["ticker"], r["name"], f"Indice {r['note']}", pea=False)
            for r in _read("indices.csv")}


@lru_cache
def load_tradable() -> dict[str, Stock]:
    return {**load_universe(), **load_etfs()}
