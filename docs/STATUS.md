# État du projet

Pour reprendre après une interruption : lire ce fichier, puis `docs/ROADMAP.md` (ce qui est fait et ce qui reste) et `CHANGELOG.md` (section 0.3.0).

Dernière mise à jour : 2026-10-16.

## Dépôt

- Branche : `angirard/arborfdm-v0.2` (sortie de `git branch --show-current`).
- Dernier commit : `aa2e8f8 Hands and tip crowns after 2nd print: island anchoring ring (overnight session), min tip separation = base + line width, single central row on narrow parts, separate 3MF layout by default with a raft; 607 tests`.
- Itération 0.3 : modifications non commitées à la rédaction de ce fichier.

## Itération 0.3 : fait

- Raft `footprint` par défaut, avec modes `bed` et `feet`, jeu de décollement `raft_release_gap`.
- Surélévation du modèle au-dessus du raft, automatique (`raft_lift_auto`) ou manuelle (`raft_lift`).
- Supports creux (`hollow_supports`) et supports allégés dans le trancheur (`light_slicer_supports`), tous deux désactivés par défaut.
- Pourcentage de surface en surplomb par orientation, pastille colorée.
- Molette sur le curseur « Cut ».
- Libellés et infobulles de tous les nouveaux paramètres.
- Avertissement d'export quand les réglages par objet ne peuvent pas s'appliquer.
- Documentation : `docs/ALGORITHM.md` (sections 4b et 4c), `docs/ROADMAP.md`, `docs/USER_GUIDE.md`, `docs/LIMITATIONS.md`, `CHANGELOG.md`.

## Reste à faire

1. Valider par impression : raft `footprint` (retrait, jeu de décollement), surélévation automatique, supports creux, supports allégés.
2. Réglages par pièce dans un seul objet 3MF : impossible aujourd'hui quand le modèle repose sur le raft (`arbor/io/export.py`).
3. Essayer l'interface graphique sur un vrai Windows (interface gelée, `scripts/run_dev.bat`), y compris « Open in OrcaSlicer ».
4. Tests : résultat final dans `docs/TEST_REPORT.md`.

## Tests et build

- Tests, depuis la racine du dépôt : `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider` (CI : `.github/workflows/tests.yml`).
- Lancement en développement : `scripts/run_dev.sh` (Linux) ou `scripts/run_dev.bat` (Windows).
- Build Windows : `scripts\build_windows.bat` (Python 3.12 à 3.14, 64 bits, 3.12 recommandé ; voir `docs/PACKAGING.md`).

## Mesures

Les scripts de mesure ont été écrits dans le répertoire temporaire de la session, qui n'est pas persistant. Les chiffres et leur protocole sont documentés dans `docs/ALGORITHM.md` (sections 4b et 4c) et `docs/PHYSICAL_TESTS.md`.

## Attribution des impressions

L'impression suivante, rapportée par l'utilisateur, a été faite avec la version livrée au commit `aa2e8f8`, c'est-à-dire AVANT cette itération. Toute analyse de cette impression se rapporte à `aa2e8f8`, jamais à l'itération 0.3.

## Usage des modèles

Haiku (paramètre `model` de l'outil Agent) a servi à l'audit de code et à cette documentation. Le travail sur la géométrie et le moteur reste sur le modèle par défaut (Opus).

## Mise à jour 2026-10-16 (après l'essai 4)

- Essai 4 (version `aa2e8f8`) : groupe de supports haut et isolé au bout du bras droit renversé par la buse ; dessous plats propres. Détail : `docs/PHYSICAL_TESTS.md`.
- Corrigé dans la 0.3 : entretoisement des groupes d'arbres hauts instables vers les troncs voisins (figurine 0,2 Normal : 7 groupes isolés -> 0, +5,7 % de volume de supports ; 0,4 Normal : 6 -> 1, signalé `isolated_tall_tree`).
- Tests : 646 passed, 1 skipped.
- Prochaine impression à faire : version 0.3 (raft `footprint` + entretoisement), figurine 0,2 Normal ; elle validera ces deux changements ensemble.
- Reste ouvert : couronnes de pointes irrégulières sous les mains.

## Mise à jour 2026-10-16 (après l'essai 5, version 0.3.0)

- Essai 5 : impression complète ; entretoisement et raft `footprint` validés ; marques profondes là où les pointes touchaient (pointes soudées), une pointe restée collée à une main. Détail : `docs/PHYSICAL_TESTS.md`.
- Branche `angirard/overhang-severity-interface-gap` : jeu d'interface (`interface_gap_layers` = 1) et sévérité des surplombs (`mild_overhang_spacing` = 2). Mesures dans `docs/ALGORITHM.md` et `docs/PHYSICAL_TESTS.md`.
- Prochaine impression à faire : figurine 0,2 Normal avec cette branche ; elle dira si les surplombs tiennent sur des pointes non soudées et si les marques sous les bras et le torse diminuent.
- Reste ouvert : marques des 7 pointes soudées par main (choix assumé, essai 2).
