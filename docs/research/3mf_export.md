# Export 3MF : structure choisie

Consulté le 2026-10-15. Aucune compatibilité slicer n'a été testée : les slicers ne sont pas installés dans l'environnement de développement. Ce document décrit ce que disent les sources, pas un résultat d'essai.

## 1. Ce que dit la spécification Core

Source : [3MF Core Specification](https://github.com/3MFConsortium/spec_core/blob/master/3MF%20Core%20Specification.md) (v1.4.0) et [la copie du wiki Slic3r](https://github.com/slic3r/Slic3r/wiki/3MF-Document-Core-Specification).

- Paquet OPC (zip) : `[Content_Types].xml`, `_rels/.rels` avec la relation StartPart `http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel` vers `/3D/3dmodel.model`. Content type du modèle : `application/vnd.ms-package.3dmanufacturing-3dmodel+xml`. Namespace Core : `http://schemas.microsoft.com/3dmanufacturing/core/2015/02`.
- `<model unit>` : `micron`, `millimeter` (défaut), `centimeter`, `inch`, `foot`, `meter`.
- `<object type>` : `model`, `solidsupport`, `support`, `surface`, `other`. Pour `support`, « A consumer MAY ignore support objects if they are not needed » et le fabricant peut choisir l'épaisseur réelle.
- Un objet contient soit un `<mesh>`, soit des `<components>` (références à des objets définis avant, avec transformation). Un `model` doit avoir au moins 4 triangles, `v1 v2 v3` distincts, ordre anti-horaire vu de l'extérieur.
- `transform` (ST_Matrix3D) : 12 valeurs `m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32`, convention vecteur-ligne, translation sur la dernière ligne.
- Le modèle transformé « SHOULD be located in the positive octant ». Les consommateurs « MAY rearrange the items » sur le plateau.
- Métadonnées sans namespace limitées à la liste officielle (Title, Designer, Description, Copyright, LicenseTerms, Rating, CreationDate, ModificationDate, Application) ; toute autre clé doit être préfixée par un namespace déclaré sur `<model>`.

## 2. Comportement des slicers (d'après sources, non testé)

| Slicer | Plusieurs objets / build items | Un objet à composants | `type="support"` |
|---|---|---|---|
| PrusaSlicer | importés comme objets séparés ; chacun est posé sur le plateau, les positions relatives en Z sont perdues [forum Prusa](https://forum.prusa3d.com/forum/prusaslicer/multipart-objects-in-3mf/) | sans métadonnées `slic3rpe`, les composants sont résolus en instances des objets feuilles (code de l'importeur, cf. Orca ci-dessous) | **ignoré** : l'importeur n'accepte que `model` ou vide |
| OrcaSlicer / Bambu Studio (fork de PrusaSlicer) | `src/libslic3r/Format/3mf.cpp` : chaque `<item>` crée une instance du `ModelObject` référencé [miroir du code](https://git.trueserve.org/PublicCodeMirror/OrcaSlicer-bambulab/src/commit/9dff44a8ad1aa2b77d3d1c4afb2cecd8effdfc62/src/libslic3r/Format/3mf.cpp) ; Bambu Studio « automatically drops the pieces to the bed » pour des objets séparés, alors qu'un groupe garde les hauteurs [forum Bambu](https://forum.bambulab.com/t/merge-items-bambu-studio-automatically-drops-to-bed/139831) | idem PrusaSlicer (aliases de composants) ; Bambu propose aussi de charger comme un objet multi-pièces | `is_valid_object_type` n'accepte que `model`/vide : l'objet support est **jeté** et l'item qui le référence échoue (« Found item with invalid object id ») |
| Cura | objets séparés avec réglages indépendants [README Enhanced FreeCAD 3mf exporter](https://github.com/Malendryn/Enhanced-FreeCAD-3mf-Exporter/blob/main/README.md) | groupe (pièces sélectionnables par Ctrl+clic) | non documenté |

Remarque : le comportement Orca/Bambu est lu sur un miroir d'une version de l'importeur ; le code courant peut différer.

## 3. Choix ArborFDM

1. **Tous les objets sont `type="model"`.** `type="support"` ferait disparaître les supports dans la famille PrusaSlicer, et la spécification autorise tout consommateur à les ignorer. Les supports sont identifiés par leur nom (`<modèle> supports`, `<modèle> raft`).
2. **Un `<object>` par pièce** (modèle, supports, raft), chacun avec un attribut `name`.
3. **Deux dispositions** (`layout`) :
   - `objects` (défaut, conforme à la demande) : un build item par pièce, pour que le slicer les affiche comme objets distincts. **Risque connu** : un slicer qui pose chaque objet sur le plateau ou réarrange les items casse l'alignement modèle/supports. Ici le modèle repose sur ses supports (z min modèle > 0) : à vérifier dans chaque slicer.
   - `components` : un objet parent composé des pièces et un seul build item. Les positions relatives sont garanties par la spécification (« MUST respect the relative positions of components »). C'est l'option de repli si `objects` s'avère cassé dans un slicer.
4. **Octant positif** : la scène est translatée (transform du build item) pour que xyz >= 0 ; la translation est renvoyée (`ExportResult.offset`).
5. Métadonnées : `Application=ArborFDM`, `Title`, `CreationDate`, plus `arbor:support_params` (JSON) dans le namespace `http://arborfdm.org/3mf/2026` déclaré sur `<model>`.
6. Pas d'extension (Production, Materials, BambuStudio `model_settings.config`, `Slic3r_PE_model.config`) : seulement le Core, pour rester neutre.

## 4. Validation automatique

`validate_export()` vérifie : intégrité zip, content types, relation StartPart, namespace Core, unité, ids uniques, `mesh` xor `components`, maillage non vide, indices dans les bornes, pas d'index répété, >= 4 triangles, références des composants/items, transforms non singulières, puis relecture par notre lecteur. Les tests relisent aussi chaque fichier avec `trimesh.load` (volumes et bornes identiques).

## 5. À faire (non vérifiable ici)

Ouvrir un export `objects` et un export `components` dans Bambu Studio, OrcaSlicer, PrusaSlicer et Cura ; noter si les supports restent alignés et s'ils sont listés comme objets distincts. Mettre à jour ce tableau avec les versions testées.
