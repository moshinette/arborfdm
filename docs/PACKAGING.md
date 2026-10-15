# Packaging

## Lancer depuis les sources

| Système | Commande |
|---|---|
| Linux / macOS | `scripts/run_dev.sh` (GUI), `scripts/run_dev.sh cli generate in.stl -o out.3mf`, `scripts/run_dev.sh test` |
| Windows | `scripts\run_dev.bat`, `scripts\run_dev.bat cli generate in.stl -o out.3mf`, `scripts\run_dev.bat test` |

Au premier lancement, un venv `.venv` est créé à la racine et `pip install -e ".[gui,dev]"` y est exécuté. `ARBOR_VENV=0 scripts/run_dev.sh ...` utilise l'interpréteur courant. Sans script : `python -m arbor ...` (CLI) ou `python -m arbor.app.main` (GUI), avec la racine du dépôt dans `PYTHONPATH`.

## CLI

```
arborfdm-cli generate in.stl -o out.3mf [--profile nozzle_04_normal] [--set key=value ...]
             [--stl-supports s.stl] [--repair] [--auto-orient] [--layout objects|components]
             [--json report.json] [-q]
arborfdm-cli check in.stl [--json report.json]
arborfdm-cli profiles [--json] [--params]
```

Codes retour : 0 succès ; 1 travail fait mais erreurs (vérification des supports, validation du 3MF, ou `check` sur un maillage invalide) ; 2 erreur d'entrée (fichier illisible, profil/paramètre inconnu, maillage invalide sans `--repair`, réparation échouée) ; 130 interruption. La progression et les avertissements vont sur stderr, le reste sur stdout.

## Build Windows (PyInstaller, onedir)

```
scripts\build_windows.bat
```
Étapes : venv `.venv-build`, `pip install ".[gui,dev]"`, tests CLI/export, `pyinstaller packaging\arborfdm.spec`, test du `arborfdm-cli.exe` gelé sur `examples\shelf.stl`, zip `dist\ArborFDM-<version>-windows-x64.zip` (PowerShell `Compress-Archive`).

Résultat `dist\ArborFDM\` :
- `ArborFDM.exe` : GUI, sans console (`console=False`) ;
- `arborfdm-cli.exe` : console ;
- `_internal\` : runtime Python, bibliothèques, `profiles\`.

**Non testé sous Windows.** Le script `.bat` n'a jamais été exécuté.

### Profils une fois gelé

`arbor/engine/params.py` : `PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"`. Avec PyInstaller ≥ 6 en onedir, `__file__` de `arbor.engine.params` vaut `<_MEIPASS>/arbor/engine/params.pyc`, donc `PROFILE_DIR = <_MEIPASS>/profiles` = `_internal/profiles`. Le spec copie `profiles/` à cet endroit. Vérifié sur le build Linux : en renommant `_internal/profiles`, `arborfdm-cli profiles` répond « no profile found » (code 2), donc les profils viennent bien du bundle et pas du dépôt.

Fragilité : `load_profiles()` renvoie un dictionnaire vide si le dossier n'existe pas (glob sur un dossier absent), sans erreur explicite. Un correctif est proposé dans le rapport de l'agent (non appliqué : fichier hors de son périmètre).

## Build de test Linux (fait le 2026-10-15)

```
pip install --break-system-packages pyinstaller        # PyInstaller 6.22.3
python3 -m PyInstaller --noconfirm --clean --distpath <tmp>/dist --workpath <tmp>/build packaging/arborfdm.spec
```
- Python 3.13, PySide6 6.12.0, pyqtgraph 0.14.0 ; build en ~3,5 min, dossier de 362 Mo (456 Mo avant d'exclure sklearn, pandas, skimage, sympy, cryptography, lxml, que trimesh importe en option).
- `arborfdm-cli --version`, `profiles`, `check examples/broken_hole.stl` (code 1 attendu), `generate examples/shelf.stl -o shelf.3mf --stl-supports sup.stl --json r.json` (code 0) ; le 3MF passe `validate_export`, chaque objet relu par trimesh est étanche, le STL aussi. `generate examples/figurine.stl --auto-orient --repair` : code 0.
- GUI : `QT_QPA_PLATFORM=offscreen ArborFDM` reste lancé 15 s sans planter (OpenGL indisponible en offscreen, donc le viewport n'est pas testé).
- Avertissements PyInstaller sans effet observé : imports cachés `pycparser.lextab`, `scipy.special._cdflib` introuvables, DLL Windows (`user32`...) absentes sous Linux.

`ARBOR_NO_GUI=1 pyinstaller packaging/arborfdm.spec` ne construit que la CLI (sans PySide6).

## Limites connues

- Pas de signature de code ni d'installeur (zip seulement) ; SmartScreen avertira probablement au premier lancement.
- Pas d'icône d'application (`icon=` non défini dans le spec).
- La version affichée par `--version` vient des métadonnées pip (`copy_metadata("arborfdm")`) ; si le paquet n'est pas installé dans l'environnement de build, la CLI affiche `0.1.0` en dur.
- Dossiers `.venv/` et `.venv-build/` à ajouter au `.gitignore` (hors périmètre de cette tâche).
