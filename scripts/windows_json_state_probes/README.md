# Sondages Windows — persistance JSON atomique (`src/json_state.py`)

**Outils de diagnostic, PAS des tests** : rien ici n'est collecté par pytest
(les noms ne suivent pas la convention `test_*.py`). Ils reproduisent et
prouvent les contrats Win32 sur lesquels repose `src/json_state.py`.
Exécutables depuis la racine du dépôt sous Windows (Python 3.11, x64) :

```bat
python scripts\windows_json_state_probes\_probe13.py
python scripts\windows_json_state_probes\_probe14.py
python scripts\windows_json_state_probes\_probe15.py
python scripts\windows_json_state_probes\_smoke_posix_rename.py
```

## Ce que chaque sondage démontre

| Fichier | Question posée | Résultat (Windows 10+, x64) |
|---|---|---|
| `_probe13.py` | Un lecteur qui **refuse** `FILE_SHARE_DELETE` bloque-t-il le renommage POSIX `FileRenameInfoEx` ? | OUI (WinError 32) — d'où la règle : **tous** les lecteurs doivent partager le suppression, sinon même le repli POSIX échoue. |
| `_probe14.py` | Le renommage POSIX fonctionne-t-il quand la destination **n'existe pas encore** (premier `save_dict`) ? Et sur un répertoire / chemin relatif ? | Destination absente : OK. |
| `_probe15.py` | Que se passe-t-il **réellement** pour une destination-répertoire (Q2) et un chemin relatif (Q3) ? | Q2 : le renommage POSIX signale « succès » mais **détruit la source** → garde `isdir` obligatoire. Q3 : la source est perdue → `abspath` obligatoire. |
| `_smoke_posix_rename.py` | Le bug historique du terminateur NUL (dépend du tas) peut-il revenir ? | 120 remplacements atomiques sous tas souillé × N exécutions : 0 trou, 0 fichier parasite, destination toujours exacte. |

Ces sondages sont les preuves citées dans la docstring de `src/json_state.py`
(`_probe13`, `_probe15`) et dans `CHANGELOG.md` (entrée « Persistance JSON
Windows »). Le correctif réel vit dans `src/json_state.py` ; sa couverture de
regression est dans `tests/test_json_state.py` (34 tests, suite ordinaire).
