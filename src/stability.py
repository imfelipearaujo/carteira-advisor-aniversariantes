"""Stability of the consolidated patrimony during the collection.

Patrimony is updated while the program runs (brokerages being linked, wallets
re-consolidated). consolidated-pl is read once at the start of the financial
audit and once more after the wallet details; this module compares the two
snapshots record by record. An unchanged company total is *not* enough to
declare the run stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.api_client import ConsolidatedPL
from src.reconciliation import to_decimal
from src.transformer import id_key


@dataclass(frozen=True)
class StabilityResult:
    """Differences between two consolidated-pl snapshots.

    Attributes:
        started_at: When the first snapshot was requested.
        finished_at: When the second snapshot was requested.
        total_before: ``totalCurrentValue`` of the first snapshot.
        total_after: ``totalCurrentValue`` of the second snapshot.
        clients_added: Client ids only in the second snapshot.
        clients_removed: Client ids only in the first snapshot.
        wallets_added: ``(clientId, walletInfoId)`` only in the second.
        wallets_removed: ``(clientId, walletInfoId)`` only in the first.
        value_changes: ``(clientId, walletInfoId)`` whose currentValue or
            investedValue changed.
    """

    started_at: datetime
    finished_at: datetime
    total_before: Decimal | None
    total_after: Decimal | None
    clients_added: list[Any] = field(default_factory=list)
    clients_removed: list[Any] = field(default_factory=list)
    wallets_added: list[tuple[Any, Any]] = field(default_factory=list)
    wallets_removed: list[tuple[Any, Any]] = field(default_factory=list)
    value_changes: list[tuple[Any, Any]] = field(default_factory=list)

    @property
    def total_changed(self) -> bool:
        return self.total_before != self.total_after

    @property
    def stable(self) -> bool:
        """True only if no record and no total changed between the snapshots."""
        return not (
            self.total_changed
            or self.clients_added
            or self.clients_removed
            or self.wallets_added
            or self.wallets_removed
            or self.value_changes
        )

    @property
    def affected_clients(self) -> list[Any]:
        """Client ids touched by any change, in first-seen order."""
        ids = [
            *self.clients_added,
            *self.clients_removed,
            *(client for client, _ in self.wallets_added),
            *(client for client, _ in self.wallets_removed),
            *(client for client, _ in self.value_changes),
        ]
        return list(dict.fromkeys(ids))


_WalletRecord = tuple[Any, Any, Decimal | None, Decimal | None]


def _wallet_index(snapshot: ConsolidatedPL) -> dict[tuple[str, str], _WalletRecord]:
    index = {}
    for entry in snapshot.clients:
        for wallet in entry.get("wallets") or []:
            if not isinstance(wallet, dict):
                continue
            key = (id_key(entry.get("clientId")), id_key(wallet.get("walletInfoId")))
            index[key] = (
                entry.get("clientId"),
                wallet.get("walletInfoId"),
                to_decimal(wallet.get("currentValue")),
                to_decimal(wallet.get("investedValue")),
            )
    return index


def compare_snapshots(
    before: ConsolidatedPL, after: ConsolidatedPL, started_at: datetime, finished_at: datetime
) -> StabilityResult:
    """Compare client ids, wallet ids and wallet values of two snapshots."""
    clients_before = {id_key(e.get("clientId")): e.get("clientId") for e in before.clients}
    clients_after = {id_key(e.get("clientId")): e.get("clientId") for e in after.clients}
    wallets_before = _wallet_index(before)
    wallets_after = _wallet_index(after)
    return StabilityResult(
        started_at=started_at,
        finished_at=finished_at,
        total_before=before.total_current_value,
        total_after=after.total_current_value,
        clients_added=[clients_after[k] for k in clients_after.keys() - clients_before.keys()],
        clients_removed=[clients_before[k] for k in clients_before.keys() - clients_after.keys()],
        wallets_added=[wallets_after[k][:2] for k in wallets_after.keys() - wallets_before.keys()],
        wallets_removed=[wallets_before[k][:2] for k in wallets_before.keys() - wallets_after.keys()],
        value_changes=[
            wallets_after[k][:2]
            for k in wallets_before.keys() & wallets_after.keys()
            if wallets_before[k][2:] != wallets_after[k][2:]
        ],
    )
