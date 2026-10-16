# Feuille de route

Évaluation des améliorations possibles (2026-10-15), à partir des retours réels : première impression sur A1 mini buse 0,2 (`docs/PHYSICAL_TESTS.md`), fil Reddit d'un outil comparable, revue QA. Notation : **Valeur** pour l'utilisateur, **Coût** technique, **Risque** pour la complexité du logiciel (faible / moyen / élevé).

## Fait dans la version 0.2

| Amélioration | Où |
|---|---|
| Seuil de surplomb aligné sur les trancheurs (débord par couche plafonné à une largeur de ligne, même seuil pour les faces et les couches), recherche sourcée | `docs/research/overhang_angles.md`, `analysis.py` |
| Surplombs colorés par angle avant génération | interface, bouton « Overhangs » |
| Presets utilisateur (enregistrer, partager en .json) | `arbor/core/presets.py` |
| Supports tranchés seuls (`support_floating_region`) et corrections (angles, sphères, entretoises) | `verify.py`, `tree.py`, `build.py` |
| Supports fragiles (`fragile_support`) et difficiles à retirer (`hard_to_remove`) | `verify.py` |
| Statistiques : volume, masse PLA, surface de contact (proxy des marques), hauteur max, élancement | `GenerationResult.stats` |
| Comparaison de configurations (`arborfdm-cli compare`, JSON / Markdown / CSV) | `arbor/core/compare.py` |
| Ouvrir dans OrcaSlicer (export 3MF dédié, supports du slicer désactivés, chemin configurable) | `arbor/io/slicer_launch.py`, interface |
| Édition des contacts : Suppr, Échap, suppressions groupées par raison, carte d'information au survol | interface, `project.py` |
| Raft fin d'une seule pièce, pieds proportionnels à la hauteur, entretoises plus épaisses | moteur, profils |

## Itération 0.3 (raft, couches, orientation, supports creux)

Chiffres : mesures avec OrcaSlicer 2.4.2 en ligne de commande (figurine 0,2 Normal), détaillées dans `docs/ALGORITHM.md` sections 4b et 4c. Aucune impression faite avec cette itération à ce jour. Tests : voir `docs/TEST_REPORT.md`. Suite : `docs/STATUS.md`.

| Fonction | Valeur | Complexité | Risques / dépendances | Tests | État réel |
|---|---|---|---|---|---|
| Raft `footprint` par défaut (`raft_coverage`) : une plaque sous la base et les pieds | haute : base plus solide sans surcoût (9,56 g contre 9,58 g en `feet`) | moyenne | modèle posé sur le raft : export « separate » en un seul objet ; retrait à valider | voir `docs/TEST_REPORT.md` | Partiel : mesuré au trancheur, impression à faire |
| Jeu de décollement `raft_release_gap` (une couche par défaut, 0 = soudé) | moyenne : retrait du modèle sans outil | faible | sans jeu, la base est soudée au raft | voir `docs/TEST_REPORT.md` | Partiel : G-code vérifié, impression à faire |
| Mode `bed` (rectangle englobant, jamais le plateau entier) | faible : plus de matière (9,73 g), pas de meilleure tenue | faible | non retenu par défaut | voir `docs/TEST_REPORT.md` | Fait (option), non recommandé |
| Surélévation automatique (`raft_lift_auto`) et manuelle (`raft_lift`, 0 à 5 mm) au-dessus du raft | haute pour sphères, bras et colonnes posés sur un point | élevée | stabilité évaluée seulement par la base plate (25 mm²), seuil empirique ; sans effet en `feet` | voir `docs/TEST_REPORT.md` | Partiel : cas synthétiques mesurés, aucune impression |
| Avertissement explicite à l'export quand les réglages par objet ne s'appliquent pas | moyenne : évite des réglages ignorés en silence | faible | aucune | voir `docs/TEST_REPORT.md` | Fait |
| Molette sur le curseur « Cut » (1 couche, Maj : 10 couches) | moyenne | faible | reconstruction 3D limitée à 60 ms | voir `docs/TEST_REPORT.md` | Fait ; à essayer sur Windows |
| Pourcentage de surface en surplomb par orientation, pastille verte / orange / rouge | haute : comparer les orientations d'un coup d'œil | faible | seuils relatifs et absolus empiriques ; indication, pas garantie | voir `docs/TEST_REPORT.md` | Fait |
| Libellés et infobulles de tous les nouveaux paramètres (onglet Advanced) | moyenne | faible | aucune | voir `docs/TEST_REPORT.md` | Fait ; à vérifier sur Windows |
| Supports creux géométriques (`hollow_supports`, désactivé par défaut) | moyenne : -13 % de filament et -4 % de temps sur les supports | moyenne | rigidité non vérifiée (`fragile_support` ignore le vide) ; sans effet en buse 0,4 | voir `docs/TEST_REPORT.md` | Partiel : mesuré au trancheur, impression à faire |
| Supports allégés dans le trancheur (`light_slicer_supports`, désactivé par défaut) | haute : -31 % de filament et -24 % de temps sur les supports | moyenne | exige raft `feet` ou sans raft ; impossible par objet quand le modèle repose sur le raft | voir `docs/TEST_REPORT.md` | Partiel : mesuré, impression à faire |

Les deux options de supports ne s'additionnent pas (les parois autour du vide coûtent plus que le remplissage faible qu'elles remplacent). Aucune des deux n'est validée par impression.

## Priorité 1 : prochaines étapes recommandées

| Idée | Valeur | Coût | Risque | Notes |
|---|---|---|---|---|
| **Campagne d'impressions** avec le banc d'essai (`scripts/make_tip_bench.py`) : diamètres de pointe 0,2 et 0,4, densités, raft | très haute | 1-2 jours d'impression | nul | Seule façon de valider les profils ; les seuils de `verify.py` sont empiriques |
| **Comparaison dans l'interface** (tableau des profils, réutilise `compare.py`) | haute | 1 jour | faible | La logique existe déjà |
| **Pied/plot élargi automatiquement pour les colonnes posées sur le modèle** signalées fragiles (option « robustesse contre marques ») | haute | 1-2 jours | moyen | Compromis marque/stabilité à exposer comme réglage |
| **Contreventement vers le tronc le plus proche** pour les arbres hauts isolés (aujourd'hui une entretoise n'est créée qu'entre troncs voisins) | haute | 2 jours | moyen | Cause directe du premier échec ; le raft d'une pièce limite le risque en attendant |
| **Profils machine/matériau** (PLA, PETG ; A1 mini, Prusa MK4…) via les presets existants, partagés par la communauté | moyenne | 0,5 jour + contributions | faible | Format déjà prêt ; ne pas prétendre qu'ils sont validés |

## Priorité 2

| Idée | Valeur | Coût | Risque | Notes |
|---|---|---|---|---|
| Hauteur de couche des supports différente du modèle dans « Open in OrcaSlicer » (déjà possible en CLI avec `--layout separate`) | moyenne | 0,5 jour | faible | Vérifier en vrai que les pointes touchent encore |
| Déplacement d'un contact par glisser sur la surface | moyenne | 1-2 jours | moyen | Picking déjà accéléré |
| Zones « interdites » peintes (visage, mains) pour l'évitement des détails | haute | 3-5 jours | moyen | Plus fiable que l'heuristique de rugosité actuelle |
| Plusieurs pièces sur un plateau (demande Reddit) | moyenne | 1 semaine | élevé | Touche le document, l'undo, l'export ; à faire après stabilisation |
| Rapport imprimable (PDF/HTML) d'une génération : stats, issues, capture | faible | 1 jour | faible | Utile pour partager sur un forum |

## Priorité 3 / à étudier

| Idée | Valeur | Coût | Risque | Notes |
|---|---|---|---|---|
| Portage du routage en code natif (Rust/C++) | moyenne (gros modèles) | 2-4 semaines | élevé | Les mesures actuelles (13,5 s pour ~1 M de triangles) ne l'imposent pas encore |
| Découpe de la figurine en morceaux avec tenons (demande Reddit) | haute | plusieurs semaines | élevé | Quasiment un second outil |
| Plugin intégré à OrcaSlicer | haute | élevé, dépend du projet Orca (AGPL, C++) | élevé | Licence GPLv3 compatible avec l'AGPL d'OrcaSlicer ; demande une contribution amont |
| Versions macOS / Linux empaquetées | moyenne | 2-3 jours chacune | moyen | Le code tourne déjà sous Linux depuis les sources ; il manque le packaging et des tests réels |

## Ce qui n'est volontairement pas prévu

- Support en cube : peu de retours, et l'idée entre en conflit avec le raft d'une pièce.
- Effets visuels supplémentaires dans l'interface : priorité à la lisibilité.
