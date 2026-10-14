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
