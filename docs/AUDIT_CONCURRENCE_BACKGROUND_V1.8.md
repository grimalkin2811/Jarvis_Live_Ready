# Audit ciblé v1.8 — timeout Live et concurrence

## Périmètre

Base auditée : `c85afd0e75e5ad3c1e021eabaa23dd667018810c`.
Le modèle principal Native Audio, la découverte Gemini 3 Live et le chemin
classique Gemini 3.8 Flash ne sont pas modifiés.

## Cycle réellement exécuté

1. `create_task` persiste `QUEUED`, puis utilise
   `run_coroutine_threadsafe` vers `jarvis-background-tasks`.
2. `_run_task` attend un slot du sémaphore général (limite 3) sous timeout
   global.
3. `_process_task` passe à `RUNNING` et appelle le Router Live.
4. Le Router résout le modèle déjà validé, ouvre sa propre session Live, envoie
   le prompt et consomme tous les messages jusqu'à la transcription et
   `turn_complete`; le context manager ferme la session.
5. L'Executor ouvre une nouvelle session Live pour simple/medium. Pour complex,
   il réserve le sémaphore 3.8 (limite 1), puis appelle Generate Content.
6. Le résultat et le document éventuel sont persistés avant `COMPLETED`.
   Annulation, timeout ou exception passent à un état terminal et les `finally`
   ferment la session/libèrent la réservation.

## Localisation du timeout observé

Le timeout de 90 secondes est la limite **d'opération modèle**, pas la limite
globale de 240 secondes. Sur le chemin simple, après `Plan validé` et
`Analyse et synthèse` (55 %), le seul await long restant est le cycle Live de
l'Executor : ouverture, envoi, puis `session.receive()` jusqu'à
`turn_complete`. Il n'existe à ce point ni verrou Task Manager détenu, ni
sémaphore 3.8 acquis par la tâche Live.

L'ancien rapport ne conservait toutefois ni le dernier événement Live, ni le
nombre de messages/transcriptions. Il permet donc de localiser l'attente dans
l'opération Live, mais pas de distinguer honnêtement : aucun message fournisseur,
audio sans transcription, transcription partielle sans `turn_complete`, ou
fermeture tardive. Le correctif ajoute ce diagnostic sans augmenter le timeout :
phase, tentative, messages, messages audio, fragments/caractères transcrits,
`turn_complete`, fermeture et dernier événement. Le harness enregistre aussi
une chronologie par tâche (statut, progression et étape).

## Absence de deadlock interne reproduit

Les sessions Live concurrentes utilisent la même boucle worker mais des context
managers WebSocket distincts. Les opérations réseau cèdent la boucle. Le
sémaphore général autorise trois tâches et le sémaphore 3.8 ne concerne que le
chemin classique après routage. Aucun verrou `threading` n'est tenu pendant un
await réseau.

Un test ciblé lance deux cycles Live simulés sur la même boucle : le premier ne
termine jamais, le second reçoit transcription + `turn_complete` et termine. Le
premier expire avec fermeture de session; le second n'est pas bloqué. Les tests
Task Manager vérifient également qu'une tâche en échec n'empêche pas l'autre de
terminer. Cela écarte un deadlock ou un couplage des futures dans le code testé;
ce n'est pas présenté comme une validation du fournisseur réel.

## Cause du refus Flash dans l'ancien scénario

Le scénario réutilisait le même fichier de quota local après le test complexe.
Trois 429 réels avaient correctement consommé trois tentatives. Le deuxième
test complexe pouvait en envoyer deux autres; la garde locale refusait la
suivante à 5 RPM. Le refus était conforme à la politique, mais rendait le test
de concurrence non diagnostique.

Le harness utilise désormais une persistance locale séparée pour le scénario
simultané. Cela ne réinitialise et ne prétend pas réinitialiser le quota Google.
Si le test complexe vient de prouver que 3.8 est indisponible chez le
fournisseur, aucun nouvel appel Flash n'est forcé : deux vraies tâches Live sont
lancées, tandis que la partie Live+Flash est marquée `NON_TESTABLE` avec la
raison fournisseur exacte.

## Classification du rapport

- `EXTERNAL_QUOTA` : 429 / `RESOURCE_EXHAUSTED` HTTP fournisseur → `NON_TESTABLE`.
- `EXTERNAL_SERVICE` : 503 / indisponibilité fournisseur → `NON_TESTABLE`.
- `LIVE_RESOURCE_EXHAUSTED` : fermeture Live 1011 dont le motif indique
  explicitement `Resource has been exhausted` → `NON_TESTABLE`, sans inventer
  la limite Google concernée.
- `LOCAL_QUOTA_GUARD` : RPM/RPD/TPM local → `NON_TESTABLE`, raison locale.
- `TIMEOUT` : délai Live avec trace de phase → `FAIL`, à analyser; jamais PASS.
- `LIVE_PROTOCOL` : flux clos sans transcription/`turn_complete` → `FAIL`.
- `CODE_OR_PROTOCOL` : JSON, configuration ou autre défaut → `FAIL`.

Un document n'est `PASS` que si la tâche est `COMPLETED`, son résultat est non
vide et tous les chemins référencés désignent des fichiers non vides.

## Ce qui reste à vérifier avec l'API réelle

Le run Windows suivant devra examiner la nouvelle trace si un timeout revient.
`phase=réception`, `messages=0` indique une attente fournisseur sans message;
des fragments sans `turn_complete` indiquent une fin de tour fournisseur
incomplète; `session_fermée=True` confirme le nettoyage après annulation. Une
réussite simulée n'est jamais utilisée pour déclarer la concurrence API réelle
validée. S1–S6 est hors périmètre de cet audit.
