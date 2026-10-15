# ArborFDM : architecture

ArborFDM est une implémentation originale d'un générateur de supports arborescents « hybrides » pour figurines FDM (troncs FDM robustes, pointes de contact fines). Elle s'inspire des fonctionnalités publiques décrites sur la page de TrestleFDM ; aucun code ni ressource de ce produit n'est utilisé ni connu.

## 1. Référence fonctionnelle (page publique TrestleFDM, consultée le 2026-10-15)

| Fonction | Documentée publiquement | Fonctionnement interne |
|---|---|---|
| Slicing couche par couche pour trouver les zones à soutenir | oui | inconnu |
| Îlots, arêtes suspendues, coins des faces plates, bas des bras/jambes | oui | inconnu |
| Pointe fine -> branche -> tronc -> pied à collerette | oui | inconnu |
| Supports posés sur le modèle (bouche, dents, menton) | oui | inconnu |
| Points colorés par raison, marqueurs rouges pour zones dans le vide | oui | inconnu |
| Clic gauche ajoute / clic droit supprime, undo/redo | oui | - |
| Orientation auto, rotation par poignées, échelle mm/% | oui | inconnu |
| Vérification / réparation de maillage | oui | inconnu |
| Presets buse 0,2 / 0,4, densité Light/Normal/Dense, couches 0,12 mm | oui | valeurs non publiées |
| Export 3MF unique (modèle + supports + raft) ou STL | oui | structure 3MF non publiée |
| Testé Bambu Studio, Orca, PrusaSlicer, Cura | affirmé | non vérifiable |
| Calibrage « mesuré sur G-code et impressions réelles » | affirmé | aucune donnée |

## 2. Architectures comparées

| Critère | A. Python + PySide6 + moteur numpy/manifold3d | B. C++ / Qt + CGAL ou manifold | C. Hybride (UI Python, noyau natif) |
|---|---|---|---|
| Temps jusqu'à un moteur testable | court | long | moyen |
| Performance du noyau | bonne si vectorisé ; les booléens et le slicing sont déjà natifs (manifold3d C++, numpy) | excellente | excellente |
| Testabilité | pytest, très simple | gtest, plus lourd | double outillage |
| Packaging Windows | PyInstaller (taille ~150 Mo) | natif compact | complexe |
| Licences | toutes permissives sauf PySide6 (LGPL, liée dynamiquement) | CGAL GPL / commercial | idem A |
| Évolutivité | un module chaud peut être porté en C++/Rust plus tard sans changer les interfaces | - | c'est l'évolution naturelle de A |

**Choix : A, structurée pour évoluer vers C.** Les opérations lourdes (booléens, slicing, ray casting, KD-tree) sont déjà déléguées à des bibliothèques natives. Le moteur n'importe jamais Qt : il est utilisable en CLI, testable seul, et ses étapes peuvent être portées individuellement si un benchmark le justifie.

## 3. Modules

```
arbor/
  core/      mesh_io (import STL/OBJ/3MF), repair (diagnostic/réparation), transform, orientation, project (document + undo/redo)
  engine/    params, types, analysis (slicing), contacts, tree (routage), build (maillage manifold), verify, pipeline
  io/        export (3MF/STL) et relecture de validation
  app/       interface PySide6 + viewport pyqtgraph.opengl (aucune géométrie ici)
  cli.py     ligne de commande
profiles/    profils JSON (héritage), buse 0,2 / 0,4 x Light/Normal/Dense
tests/       pytest, modèles synthétiques
```

Repère unique : millimètres, Z vers le haut, plateau en z = 0.

## 4. Dépendances et licences

| Paquet | Usage | Licence |
|---|---|---|
| numpy, scipy | calcul vectoriel, KD-tree | BSD-3 |
| trimesh | structure de maillage, slicing, ray casting | MIT |
| manifold3d | union booléenne robuste des supports | Apache-2.0 |
| shapely | polygones de couches (différences, aires) | BSD-3 |
| networkx | composantes, graphes (dépendance de trimesh) | BSD-3 |
| PySide6 | interface | LGPL-3.0 (dynamique) |
| pyqtgraph, PyOpenGL | viewport 3D | MIT / BSD |
| pytest, PyInstaller (dev) | tests, packaging | MIT / GPL avec exception bootloader |

rtree et lxml ont été écartés (bibliothèques natives supplémentaires sans bénéfice mesuré).
