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
- `LICENSE.txt`, `THIRD_PARTY_NOTICES.txt` (généré par `packaging/collect_notices.py` à partir des paquets installés : licences complètes de Python, numpy/OpenBLAS, scipy, GEOS, Qt/PySide6 + textes LGPLv3/GPLv3, bootloader PyInstaller...), `README.md` : copiés par la fin du spec, donc présents dans le zip quel que soit le script qui lance PyInstaller ;
- `_internal\` : runtime Python, bibliothèques, `profiles\`.

Validé sous Windows le 2026-10-15 (Python 3.12.10) : tests, PyInstaller, CLI gelée, zip. Le GUI gelé reste à essayer sous Windows. L'ajout des notices au spec a été vérifié par un build Linux le 2026-10-15 (les quatre fichiers sont à la racine de `dist/ArborFDM/`), pas encore sous Windows.

Pour un build reproductible, utiliser les versions figées de la release : `pip install -c packaging/requirements-build.txt ".[gui,dev]"` (fichier de contraintes ; roues Windows cp312 vérifiées disponibles pour toutes ces versions).

## Release GitHub (`.github/workflows/release.yml`)

1. Mettre à jour `arbor/__init__.py` (`__version__`) et `CHANGELOG.md`, commiter.
2. `git tag v0.3.0` puis `git push origin v0.3.0`. Sans pousser de tag : onglet *Actions*, workflow *release*, *Run workflow* sur `main` avec le champ `tag` (ex. `v0.3.0`) ; le tag est créé sur ce commit à la publication de la release.
3. Le workflow (runner `windows-latest`, Python 3.12) installe les versions de `packaging/requirements-build.txt`, vérifie que le tag correspond à la version du paquet, lance les tests CLI/export, PyInstaller, un test de la CLI gelée, crée le zip, `SHA256SUMS.txt`, et une **release brouillon** avec le zip, les sommes et `THIRD_PARTY_NOTICES.txt`. Le zip est aussi disponible 30 jours en artefact du workflow.
4. Relire la release brouillon (notes, fichiers), tester le zip sur une machine Windows, puis cliquer *Publish release*.

`tests.yml` lance la suite complète sur Ubuntu (Python 3.12, 3.13, 3.14) et Windows (3.12) à chaque push sur `main` et chaque pull request. Ces deux workflows ont été validés localement (`actionlint` 1.7.12, analyse YAML) mais **n'ont jamais tourné sur GitHub** : le premier passage peut demander des ajustements (bibliothèques système Qt sous Linux, durée).

## SmartScreen, antivirus et signature

### Pourquoi des alertes

- **SmartScreen** (« Windows a protégé votre ordinateur ») : tout exécutable non signé, ou signé par un éditeur sans réputation, téléchargé depuis Internet. Ce n'est pas une détection de virus. L'utilisateur clique *Informations complémentaires*, puis *Exécuter quand même*.
- **Faux positifs antivirus** : les exécutables PyInstaller partagent le même bootloader que beaucoup de logiciels malveillants écrits en Python ; les moteurs heuristiques / « machine learning » (noms en `!ml`, par exemple `Trojan:Script/Wacatac.H!ml` chez Microsoft Defender, signalé par un utilisateur Reddit sur un outil similaire) les signalent régulièrement. Le mode `onedir` (utilisé ici) déclenche moins d'alertes que `onefile` (qui s'auto-extrait dans un dossier temporaire), et UPX est désactivé (`upx=False`), ce qui évite une autre source classique de faux positifs.

### Que faire à chaque release

1. Publier `SHA256SUMS.txt` (fait par `release.yml`) et ne distribuer que depuis les Releases GitHub.
2. Soumettre le zip à VirusTotal et garder le lien dans les notes de release (transparence ; ne pas promettre « 0 détection »).
3. En cas de détection Microsoft Defender : soumettre le fichier comme faux positif sur https://www.microsoft.com/wdsi/filesubmission (compte Microsoft, catégorie « Software developer »). Les autres éditeurs ont des formulaires équivalents. Délai typique : quelques jours ; la détection peut revenir à la version suivante.
4. Ne jamais demander aux utilisateurs de désactiver leur antivirus ; leur indiquer plutôt de vérifier le SHA-256 et la source du téléchargement, ou d'installer depuis les sources.
5. Option qui réduit les faux positifs : recompiler le bootloader PyInstaller localement (`PYINSTALLER_COMPILE_BOOTLOADER=1 pip install --no-binary pyinstaller pyinstaller`), ce qui change sa signature binaire. Demande un compilateur C (MSVC) dans le workflow ; non fait.

### Signature de code (options, vérifiées le 2026-10-15 sur la page Microsoft « Code signing options »)

| Option | Coût indicatif | Effet SmartScreen | Remarques |
|---|---|---|---|
| Aucune signature (actuel) | 0 | avertissement à chaque nouvelle version | acceptable pour un projet communautaire si c'est expliqué |
| **SignPath Foundation** (programme gratuit pour l'open source) | gratuit | signature de niveau OV via un pipeline géré, réputation progressive | conditions : licence OSI, build dans un CI public (GitHub Actions convient), dossier accepté par la fondation ; recommandé si le projet prend de l'ampleur |
| Azure **Artifact Signing** (ex-Trusted Signing) | ~10 $ / mois | bonne intégration | **particuliers : USA et Canada uniquement** ; organisations : USA, Canada, UE, Royaume-Uni. Donc pas accessible à un particulier en France, seulement via une structure (association, société) |
| Certificat OV (Sectigo, DigiCert, Certum...) | ~150 à 300 $ / an (Certum propose une offre open source moins chère) | réputation SmartScreen à construire (nombre de téléchargements) | la clé doit être sur un token matériel ou un HSM cloud, ce qui complique la signature dans GitHub Actions |
| Certificat EV | 400 $ et plus / an | plus de passe-droit SmartScreen immédiat depuis 2024 | réservé aux entités enregistrées, token matériel ; pas intéressant ici |
| Microsoft Store (MSIX) | gratuit (compte développeur particulier) | pas d'avertissement, le Store signe | demande un paquet MSIX et la soumission au Store ; à étudier plus tard |

La signature n'empêche pas totalement les faux positifs heuristiques mais les réduit, et elle supprime l'écran « éditeur inconnu » une fois la réputation acquise.

## Profils une fois gelé

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

- Pas de signature de code ni d'installeur (zip seulement) : voir ci-dessus.
- Pas d'icône d'application (`icon=` non défini dans le spec).
- La version vient de `arbor/__init__.py` (`__version__`, lue par `pyproject.toml`). `--version` lit les métadonnées pip (`copy_metadata("arborfdm")`) ; si le paquet n'est pas installé dans l'environnement de build, `arbor/cli.py` affiche encore une valeur de repli `0.1.0` en dur (constaté sur le build Linux de contrôle) : à remplacer par `arbor.__version__` (fichier hors périmètre de la tâche release). `build_windows.bat` et `release.yml` installent le paquet, donc affichent la bonne version.
- `pip install .` (non éditable) n'embarque pas `profiles/` dans le paquet : les profils ne sont trouvés qu'avec une installation éditable ou dans le build PyInstaller. Correction possible : déplacer les profils dans `arbor/` (données de paquet) et adapter `PROFILE_DIR`.
- Le nom de paquet Python `arbor` est déjà pris sur PyPI par un autre projet (simulateur neuronal, même nom d'import) : publier sur PyPI demanderait de renommer le paquet importable.

## Versions minimales des dépendances (2026-10-15)

Les minimums de `pyproject.toml` ont été vérifiés dans un venv Python 3.12 créé avec `uv pip install --resolution lowest-direct --only-binary :all:` :

- trimesh 4.0.0 à 4.0.2 : pas de moteur booléen `manifold` ; jusqu'à 4.4 : appelle `Manifold.from_mesh` (supprimé dans manifold3d 3.x) et exige `mapbox_earcut` pour extruder des polygones (les deux dans les aides de test) : minimum 4.5 ;
- manifold3d 3.0 à 3.3.2 : unions en échec (`Error.InvalidConstruction`) : minimum 3.4 ;
- pyqtgraph 0.13.x : `GLViewWidget.projectionMatrix(region, viewport)` absent : minimum 0.14 ;
- rtree manquant : 28 tests échouaient (requêtes de proximité de trimesh dans les tests) : ajouté aux dépendances `dev`.

Résultat de la suite complète avec les minimums (Python 3.12 ; numpy 1.26.0, scipy 1.11.2, trimesh 4.5.0, manifold3d 3.4.0, shapely 2.0.2, networkx 3.0, PySide6 6.6.0, pyqtgraph 0.14.0, PyOpenGL 3.1.5, rtree 1.1.0) : **506 passed, 1 failed, 3 skipped** en 6 min 48. L'échec, `test_app.py::test_open_in_orca_and_preferences_entries` (test en cours d'écriture le 2026-10-15), lève `RuntimeError: Internal C++ object (PySide6.QtWidgets.QMenu) already deleted` sur PySide6 6.6 à 6.11 et passe avec 6.12 : durée de vie du wrapper Python de `QAction.menu()` dans le test, probablement pas un défaut de l'application.

`pip-audit` (2.10.1) le même jour : aucune vulnérabilité connue pour les versions de `packaging/requirements-build.txt` ; pour les minimums, seulement lxml < 6.1 (PYSEC-2026-87, XXE) et pytest < 9.0.3 (PYSEC-2026-1845), deux outils de test, d'où les minimums `lxml>=6.1` et `pytest>=9.0.3` dans `dev`.
