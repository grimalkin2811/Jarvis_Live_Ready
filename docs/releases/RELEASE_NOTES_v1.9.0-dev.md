# Jarvis Live Ready 1.9.0-dev

> **Statut : candidat de développement, non publié.** Aucun tag ni artefact stable n'a été créé. Ne pas annoncer une validation Gemini Live réelle : elle reste à exécuter avec une clé API valide.

## Changements

- Ajout de l'envoi de texte depuis le Dashboard dans la session Gemini Live et le contexte partagés avec la voix, avec refus explicite pendant un tour occupé, filet de délai d'attente et traitement lors des reconnexions.
- Fiabilisation des reprises : détection de handles expirés (erreurs 1007/1008), rejeu du contexte local lorsque la reprise serveur est refusée, confirmation du seed avant réouverture du micro, et reprise bornée après génération vide ou interruption.
- Conservation exacte du checkpoint serveur : un handle reçu avant le commit local ne peut plus être présenté comme couvrant le dernier tour; un handle refusé après établissement est fermé avant la tentative fraîche.
- Le validateur réel `--mode text` utilise maintenant le même `submit_text` que l'application, avec porte micro et flux audio gérés par `GeminiLive`.
- Renforcement des commandes vocales Dashboard : commandes explicites, page réellement confirmée par l'interface, et rejet des mentions, questions et négations ambiguës.
- Limitation de la construction incrémentale du Dashboard à un cadre par tour de boucle Qt pour réduire les pics de latence.
- Remplacement d'un helper privé du SDK Gemini dans le faux serveur de tests.
- Version applicative `1.9.0`, canal `dev`; l'installateur manuel utilise maintenant le même numéro de version par défaut.

- **Verdict S4 fondé sur les preuves** : `OUTILS RÉELS` exige désormais la chaîne complète et ordonnée `TOOL_CALL_RECEIVED → TOOL_CALL_VALIDATED → TOOL_CALL_EXECUTED → TOOL_RESPONSE_SENT → TOOL_RESULT_TURN_COMPLETED`, chaque maillon nommant `validation_ping` et borné au tour du scénario. Un outil exécuté par un scénario précédent ne peut plus créditer S4. Chaque maillon manquant est nommé dans les notes du run.
- **Verdict S6 : transcription inexacte ≠ perte de contexte** : une question mal entendue rend le scénario `NON TESTABLE` au lieu de `FAIL`, sauf si le contexte est vérifié indépendamment de l'ASR par la relance texte (`submit_text_threadsafe`, même session, sans audio). Une entrée correcte suivie d'une réponse fausse reste un `FAIL`.
- **Modèle transmis aux DEUX validateurs** : `validate_reconnect_v174_real.py` accepte `--model` (priorité : argument, puis `GEMINI_MODEL`, puis défaut) et le workflow le lui transmet. Auparavant S1-S6 tournaient toujours sur le modèle par défaut, quel que soit le choix fait dans l'interface — un essai annoncé « sur un autre modèle » n'aurait rien testé de tel.
- **Diagnostic 1008 discriminant** : `scripts/diag_1008_matrix.py` — plan factoriel 2×2 (`historyConfig` × compression) plus une sonde témoin **E** sans réutilisation de handle. Le script refuse de conclure « cycle de vie » quand les quatre cases échouent, affiche le modèle réellement demandé, le code de fermeture et le lieu de la défaillance par sonde.

## Validation et limites

- Build Windows officiel exécuté avec Python 3.11.9 dans `%TEMP%` : layout PyInstaller, application, ZIP portable, modèles ONNX, SHA-256 et smoke test de `Jarvis.exe` validés. Aucun installateur Inno Setup n'a été produit.
- Suite complète sur le commit final de cette campagne : **1647 réussis, 9 ignorés, 708 sous-tests réussis, 0 échec** en 994,89 s. 23 avertissements (dépréciations Qt et avertissements de décodage UTF-8 de lecteurs de sous-processus sur la page de code Windows) restent à traiter séparément.
- Tests API réels en mode texte : S1 et l'outil ont passé certains runs; un scénario minimal texte → coupure sans handle → rejeu → rappel du prénom a réussi. Le protocole multi-scénario échoue encore de manière intermittente sur erreurs 1008, timeouts de handshake/seed et reprise par handle; une tentative audio `auto` s'est arrêtée avant S1 sur `1011 keepalive ping timeout`. En mode `text`, le verdict audio est maintenant `NON TESTABLE`, jamais `PASS`.
- La clé est disponible dans la configuration locale, mais aucun secret Gemini n'est configuré sur GitHub. Le workflow réel doit donc échouer explicitement comme non concluant jusqu'à configuration sûre d'un secret valide.
- Les profils natifs ne sont pas encore parfaitement reproductibles sous charge (certains dépassent ponctuellement 150 ms/20 %); un profil final complet a toutefois passé tous les seuils. La candidate **n'est pas prête pour une release stable** tant que la reprise API réelle, le signal CI externe et la marge de performance ne sont pas conclusifs.

## État de la validation au 2026-10-27

- **Hors ligne (vérifié)** : 1784 tests réussis, 9 ignorés (1 contrainte Python ≥ 3.12, 8 absence d'`openwakeword`), 713 sous-tests, 0 échec ; `ruff` sans erreur ; `compileall` sans erreur. Aucun test n'a été rendu moins exigeant : les nouveaux tests ÉCHOUENT sur le code d'origine (test de mutation), et un jeu de tests de câblage garantit que la CI exécute bien le code corrigé.
- **Verrou de version** : le test `test_version.py::test_release_version_is_1_9_0` impose `1.9.0`, et `CHANNEL` reste `dev`.
- **Gemini Live réel : NON TESTABLE depuis l'environnement de préparation.** Aucune clé n'y est disponible ; les validateurs sortent en code 2 avec `NON TESTABLE`. Aucun scénario S1-S6 n'est déclaré validé.
- **CI** : le job `Validate Gemini Live (reel)` reste rouge. L'étape « Conclusion » refuse correctement tout `FAIL` et tout `NON TESTABLE` ; elle n'a pas été assouplie.
- **Blocage de publication** : l'environnement de préparation ne dispose d'aucun accès en écriture au dépôt. Les correctifs sont donc livrés sous forme de commits locaux et de patchs vérifiés, à pousser par une personne disposant de ce droit.

> **Cette candidate n'est pas publiable en l'état.** Les correctifs de validation sont prêts et testés, mais la validation Gemini Live réelle (S1-S6, dont S4 et S6) reste à exécuter sur le SHA final. Aucun tag `v1.9.0` ne doit être créé avant que le job de validation réelle soit vert sur ce SHA.
