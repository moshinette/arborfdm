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
| Plugin intégré à OrcaSlicer | haute | élevé, dépend du projet Orca (AGPL, C++) | élevé | Possible grâce à la licence MIT, mais demande une contribution amont |
| Versions macOS / Linux empaquetées | moyenne | 2-3 jours chacune | moyen | Le code tourne déjà sous Linux depuis les sources ; il manque le packaging et des tests réels |

## Ce qui n'est volontairement pas prévu

- Support en cube, supports creux : peu de retours, et l'idée du cube entre en conflit avec le raft d'une pièce.
- Effets visuels supplémentaires dans l'interface : priorité à la lisibilité.
