# Intégration historique — suivi persistant

## État initial vérifié (2026-10-14)

- main : `31ca953a78817257c31a6e40cc3b3ff86f6837f3` ; CI Build & Release
  `38622916532` réussie ; aucune PR ouverte ; accès GitHub ADMIN.
- Lecture des protections : HTTP 403 pour l'intégration GitHub ; aucune tentative
  de contournement. Fusion normale uniquement après contrôles verts.
- Notes `docs/releases/RELEASE_NOTES_v1.8.0.md` présentes.
- v1.8.2 publiée, trois assets ; aucun tag ni asset à modifier.
- Session liée à `arena/b23e104f-jarvis-live-ready` : PR séquentielles sur cette
  même branche, commits cohérents, pas de nouvelle branche ni push main.

## Inventaire exhaustif des branches distantes

| Branche (suffixe jarvis-live-ready omis) | Tête auditée | Décision |
|---|---|---|
| arena/01a05d20 | fec5acd | Tâches, activité et outils nouveaux : adaptation, jamais fusion mécanique |
| arena/01a07d57 | 227c57d | Données utiles du catalogue uniquement ; moteur concurrent écarté |
| arena/01a07d5f | ac6c6da | Données utiles du catalogue uniquement ; moteur et pagination anciens écartés |
| arena/01a08c55 | 0203b7e | Packaging déjà intégré (#16), seul merge-back divergent |
| arena/252733a8 | 864bc3c | Notes v1.8.0 strictement identiques à main |
| arena/9076c6a8 | 7416aaa | Ancêtre de main, maintenance déjà intégrée (#44) |
| docs/archive-v1.8.0-release-notes | 97f02a6 | Ancêtre de main (#45) |
| fix/v1.0.2-test-suite | 9c88a20 | Ancêtre de main (#17) |

Toutes les PR historiques listées (dont #10 fermée sans fusion) ont été
recoupées avec les commits et différences des branches encore présentes.
Aucune branche source supprimée : statut de session active non vérifiable.

## Décisions fonctionnelles et limites

### Groupe 1 : activité et tâches

- TODO personnelle persistante SQLite, distincte des tâches Gemini 1.8.
  Dates via timeparse, priorité, terminer/rouvrir, suppression confirmée.
- Connexions transactionnelles **et fermées**, chemins via src.paths.
  Recherche exacte non ambiguë (pas de wildcard SQL ni sélection arbitraire).
- Activité **opt-in** (`JARVIS_ACTIVITY_ENABLED=1`), rétention 30 jours,
  seulement outil/date/succès. Aucun argument, contenu ou message d'erreur privé.
  Le journal est branché après le garde de mode ; un outil bloqué n'est pas exécuté.
- Suppressions exclues des routines ; confirmation booléenne stricte.

### Étapes restantes

1. Valider puis livrer groupe 1 (tests locaux, CI Windows, fusion).
2. Adapter outils système, réseau, fichiers, sauvegarde et presse-papiers ;
   classer aussi météo, films, mail, clavier et lecture de texte.
3. Retenir les presets utiles sans nouveau moteur ; évaluer l'interface.
4. Suite finale, CI main, filiation, intégrité releases/tags, état des branches.

## Validation locale initiale

Environnement virtuel local avec dépendances du projet (sans modification des
requirements). La suite de référence rencontre 14 erreurs de collecte UI :
`libGL.so.1` absente. Ce n'est PAS une suite verte. Tests Windows distants requis.

## Empreintes release v1.8.2 initiales (API GitHub)

| Asset | Octets | SHA-256 |
|---|---:|---|
| Jarvis-v1.8.2-portable.zip | 155198858 | 37f01ba8a8dcd8f3435f361dcb7489f5806effc343f0e0bb71266c9994365ce0 |
| Jarvis-v1.8.2-portable.zip.sha256 | 94 | 12874ef0ae3c8626a8277aeca31fea83af36a074f98cd1581ce690c4a72da378 |
| JarvisSetup-1.8.2.exe | 150746532 | d355a6493dd7170454b3727eaf5426a905e669cc860ea20453f7c3dec1ea9285 |

### Groupe 1 — validation avant PR

- 137 tests ciblés + 423 sous-tests réussis (puis 36 + 399 pour la
  dernière correction : refus fermé si le gestionnaire de sécurité échoue).
- Ruff dépôt entier, compileall, diff --check et check_release_lineage : OK.
- Suite Linux élargie en cours ; les six échecs AppearanceStateIntegration
  observés sont également causés par libGL absente.
- Téléchargement local des assets impossible : domaine de stockage GitHub
  hors liste réseau autorisée. Vérification Windows distante prévue.
- Baseline publique et non secrète des tags/assets conservée dans
  `docs/releases/INTEGRATION_BASELINE.json` pour les reprises et comparaisons.

### Groupe 1 — résultats observés et limites externes

- PR [#46](https://github.com/grimalkin2811/Jarvis_Live_Ready/pull/46),
  commit `140ec8f`. Dernier ciblage : 138 tests + 423 sous-tests réussis.
- Suite Linux élargie : 959 réussis, 114 ignorés, 482 sous-tests réussis ;
  six échecs uniquement libGL, après exclusion des 14 modules non collectables.
- Le workflow Gemini réel est vert mais indique **NON TESTABLE**, faute de clé
  API. Il ne constitue donc pas une validation audio/Gemini réelle.
- Dispatch de Verify Release demandé pour v1.8.2 : HTTP 403 (permission Actions
  absente). Pas de modification de workflow pour contourner cette restriction.
  La preuve d'intégrité accessible reste la comparaison des références Git et
  des métadonnées/empreintes publiées, pas un nouveau lancement des binaires.

### Groupe 2 : outils adaptés, préparation de livraison

- Veille/hibernation/écran et corbeille : booléen de confirmation strict,
  Windows uniquement, erreurs honnêtes, pas de fallback destructif ou forcé.
- WoL : MAC unicast, IPv4 privée/broadcast local, ports 7/9, un seul paquet,
  confirmation, aucune prétention de confirmer le réveil.
- Presse-papiers : capture manuelle confirmée, 20 textes/4000 caractères max,
  RAM uniquement, pas de veilleur ; recopie confirmée, **pas de frappe clavier**.
- Dossiers : nom exact dans la liste blanche, racine du profil exclue, scan
  borné (10000 entrées/3 s), liens sortants exclus, résultat partiel explicite.
- Lecture : nom relatif exact, UTF-8, extensions textuelles limitées, 1 Mio max,
  confirmation de transmission au modèle, aucun choix arbitraire d'un fichier.
- Prévisions 1–7 jours : Open-Meteo, URL fixes, réponse bornée, erreur si données
  invalides ; mocks réseau. Pas de météo réelle validée dans ce bac à sable.
- Sauvegarde ZIP locale bornée à 50 Mio, noms générés sans écrasement, snapshots
  SQLite incluant WAL, routines complètes (journal d'installation inclus).
  Pas de configuration/clé/OAuth. Restauration avec manifeste et SHA-256 vers
  **un dossier neuf de préparation** ; jamais vers les données actives.
  Activation manuelle seulement après fermeture de Jarvis et inspection.
- Tous ces outils passent par `_RAW_TOOL_FUNCTIONS`, `_guard_tool_function`,
  `TOOL_FUNCTIONS`, déclarations statiques ; outils sensibles exclus des routines.

### Autres nouveautés de 01a05d20 — décisions exhaustives

| Élément historique | Classement et justification |
|---|---|
| show_notification / toasts des rappels et minuteurs | Déjà couvert par notify_user, notifications et scheduler actuels ; ancien PowerShell redondant |
| type_text | Déjà couvert par Writing System, avec contrôle d'intention et interrupteurs ; ancien injecteur Unicode incorrect écarté |
| press_key | Écarté : injection arbitraire dans la fenêtre active, possibilité d'exécuter des commandes via Win+R/terminal ; pas de cible/consentement de fenêtre fiable |
| draft_email | Rédaction déjà couverte par Writing System dans le client actif ; ancien mailto tronquait le corps à 5000 caractères et introduisait une seconde politique d'écriture |
| find_something_to_watch | Recherche web/site déjà disponible ; simple ouverture de liens JustWatch, sans véritable moteur de recommandation |
| imdb / allocine / senscritique | Déjà dans SITES sur main |
| justwatch / letterboxd / themoviedb | Écartés comme ajouts de raccourcis redondants : recherche Web existante, pas d'intégration de service nouvelle |
| Hooks main/ui du watcher | Écartés : collecte silencieuse permanente de données privées |
| Ancien wrapper d'activité | Remplacé par journal opt-in dans le garde actuel, sans arguments ni erreurs privées |
| Ancien export JSON partiel | Remplacé : tronquait mémoire/rappels et omettait le WAL ; backup/restore borné sans écrasement |

- Groupe 2, ciblage intermédiaire : 207 tests + 468 sous-tests réussis.
  Contrôle SDK ajouté après détection et correction locale d'un enum numérique
  refusé par Google GenAI (port WoL). Le port reste validé strictement à l'exécution.
- Ruff, compileall, diff --check et filiation : OK ; suite Linux compatible en cours.
- Les validations physiques (hibernation, corbeille, réseau local) sont volontairement
  mockées. Aucune action réelle de ce type n'a été exécutée.

### Livraison groupe 1

PR #46 fusionnée normalement : merge
`26b6ddad83d1d8b3bf659cf84aaf4a6870ad9aa2`.
CI PR `38630511105` : Tests Windows (11 min 15 s) et Lint réussis.
Main récupéré et avancé fast-forward sur la branche de session avant le lot suivant.
CI main `38632176672` déclenchée automatiquement, à vérifier avec la CI finale.

### Contrôles groupe 2 avant PR

- 66 tests ciblés + 444 sous-tests réussis sur les nouveaux outils, sauvegarde,
  météo, déclarations SDK et connexion Gemini mockée (après corrections).
- 207 tests + 468 sous-tests du ciblage élargi précédent réussis.
- Suite compatible Linux en cours, hors modules/classes bloqués par les bibliothèques
  Qt système manquantes. Les sources nouvelles n'ajoutent aucune dépendance externe.
