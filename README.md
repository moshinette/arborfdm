# ArborFDM

Générateur de supports arborescents « hybrides » pour l'impression FDM de figurines et de miniatures : des troncs et branches FDM robustes, qui touchent le modèle uniquement par une pointe fine.

Implémentation originale, inspirée des fonctionnalités publiques de TrestleFDM (aucun code, ressource ou donnée de ce produit n'est utilisé ni connu). Voir `docs/ARCHITECTURE.md` pour la comparaison fonctionnelle.

> État : prototype fonctionnel (0.1.0). Le moteur, l'interface, la CLI et les exports fonctionnent et sont testés automatiquement (voir `docs/TEST_REPORT.md`). **Aucune impression physique n'a encore validé les profils**, et l'import dans les slicers n'a pas été testé dans les logiciels eux-mêmes. Lisez `docs/LIMITATIONS.md`.

## Fonctionnalités

- Import STL (ASCII/binaire), OBJ, 3MF ; dimensions réelles, échelle en mm ou %, rotation, pose sur le plateau, orientations suggérées avec coût de support explicite.
- Diagnostic du maillage (trous, arêtes non-manifold, faces dégénérées, normales incohérentes) et réparation sur une copie, jamais en silence.
- Analyse couche par couche (slicing virtuel) : îlots, surplombs, arêtes suspendues, coins de faces plates, points bas.
- Placement des contacts avec une raison pour chacun (îlot, point bas, coin, arête, surface, manuel), évitement des détails fins.
- Arbres : pointe fine, branche, fusion, tronc qui s'épaissit, pied large à collerette ; entretoises, raft, supports posés sur le modèle (option), supports courts pour les faibles dégagements.
- Vérification après génération : contacts non soutenus, zones dans le vide, supports flottants, branches déconnectées, troncs instables, collisions avec le modèle, maillage invalide.
- Édition manuelle (clic gauche ajoute, clic droit supprime), undo/redo, régénération des branches seules.
- Profils buse 0,2 / 0,4 mm, Light / Normal / Dense, modifiables dans les paramètres avancés.
- Export 3MF (modèle + supports + raft, objets séparés) et STL ; relecture de validation après écriture.
- CLI scriptable.

## Installation (développement)

Prérequis : Python 3.10 ou plus récent.

```
git clone <dépôt> arborfdm
cd arborfdm
python -m venv .venv
# Windows : .venv\Scripts\activate    Linux/macOS : source .venv/bin/activate
pip install -e ".[gui,dev]"
```

Lancement :

```
scripts\run_dev.bat          # Windows, interface
./scripts/run_dev.sh         # Linux/macOS, interface
python -m arbor --help       # CLI
```

## Utilisation rapide (CLI)

```
python -m arbor profiles
python -m arbor check examples/figurine.stl
python -m arbor generate examples/figurine.stl -o out/figurine.3mf --profile nozzle_04_normal --json out/report.json
python -m arbor generate model.stl -o out.3mf --profile nozzle_02_dense --set tip_diameter=0.3 --repair --auto-orient
```

Codes retour : 0 succès, 1 fichier écrit mais erreurs de vérification, 2 erreur d'entrée, 130 interruption.

## Version Windows autonome

`scripts\build_windows.bat` crée un environnement, installe les dépendances, lance PyInstaller (`packaging/arborfdm.spec`) et produit `dist\ArborFDM-<version>-windows-x64.zip` contenant `ArborFDM.exe` (interface) et `arborfdm-cli.exe`. Aucune installation de Python n'est nécessaire sur le poste cible. Voir `docs/PACKAGING.md`. Ce script n'a pas encore été exécuté sous Windows (le spec a été validé par un build Linux).

## Dans le slicer

Les supports sont dans le fichier : **désactivez les supports automatiques du slicer**. Dans PrusaSlicer, OrcaSlicer et Bambu Studio, répondez **Oui** à la question « objet multi-pièces » pour que le modèle et ses supports restent positionnés ensemble. Détails et différences entre slicers : `docs/SLICERS.md`.

## Tests

```
QT_QPA_PLATFORM=offscreen python -m pytest -q        # Linux / CI
set QT_QPA_PLATFORM=offscreen && python -m pytest -q # Windows
python scripts/bench.py                              # benchmarks -> docs/BENCHMARKS.md
python scripts/make_tip_bench.py --supports          # banc d'essai imprimable
```

## Documentation

| Fichier | Contenu |
|---|---|
| `docs/USER_GUIDE.md` | Guide utilisateur de l'interface |
| `docs/ALGORITHM.md` | Fonctionnement du moteur |
| `docs/ARCHITECTURE.md` | Architecture, choix, dépendances, licences |
| `docs/LIMITATIONS.md` | Limitations connues |
| `docs/TEST_REPORT.md` | Tests réellement exécutés |
| `docs/QA_REVIEW.md` | Revue critique indépendante et suivi des défauts |
| `docs/BENCHMARKS.md` | Performances mesurées |
| `docs/SLICERS.md` | Compatibilité slicers |
| `docs/PACKAGING.md` | Packaging Windows |
| `docs/PHYSICAL_TESTS.md` | Protocole d'essais d'impression (à réaliser) |

## Licence

MIT pour le code d'ArborFDM. Dépendances et licences : `docs/ARCHITECTURE.md` (PySide6 est sous LGPL, lié dynamiquement).
