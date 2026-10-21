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

## Validation et limites

- Build Windows officiel exécuté avec Python 3.11.9 dans `%TEMP%` : layout PyInstaller, application, ZIP portable, modèles ONNX, SHA-256 et smoke test de `Jarvis.exe` validés. Aucun installateur Inno Setup n'a été produit.
- Suite complète sur le commit final de cette campagne : **1647 réussis, 9 ignorés, 708 sous-tests réussis, 0 échec** en 994,89 s. 23 avertissements (dépréciations Qt et avertissements de décodage UTF-8 de lecteurs de sous-processus sur la page de code Windows) restent à traiter séparément.
- Tests API réels en mode texte : S1 et l'outil ont passé certains runs; un scénario minimal texte → coupure sans handle → rejeu → rappel du prénom a réussi. Le protocole multi-scénario échoue encore de manière intermittente sur erreurs 1008, timeouts de handshake/seed et reprise par handle; une tentative audio `auto` s'est arrêtée avant S1 sur `1011 keepalive ping timeout`. En mode `text`, le verdict audio est maintenant `NON TESTABLE`, jamais `PASS`.
- La clé est disponible dans la configuration locale, mais aucun secret Gemini n'est configuré sur GitHub. Le workflow réel doit donc échouer explicitement comme non concluant jusqu'à configuration sûre d'un secret valide.
- Les profils natifs ne sont pas encore parfaitement reproductibles sous charge (certains dépassent ponctuellement 150 ms/20 %); un profil final complet a toutefois passé tous les seuils. La candidate **n'est pas prête pour une release stable** tant que la reprise API réelle, le signal CI externe et la marge de performance ne sont pas conclusifs.
