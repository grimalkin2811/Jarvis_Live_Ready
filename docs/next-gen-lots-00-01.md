# Jarvis Next-Gen — lots 00/01 : rapport de travail

## A. Base et périmètre

- Dépôt `grimalkin2811/Jarvis_Live_Ready`, travail sur la branche Arena `arena/8edade8b-jarvis-live-ready` (aucun travail sur `main`). Checkout initial : `ec944e26457b22492b5872ee520d6bb3ce410081`, identique à `origin/main` au contrôle. Arbre initial propre.
- Base avancée par fast-forward à `43ff0029ca1eadfcaca40fd0cbdddb18248e11e8` (`origin/feature/v1.9.0`). Elle descend de `main` et du tag `v1.9.0` (`66c22588a3bb9ff64e5d9ebc33b3caeafc371c56`) et contient 13 commits post-tag, notamment Dashboard et correctifs Live/CI. La branche bêta divergente a été conservée intacte. PR #50 : fermée en brouillon, non fusionnée ; ses acquis se trouvent sur d'autres lignées.
- Version applicative inchangée : `1.9.0`, canal `dev`. Aucun tag ni release créés. Ceci n'est pas une certification de production.

## B. Architecture avant / après

Avant : `src/main.py` et `src/ui.py` assemblaient chacun mémoire persistante, conversation éphémère, scheduler, tâches, audio, Gemini, callbacks et boucle de reconnexion. L'orbe, Desktop et Dashboard utilisaient déjà le même processus UI et le tableau `src.runtime_status.RUNTIME`, notamment pour envoyer du texte à la session vocale.

Après : console et thread vocal UI délèguent à `src.core.CoreRuntime`. L'orbe et Desktop continuent d'utiliser leurs ponts et signaux existants ; le Dashboard observe toujours `RUNTIME` et son handler de texte renvoie au même fournisseur que la voix. Le contexte éphémère est réinitialisé à chaque démarrage, séparément de la mémoire persistante. `RuntimeFactories` permet des doubles sans Qt, audio ou Gemini ; la fabrique de production reste paresseuse. Le protocole attendu du fournisseur reprend encore plusieurs attributs et concepts Live/Gemini : l'injection rend les tests indépendants du SDK mais ne constitue pas un adaptateur universel achevé pour tout fournisseur.

Le runtime possède le cycle de vie (démarrage, connexion/reconnexion, échec et arrêt), un registre de services avec nettoyage LIFO, et un petit bus d'événements typés. Les callbacks du bus sont synchrones sur le thread émetteur : les interfaces Qt doivent utiliser leurs signaux pour passer au thread graphique. `RUNTIME` demeure la source d'état partagée, pas un deuxième modèle d'état concurrent. Les callbacks audio conservent les protections de génération/époque, le barge-in, les notifications, la reprise et la compression propres à GeminiLive. Un seul runtime actif par processus évite la concurrence entre singletons existants. La fermeture demandée depuis l'UI annule le receive asynchrone, ferme la session puis arrête audio, tâches et scheduler ; l'UI signale si le thread n'est pas terminé après trois secondes. L'arrêt normal, l'authentification refusée, les erreurs d'initialisation et les échecs explicites de nettoyage sont différenciés.

Limites : les services historiques restent en partie des singletons ; le registre ne rend pas tous les modules métiers indépendants. Les abonnés du bus doivent rester rapides. L'arrêt d'un thread de tâches n'est attendu que pendant une durée bornée par son API historique ; il n'est pas prouvé que toute opération bloquante externe puisse s'arrêter dans ce délai. Pas de mesure de latence, CPU ou mémoire sur Pentium G5400 : aucun gain de performance n'est revendiqué.

## C. Changements techniques

- `src/core/` : orchestration, hooks, injection, événements, registre et frontière limitée d'actions proposées.
- `src/main.py`, `src/ui.py` : assemblage partagé, raccordement audio/texte/visibilité existant ; pas de second backend Dashboard.
- `src/gemini_live.py` : vérification des propositions Live avant l'appel des outils.
- `src/background_tasks/manager.py` : snapshot et écriture sérialisés sous le même verrou de persistance, pour éviter qu'un ancien snapshot écrase le plus récent.
- `src/tools.py` : arrêt/redémarrage rapportent l'échec de la commande système au lieu d'annoncer un succès à tort.
- `tests/test_core_*.py` et `tests/test_conversation_pipeline.py` : tests isolés et adaptation du test architectural au nouveau lieu d'assemblage ; pas de véritables opérations système.
- `scripts/check_live_verdicts.py` : import inutilisé retiré. Le contrôle CI existant sur les verdicts manquants et les conclusions Live est conservé.
- `requirements-lock/windows-py311.txt` : résolution Windows x64/Python 3.11, avec versions transitives et empreintes ; CI Windows et `scripts/build_windows.ps1` utilisent ce verrou avec `--require-hashes` et `pip check`. Ruff CI est épinglé. Les fichiers `requirements*.txt` restent les spécifications d'entrée souples et `setup.bat` conserve son installation non verrouillée. Un build reproductible signifie ici dépendances verrouillées, pas archive bit-à-bit reproductible (outillage externe, modèles et horodatages ne sont pas verrouillés). Le verrou résout les versions disponibles au moment de sa création ; il exige validation Windows avant d'être déclaré compatible avec le matériel et les versions de SDK en production.

Régénération intentionnelle (installer `uv==0.13.6` dans un environnement d'outillage) :

```sh
uv pip compile --python-platform windows --python-version 3.11 --generate-hashes --no-build -o requirements-lock/windows-py311.txt requirements.txt requirements-dev.txt
```

Sur Windows x64 avec Python 3.11 : `python -m pip install --require-hashes -r requirements-lock/windows-py311.txt`, puis `python -m pip check` et `python -m pytest -q`. La résolution et les roues d'autres versions de Python/OS ne sont pas certifiées. Les changements de contraintes ou la mise à jour du verrou doivent être revus et retestés ensemble.

## D. Tests et référence

Environnement local : Linux Python 3.11 isolé ; PySide6 6.11.3, google-genai 2.34.0, pas de périphériques ni de clé Gemini. Avant modification, `pytest` s'est interrompu à la collecte avec 21 erreurs `libGL.so.1` absent : zéro résultat de test de référence, pas une suite réussie. L'import `sounddevice` échoue aussi sans PortAudio. Les bibliothèques système ne sont pas disponibles dans l'image ; l'installation apt a échoué faute de listes de paquets. Aucun résultat Windows local.

- Suite ciblée après migration : **206 réussis, 5 sous-tests réussis**, comprenant runtime, reconnexion, dispatch Live simulé, verrou de persistance, conversation, tâches, outils et verdicts, sans Qt ni API externe.
- Suite élargie avec 21 modules Qt non collectables exclus et timeout artificiel de 15 s : **1425 réussis, 561 sous-tests réussis, 114 ignorés, 23 échecs** en 330,63 s. Parmi ces échecs : 15 imports Qt sans `libGL.so.1`, 7 tests temporels interrompus par le timeout artificiel, 1 assertion statique devenue obsolète après centralisation (adaptée et repassée dans la suite ciblée). Ces chiffres décrivent cette exécution partielle, non la suite complète et non une référence avant changement.
- `pip check` : réussi dans le venv Linux ; `ruff check src UI launcher scripts tests` : réussi ; `git diff --check` : réussi.
- `python -m src.main --version` : `1.9.0` ; `--smoke-test` non-frozen : code 0, MAIS PortAudio absent et modèles ONNX wake-word absents, explicitement non bloquants en développement. Ce n'est pas un test audio/wake-word réussi.
- Une résolution du verrou Windows a été produite par uv. Un essai pip `--platform win_amd64 --dry-run` exécuté sur Linux a échoué sur un marqueur Linux de `openwakeword` (`tflite-runtime`) : pip évalue certains marqueurs sur l'hôte, donc ce résultat ne prouve ni ne réfute l'installation sous Windows. Une installation propre et complète sous Windows est indispensable.
- Non exécutés ou non conclus : GUI Qt complète, installation/build Windows natifs, micro/haut-parleurs réels, réveil avec modèles, Gemini réel et coûts réseau, parcours utilisateur complet, comparaison de performances. Les succès CI de la release historique ne valident pas ces nouveaux commits. Les validations Live historiques relevées échouaient à leur étape Conclusion ; cause non établie.

## E. Frontière d'autorisation — portée réelle

Le dispatch Gemini Live refuse toute proposition avec `confirm` véridique et la suppression directe `delete_memory`, indépendamment des déclarations de l'IA ; le résultat explicite un besoin d'autorisation et l'outil n'est pas appelé. Les autres propositions passent encore par la politique de mode existante. Ce refus conservateur désactive donc des opérations vocales auparavant possibles, même si l'utilisateur les demande réellement. Les appels directs UI/internes ne passent pas par ce filtre. Il ne s'agit **pas** d'un système complet de permission, ni d'une preuve que tous les effets sensibles sont couverts : les actions sans `confirm`, les chaînes routines/protocoles, origines indirectes, les frontières de données et tous les outils doivent encore être inventoriés et traités. Les contrôles Dashboard existants restent distincts.

## F. Lot 02 à réaliser

Inventaire exhaustif des capacités et effets indirects ; identité d'origine et provenance non falsifiable ; politique centrale par action et arguments ; consentement explicite émis par une interface de confiance (durée, portée, révocation, expiration, anti-rejeu), traitement sûr des routines/scheduler et des changements de contexte ; journal d'audit sans secrets ; tests adversariaux multi-chemins ; validation fonctionnelle UI et ergonomique. Retirer ou remplacer le refus provisoire seulement après branchement réel et validation de ce parcours. Pas de promesse de sécurité globale sur la base de ce lot.

## G. Livraison et limites de décision

Ces modifications constituent une implémentation de la centralisation et une base de CI à valider, pas une acceptation finale des lots sur Windows. Ne pas fusionner ni publier avant CI Windows, tests audio et tests Live réels contrôlés ; vérifier aussi la compatibilité du verrou avec le SDK et le matériel cible. Les mesures matérielles, la validation complète et le lot 02 restent à réaliser.
