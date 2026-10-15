# Licences : inventaire et obligations

Relevé le 2026-10-15 avec `pip-licenses 5.5.5` et les fichiers `*.dist-info/licenses/` des paquets installés (Linux, Python 3.13), complété par l'inspection des roues **Windows** `win_amd64` / cp312 des mêmes versions (`pip download --platform win_amd64`). Le fichier livré avec la release (`THIRD_PARTY_NOTICES.txt`) est régénéré à chaque build par `packaging/collect_notices.py` à partir des paquets réellement installés : c'est lui qui fait foi pour une version donnée.

Ceci n'est pas un avis juridique.

## 1. Code d'ArborFDM

`LICENSE` : MIT (recommandation, choix final au mainteneur, voir § 5). `pyproject.toml` déclare `license = "MIT"` (SPDX, PEP 639).

## 2. Embarqué dans l'exécutable Windows (PyInstaller onedir)

| Paquet | Version testée | Licence (métadonnées réelles) | Code natif embarqué dans la roue Windows | Remarques |
|---|---|---|---|---|
| CPython | 3.12 (build Windows) | PSF-2.0 | zlib, OpenSSL (Apache-2.0), libffi, bzip2, xz, sqlite... (listés dans `LICENSE.txt` de Python) | texte PSF inclus dans les notices |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `libscipy_openblas64_*.dll` : OpenBLAS (BSD-3), LAPACK (BSD-3-Open-MPI), runtime GCC (GPL-3.0+ **avec exception GCC 3.1**) ; `msvcp140` | l'exception GCC autorise la redistribution sans contaminer le programme |
| scipy | 1.18.1 | BSD-3-Clause (+ composants listés : Qhull, DUCC, etc.) | `libscipy_openblas*.dll` (idem numpy) | |
| trimesh | 5.1.1 | MIT | non | |
| manifold3d | 3.5.4 | Apache-2.0 | lié statiquement (Clipper2 BSL-1.0, etc.), `msvcp140` | Apache-2.0 : garder la licence et le fichier AUTHORS (fait par les notices) |
| shapely | 2.2.0 | BSD-3-Clause | **GEOS 3.14 en LGPL-2.1** (`geos.dll`, `geos_c.dll`), `msvcp140` | DLL séparées, donc remplaçables : conforme LGPL avec notice + texte + source |
| networkx | 3.6.1 | BSD-3-Clause | non | |
| PySide6 / PySide6-Essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Qt 6.12 (`Qt6*.dll`, plugins), `opengl32sw.dll` (Mesa llvmpipe, MIT), runtime MSVC | voir § 3 ; la roue ne contient **aucun** texte LGPL (seulement `LicenseRef-Qt-Commercial.txt`), d'où `packaging/licenses/LGPL-3.0.txt` et `GPL-3.0.txt` |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `shiboken6.abi3.dll` | idem |
| pyqtgraph | 0.14.0 | MIT | non | |
| PyOpenGL | 3.1.10 | BSD (licence PyOpenGL) | non (utilise l'`opengl32.dll` du système) | pas de fichier licence dans la roue : lien vers le projet |
| Bootloader PyInstaller | 6.22.3 | GPL-2.0-or-later **avec exception bootloader** | dans chaque `.exe` | l'exception permet de distribuer l'exécutable sous n'importe quelle licence |
| Runtime MSVC (`vcruntime140.dll`, `msvcp140.dll`...) | - | redistribuables Microsoft | oui | redistribution autorisée sans modification |

Bibliothèques Qt réellement gelées (build Linux de contrôle du 2026-10-15, 25 bibliothèques) : Core, Gui, Widgets, OpenGL, OpenGLWidgets, plus celles tirées par les plugins Qt que les hooks PyInstaller collectent (DBus, Network, Svg, Pdf, Qml, Quick, VirtualKeyboard, Test, Wayland, EglFS). Toutes relèvent de la même licence LGPLv3 ; les notices couvrent donc aussi PySide6-Addons et PySide6-Pdf. Qt WebEngine (Chromium) n'est **pas** gelé et n'est pas listé. Réduire cette liste (exclure Quick/Qml/Pdf/VirtualKeyboard dans le spec) gagnerait de la place mais demande un test du GUI gelé. Le spec exclut aussi sklearn, pandas, skimage, sympy, cryptography, lxml.

pyqtgraph 0.14 dépend de **colorama** (BSD-3-Clause), embarqué lui aussi et listé dans les notices.

## 3. Obligations LGPL pour une distribution PyInstaller (Qt/PySide6 LGPLv3, GEOS LGPLv2.1)

| Obligation | Comment c'est satisfait |
|---|---|
| Liaison permettant de remplacer la bibliothèque (LGPLv3 § 4d) | build **onedir** : les DLL Qt, PySide6, shiboken6 et GEOS sont des fichiers séparés dans `_internal\`, chargés dynamiquement ; un utilisateur peut les remplacer par une version ABI-compatible. Ne **pas** passer en `onefile` sans le documenter (l'archive est extraite à l'exécution, remplacer reste possible mais moins évident) ; ne jamais lier Qt statiquement. |
| Avis que la bibliothèque est utilisée et sous quelle licence (§ 4a) | `THIRD_PARTY_NOTICES.md` (dépôt) et `THIRD_PARTY_NOTICES.txt` (zip) |
| Copie du texte LGPL et GPL (§ 4b) | textes complets dans `THIRD_PARTY_NOTICES.txt` (copiés depuis `packaging/licenses/`) |
| Code source de la bibliothèque (§ 4d, renvoyant à GPLv3 § 6) | liens vers les sources exactes (download.qt.io, code.qt.io, libgeos.org) + offre de fournir une copie sur demande pendant 3 ans (dans `THIRD_PARTY_NOTICES.md`). Option la plus sûre : joindre ou héberger les archives sources de la version exacte de Qt/PySide6 à côté de la release (volumineux). |
| Pas de restriction à la modification / rétro-ingénierie pour le débogage (§ 4) | la licence MIT n'en impose aucune |
| « Installation Information » (GPLv3 § 6, produits grand public) | non applicable : logiciel, pas un appareil |
| Ne pas modifier Qt sans publier les modifications | Qt n'est pas modifié |

Choisir GPL (2 ou 3) pour PySide6 au lieu de LGPL n'apporterait rien : l'option LGPLv3 suffit et laisse ArborFDM sous MIT.

## 4. Développement et tests seulement (non embarqués)

| Paquet | Licence | Usage |
|---|---|---|
| pytest 9.1.1 | MIT | tests |
| lxml 6.1.3 | BSD-3-Clause | lecture des 3MF par trimesh dans les tests ; exclu du build |
| rtree 1.4.1 | MIT (libspatialindex MIT) | requêtes de proximité / rayons de trimesh **dans les tests** (le moteur n'en a pas besoin) ; ajouté aux dépendances `dev` le 2026-10-15 car 28 tests échouaient sans lui dans un environnement propre |
| PyInstaller 6.22.3, pyinstaller-hooks-contrib 2026.8 | GPL-2.0-or-later avec exception ; hooks Apache-2.0/GPL-2.0 | build seulement ; la GPL ne s'applique pas au programme gelé |
| setuptools | MIT | backend de build (`>=78.1.1`) |

## 5. Choix de la licence d'ArborFDM

Toutes les dépendances sont compatibles avec MIT, Apache-2.0, LGPL et GPL-3.0 (aucune n'est GPL sans exception ; PySide6 en LGPLv3 dynamique fonctionne avec une licence permissive comme avec une licence copyleft).

| Option | Conséquence |
|---|---|
| **MIT** (recommandée, déjà déclarée) | Simple, adoption maximale, intégrable partout (y compris dans un slicer AGPL comme OrcaSlicer/PrusaSlicer, ou un produit commercial). Contrepartie : n'importe qui, y compris un éditeur concurrent, peut reprendre le code dans un logiciel fermé et payant. |
| Apache-2.0 | Comme MIT, avec une licence de brevets explicite et une clause NOTICE ; un peu plus lourd. |
| GPL-3.0-or-later | Les versions modifiées distribuées doivent rester libres (pas de fork fermé). Compatible avec les dépendances. Freine l'intégration dans des produits propriétaires ; changer plus tard nécessite l'accord de tous les contributeurs (ou une CLA). |
| AGPL-3.0 | Comme GPL + usage en service web ; peu pertinent pour une application de bureau. |

Point d'attention : une grande partie du code a été écrite avec un assistant IA. La protection par le droit d'auteur de code généré sans apport créatif humain significatif est incertaine selon les pays, ce qui affaiblit surtout l'intérêt d'une licence copyleft (difficile à faire respecter) et plaide pour la simplicité de MIT.
