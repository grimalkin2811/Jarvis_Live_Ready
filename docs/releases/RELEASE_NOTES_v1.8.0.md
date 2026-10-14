# Notes de release — Jarvis Live Ready v1.8.0

> **Provenance et statut.** Ce document conserve le texte exact des notes de la
> release GitHub **v1.8.0**, qui était demeurée en **brouillon** (`draft: true`)
> avec **aucun actif** attaché. L'objet de release en brouillon a été supprimé le
> 2026-10-14 pour clore la finalisation administrative, après archivage des
> présentes notes dans le dépôt.
>
> Le **tag `v1.8.0` est conservé** et n'a subi aucune modification : tag annoté
> `3b6a091bc9a96f5a2be8967f05cb0d831917465b`, pointant sur le commit
> `1c41df135a0a1d1a3943594ec11a7234c98fd974`.
>
> Texte récupéré via l'API GitHub (`GET /repos/grimalkin2811/Jarvis_Live_Ready/releases/409685643`)
> **avant** la suppression de l'objet de release.
> SHA-256 du corps d'origine : `f7478a21219531ed0d7881a1acfe347494f4f1c2f0b3442935df78bfab62d03f`.
>
> Le corps original est reproduit **verbatim** ci-dessous, sans aucune modification.

---

> **Publication suspendue :** le workflow automatique déclenché par le tag a échoué avant la construction des artefacts Windows, car le job installe `requirements.txt` mais la découverte de `tests/test_background_tasks.py` requiert `pytest`. Aucun binaire v1.8.0 n’a donc été produit ni joint. Le tag reste immuable sur le commit candidat; cette release demeure en brouillon jusqu’à résolution explicite de ce blocage de publication.

## Jarvis Live Ready v1.8.0

Cette version ajoute un système complet de tâches d’arrière-plan asynchrones, isolé de la conversation principale Gemini 2.5 Flash Native Audio.

### Nouveautés

- Task Manager dédié avec thread et boucle asyncio propres, états `QUEUED` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED`, progression, persistance et résultats non lus.
- Routage dynamique : tâches simples et moyennes via Gemini 3 Live (`Live/BidiGenerateContent`), tâches complexes via Gemini 3.8 Flash (`GenerateContent`).
- Découverte dynamique du modèle Gemini 3 Live et validation AUDIO → transcription de sortie → signal terminal officiel.
- Exécution concurrente sans transfert de ressources asyncio entre boucles.
- Gestion des documents, sources, notifications silencieuses et interface Qt des tâches.
- Protection locale des quotas Gemini 3.8 (RPM, RPD, TPM et concurrence) avec comptabilisation persistante des appels réellement tentés.

### Robustesse et diagnostics

- Classification distincte des quotas fournisseur, indisponibilités externes, saturations Live, timeouts et erreurs de protocole.
- Une erreur explicite `You exceeded your current quota` est conservée comme `EXTERNAL_QUOTA` et n’est plus réessayée inutilement.
- Les réponses Live exigent une transcription non vide et un signal terminal valide ; les réponses partielles ou interrompues restent des échecs.
- Le validateur Google Search ne confirme désormais le grounding qu’en présence d’une source Web HTTP/HTTPS structurée et exploitable. Une réponse textuelle seule n’est pas une preuve.
- Le code de sortie du validateur donne priorité à tout véritable `FAIL`, même en présence de contrôles `NON_TESTABLE`.

### Validation effectuée

- Suite Windows complète exécutée sur un commit candidat antérieur : **1203 tests réussis, 3 ignorés**, avec **610 sous-tests réussis**.
- Tests Windows ciblés des tâches d’arrière-plan et du validateur réussis après le correctif quota.
- Sur le commit final, tests Arena ciblés du validateur : **15 réussis**.
- Sur le commit final, tests Arena des tâches d’arrière-plan et du validateur : **75 réussis**.
- Ruff, `compileall` et `git diff --check` réussis.
- Préflight Windows réussi.
- Une tâche complexe réelle Gemini 3.8 Flash a terminé avec succès.

### Limite connue de validation

La validation API v1.8 complète n’a pas été confirmée sur ce commit. Une tâche simple réelle Gemini Live a été refusée par le fournisseur avec une fermeture `1011` et le message explicite `You exceeded your current quota`; ce refus est externe et ne constitue pas une preuve de défaut du protocole Live. Le correctif évite désormais toute seconde tentative inutile, mais ne contourne pas le quota Google.

La détection renforcée du grounding est couverte par des tests déterministes, mais un grounding réel complet n’a pas été reconfirmé par un nouvel appel API après ce correctif. La suite Windows complète de dix minutes et les scénarios S1–S6 n’ont pas été relancés sur le commit final.

**Commit publié :** `1c41df135a0a1d1a3943594ec11a7234c98fd974`

