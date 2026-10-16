# Rapport des tests exécutés

Date : 2026-10-15. Machine : conteneur Linux, 2 cœurs, 7,8 Gio, Python 3.13.16, numpy 2.5.3, trimesh 5.1.1, manifold3d 3.5.4, PySide6 6.12.0. Tous les chiffres ci-dessous proviennent de commandes réellement exécutées.

## 1. Suite automatisée

Commande : `QT_QPA_PLATFORM=offscreen python3 -m pytest -q -p no:cacheprovider`

Résultat : **640 passed, 1 skipped, 0 failed, 473 s** (2026-10-16, itération 0.3 : raft footprint/bed, surélévation, molette de couches, pourcentages d orientation, supports creux et allégés ; le test sauté vérifie un dossier en lecture seule, sans effet en root).

Les 3 avertissements sont des `RuntimeWarning: invalid value encountered in divide` dans `trimesh/triangles.py` (normales de triangles d'aire nulle), sans effet sur les assertions.

| Fichier | Tests | Couvre |
|---|---|---|
| test_synthetic | 69 | modèles synthétiques (cube, sphère, pente, étagère, pont, bras, figurine, cassés, gros maillage) |
| test_integration | 47 | pipeline complet : surface, étanchéité, fondation, cycles, collisions, couverture, unités, profils, densité, impossibles, édition, annulation, exports |
| test_qa_review | 45 | revue QA indépendante (GUI réelle, exports lib3mf, cas extrêmes, honnêteté de la vérification) |
| test_qa_stages | 39 | étapes isolées, tessellation, petits triangles |
| test_qa_repair | 25 | diagnostic et réparation |
| test_cli | 21 | CLI (`main(argv)` et sous-processus), codes retour, JSON |
| test_analysis | 19 | slicing, îlots, surplombs |
| test_mesh_io | 16 | STL/OBJ/3MF, unités, fichiers invalides |
| test_app | 15 | GUI offscreen : actions, verrouillage, picking, maillage cassé |
| test_contacts, test_contact_fixes | 15 + 15 | placement, indépendance à la tessellation, mémoire bornée |
| test_tree_fixes | 14 | collisions, supports courts, raft |
| test_project | 13 | document, undo/redo |
| test_repair | 13 | réparation |
| test_transform | 11 | transformations |
| test_export, test_export_extra | 7 + 9 | 3MF/STL, soudure, relecture |
| test_orientation | 5 | orientations candidates |

Historique sur cette session : 8 échecs au départ (module de vérification absent), puis 0 ; la revue QA a ajouté 18 xfail (défauts réels), tous corrigés ensuite.

## 2. Validations hors pytest

- **3MF** : validé par lib3mf 2.5.0 en mode strict (0 avertissement) et relu par trimesh (objets étanches). Voir `docs/QA_REVIEW.md`.
- **Packaging** : build PyInstaller Linux avec `packaging/arborfdm.spec` (environ 3,5 min, 362 Mo) ; CLI gelée : `generate examples/shelf.stl` code 0, `check broken_hole` code 1, figurine `--auto-orient --repair` code 0 ; GUI gelée lancée 15 s en offscreen sans plantage.
- **Captures réelles** (xvfb, rendu GL) : `docs/screenshots/`.

## 3. Performances mesurées

`python3 scripts/bench.py --sizes 10000 100000 500000 1000000 --repeat 2` (détail : `docs/BENCHMARKS.md`), figurine procédurale, profil 0.4 Normal :

| Faces | Total (s) | Contacts | Erreurs |
|---|---|---|---|
| 11 978 | 1,67 | 86 | 0 |
| 119 612 | 2,94 | 82 | 0 |
| 541 228 | 7,48 | 88 | 0 |
| 982 054 | 13,50 | 87 | 0 |

Mesures QA (getrusage) : figurine 0.4 Normal 2,3 s / 228 Mo ; 0.2 Dense 4,7 s / 348 Mo ; environ 1 M de triangles, CLI de bout en bout 32,9 s / 2,25 Go.

## 4. Non testé

- Interface graphique gelée sous Windows (le build Windows et la CLI gelée ont réussi le 2026-10-15, Python 3.12.10).
- Import dans Bambu Studio, OrcaSlicer, PrusaSlicer, Cura.
- Impressions physiques : aucune. Banc d'essai prêt (`scripts/make_tip_bench.py`, protocole `docs/PHYSICAL_TESTS.md`).
- Fluidité du rendu sur une vraie carte graphique.
