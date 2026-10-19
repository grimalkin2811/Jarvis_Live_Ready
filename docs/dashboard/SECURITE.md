# Sécurité des actions et honnêteté de l'interface

Code : `UI/dashboard/security.py` (politiques), `UI/dashboard/dialogs.py`
(`ConfirmDialog`), `UI/dashboard/actions.py` (actions rapides),
`src/runtime_status.redact_secrets`, `UI/dashboard/log_buffer.py`.

## 1. Trois niveaux

| Niveau | Comportement | Personnalisable ? |
|---|---|---|
| **1 — immédiat** | Exécution directe (sans effet de bord important). | — |
| **2 — confirmation** | Boîte de confirmation (résumé + détails). | Oui, action par action, **sauf** si la politique impose `min_level=2`. |
| **3 — destructif** | Résumé de ce qui va disparaître + avertissement + **confirmation renforcée** : la case « Je confirme cette action » doit être cochée pour activer le bouton ; « Annuler » est le bouton par défaut. | **Jamais** : `ActionPolicy` force `min_level=3` (garde-fou structurel, testé). |

Les préférences (page Paramètres → « Sécurité des actions ») ne peuvent
qu'abaisser une action de niveau 2 abaissable au niveau 1 ;
`SecurityPolicy.set_confirmation` refuse tout le reste. Une action non
déclarée est traitée au niveau 2 non abaissable (prudence par défaut).

## 2. Politiques déclarées

| Action | Niveau | Abaissable |
|---|---|---|
| Couper / rétablir le micro (`mic.toggle`) | 1 | — |
| Interrompre la réponse (`voice.stop`) | 1 | — |
| Passer en mode orbe / Desktop (`interface.blob`, `interface.desktop`) | 1 | — |
| Ouvrir un dossier (résultats, documents, journaux) / un résultat de tâche | 1 | — |
| Lancer un protocole visuel (`protocol.run`) | 1 | — |
| Ajouter / terminer une tâche personnelle, ajouter une note | 1 | — |
| Créer un souvenir (`memory.create`) | 1 | — (aucune confirmation demandée par le cahier des charges) |
| Vider les notifications, vérifier les mises à jour, vérifier l'intégrité mémoire | 1 | — |
| Relancer une tâche (`task.retry`, consomme du quota) | 2 | oui |
| Annuler une tâche (`task.cancel`) | 2 | oui |
| Nouvelle conversation (`conversation.new`, efface le contexte, pas la mémoire) | 2 | oui |
| Lancer une routine (`routine.run`, actions réelles sur le PC) | 2 | oui |
| Réinitialiser une page / appliquer un préréglage | 2 | oui |
| Modifier un souvenir (`memory.update`) | 2 | **non** (`min_level=2`) |
| Restaurer une version de souvenir (`memory.restore`) | 2 | **non** |
| Supprimer une page personnalisée (`page.delete`) | 2 | **non** |
| Supprimer un souvenir (`memory.delete`) | **3** | jamais |
| Quitter Jarvis (`jarvis.quit`, mode de fermeture « arrêt complet ») | **3** | jamais — dialogue d'arrêt dédié : résumé de ce qui s'arrête et de ce qui est conservé, bouton explicite « Quitter Jarvis » ; si des tâches sont actives, elles sont listées et la case « J'accepte d'interrompre les tâches en cours » est obligatoire |

## 3. Règles d'honnêteté (toutes les actions)

* **Succès annoncé uniquement après confirmation du service** : chaque action
  retourne un `ActionResult` construit à partir de la réponse réelle et
  vérifiée (`TaskManager.cancel_task`/`retry_task`, `MemoryManager.*_checked`,
  état du micro relu après bascule, mode d'interface effectivement appliqué,
  dossier réellement ouvert…). Quand la confirmation manque, le résultat le
  dit : les outils vocaux `open_dashboard`/`close_dashboard` renvoient
  `confirmed=False` (« demandée, non encore confirmée ») si la fenêtre n'a
  pas rapporté son état réel dans les 1,5 s.
* **Pas de commande système arbitraire** : les actions rapides sont une liste
  fermée (`actions.build_actions`) ; les routines et protocoles sont ceux déjà
  définis par l'utilisateur dans Jarvis ; l'ouverture de dossiers passe par
  `QDesktopServices` sur des chemins connus de Jarvis.
* **Indisponible = affiché indisponible** : une action impossible (micro
  sans moteur, routine désactivée, dossier absent, tâche dans un statut qui
  ne le permet pas) est grisée avec sa raison ; jamais simulée.
* **Pas de fausse fonctionnalité** : pas de pause/reprise de tâche (non
  supportée par le Task Manager), sélecteur manuel de modèle verrouillé,
  aucun modèle ni quota inventé.

## 4. Secrets

* `redact_secrets()` masque les clés Google/Gemini (`AIza…`), les jetons
  `Bearer`, les paires `api_key=`, `token:`, `password=`… et les paramètres
  d'URL sensibles (`?key=`, `access_token=`…). Il est appliqué à **tout**
  texte d'erreur ou de journal affiché : journal d'activité (tampon
  `log_buffer`), erreurs du moteur publiées dans `RUNTIME`, rapport de
  diagnostic copiable.
* Les préférences du Dashboard ne contiennent aucun secret ; elles sont
  stockées hors du dépôt (`paths.ui_state_dir()`).
* Tests : `tests/test_runtime_status.py` (motifs de masquage, erreurs publiées),
  `tests/test_dashboard_security.py` (niveaux, non-abaissement du niveau 3,
  journal d'activité et rapport de diagnostic sans secret),
  `tests/test_dashboard_notifications.py` (notification d'échec de tâche sans
  secret).
