# Import du 3MF ArborFDM dans les slicers

Consulté le 2026-10-15. **Aucun slicer n'a été lancé** : ils ne sont pas installés dans l'environnement de développement. Tout ce qui suit vient de la lecture du code source des importeurs (versions indiquées) et de messages de forum. C'est une prévision à confirmer, pas un résultat d'essai. Voir aussi [research/3mf_export.md](research/3mf_export.md).

> **À l'impression : désactivez les supports du slicer** (PrusaSlicer « Supports : aucun », Bambu/Orca « Enable support » décoché, Cura « Generate Support » décoché). Les supports ArborFDM sont un objet du fichier ; si le slicer en ajoute d'autres, ils s'ajoutent sous le modèle et sous nos arbres.

## 1. Ce que contient notre export

`arbor.io.export.export_3mf` (GUI, et `arborfdm-cli generate ... -o x.3mf`) :

| Élément | Valeur |
|---|---|
| Unité | `millimeter` |
| Objets | un `<object type="model">` par pièce : `<nom>`, `<nom> supports`, `<nom> raft` (si radeau). Pas de `type="support"` (jeté par la famille PrusaSlicer, cf. § 2) |
| Disposition `objects` (défaut) | un `<item>` par pièce, même transform (translation vers l'octant positif) |
| Disposition `components` (`--layout components`) | un objet parent `<nom> assembly` fait de `<component>`, un seul `<item>` |
| Position | modèle et supports en coordonnées plateau ; le modèle **ne touche pas** le plateau quand il repose sur ses supports (z min modèle > 0) |
| Maillages | chaque pièce est fermée et 2-manifold ; les sommets à moins de 1e-5 mm sont soudés à l'écriture (cf. § 4) |
| Extensions | aucune (ni `Slic3r_PE_model.config`, ni `model_settings.config` Bambu, ni Production) |
| Métadonnées | `Application`, `Title`, `CreationDate`, `arbor:support_params` (namespace déclaré) |

## 2. Comportement attendu par slicer (lecture de code, non testé)

| | PrusaSlicer 2.9.6 | OrcaSlicer (main) / Bambu Studio | Cura (main) |
|---|---|---|---|
| `type="model"` | accepté | accepté (`model`, `other`) | accepté |
| `type="support"` | **objet ignoré** : `VALID_OBJECT_TYPES = {"model"}`, `support`/`solidsupport`/`surface` listés invalides [^ps3mf] | **ignoré** : `BBS_VALID_OBJECT_TYPES = {"model","other"}` [^orca3mf] | non déterminé : le XML est lu par la bibliothèque Savitar, `ThreeMFReader.py` ne teste pas le type [^cura] |
| Plusieurs `<item>` | un objet par item | idem | un nœud par item |
| `<components>` | résolus en **instances séparées des objets feuilles** (`_create_object_instance`, alias de composants) : même résultat que `objects` [^ps3mf] | idem (même origine de code) | un nœud **groupe** avec enfants (transforms composées) [^cura] |
| Pose sur le plateau | pour un 3MF qui n'est pas un projet PrusaSlicer, chaque objet passe par `ensure_on_bed(false)` : **chaque objet est descendu à z = 0** [^psplater] [^psmodel] | même appel `ensure_on_bed(is_project_file)` [^orcaplater] ; confirmé par les forums [^bambudrop] | réglage par nœud `drop_to_buildplate` sinon préférence « Automatically drop models to the build plate » (non vérifié : valeur par défaut) ; recentrage de l'ensemble (`centerNodesToBuildPlate`) [^cura] |
| Dialogue multi-pièces | si plusieurs objets dont les boîtes englobantes se chevauchent ou se touchent : « This file contains several objects positioned at multiple heights. Instead of considering them as multiple objects, should the file be loaded as a single object having multiple parts? » [^psplater] [^psfr] | même dialogue (« ... single object with multiple parts? ») si le fichier n'est pas un projet [^orcaplater] [^bambuwarn] | pas de dialogue |

### Conséquences pour nos fichiers

- **PrusaSlicer, OrcaSlicer, Bambu Studio** : nos supports touchent le modèle, donc les boîtes se chevauchent et le dialogue « Multi-part object detected » devrait apparaître.
  - Répondre **Oui** : le fichier devient un seul objet à plusieurs pièces, positions relatives conservées (`convert_to_multipart_object` / `convert_multipart_object`). C'est la réponse à donner.
  - Répondre **Non** : chaque objet est descendu sur le plateau ; le modèle tombe à travers ses supports. **Alignement cassé.**
  - `--layout components` ne change rien dans cette famille (les composants deviennent des objets séparés) [^ps3mf].
- **Cura** : avec la disposition `objects`, chaque nœud risque d'être descendu séparément si la descente automatique est active ; avec `--layout components`, le groupe est descendu d'un bloc (z min du groupe = pied des supports = 0), ce qui conserve l'alignement. **Pour Cura, préférer `--layout components`** ou désactiver « Automatically drop models to the build plate ». Les forums Ultimaker indiquent que les objets d'un 3MF gardent « normalement » leur position [^curaforum].
- Le Core spec autorise tout consommateur à réarranger les items (« MAY rearrange ») : seule la disposition en un seul objet/groupe est protégée par la spécification (« MUST respect the relative positions of components »).

## 3. Ce qui n'est PAS vérifié

- Aucun des quatre slicers n'a ouvert un fichier ArborFDM. Versions lues : PrusaSlicer tag `version_2.9.6` ; OrcaSlicer branche `main` et Cura branche `main` au 2026-10-15 ; Bambu Studio : seulement des messages de forum (code non lu, Orca en est un fork).
- La branche `master` de PrusaSlicer (3.0 alpha) a été réorganisée (`src/slic3r-shared/...`) ; l'importeur 3MF y existe encore (types `model`/`support` dans `Model3mf.cpp`) mais le comportement de chargement n'a pas été relu.
- Valeur par défaut de la descente automatique de Cura et comportement exact de `centerNodesToBuildPlate` avec plusieurs nœuds.
- Que « Oui » dans le dialogue multi-pièces conserve exactement les positions (déduit du nom de la fonction et des forums [^bambuwarn]).
- Coloration / affectation d'extrudeur, réglages par objet (ex. périmètres des supports) : rien n'est écrit pour l'instant.
- Bambu Studio peut afficher d'autres avertissements pour un 3MF « non Bambu » (fichier sans `model_settings.config`).

## 4. Pourquoi les maillages sont « soudés » à l'écriture

Les supports sortent de l'union manifold3d avec des triangles d'aire nulle dont les sommets distincts ont la même position flottante. Par indices, le maillage est fermé ; mais un lecteur qui fusionne les sommets par position (trimesh, et c'est l'usage des slicers pour les STL) voit des arêtes non-manifold. L'export soude donc les sommets distants de moins de 1e-5 mm (sur les valeurs réellement écrites : 6 décimales en 3MF, float32 en STL), retire les triangles effondrés et les paires de triangles opposés confondus (ailettes de volume nul), **seulement si** le résultat reste fermé et bien orienté (3MF Core 4.1.3 : « SHOULD NOT include duplicate vertices unless coalescing duplicates would create non-manifold edges »). Chaque soudure est rapportée (`ExportResult.notes`, sortie CLI, rapport JSON). Déplacement maximal observé : ~1.2e-5 mm.

## 5. Protocole de test à faire (machine avec les slicers)

```
arborfdm-cli generate examples/shelf.stl -o shelf_objects.3mf
arborfdm-cli generate examples/shelf.stl -o shelf_components.3mf --layout components
```
Pour chaque slicer et chaque fichier : noter la version, le dialogue affiché, la réponse donnée, si le modèle reste posé sur ses supports (vue de côté), si les supports apparaissent comme objet distinct, et le résultat de l'aperçu de tranchage (les pointes touchent-elles le modèle ?). Reporter ici avec la date.

---
[^ps3mf]: PrusaSlicer `src/libslic3r/Format/3mf.cpp`, tag `version_2.9.6` : `VALID_OBJECT_TYPES`, `is_valid_object_type`, `_create_object_instance` — <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/libslic3r/Format/3mf.cpp>
[^psplater]: PrusaSlicer `src/slic3r/GUI/Plater.cpp`, tag `version_2.9.6`, `load_files` (dialogue « Multi-part object detected », `ensure_on_bed(is_project_file)`) — <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/slic3r/GUI/Plater.cpp>
[^psmodel]: PrusaSlicer `src/libslic3r/Model.cpp`, `ModelObject::ensure_on_bed` (`z_offset = -min_z` si `allow_negative_z` est faux) — <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/libslic3r/Model.cpp>
[^psfr]: PrusaSlicer `src/libslic3r/FileReader.cpp`, `looks_like_multipart_object` (boîtes qui se chevauchent ou partagent une frontière) — <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/libslic3r/FileReader.cpp>
[^orca3mf]: OrcaSlicer `src/libslic3r/Format/bbs_3mf.cpp` (main) : `BBS_VALID_OBJECT_TYPES` — <https://github.com/SoftFever/OrcaSlicer/blob/main/src/libslic3r/Format/bbs_3mf.cpp>
[^orcaplater]: OrcaSlicer `src/slic3r/GUI/Plater.cpp` (main) : `looks_like_multipart_object`, `convert_multipart_object`, `ensure_on_bed(is_project_file)` — <https://github.com/SoftFever/OrcaSlicer/blob/main/src/slic3r/GUI/Plater.cpp>
[^cura]: Cura `plugins/3MFReader/ThreeMFReader.py` (main) : nœuds par item, groupes pour les composants, `drop_to_buildplate`, `centerNodesToBuildPlate` — <https://github.com/Ultimaker/Cura/blob/main/plugins/3MFReader/ThreeMFReader.py>
[^bambudrop]: Forum Bambu Lab, « Merge items Bambu Studio automatically drops to bed » — <https://forum.bambulab.com/t/merge-items-bambu-studio-automatically-drops-to-bed/139831>
[^bambuwarn]: Forum Bambu Lab, « Warning message » (texte du dialogue multi-hauteurs, choix « Oui ») — <https://forum.bambulab.com/t/warning-message/110576>
[^curaforum]: Forum UltiMaker, « How do you actually load parts in the position they were saved? » — <https://community.ultimaker.com/topic/43309-how-do-you-actually-load-parts-in-the-position-they-were-saved/>
