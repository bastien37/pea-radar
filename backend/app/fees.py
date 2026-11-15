"""Frais de courtage : pourcentage, minimum par ordre et forfait fixe, combinables."""

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class FeeModel:
    rate: float = 0.005  # part du montant de l'ordre
    minimum: float = 0.0  # plancher par ordre, en euros
    fixed: float = 0.0  # forfait par ordre, en euros

    def fee(self, amount: float) -> float:
        if amount <= 0:
            return 0.0
        return min(amount, max(self.minimum, self.fixed + self.rate * amount))

    def label(self) -> str:
        parts = []
        if self.rate:
            parts.append(f"{self.rate * 100:g} %".replace(".", ","))
        if self.fixed:
            parts.append(f"{self.fixed:g} € fixes".replace(".", ","))
        text = " + ".join(parts) or "aucuns frais"
        return f"{text} (minimum {self.minimum:g} €)".replace(".", ",") if self.minimum else text


def default_fees() -> FeeModel:
    s = get_settings()
    return FeeModel(s.order_fee_rate, s.order_fee_min, s.order_fee_fixed)
