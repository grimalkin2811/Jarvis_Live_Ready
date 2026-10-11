# Rapport candidat Jarvis Live Ready v1.8.0 — 10 octobre 2026

## Verdict

**Candidat localement validé, publication bloquée par la validation Windows/API réelle.**

Aucun tag et aucune release n'ont été créés. Aucun appel Google réel ni scénario
S1–S6 n'a été lancé pendant cet audit Linux.

## Corrections vérifiées localement

- La passerelle background accumule la transcription audio sans jouer l'audio.
- Une réponse ne réussit qu'avec une transcription non vide et un signal terminal
  officiel : `generation_complete`, `turn_complete` ou
  `interaction_status=IDLE`.
- `interrupted`, un flux clos prématurément et un timeout restent des échecs.
- La trace sépare désormais génération terminée, tour terminé, état IDLE,
  interruption, fermeture de session et annulation consécutive au timeout.
- `google-genai` est borné à `>=2.23,<3`, première série du SDK prenant en charge
  la complétion dynamique par `interaction_status` dans `receive()`.
- Simple/medium exécutent réellement Live/BidiGenerateContent; complex exécute
  réellement Generate Content classique. Le harness ne rapporte plus Live pour
  une décision complexe.
- Les tâches terminales ne peuvent plus être écrasées par un résultat tardif.
  L'annulation est enregistrée avant l'annulation de la future.
- Un résultat vide, un document demandé mais absent, ou un fichier absent/vide
  interdit `COMPLETED`.
- Le transport, le signal de fin et les URI de grounding disponibles sont
  persistés avec le résultat.
- Le cycle non-lu → consulté et la récupération après redémarrage sont couverts.

## Cause du timeout réel : portée de la preuve

Le défaut local démontré était l'attente exclusive de `turn_complete`. La
référence Live indique que `generation_complete` arrive une fois tout le contenu
généré et après la dernière transcription, tandis que `turn_complete` peut être
retardé par le temps de lecture audio estimé. Le background ne joue pas l'audio.
Le SDK récent peut également terminer `receive()` sur
`interaction_status=IDLE`.

Les anciennes traces Windows comptaient les messages, l'audio, la transcription
et `turn_complete`, mais pas `generation_complete` ni `interaction_status`.
Elles ne prouvent donc pas rétrospectivement lequel de ces signaux a été reçu.
Le correctif est conforme au protocole officiel, mais son effet sur les sessions
réelles de 698/488/442 messages doit encore être confirmé sous Windows.

## Validation locale

| Contrôle | Verdict | Résultat |
|---|---|---|
| Tests background + harness ciblés | PASS | 67 tests |
| Régressions Live/conversation principales ciblées | PASS | 180 tests |
| Suite non-Qt élargie, hors S1–S6 | PASS | 775 tests + 408 sous-tests |
| Ruff | PASS | dépôt complet |
| compileall | PASS | `src`, `scripts`, `tests` |
| `git diff --check` | PASS | aucun défaut |
| PySide6 installé | PASS partiel | paquet Python installé dans `.venv` |
| Import Qt / suite Qt | NON_TESTABLE | bibliothèque système `libGL.so.1` absente; installation APT impossible faute d'accès au miroir |
| Windows/API réelle | NON_TESTABLE | environnement Linux, aucun appel externe effectué |
| Tâche Live simple réelle | NON_TESTABLE | validation Windows requise |
| Deux tâches Live réelles | NON_TESTABLE | validation Windows requise |
| Google Search réel | NON_TESTABLE | aucun quota/API consommé |
| Gemini 3.8 Flash réel | NON_TESTABLE | aucun quota/API consommé |
| Modèle principal | PASS local | constantes inchangées et tests de non-régression passants |

## Vérification finale requise sous Windows

Dans PowerShell, depuis le dépôt et avec l'environnement prévu pour Jarvis :

```powershell
py -3 -m pip install -r requirements.txt -r requirements-dev.txt
py -3 -m pytest -q
py -3 -m ruff check .
py -3 -m compileall -q src scripts tests
py -3 scripts\validate_v180_real.py --preflight
py -3 scripts\validate_v180_real.py --api
```

Ne lancer `--api` qu'après vérification du quota et une seule fois. Le rapport
doit confirmer :

1. découverte avec transcription et signal terminal explicite ;
2. tâche simple `COMPLETED`, transport Live et résultat non vide ;
3. deux sessions Live indépendantes terminées ;
4. décision complexe et tâche complexe en `GenerateContent` ;
5. fichiers référencés présents et non vides ;
6. non-lu puis vu ;
7. modèle principal encore disponible après les erreurs background.

Les scénarios S1–S6 et l'observation audio/UI manuelle restent des validations
séparées à exécuter uniquement selon le protocole de release existant.
