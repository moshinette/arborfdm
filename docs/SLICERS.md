# Import du 3MF ArborFDM dans les slicers

Consulté le 2026-10-15. Les § 1 à 5 viennent de la lecture du code source des importeurs (versions indiquées) et de messages de forum. Les deux dernières sections ajoutent un essai réel de l'utilisateur dans OrcaSlicer 2.4.2 (Windows) et des essais de **la CLI d'OrcaSlicer 2.4.2** (AppImage Linux, même `libslic3r` que l'interface) faits dans l'environnement de développement. L'interface graphique d'Orca, Bambu Studio, PrusaSlicer et Cura n'ont pas été lancés ici. Voir aussi [research/3mf_export.md](research/3mf_export.md).

> **À l'impression : désactivez les supports du slicer** (PrusaSlicer « Supports : aucun », Bambu/Orca « Enable support » décoché, Cura « Generate Support » décoché). Les supports ArborFDM sont un objet du fichier ; si le slicer en ajoute d'autres, ils s'ajoutent sous le modèle et sous nos arbres.

## 1. Ce que contient notre export

`arbor.io.export.export_3mf` (GUI, et `arborfdm-cli generate ... -o x.3mf`) :

| Élément | Valeur |
|---|---|
| Unité | `millimeter` |
| Objets | un `<object type="model">` par pièce : `<nom>`, `<nom> supports`, `<nom> raft` (si radeau). Pas de `type="support"` (jeté par la famille PrusaSlicer, cf. § 2) |
| Disposition `objects` (défaut) | un `<item>` par pièce, même transform (translation vers l'octant positif) |
| Disposition `components` (`--layout components`) | un objet parent `<nom> assembly` fait de `<component>`, un seul `<item>` |
| Disposition `separate` (`--layout separate`) | deux objets **qui touchent tous les deux le plateau** : `<nom>` et `<nom> supports + raft` (radeau et supports dans le même maillage) ; les arbres posés uniquement sur le modèle vont dans l'objet modèle. Option : `Metadata/model_settings.config` avec une hauteur de couche par objet (Orca/Bambu). Cf. § « Cause trouvée » |
| Position | modèle et supports en coordonnées plateau ; le modèle **ne touche pas** le plateau quand il repose sur ses supports (z min modèle > 0) |
| Maillages | chaque pièce est fermée et 2-manifold ; les sommets à moins de 1e-5 mm sont soudés à l'écriture (cf. § 4) |
| Extensions | aucune (ni `Slic3r_PE_model.config`, ni Production) ; `Metadata/model_settings.config` (Orca/Bambu) seulement avec `--layout separate` et une hauteur de couche par objet, et dans le fichier « Open in OrcaSlicer » (`enable_support = 0` par objet, dernière section) |
| Métadonnées | `Application`, `Title`, `CreationDate`, `arbor:support_params` (namespace déclaré) |

## 2. Comportement attendu par slicer (lecture de code, non testé)

| | PrusaSlicer 2.9.6 | OrcaSlicer (main) / Bambu Studio | Cura (main) |
|---|---|---|---|
| `type="model"` | accepté | accepté (`model`, `other`) | accepté |
| `type="support"` | **objet ignoré** : `VALID_OBJECT_TYPES = {"model"}`, `support`/`solidsupport`/`surface` listés invalides [^ps3mf] | **ignoré** : `BBS_VALID_OBJECT_TYPES = {"model","other"}` [^orca3mf] | non déterminé : le XML est lu par la bibliothèque Savitar, `ThreeMFReader.py` ne teste pas le type [^cura] |
| Plusieurs `<item>` | un objet par item | idem | un nœud par item |
| `<components>` | résolus en **instances séparées des objets feuilles** (`_create_object_instance`, alias de composants) : même résultat que `objects` [^ps3mf] | **corrigé (lu dans v2.4.2 et essayé en CLI)** : un seul objet dont chaque composant devient une pièce (`bbs_3mf.cpp` l. 2138-2160) ; positions conservées | un nœud **groupe** avec enfants (transforms composées) [^cura] |
| Pose sur le plateau | pour un 3MF qui n'est pas un projet PrusaSlicer, chaque objet passe par `ensure_on_bed(false)` : **chaque objet est descendu à z = 0** [^psplater] [^psmodel] | même appel `ensure_on_bed(is_project_file)` [^orcaplater] ; confirmé par les forums [^bambudrop] | réglage par nœud `drop_to_buildplate` sinon préférence « Automatically drop models to the build plate » (non vérifié : valeur par défaut) ; recentrage de l'ensemble (`centerNodesToBuildPlate`) [^cura] |
| Dialogue multi-pièces | si plusieurs objets dont les boîtes englobantes se chevauchent ou se touchent : « This file contains several objects positioned at multiple heights. Instead of considering them as multiple objects, should the file be loaded as a single object having multiple parts? » [^psplater] [^psfr] | même texte, mais **autre critère** : les objets n'ont pas tous le même z min (`Model::looks_like_multipart_object`, cf. § « Cause trouvée ») [^orcaplater] [^bambuwarn] | pas de dialogue |

### Conséquences pour nos fichiers

- **PrusaSlicer, OrcaSlicer, Bambu Studio** : nos supports touchent le modèle, donc les boîtes se chevauchent et le dialogue « Multi-part object detected » devrait apparaître.
  - Répondre **Oui** : le fichier devient un seul objet à plusieurs pièces, positions relatives conservées (`convert_to_multipart_object` / `convert_multipart_object`). C'est la réponse à donner.
  - Répondre **Non** : chaque objet est descendu sur le plateau ; le modèle tombe à travers ses supports. **Alignement cassé.**
  - `--layout components` ne change rien dans PrusaSlicer (les composants deviennent des objets séparés) [^ps3mf] ; dans OrcaSlicer 2.4.2 il donne un seul objet à plusieurs pièces, sans dialogue.
- `--layout separate` (cf. dernière section) : aucun objet ne flotte, donc « Oui » comme « Non » gardent l'alignement dans PrusaSlicer, et OrcaSlicer ne pose pas la question.
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
[^ps3mf]: PrusaSlicer `src/libslic3r/Format/3mf.cpp`, tag `version_2.9.6` : `VALID_OBJECT_TYPES`, `is_valid_object_type`, `_create_object_instance`, <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/libslic3r/Format/3mf.cpp>
[^psplater]: PrusaSlicer `src/slic3r/GUI/Plater.cpp`, tag `version_2.9.6`, `load_files` (dialogue « Multi-part object detected », `ensure_on_bed(is_project_file)`), <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/slic3r/GUI/Plater.cpp>
[^psmodel]: PrusaSlicer `src/libslic3r/Model.cpp`, `ModelObject::ensure_on_bed` (`z_offset = -min_z` si `allow_negative_z` est faux), <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/libslic3r/Model.cpp>
[^psfr]: PrusaSlicer `src/libslic3r/FileReader.cpp`, `looks_like_multipart_object` (boîtes qui se chevauchent ou partagent une frontière), <https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.6/src/libslic3r/FileReader.cpp>
[^orca3mf]: OrcaSlicer `src/libslic3r/Format/bbs_3mf.cpp` (main) : `BBS_VALID_OBJECT_TYPES`, <https://github.com/SoftFever/OrcaSlicer/blob/main/src/libslic3r/Format/bbs_3mf.cpp>
[^orcaplater]: OrcaSlicer `src/slic3r/GUI/Plater.cpp` (main) : `looks_like_multipart_object`, `convert_multipart_object`, `ensure_on_bed(is_project_file)`, <https://github.com/SoftFever/OrcaSlicer/blob/main/src/slic3r/GUI/Plater.cpp>
[^cura]: Cura `plugins/3MFReader/ThreeMFReader.py` (main) : nœuds par item, groupes pour les composants, `drop_to_buildplate`, `centerNodesToBuildPlate`, <https://github.com/Ultimaker/Cura/blob/main/plugins/3MFReader/ThreeMFReader.py>
[^bambudrop]: Forum Bambu Lab, « Merge items Bambu Studio automatically drops to bed », <https://forum.bambulab.com/t/merge-items-bambu-studio-automatically-drops-to-bed/139831>
[^bambuwarn]: Forum Bambu Lab, « Warning message » (texte du dialogue multi-hauteurs, choix « Oui »), <https://forum.bambulab.com/t/warning-message/110576>
[^curaforum]: Forum UltiMaker, « How do you actually load parts in the position they were saved? », <https://community.ultimaker.com/topic/43309-how-do-you-actually-load-parts-in-the-position-they-were-saved/>

## Essai réel : OrcaSlicer 2.4.2, Bambu Lab A1 mini, buse 0,2 (2026-10-15)

Premier essai dans un vrai slicer, réalisé par l'utilisateur sous Windows (aperçu du découpage, pas encore d'impression).

- `examples/figurine.stl`, profil `nozzle_02_*` : Orca regroupe automatiquement figurine, supports et raft en **un seul objet à 3 pièces** ; aucune question « multi-part object » n'est posée ; l'alignement est conservé.
- `bridge` : deux objets séparés (modèle et supports), alignement correct, pas de question non plus (tout repose sur le plateau).
- **La buse réglée dans le slicer doit correspondre au profil ArborFDM.** Avec un profil ArborFDM 0,2 découpé alors que la buse réglée dans Orca était différente, les branches fines sortaient en « gap infill » discontinu et les pointes disparaissaient (support qui ne touche plus le modèle). Avec la buse réglée sur 0,2 : supports en parois continues, gap infill à 0,1 %, pointes jusqu'au dessous de l'épée.
- Supports du slicer désactivés (« Enable support » décoché).
- Reste à faire : vérifier couche par couche la couche de contact de chaque pointe, et une impression réelle.

## Cause trouvée : regroupement automatique dans OrcaSlicer 2.4.2 (code lu au tag `v2.4.2`, commit `8500fcdc`)

Fichiers lus : `src/slic3r/GUI/Plater.cpp` (`Plater::priv::load_files`), `src/libslic3r/Model.cpp`, `src/libslic3r/Format/bbs_3mf.cpp`, `src/libslic3r/GCode.cpp`, `src/libslic3r/Print.cpp` [^orca242].

1. **Notre 3MF n'est pas un projet Orca/Bambu.** Le lecteur ne le reconnaît comme tel que si `Application` commence par `BambuStudio-` ou `OrcaSlicer-`, ou s'il y a la métadonnée `OrcaSlicer` (`bbs_3mf.cpp` l. 3946-3962). Sinon `En3mfType::From_Other` : géométrie importée sans message (`Plater.cpp` l. 6178-6186) et `is_project_file` reste `false` (il ne passe à `true` qu'avec une configuration de projet chargée, l. 6489).
2. **Critère du regroupement** (`Model::looks_like_multipart_object`, `Model.cpp` l. 752-769) : vrai s'il y a plusieurs objets, chacun à une seule pièce et sans réglage par objet, et que **leurs z min ne sont pas tous égaux**. Ce n'est pas le chevauchement des boîtes (critère de PrusaSlicer).
   - `bridge` (sans radeau) : modèle et supports ont z min = 0, donc pas de question, deux objets. C'est l'observation de l'utilisateur.
   - `figurine` avec radeau : modèle z min = 0, radeau z min = 0, **supports z min = 0,8 mm** (ils partent du dessus du radeau). Le critère est vrai.
3. Dans ce cas, `Plater.cpp` l. 6734-6743 affiche « Multi-part object detected » et, sur **Oui**, appelle `Model::convert_multipart_object` (`Model.cpp` l. 780-822) : un seul objet nommé d'après le **nom du fichier** (`input_file` sans extension), une pièce par objet du 3MF nommée comme lui. C'est exactement le résultat observé (« figurine » avec « figurine », « figurine supports », « figurine raft »). C'est le **seul** appel de `convert_multipart_object` pour un 3MF (l'autre, l. 6847, ne concerne que l'import de plusieurs fichiers STL/OBJ à la fois). Le code ne contient pas de « ne plus demander » pour ce dialogue (`MessageDialog` simple). **Point non expliqué** : l'utilisateur dit ne pas avoir vu le dialogue ; d'après le code, il a été affiché et validé par Oui (bouton par défaut ?). À revérifier.
4. Sur **Non**, chaque objet passe par `ensure_on_bed(is_project_file = false)` (`Plater.cpp` l. 6779, `ModelObject::ensure_on_bed` `Model.cpp` l. 1721-1750) : décalage `-min_z`, donc **les supports descendent de 0,8 mm** dans le radeau et sous le modèle. Vérifié avec la CLI (qui fait le même `ensure_on_bed()` pour un 3MF non projet, `src/OrcaSlicer.cpp` l. 1849) : supports z min 0,8 → 0, et le découpage signale « gcode path conflicts found between figurine raft and figurine supports ».
5. **Pourquoi on ne peut pas garder trois objets séparés dont l'un flotte**, même en empêchant la descente :
   - l'attribut d'item `auto_drop="0"` (lu pour tout 3MF, `bbs_3mf.cpp` l. 3906 ; vrai par défaut, l. 516-520) empêche bien la descente (vérifié en CLI : supports restés à z min 0,8) ;
   - mais chaque objet est découpé de z = 0 à son z max (`PrintObject.cpp` l. 113, `slicing_parameters`) et `GCode::collect_layers_to_print` (`GCode.cpp` l. 1776-1779) lève « One object has an empty first layer and can't be printed » pour un objet sans extrusion à la première couche. Vérifié en CLI : échec du découpage (`SlicingErrors`). `auto_drop` n'a donc pas été retenu.
   - Conclusion : **dans Orca, un objet séparé doit toucher le plateau**. Les supports posés sur le radeau ne peuvent être un objet séparé que s'ils sont dans le même objet que le radeau.
6. **Réglages par objet sans projet** : `Metadata/model_settings.config` est lu pour tout 3MF (`bbs_3mf.cpp` l. 1952-1960) ; les `<metadata key=... value=...>` d'un `<object>` vont dans la configuration de l'objet (l. 2121-2131), par exemple `layer_height`. Un objet qui a au moins deux réglages (`extruder` + `layer_height`) n'entre plus dans `looks_like_multipart_object`. Sans ce fichier, un objet fait de `<components>` devient un objet à plusieurs pièces (l. 2138-2160).
7. **Avertissements attendus avec des objets séparés qui se touchent** : le contrôle de collision entre objets (`Print.cpp` l. 2591-2608, `ConflictChecker::find_inter_of_lines_in_diff_objs`) signale des trajets qui se croisent quand les pointes pénètrent le modèle (`tip_penetration`) à la même hauteur de couche ; dans l'interface c'est un « Serious warning » (`GLCanvas3D.cpp` l. 10078-10090), pas une erreur bloquante d'après le code (non vérifié dans l'interface). Les arbres posés sur le modèle laissés dans un objet supports y créeraient des couches vides (« Object can't be printed for empty layer », `GCode.cpp` l. 1820, aussi « Serious warning », `Plater.cpp` l. 9766-9770) : c'est pourquoi ils sont rangés avec le modèle.

## Solution : `--layout separate` (API `export_3mf(..., layout="separate", layer_heights=...)`)

- Deux objets, **tous deux posés sur le plateau** (z min = 0) : `<nom>` (modèle + arbres posés seulement sur le modèle) et `<nom> supports + raft` (coques de supports qui partent du plateau ou du radeau, et le radeau, dans un même `<mesh>`). Chaque partie est soudée séparément puis concaténée ; aucun triangle n'est modifié ; chaque regroupement est signalé dans `ExportResult.notes` / la sortie CLI. Si le modèle lui-même ne touche pas le plateau, tout est écrit en un seul objet (note).
- Option `--model-layer-height MM` / `--support-layer-height MM` (API `layer_heights={"model": .., "supports": ..}`) : écrit `Metadata/model_settings.config` (`object id` = id de l'objet 3MF, `part id` identique, `name`, `extruder` = 1, `layer_height`) et le type de contenu `config` (`application/xml`). Refusé avec une autre disposition (les réglages par objet supprimeraient la question multi-pièces alors que des objets flottent encore).
- Le paquet reste du 3MF Core pur : `validate_export`, relecture `trimesh` et **lib3mf 2.5.0 en mode strict : 0 avertissement**, objets « manifold and oriented ».
- Hors Orca/Bambu : PrusaSlicer peut toujours poser la question multi-pièces (boîtes qui se touchent) mais, aucun objet ne flottant, les deux réponses gardent l'alignement (déduit du code § 2, non essayé) ; Cura ne fait rien de particulier (deux nœuds posés à z = 0, non essayé). `model_settings.config` est ignoré par eux.

### Essais réels (CLI OrcaSlicer 2.4.2, AppImage Linux, préréglages système « Bambu Lab A1 mini », 2026-10-15)

Commande : `orca-slicer --load-settings "machine.json;process.json" --load-filaments filament.json --arrange 0 --orient 0 --slice 0 --export-3mf result.3mf fichier.3mf` (préréglages système aplatis), puis relecture des positions dans le projet exporté par Orca et des couches par objet dans le G-code.

| Fichier (buse 0,2, A1 mini 0,2, `nozzle_02_normal`, radeau) | Positions après chargement | Découpage |
|---|---|---|
| `--layout objects` (défaut actuel) | supports descendus de 0,8 mm (z min 0) : **alignement cassé** (= réponse « Non ») | conflit de trajets radeau/supports |
| `--layout objects` + `auto_drop="0"` (essai manuel, buse 0,4) | conservées | **échec** : première couche vide de l'objet supports |
| `--layout components` (buse 0,4) | un objet, 3 pièces, conservées | OK |
| `--layout separate` | 2 objets, conservées | avertissement « conflit de trajets supports/modèle » (contact voulu, même hauteur de couche 0,1) ; G-code produit avec `--no-check` |
| `--layout separate --model-layer-height 0.06 --support-layer-height 0.12` | 2 objets, conservées | **OK sans avertissement critique** ; G-code : modèle 999 couches de 0,06 mm, supports + radeau 400 couches de 0,12 mm |
| `bridge`, `--layout separate` | 2 objets, conservées | OK |

Avec la buse 0,4 (`0.20mm Standard @BBL A1M`) : `separate` avec 0,12 / 0,24 mm découpe sans erreur, modèle 499 couches de 0,12, supports 175 couches de 0,24.

### Ce qui reste à vérifier par l'utilisateur (interface graphique)

Non vérifiable ici : l'interface d'Orca (dialogues, liste d'objets), Bambu Studio, PrusaSlicer, Cura, et l'impression.

```
arborfdm-cli generate examples/figurine.stl -o figurine_separate.3mf --profile nozzle_02_normal --set raft_enabled=true --layout separate --model-layer-height 0.06 --support-layer-height 0.12
```
Dans OrcaSlicer 2.4.2 (A1 mini, buse 0,2, supports Orca désactivés), Fichier > Importer :
1. **Aucune** question « Multi-part object detected » ; la liste d'objets montre **2 objets** : `figurine` et `figurine supports + raft`.
2. Vue de côté : les pointes touchent le dessous du modèle ; le radeau est sous les arbres ; rien n'a bougé en Z.
3. Clic droit sur chaque objet > réglages : hauteur de couche 0,06 pour `figurine`, 0,12 pour `figurine supports + raft` (modifiable ensuite).
4. Découper : noter les avertissements (attendu au plus : « Conflicts of G-code paths ... » si les deux objets ont la même hauteur de couche) et si le bouton d'export/impression reste actif.
5. Aperçu couche par couche aux hauteurs des pointes : les pointes à 0,12 mm doivent encore toucher le modèle (le dernier niveau des supports peut s'arrêter jusqu'à une demi-couche de support sous la surface ; préférer une hauteur de supports multiple de celle du modèle).
6. Refaire l'import avec le fichier `--layout objects` et noter si le dialogue « Multi-part object detected » apparaît (point 3 de la cause).

[^orca242]: OrcaSlicer, tag `v2.4.2` (commit `8500fcdccaa10b5099ac20d252af3a7c560046f1`) : <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/slic3r/GUI/Plater.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Model.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Format/bbs_3mf.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/GCode.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Print.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/OrcaSlicer.cpp>. Binaire essayé : `OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage` (release GitHub v2.4.2).

## Ouvrir dans OrcaSlicer (bouton « Open in OrcaSlicer », `arborfdm-cli generate ... --open-in-orca`)

Code lu au tag `v2.4.2` d'OrcaSlicer (mêmes fichiers que [^orca242], plus `src/slic3r/GUI/GUI_App.cpp`, `InstanceCheck.cpp`, `AppConfig.cpp`, `Preferences.cpp`, `CMakeLists.txt`, `scripts/flatpak/com.orcaslicer.OrcaSlicer.yml`) [^orcalaunch]. Numéros de ligne de ce tag.

### Fonctionnement

1. ArborFDM écrit un 3MF dans un **dossier dédié**, jamais à côté ni à la place du modèle source : `%LOCALAPPDATA%\ArborFDM\to_slicer` (Windows), `~/.local/share/arborfdm/to_slicer` (Linux, `$XDG_DATA_HOME`), `~/Library/Application Support/ArborFDM/to_slicer` (macOS) ; variable `ARBOR_TO_SLICER_DIR` pour changer. Nom `<modèle>_<AAAAMMJJ-HHMMSS>.3mf`, unique (création exclusive, suffixe `_2`, `_3`... si besoin). Les plus anciens sont supprimés au-delà de 20, **uniquement** les fichiers écrits par ArborFDM et non modifiés depuis (marque `Application=ArborFDM` dans `3D/3dmodel.model`) : un projet qu'Orca aurait réenregistré par-dessus n'est jamais supprimé.
2. Disposition `separate` (§ « Solution ») : 2 objets posés sur le plateau, `<nom>` et `<nom> supports + raft`. Hauteurs de couche par objet en option (`layer_heights`).
3. **Supports Orca désactivés par objet** : `Metadata/model_settings.config` contient `enable_support = 0` sur chaque objet (cf. « Doublons » ci-dessous).
4. Lancement non bloquant : `orca-slicer.exe <fichier>` (Windows, processus détaché), `<AppImage ou binaire> <fichier>` (Linux, nouvelle session), `open -a OrcaSlicer.app <fichier>` (macOS). L'utilisateur continue dans Orca (imprimante, filament, tranchage).

### Pourquoi passer simplement le chemin du fichier

- `orca-slicer <fichier>` sans option d'action démarre l'interface : `start_gui = m_actions.empty()` (`src/OrcaSlicer.cpp` l. 1365), puis `GUI_App::post_init` appelle `Plater::load_files` avec les fichiers (`GUI_App.cpp` l. 758-783). Pas d'autre option nécessaire.
- Un seul 3MF : `Plater::open_3mf_file` (`Plater.cpp` l. 14345). Comportement par défaut « demander si pertinent » (`AppConfig.cpp` l. 513) : sur un plateau **vide** le fichier est ouvert comme projet (`load_project`), sans question ; notre 3MF n'étant pas un projet Orca, seule la géométrie est importée, sans message (`Plater.cpp` l. 6180-6187), les préréglages (imprimante, filament, process) de l'utilisateur restent ceux en cours.
- **Instance unique** : désactivée par défaut (`AppConfig.cpp` l. 172, préférence « Allow only one OrcaSlicer instance », `Preferences.cpp` l. 1554) : chaque clic ouvre **une nouvelle fenêtre** Orca. Si l'utilisateur l'active, la ligne de commande est transmise à l'instance ouverte, qui charge le fichier dans son plateau (`EVT_LOAD_MODEL_OTHER_INSTANCE`, `Plater.cpp` l. 5434) ; plateau non vide : Orca demande « ouvrir comme projet / importer la géométrie » (`Plater.cpp` l. 14352). Les options `--single-instance` / `--no-single-instance` existent (`InstanceCheck.cpp` l. 72) mais ArborFDM ne les passe pas : la préférence de l'utilisateur s'applique.
- macOS : `open -a` passe par `GUI_App::MacOpenFiles` ; sans instance unique, un 3MF ouvert alors qu'Orca tourne démarre une nouvelle instance (`GUI_App.cpp` l. 8591).
- Position : Orca recentre la scène entière sur le plateau (même décalage XY pour tous les objets, `Model::center_instances_around_point`, `Plater.cpp` l. 6795, `Model.cpp` l. 704) et pose chaque objet sur le plateau (l. 6779) : les deux objets touchant déjà le plateau, **positions relatives, orientation et échelle sont conservées** (unité `millimeter`, aucune mise à l'échelle).
- Le fichier ouvert devient le nom du projet (`set_project_filename`, `Plater.cpp` l. 12186) : **« Enregistrer » dans Orca écrit dans le dossier `to_slicer`**. Pour garder le projet, utiliser « Enregistrer sous » ailleurs (le nettoyage ne supprime de toute façon pas un fichier réenregistré par Orca).

### Pourquoi 3MF et pas STL

| | 3MF (`separate`) | STL |
|---|---|---|
| Objets séparés | oui, un `<object>` + `<item>` chacun | non : un seul maillage par fichier (format sans liste d'objets) |
| Position relative | conservée (même décalage pour toute la scène, ci-dessus) | perdue avec deux STL : chaque objet non 3MF est recentré (`center_around_origin`, `Plater.cpp` l. 6765-6766) ; un seul STL fusionne modèle et supports en un objet |
| Unités | `unit="millimeter"` explicite (3MF Core, `<model unit>`) | aucune ; Orca suppose des mm |
| Réglages par objet (supports Orca off, hauteur de couche) | oui, `Metadata/model_settings.config`, lu pour tout 3MF | impossible (seul le réglage global du process) |

Conclusion : **3MF, disposition `separate`**.

### Doublons avec les supports automatiques d'Orca

- `enable_support` est une option de `PrintObjectConfig` (`PrintConfig.hpp` l. 948), donc réglable par objet. Le lecteur 3MF lit `Metadata/model_settings.config` pour **tout** 3MF (`bbs_3mf.cpp` l. 1952-1960) et met chaque `<metadata key value>` d'un `<object>` dans la configuration de l'objet (`set_deserialize`, l. 2131). Pour un 3MF « autre » (le nôtre), `Plater` ne remet pas cette configuration à zéro (la remise à zéro, l. 6193-6215, est dans une autre branche que `From_Other`, l. 6180).
- **Vérifié avec la CLI d'Orca 2.4.2** (AppImage, préréglages A1 mini 0,2, **process avec `enable_support = 1`, support `tree(auto)`**), figurine + radeau, hauteurs 0,06 / 0,12 :
  - fichier `export_for_slicer` (avec `enable_support = 0` par objet) : G-code sans aucune ligne `; FEATURE: Support` ; le projet réexporté par Orca garde `enable_support = 0` sur les deux objets alors que le process global vaut 1 ;
  - même fichier sans ce réglage (`disable_slicer_supports=False`) : 859 `; FEATURE: Support` + 55 `; FEATURE: Support interface` (supports Orca ajoutés en double) et l'erreur Orca « Potentially lost branch! ».
- Le réglage reste visible et modifiable dans Orca (réglages de l'objet, onglet « Objet » du process). Les autres slicers ignorent ce fichier : y désactiver les supports à la main.

### Détection d'OrcaSlicer (`find_orcaslicer`, `candidate_paths`)

Ordre : chemin configuré (s'il est valide) ; puis exécutables `orca-slicer`, `OrcaSlicer`, `orcaslicer` dans le `PATH` ; puis par plateforme :

- **Windows** : l'installateur est NSIS via CPack (`CMakeLists.txt` l. 931-965 : `CPACK_PACKAGE_VENDOR "SoftFever"`, `CPACK_PACKAGE_INSTALL_REGISTRY_KEY "OrcaSlicer"`, exécutable `orca-slicer.exe`). Le modèle NSIS de CMake écrit `HKLM` ou `HKCU\Software\SoftFever\OrcaSlicer` (valeur par défaut = dossier d'installation) et `...\Uninstall\OrcaSlicer` (`UninstallString`, `DisplayIcon`) [^cpacknsis] : ces clés sont lues (vues 64 et 32 bits). `App Paths\orca-slicer.exe` est aussi lu, mais l'installateur ne l'écrit pas d'après ce modèle. Dossiers : `%ProgramW6432%`, `%ProgramFiles%`, `%ProgramFiles(x86)%` `\OrcaSlicer\orca-slicer.exe`, `%LOCALAPPDATA%\Programs\OrcaSlicer`, `%USERPROFILE%\Documents\OrcaSlicer` (installation « pour moi seulement » de CPack NSIS : `$DOCUMENTS\OrcaSlicer`), `%OneDrive%\Documents\OrcaSlicer`, Scoop `~\scoop\apps\orcaslicer\current` (non vérifié). **Version portable (zip)** : pas de détection, indiquer le chemin.
- **Linux** : flatpak `com.orcaslicer.OrcaSlicer` (identifiant du manifeste v2.4.2) et l'ancien `io.github.softfever.OrcaSlicer`, via les lanceurs exportés `~/.local/share/flatpak/exports/bin/<id>` et `/var/lib/flatpak/exports/bin/<id>` (le flatpak a `--filesystem=home`, il voit donc le dossier `to_slicer`) ; `/usr/bin`, `/usr/local/bin`, `/opt/OrcaSlicer/bin`, `/opt/orcaslicer/bin` ; AppImage `*orca*.AppImage` (le plus récent) dans `~/Applications`, `~/AppImages`, `~/Downloads`, `~/Desktop`, `~/.local/bin`, `~/bin`, `/opt`. Une AppImage non exécutable est signalée (`chmod +x`).
- **macOS** : `/Applications/OrcaSlicer.app`, `~/Applications/OrcaSlicer.app` (non essayé).

Chemin manuel : réglage `orca_path` de `%APPDATA%\ArborFDM\settings.json` (Linux `~/.config/arborfdm/settings.json`), écrit par la GUI ou par `arborfdm-cli orca --set-path "C:\...\orca-slicer.exe"` ; `arborfdm-cli orca --clear-path` revient à la détection ; `arborfdm-cli orca --find` affiche le programme trouvé ou la liste des emplacements essayés. Un chemin configuré invalide est ignoré (avertissement) et la détection prend le relais ; `--orca-path` invalide en CLI est une erreur.

Erreurs : programme introuvable ou invalide → `SlicerNotFound` ; fichier absent, échec du lancement ou sortie immédiate avec un code d'erreur → `SlicerLaunchError`. Le fichier exporté reste dans `to_slicer` et son chemin est affiché : l'utilisateur peut l'ouvrir à la main (Fichier > Importer). Si ArborFDM est gelé (PyInstaller), les chemins de bibliothèques ajoutés par PyInstaller (`LD_LIBRARY_PATH`, `PATH` vers `_MEIPASS`, `QT_PLUGIN_PATH`) sont retirés de l'environnement passé à Orca.

### Essai réel de l'interface (Linux, conteneur)

`launch_orcaslicer(fichier, AppRun)` sur l'AppImage 2.4.2 extraite, sous Xvfb, profil Orca vierge : la fenêtre s'ouvre, le journal d'Orca montre `post_init, init with input files, size 1` puis `load_project` puis « 3MF has no slicer metadata/project config, importing geometry only » ; la liste d'objets affiche **2 objets** `figurine` et `figurine supports + raft` sur « Plate 1 », aucune question « Multi-part object », titre de fenêtre = nom du fichier. L'assistant de premier lancement d'Orca s'affiche par-dessus (profil vierge). Premier essai : Orca a refusé de démarrer faute de locale `en_US` dans le conteneur (message « Switching Orca Slicer to language en_US failed ») ; ce n'est pas un problème d'ArborFDM. Non essayé : Windows, macOS, flatpak, instance unique.

### Limites

- Dans Orca, objets séparés à la même hauteur de couche : avertissement « Conflicts of G-code paths » attendu (pointes qui pénètrent le modèle, § « Cause trouvée » point 7). Avec 0,06 / 0,12 sur la figurine actuelle, la CLI d'Orca le signale aussi (`--no-check` nécessaire en CLI) ; dans l'interface c'est un « Serious warning » d'après le code, effet sur le bouton d'impression non vérifié.
- Le décalage XY appliqué par Orca (recentrage) est voulu ; la position absolue sur le plateau n'est pas celle d'ArborFDM.
- Profil d'imprimante / buse : choisis dans Orca, pas transmis (la buse doit correspondre au profil ArborFDM, cf. essai A1 mini).

### Vérification à faire sous Windows (A1 mini, Orca 2.4.2)

1. `arborfdm-cli orca --find` : doit afficher `C:\Program Files\OrcaSlicer\orca-slicer.exe` (ou l'emplacement réel). Sinon noter la sortie et l'emplacement d'Orca, puis `arborfdm-cli orca --set-path "<chemin>\orca-slicer.exe"`.
2. Orca fermé : `arborfdm-cli generate examples\figurine.stl --profile nozzle_02_normal --set raft_enabled=true --open-in-orca --model-layer-height 0.06 --support-layer-height 0.12`. Orca s'ouvre seul ; noter le temps et tout dialogue.
3. Liste d'objets : 2 objets `figurine` et `figurine supports + raft`, pas de « Multi-part object detected ». Vue de côté : pointes contre le modèle, rien n'a bougé en Z.
4. Process, onglet « Objet », sur chaque objet : « Enable support » décoché ; hauteur de couche 0,06 / 0,12. Cocher « Enable support » dans le process global : les objets doivent rester sans supports Orca. Trancher : aperçu sans supports Orca en plus des arbres.
5. Orca déjà ouvert : relancer la commande. Attendu : une **deuxième fenêtre** (instance unique désactivée par défaut). Puis, avec « Préférences > Allow only one OrcaSlicer instance » activé : le fichier doit arriver dans la fenêtre existante (question projet / géométrie si le plateau n'est pas vide).
6. Renommer temporairement le dossier d'Orca (ou `--orca-path C:\nope.exe`) : message clair, fichier quand même écrit dans `%LOCALAPPDATA%\ArborFDM\to_slicer`.
7. Vérifier que le modèle source (`examples\figurine.stl`) n'a pas changé (date de modification) et qu'aucun fichier n'a été créé à côté.

[^orcalaunch]: OrcaSlicer tag `v2.4.2` : <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/OrcaSlicer.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/slic3r/GUI/GUI_App.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/slic3r/GUI/InstanceCheck.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/slic3r/GUI/Plater.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/AppConfig.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/slic3r/GUI/Preferences.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Format/bbs_3mf.cpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/PrintConfig.hpp>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/CMakeLists.txt>, <https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/scripts/flatpak/com.orcaslicer.OrcaSlicer.yml>.
[^cpacknsis]: CMake `Modules/Internal/CPack/NSIS.template.in` (v3.31.0) : `WriteRegStr SHCTX "Software\@CPACK_PACKAGE_VENDOR@\@CPACK_PACKAGE_INSTALL_REGISTRY_KEY@" "" $INSTDIR`, clés `Uninstall\...`, `$DOCUMENTS\...` pour une installation « JustMe », <https://github.com/Kitware/CMake/blob/v3.31.0/Modules/Internal/CPack/NSIS.template.in> ; racine par défaut `$PROGRAMFILES64` en 64 bits (`Modules/CPack.cmake`).
