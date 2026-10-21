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
- Tests Dashboard unitaires, intégration et perf exécutés avec moteurs/données simulés; ils ne valident pas l'API Gemini réelle. Le protocole natif Windows a passé un profil complet (ouverture 448 ms, contenu 891 ms, latence max 144 ms, OnePage complet 787 ms, CPU poussé 18,75 %, Dashboard masqué 0 %), mais d'autres répétitions natives ont dépassé ponctuellement les seuils sous charge.
- Tests API réels : S1/S2 et un scénario minimal texte → reconnexion fraîche → rappel du contexte ont réussi lors d'exécutions distinctes; le test multi-scénario reste en échec sur reprises/1008, timeouts de handshake et seed non confirmé. Une tentative audio `auto` a échoué avant S1 sur `1011 keepalive ping timeout`. Les outils ont passé certains runs. Le mode texte marque maintenant l'audio `NON TESTABLE`, jamais `PASS`.
- La clé est disponible dans la configuration locale, mais aucun secret Gemini n'est configuré sur GitHub. La validation réelle de CI ne peut donc pas passer sans prérequis externe.
- La suite complète doit être exécutée sur le commit final; les corrections des courses audio doivent rester reproductibles. En l'état, cette candidate **n'est pas prête pour une release stable**.
