"""Niveaux de sécurité des actions du Dashboard — sans Qt.

* **Niveau 1** — exécution immédiate (sans effet de bord important).
* **Niveau 2** — confirmation demandée. L'utilisateur peut la désactiver
  action par action, SAUF si l'action déclare ``min_level=2`` (par exemple
  la modification d'un souvenir : le cahier des charges impose une
  confirmation).
* **Niveau 3** — action destructive ou irréversible : résumé + confirmation
  renforcée (case à cocher explicite). Ce niveau ne peut JAMAIS être abaissé.

La politique ne fait que décider « faut-il confirmer et comment ». Le
résultat d'une action n'est annoncé qu'après la réponse du service concerné
(cf. ``actions.ActionResult``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

LEVEL_IMMEDIATE = 1
LEVEL_CONFIRM = 2
LEVEL_DESTRUCTIVE = 3

LEVEL_LABELS = {
    LEVEL_IMMEDIATE: "Niveau 1 — immédiat",
    LEVEL_CONFIRM: "Niveau 2 — confirmation",
    LEVEL_DESTRUCTIVE: "Niveau 3 — destructif (confirmation renforcée)",
}


@dataclass(frozen=True)
class ActionPolicy:
    action_id: str
    label: str
    level: int
    #: Niveau minimal toléré : une action de niveau 2 avec ``min_level=1``
    #: peut être rendue immédiate par l'utilisateur.
    min_level: int = 1
    description: str = ""

    def __post_init__(self) -> None:
        if self.level not in LEVEL_LABELS:
            raise ValueError(f"niveau invalide pour {self.action_id}: {self.level}")
        if self.level == LEVEL_DESTRUCTIVE and self.min_level != LEVEL_DESTRUCTIVE:
            # Garde-fou structurel : un niveau 3 n'est jamais abaissable.
            object.__setattr__(self, "min_level", LEVEL_DESTRUCTIVE)

    @property
    def lowerable(self) -> bool:
        return self.level == LEVEL_CONFIRM and self.min_level <= LEVEL_IMMEDIATE


#: Politiques de toutes les actions déclenchables depuis le Dashboard.
DEFAULT_POLICIES: tuple[ActionPolicy, ...] = (
    ActionPolicy("mic.toggle", "Couper / rétablir le micro", LEVEL_IMMEDIATE),
    ActionPolicy("voice.stop", "Interrompre la réponse en cours", LEVEL_IMMEDIATE),
    ActionPolicy("interface.blob", "Passer en mode orbe", LEVEL_IMMEDIATE),
    ActionPolicy("interface.desktop", "Passer en mode Desktop", LEVEL_IMMEDIATE),
    ActionPolicy("folder.results", "Ouvrir le dossier des résultats", LEVEL_IMMEDIATE),
    ActionPolicy("folder.documents", "Ouvrir le dossier des documents", LEVEL_IMMEDIATE),
    ActionPolicy("folder.logs", "Ouvrir le dossier des journaux", LEVEL_IMMEDIATE),
    ActionPolicy("task.open_result", "Ouvrir le résultat d'une tâche", LEVEL_IMMEDIATE),
    ActionPolicy("task.retry", "Relancer une tâche échouée ou annulée", LEVEL_CONFIRM,
                 description="Consomme du quota de modèle."),
    ActionPolicy("task.cancel", "Annuler une tâche en cours", LEVEL_CONFIRM,
                 description="Le travail en cours de la tâche est perdu."),
    ActionPolicy("conversation.new", "Nouvelle conversation", LEVEL_CONFIRM,
                 description="Efface le contexte conversationnel (pas la mémoire)."),
    ActionPolicy("routine.run", "Lancer une routine", LEVEL_CONFIRM,
                 description="Exécute des actions réelles sur le PC."),
    ActionPolicy("protocol.run", "Lancer un protocole visuel", LEVEL_IMMEDIATE),
    ActionPolicy("todo.add", "Ajouter une tâche personnelle", LEVEL_IMMEDIATE),
    ActionPolicy("todo.complete", "Terminer une tâche personnelle", LEVEL_IMMEDIATE),
    ActionPolicy("note.add", "Ajouter une note", LEVEL_IMMEDIATE),
    ActionPolicy("memory.create", "Créer un souvenir", LEVEL_IMMEDIATE),
    ActionPolicy("memory.update", "Modifier un souvenir", LEVEL_CONFIRM, min_level=LEVEL_CONFIRM,
                 description="Confirmation imposée par la politique mémoire."),
    ActionPolicy("memory.restore", "Restaurer une version de souvenir", LEVEL_CONFIRM, min_level=LEVEL_CONFIRM,
                 description="Remplace la version actuelle (elle reste dans l'historique)."),
    ActionPolicy("memory.delete", "Supprimer un souvenir", LEVEL_DESTRUCTIVE),
    ActionPolicy("layout.reset", "Réinitialiser une mise en page", LEVEL_CONFIRM),
    ActionPolicy("layout.preset", "Appliquer un préréglage de mise en page", LEVEL_CONFIRM),
    ActionPolicy("page.delete", "Supprimer une page personnalisée", LEVEL_CONFIRM, min_level=LEVEL_CONFIRM,
                 description="Supprime la page et sa disposition (aucune donnée métier)."),
    ActionPolicy("notifications.clear", "Vider les notifications du Dashboard", LEVEL_IMMEDIATE),
    ActionPolicy("update.check", "Vérifier les mises à jour", LEVEL_IMMEDIATE),
    ActionPolicy("diagnostics.integrity", "Vérifier l'intégrité de la base mémoire", LEVEL_IMMEDIATE),
    ActionPolicy("jarvis.quit", "Quitter Jarvis", LEVEL_DESTRUCTIVE,
                 description="Arrête la voix, la conversation et les tâches en cours."),
)


@dataclass
class SecurityPolicy:
    """Politique effective : niveaux par défaut + préférences utilisateur."""

    policies: dict[str, ActionPolicy] = field(default_factory=dict)
    #: action_id → confirmation souhaitée (préférences persistées).
    overrides: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def default(cls, overrides: dict[str, bool] | None = None,
                policies: Iterable[ActionPolicy] = DEFAULT_POLICIES) -> "SecurityPolicy":
        return cls({p.action_id: p for p in policies}, dict(overrides or {}))

    def policy(self, action_id: str) -> ActionPolicy:
        try:
            return self.policies[action_id]
        except KeyError:
            # Action non déclarée : prudence maximale raisonnable.
            return ActionPolicy(action_id, action_id, LEVEL_CONFIRM, min_level=LEVEL_CONFIRM)

    def effective_level(self, action_id: str) -> int:
        policy = self.policy(action_id)
        if policy.level == LEVEL_CONFIRM and policy.lowerable:
            if self.overrides.get(action_id) is False:
                return LEVEL_IMMEDIATE
        return policy.level

    def set_confirmation(self, action_id: str, confirm: bool) -> bool:
        """Change la préférence ; refuse (False) si l'action n'est pas abaissable."""
        policy = self.policy(action_id)
        if not policy.lowerable:
            return False
        if confirm:
            self.overrides.pop(action_id, None)
        else:
            self.overrides[action_id] = False
        return True

    def lowerable_policies(self) -> list[ActionPolicy]:
        return [p for p in self.policies.values() if p.lowerable]

    def locked_policies(self) -> list[ActionPolicy]:
        return [p for p in self.policies.values() if p.level >= LEVEL_CONFIRM and not p.lowerable]
