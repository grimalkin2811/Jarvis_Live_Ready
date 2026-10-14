# Intégration historique — suivi persistant

## Synthèse de livraison du code

Les fonctionnalités retenues sont intégrées sur main via trois PR séquentielles :

| PR | Commit de travail | Commit de fusion | Validation PR |
|---|---|---|---|
| [#46](https://github.com/grimalkin2811/Jarvis_Live_Ready/pull/46) | `140ec8f` | `26b6ddad83d1d8b3bf659cf84aaf4a6870ad9aa2` | Windows/Lint verts, run `38630511105` |
| [#47](https://github.com/grimalkin2811/Jarvis_Live_Ready/pull/47) | `c0cd0d6` | `5dbc9fe5f01f3003829362df5b9ac7fb58fd9c49` | Windows/Lint verts, run `38632295383` |
| [#48](https://github.com/grimalkin2811/Jarvis_Live_Ready/pull/48) | `d918282` | `d564c429ed148748caaa63f62b029461fbc369af` | Windows/Lint verts, run `38634041376` |

État produit après #48 : **148 outils** (23 ajoutés) et **24 presets** (12 ajoutés).
Aucun ancien moteur de presets repris. Lancement graphique adapté au catalogue
actuel, sans pagination supplémentaire. Aucun changement des binaires publiés :
les nouveautés sont sur main, dans la section **Non publié** du changelog.

La clôture ajoute un contrôle de préservation reproductible et retire seulement
un helper d'activité sans appelant et deux libellés d'outils anciens ; pas de
nouvelle fonctionnalité ou dépendance. Les tests de cette clôture sont mockés.

### Reprise et preuve de l'état distant final

Ne pas interpréter les paragraphes historiques « en cours » plus bas comme
l'état courant : ils retracent les vérifications à leur date d'exécution.
La CI de main après #46 et après #47 est verte. La CI de PR #48, **incluant les
nouveaux tests Qt**, est verte (Windows 11 min 30 s). Pour l'état du commit
courant, y compris la dernière PR de clôture, utiliser les commandes ci-dessous ;
le dernier commentaire de livraison de cette PR consigne le SHA et les runs finaux.

```bash
git fetch origin main
git rev-parse origin/main
gh pr list --state open
gh run list --branch main --limit 5
python scripts/check_release_lineage.py
python scripts/check_integration_preservation.py
```

La branche de session reste `arena/b23e104f-jarvis-live-ready`. Aucun push direct
main, force push, réécriture, suppression de branche ou modification de tag.
Toutes les têtes historiques listées ci-dessous ont été relues après intégration
et restent identiques à l'inventaire initial. Le statut de leurs sessions n'étant
pas vérifiable, **toutes sont conservées**, même quand leur contenu est déjà couvert.

### Limites externes, sans contournement

- **Recertification binaire de v1.8.2 non exécutée** : téléchargements bloqués
  par l'accès au domaine de stockage GitHub ; dispatch du workflow Verify Release
  refusé HTTP 403. Les identités, dates, tailles, SHA-256 publiés, corps de release,
  tags et notes v1.8.0 sont vérifiés inchangés par API/Git, pas par relecture des
  octets des exécutables. Action externe suffisante : autoriser le déclenchement
  du workflow existant `verify-release.yml` sur la connexion GitHub, ou le lancer
  depuis GitHub avec `tag=v1.8.2`. Ne modifier ni les assets ni les tags.
- **Gemini/audio réel non testable** : workflow sans clé configurée ; ses checks
  verts annoncent explicitement NON TESTABLE. Les tests de protocole et outils
  mockés passent. Une validation Live réelle demande la configuration du secret
  prévu par ce workflow dans GitHub, jamais dans le chat.
- **UI non exécutable localement** : bibliothèques système Qt absentes. Tests Qt
  exécutés en CI Windows ; aucun test local ignoré n'est présenté comme réussi.
- Lecture des protections GitHub refusée HTTP 403, mais toutes les fusions ont
  utilisé l'opération normale, après checks verts, sans mode administrateur.

Ces limites empêchent d'affirmer une nouvelle certification physique/audio et
binaire complète. Aucun travail de code indépendant n'est laissé pour cette raison.

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

### Suivi d'avancement

1. Groupes 1 et 2 : livrés par #46/#47, CI PR et main vertes.
2. Groupe 3 : livré par #48 après suite Windows/Lint verte, moteur actuel conservé.
3. Clôture : contrôle de préservation et nettoyage minimal, suite compatible et
   CI de la dernière PR puis main à vérifier avant le compte rendu de clôture.
4. Pas de suppression des branches historiques ; limites de certification
   externe détaillées dans la synthèse ci-dessus.

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

### Groupe 3 — comparaison complète des catalogues

Douze macros retenues, toutes manuelles et désactivées ; 24 presets au total.
Seulement des données au format actuel : aucun ancien moteur/importeur repris.
Les dépendances et effets de bord sont décrits dans chaque entrée. Le moteur
continue les étapes indépendantes après erreur et rapporte les échecs : comportement
actuel conservé et testé, aucune prétention de succès complet si une étape échoue.

| Source 01a07d57 (14 entrées) | Décision |
|---|---|
| mode travail | Intégré : Poste de travail, fusionné avec le catalogue 5f |
| focus | Intégré comme Pomodoro 25 minutes ; distinct du vrai Mode focus actuel |
| réunion | Intégré : Réunion 45 minutes |
| pause café | Intégré : Pause café 5 minutes |
| capture | Déjà couvert par take_screenshot ; ouvrir « images » après une capture n'apporte pas de garantie sur son emplacement |
| musique | Intégré : Pause musicale (ouverture Spotify, pas lecture prétendue) |
| actualités | Intégré : Lire les actualités |
| bonjour | Briefing actuel + outils date/météo/notes ; ancienne suite sans rendu consolidé, météo Paris imposée, lecture privée des notes non retenue |
| bilan système | Intégré : Bilan du PC (résultat à la voix, absence de batterie signalée) |
| je pars | Intégré : Je pars, manuel, verrouillage annoncé |
| je reviens | Redondant : unmute_audio et heure déjà disponibles ; aucune nouvelle séquence utile |
| mode cinéma | Intégré : Séance cinéma, volume prudent 40 % au lieu de 80 % |
| bonne nuit | Fusionné avec Nuit calme ; luminosité forcée et verrouillage programmé écartés, verrouillage manuel couvert par Je pars |
| réveil | Ouverture du matin couvre les liens matinaux ; audio/radio imposés à 7h30 écartés, actions audio existantes personnalisables |

| Source 01a07d5f (13 entrées) | Décision |
|---|---|
| réveil | Même décision que 57 : pas d'audio/radio automatiques imposés |
| journal du matin | Intégré : Ouverture du matin |
| mode travail | Intégré : Poste de travail (VS Code, GitHub, volume) |
| pomodoro | Fusionné avec Pomodoro 25 minutes, durée modifiable, pas deux doublons 25/50 minutes |
| pause café | Fusionné avec Pause café 5 minutes |
| déjeuner | Déjà couvert par Pause déjeuner ; ancien mute/bureau/minuteur 45 min redondant avec actions de pause/réunion |
| reprise d'après-midi | Redondant avec Poste de travail et actions audio ; pas de second lancement planifié de VS Code |
| bilan du soir | Fin de journée et Préparer demain déjà présents ; notes factices quotidiennes + Trello imposé écartés |
| mode détente | Fermeture forcée de Teams/Slack écartée (travail non sauvegardé) ; sites et Pause musicale existants |
| nuit calme | Intégré : Nuit calme |
| mode gaming | Vrai Mode jeu déjà présent et configurable ; ouvrir Steam + volume 70 % n'est pas une optimisation système |
| entretien du PC | Intégré : Entretien guidé du PC ; ouvre Windows, ne supprime rien |
| préparation de la semaine | Revue hebdomadaire actuelle + Ouverture du matin ; création de notes factices et choix Trello écartés |

### Interface : décision pagination

Le code actuel de `UI/routines_dialog.py` fournit déjà un QScrollArea pour
**toutes** les routines, y compris les personnelles au-delà des six raccourcis.
`UI/jarvis_menu.py` réserve ces six emplacements aux macros personnelles actives.
L'ancien menu paginé 5f concurrencerait ce panneau sans rendre une routine
supplémentaire accessible. Il est donc écarté. Tests Qt adaptés pour vérifier
les 24 entrées, l'accès à la dernière et les macros personnelles au-delà de six.
La fonction « Lancer » de 5f est en revanche retenue : bouton par carte du
catalogue, désactivé tant que la routine ne l'est pas, worker hors thread Qt,
signal de résultat en file Qt, protection contre les doubles clics. Aucun code
ancien de suivi du survol/nom tronqué n'est repris. Fermer le panneau n'annule
pas une routine déjà lancée. Aucun changement du solveur anti-chevauchement.

Autres éléments 57/5f : l'installation initiale et les interrupteurs d'activation
sont déjà couverts par le moteur/panneau actuel. `restore_preset_routines`, le
bouton de réinstallation forcée et leurs tombstones concurrents sont écartés :
ils réintroduiraient une seconde politique de suppression/résurrection sans
confirmation. Le journal `installed_presets` actuel reste l'unique autorité.

### Groupe 2 — suite Linux compatible terminée

1008 tests réussis, 114 ignorés, 6 désélectionnés, 527 sous-tests réussis
(413,87 s). Les 14 modules non collectables ont été exclus explicitement ;
les six méthodes de la classe Qt AppearanceStateIntegration ont été désélectionnées.
Ce résultat ne remplace pas la suite Windows complète de la PR #47
(commit `c0cd0d6`, workflow `38632295383`).

### Groupe 3 — ciblage et couverture

128 tests + 480 sous-tests réussis : catalogues, exécution avec mocks, erreurs,
mode bloquant, collisions de noms, suppression/non-résurrection, SDK et backup.
Tests Qt du bouton Lancer et des 24 cartes ajoutés, exécution confiée à Windows.
Le pipeline Linux final compatible est également lancé (pas présenté comme suite UI).
Ruff et compileall réussis ; recherche de signatures usuelles de secrets dans
le diff cumulé : aucune correspondance ; aucun artefact local destiné à Git.

Les bibliothèques système Qt manquantes identifiées par ldd sont libGL, libEGL,
libxkbcommon et libdbus. Dépendances Python installées dans .venv uniquement ;
`pip check` réussit (Google GenAI 2.30.1 conforme à >=2.23,<3).

### Livraison groupe 2 et validation groupe 3 avant PR

- PR [#47](https://github.com/grimalkin2811/Jarvis_Live_Ready/pull/47) fusionnée
  normalement, merge `5dbc9fe5f01f3003829362df5b9ac7fb58fd9c49`.
  Tests Windows 11 min 16 s et Lint réussis (`38632295383`).
- CI main du groupe 1 `38632176672` : réussite confirmée.
- Branche de session avancée fast-forward sur main après #47 avant commit suivant.
- Suite Linux finale compatible : **1026 réussis, 114 ignorés, 6 désélectionnés,
  539 sous-tests réussis**, 414,14 s. Même exclusions Qt explicites que précédemment.
- UI nouvelle : tests de lancement différé, double clic, erreur de routine,
  échec de création du thread, fermeture/destruction du panneau. Tous les effets
  sont mockés ; exécution de ces tests Qt sur Windows requise avant fusion.
- Contrôle API/refs répété : notes v1.8.0, tags et trois assets v1.8.2 inchangés.
  Filiation v1.8.2 vérifiée séparément : OK. Aucun brouillon v1.8.0 présent.

### Dernier ciblage de clôture

- Suite Linux compatible après ajout du vérificateur : **1037 réussis,
  114 ignorés, 6 désélectionnés, 539 sous-tests**, 413,65 s.
- Les 114 skips sont tous Qt/OpenGL ; 14 modules Qt exclus à la collecte.
- Après nettoyage du helper/libellés d'activité : 37 tests ciblés réussis
  (TODO, activité, vérificateur), et 48 + 444 sous-tests sur le ciblage élargi.
- `check_integration_preservation.py` réussit en lecture seule sur GitHub :
  aucune modification de release/assets/tags/notes historiques détectée.
  Ses tests négatifs couvrent changement d'ID, taille, digest, notes, tag,
  ajout/retrait d'asset, brouillon et absence d'accès (code 2, jamais faux succès).
- L'état de CI du commit final sera consigné dans le commentaire de livraison
  de la PR de clôture, afin de ne pas créer une boucle de commits documentaires
  qui changeraient à nouveau le SHA qu'ils prétendent vérifier.

Ciblage final avant PR de clôture : **174 tests + 480 sous-tests réussis**
(outils nouveaux, catalogues, sauvegarde, SDK, version, packaging, vérificateur).
Ruff dépôt entier, compileall, diff --check, filiation HEAD et contrôle de
préservation distant réussis sur cet état.
