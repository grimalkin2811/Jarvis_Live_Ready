# Mémoire — édition depuis le Dashboard

Code : `src/memory.py` (méthodes d'administration + migration v2),
`UI/dashboard/memory_service.py` (couche de service, sans Qt),
`UI/dashboard/widgets/memory_widget.py` (formulaires).

## 1. Une seule source de vérité

Le Dashboard utilise le **même** `MemoryManager` SQLite que la voix
(`get_default_memory_manager()`, relu à chaque appel : le Dashboard suit
toujours l'instance de la boucle vocale). `MemoryService` ne garde **aucune**
copie de l'état : chaque lecture interroge la base. Un souvenir créé ou
modifié à la voix (outil `remember`, extraction automatique) apparaît dans le
widget (rafraîchi à la fin des outils mémoire et sur `memory_changed`), et
inversement.

## 2. Fonctions disponibles

| Fonction | Comment | Confirmation |
|---|---|---|
| Parcourir / rechercher | Liste (200 souvenirs les plus récents par défaut, réglable jusqu'à 500 ; total affiché), filtre par catégorie, recherche par mots tolérante aux accents. **Sans effet de bord** (n'incrémente pas le compteur d'accès, contrairement au rappel de Jarvis). | — |
| Consulter | Contenu, catégorie, importance, source, dates, nombre d'accès, sujet. | — |
| Créer | Formulaire (contenu, catégorie, importance 1–5). Si un souvenir **actif** porte déjà le même sujet, rien n'est écrit : choix explicite « Mettre à jour l'existant », « Créer un souvenir distinct » ou « Annuler » (jamais d'écrasement silencieux). | aucune (le choix explicite tient lieu de confirmation en cas de conflit) |
| Modifier | Même formulaire ; résumé des changements. | **oui**, niveau 2 imposé |
| Supprimer | Archivage, exactement comme « oublie… » à la voix (`archived = 1` : Jarvis ne s'en sert plus). | **renforcée**, niveau 3 |
| Historique | Versions antérieures (jusqu'à 20 par souvenir) avec **différences** lisibles mot à mot. | — |
| Restaurer une version | La version actuelle est remplacée **et rejoint l'historique** (restauration réversible). Une version **identique** à la version actuelle n'est pas « restaurée » : rien n'est écrit et le Dashboard l'indique (« Cette version est identique à la version actuelle. »). | **oui**, niveau 2 imposé |
| Intégrité | `PRAGMA quick_check` en lecture seule (page Système). | — |

Aucun JSON ni SQL brut n'est exposé : formulaires structurés uniquement,
validés par `validate_form` (contenu non vide, ≤ 4 000 caractères, catégorie
connue, importance 1–5) **avant** toute écriture, avec message par champ.

## 3. Concurrence et transactions

* **Contrôle de concurrence optimiste** : chaque écriture fournit la version
  « attendue » (`version_of(record)` = contenu, catégorie, importance,
  `updated_at`). Si le souvenir a changé entre-temps (voix, autre instance du
  widget, autre page), **rien n'est écrit** et la version actuelle est
  renvoyée (`conflict=True`). Le widget propose alors : recharger la version
  actuelle, écraser avec la sienne (nouvelle confirmation ; l'ancienne reste
  dans l'historique) ou continuer l'édition.
* `updated_at` est strictement croissant pour un souvenir (résolution à la
  seconde compensée) : deux modifications dans la même seconde restent
  détectées.
* Chaque écriture est une transaction SQLite (`with conn`) protégée par le
  verrou du gestionnaire : en cas d'erreur (base verrouillée, disque plein,
  contrainte), la transaction est annulée, **aucune donnée n'est écrite à
  moitié**, l'erreur est affichée (« rien n'a été modifié ») et la mémoire
  n'est **pas** marquée indisponible pour autant.
* Les colonnes inconnues d'un souvenir sont lues (`SELECT *`) et jamais
  réécrites : les écritures ne touchent que les colonnes éditées.

## 4. Schéma v2 (migration additive)

`MEMORY_SCHEMA_VERSION` passe de 1 à 2 (`_MIGRATIONS[2]`) :

* table `memory_revisions` (id, memory_id, recorded_at, valid_from, content,
  category, importance, archived, source) + index ;
* déclencheur `memories_revision_au` : après toute modification **réelle**
  du contenu, de la catégorie, de l'importance ou de l'archivage — quelle
  qu'en soit l'origine (outil vocal, extraction automatique, Dashboard) —
  l'état **précédent** est enregistré ; historique borné à
  `MEMORY_REVISIONS_KEPT = 20` versions par souvenir ;
* déclencheur `memories_revision_ad` : une suppression **physique** d'un
  souvenir efface aussi son historique (confidentialité).

Aucune donnée existante n'est modifiée. Une version antérieure de Jarvis
ouvrant une base v2 continue de fonctionner (elle ignore la table ; les
déclencheurs restent actifs). La migration a été testée sur une base v1
**réaliste** (créée par le code v1 puis rétrogradée), pas sur une base
synthétique.

## 5. Tests (données isolées uniquement)

`tests/test_dashboard_memory_service.py` — toutes les bases sont créées dans
un dossier temporaire ; un test vérifie que la base réelle de l'utilisateur
n'est jamais utilisée :

* migration v2 et base v1 existante migrée sans perte ;
* création sans écrasement silencieux ; saisie invalide refusée avant écriture ;
* modification → historique + diff ; modification identique non écrite ;
* modification concurrente détectée (même dans la même seconde) ; écrivains
  parallèles : un seul gagne ;
* suppression : version courante exigée, archivage ;
* restauration elle-même réversible ; historique borné ;
* échec d'écriture → annulation + erreur, aucune corruption
  (`PRAGMA quick_check`) ;
* cohérence après redémarrage (nouvelle instance sur la même base) ;
* recherche sans effet de bord, accents ; mémoire désactivée signalée ;
  suppression physique → historique effacé.

## 6. Limites

* Pas de corbeille dans l'interface : un souvenir supprimé (archivé) n'est
  plus listé ; son historique reste en base tant qu'il n'est pas supprimé
  physiquement.
* Pas d'édition des champs techniques (`subject_key`, `source`, compteurs).
