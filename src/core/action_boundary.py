"""Fail-closed boundary for model-proposed actions, pending lot 02.

This is not a consent engine. A model-supplied ``confirm`` is never proof of
user consent. Confirmed operations are refused until an independent trusted
confirmation flow exists. Some legacy mutations without a confirmation flag
are explicitly blocked as well; this list is NOT a full capability policy.
Direct UI actions and trusted internal calls are not routed through this gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ActionProposal:
    name: str
    arguments: Mapping[str, Any]
    origin: str = "model"


# Legacy destructive operations without a confirm argument.
BLOCKED_MODEL_ACTIONS = frozenset({"delete_memory"})


def reject_unverified_action(proposal: ActionProposal) -> str | None:
    """Return a reason if a proposal cannot safely use the legacy path."""
    if proposal.origin != "model":
        raise ValueError("Only untrusted model proposals are accepted here")
    if proposal.name in BLOCKED_MODEL_ACTIONS or bool(proposal.arguments.get("confirm", False)):
        return ("Action refusée : une confirmation fournie par le modèle ne constitue pas "
                "une autorisation utilisateur vérifiée. Cette action nécessite le futur moteur de sécurité.")
    return None
