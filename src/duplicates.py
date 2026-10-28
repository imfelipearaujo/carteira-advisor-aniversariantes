"""Possible patrimony duplication between different client registrations.

Motivated by family registrations (e.g. a "Família" record) that may hold
copies of the members' own wallets. Only arithmetic and identifier evidence
is used — never name similarity — and nothing is ever subtracted from any
total: findings are for manual review.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.currency import WalletDetail
from src.reconciliation import to_decimal
from src.transformer import id_key

MANUAL_REVIEW = "POSSÍVEL DUPLICIDADE — CONFERÊNCIA MANUAL"


class EvidenceKind(StrEnum):
    """Strength of the evidence that two wallets hold the same patrimony."""

    SAME_WALLET = "mesmo walletInfoId em clientes diferentes"
    IDENTICAL_VALUES = "valor atual e valor investido idênticos, mesma moeda"


@dataclass(frozen=True)
class DuplicateEvidence:
    """Two wallets of different clients that look like the same patrimony.

    Attributes:
        kind: Evidence type.
        client_a, wallet_a: First client id and walletInfoId.
        client_b, wallet_b: Second client id and walletInfoId.
        coin: ``walletCoin`` of the wallets (``None`` if unknown).
        current_value: Shared ``currentValue`` (original currency).
    """

    kind: EvidenceKind
    client_a: Any
    wallet_a: Any
    client_b: Any
    wallet_b: Any
    coin: str | None
    current_value: Decimal

    @property
    def status(self) -> str:
        # Even the same walletInfoId only shows a shared record; whether the
        # family registration should exist is a business decision.
        return MANUAL_REVIEW

    def involves(self, client_id: Any) -> bool:
        return id_key(client_id) in (id_key(self.client_a), id_key(self.client_b))

    def other(self, client_id: Any) -> tuple[Any, Any]:
        """The (client, wallet) on the other side of the pair."""
        if id_key(self.client_a) == id_key(client_id):
            return self.client_b, self.wallet_b
        return self.client_a, self.wallet_a

    def own_wallet(self, client_id: Any) -> Any:
        return self.wallet_a if id_key(self.client_a) == id_key(client_id) else self.wallet_b


def find_cross_client_duplicates(
    pl_entries: list[dict[str, Any]], details: dict[Any, WalletDetail]
) -> list[DuplicateEvidence]:
    """Find wallets of *different* clients that look like the same patrimony.

    Evidence:
        - the same ``walletInfoId`` listed under two clients;
        - same coin, and identical non-zero ``currentValue`` **and**
          ``investedValue`` (two independent amounts matching to the cent).

    Equal names or a single coinciding value are deliberately not evidence.
    Wallets with an unknown coin are only compared with each other.
    """
    wallets = []
    for entry in pl_entries:
        for wallet in entry.get("wallets") or []:
            if not isinstance(wallet, dict):
                continue
            detail = details.get(wallet.get("walletInfoId"))
            wallets.append(
                (
                    entry.get("clientId"),
                    wallet.get("walletInfoId"),
                    detail.coin if detail else None,
                    to_decimal(wallet.get("currentValue")),
                    to_decimal(wallet.get("investedValue")),
                )
            )

    evidence: list[DuplicateEvidence] = []
    for i, (client_a, wallet_a, coin_a, current_a, invested_a) in enumerate(wallets):
        for client_b, wallet_b, coin_b, current_b, invested_b in wallets[i + 1 :]:
            if id_key(client_a) == id_key(client_b):
                continue
            if id_key(wallet_a) == id_key(wallet_b):
                kind = EvidenceKind.SAME_WALLET
            elif (
                coin_a == coin_b
                and current_a is not None
                and invested_a is not None
                and current_a != 0
                and invested_a != 0
                and (current_a, invested_a) == (current_b, invested_b)
            ):
                kind = EvidenceKind.IDENTICAL_VALUES
            else:
                continue
            evidence.append(
                DuplicateEvidence(
                    kind, client_a, wallet_a, client_b, wallet_b, coin_a, current_a or Decimal(0)
                )
            )
    return evidence
