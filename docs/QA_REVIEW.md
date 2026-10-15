# ArborFDM : revue QA indépendante (2026-10-15)

Revue faite par le rôle qa-engineer. Tout ce qui est écrit ici a été **exécuté** sur cette
machine (Linux, 2 cœurs, 7,7 Go de RAM, Python 3.13, trimesh 4.x, PySide6 6.12, pas de GPU,
Qt en `offscreen`). Les sorties citées sont des sorties réelles.

Tests de non-régression : `tests/test_qa_review.py` (45 tests : 27 passent, 18 en
`xfail(strict=True)`, un par défaut non corrigé). Quand un défaut est corrigé, son test
passe en XPASS, ce qui fait échouer la suite : il faut alors retirer le marqueur.

```
QT_QPA_PLATFORM=offscreen python3 -m pytest -q -p no:cacheprovider
367 passed, 18 xfailed in 247.23s        (suite complète : 340 existants + 27 nouveaux)
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_qa_review.py -q -p no:cacheprovider
27 passed, 18 xfailed in 136.41s
```

Chaque xfail a aussi été lancé avec `--runxfail` pour vérifier qu'il échoue pour la raison
annoncée et pas à cause d'une erreur du test (18 échecs, messages cités plus bas).

## Synthèse

| Id | Gravité | Défaut |
|---|---|---|
| QA-1 | majeur | Faux `floating_support` (erreur) : vides internes de l'union comptés comme supports flottants. Le CLI sort en code 1 sur `examples/figurine.stl` avec le profil par défaut |
| QA-2 | majeur | `contact_spacing = 0.2` (minimum du champ GUI, accepté par `validate`) épuise la mémoire (> 4 Go, processus tué) |
| QA-3 | majeur | Pendant une génération les panneaux restent actifs : les modifications faites pendant le calcul sont écrasées sans rien dire, et l'export écrit des paramètres qui ne correspondent pas à la géométrie |
| QA-4 | majeur | Un support qui traverse le modèle n'est qu'un *warning* (code de sortie 0) |
| QA-5 | majeur | La vérification croit l'étiquette `MODEL_FOOT` de l'arbre : un support « posé sur le modèle » à 40 mm de celui-ci passe sans erreur |
| QA-6 | majeur | Un maillage cassé non réparé est traité dans la GUI sans aucun avertissement sur le maillage (99 erreurs `unrouted_contact` sans cause) |
| QA-7 | majeur | La GUI plante dans `RightPanel.set_issues` quand deux issues ont le même message (deux figurines identiques sur le plateau) |
| QA-8 | mineur | Pas de garde-fou de taille : modèle de 1,5 m, `MemoryError` ou OOM au lieu d'un refus clair |
| QA-9 | mineur | Activer le raft rend un contact de la figurine impossible à router |
| QA-10 | mineur | `validate()` incomplet : diamètres négatifs/nuls, `segment_length = 0`, `nan` acceptés ; `raft_thickness = -1` donne une trace Python |
| QA-11 | mineur | GUI, maillage de 1 M de triangles : picking sur le thread UI (4,5 s de gel au premier survol, puis 120 ms par survol) |
| QA-12 | mineur | Pas d'avertissement d'unité pour une figurine en pouces (2,36 mm), et 0 support sans message |
| QA-13 | mineur | L'avertissement « contacts modifiés depuis la génération » à l'export est effacé aussitôt, l'utilisateur ne le voit jamais |

**Aucun défaut bloquant pour le flux nominal**, testé de bout en bout avec la vraie figurine :
chargement, génération, ajout de contact par vrai clic souris, suppression par clic droit,
undo/redo, reconstruction des branches, export 3MF et STL, relecture. Les 3MF passent
lib3mf en mode strict sans aucun avertissement. QA-2 et QA-7 font tomber l'application
(perte du travail non sauvegardé), mais seulement avec une valeur extrême ou un plateau à
plusieurs figurines identiques. Ils sont classés majeurs, pas bloquants.

---

## Défauts majeurs

### QA-1 : faux supports flottants (vides internes)

*Reproduction*
```
python3 -m arbor.cli generate examples/figurine.stl -o /tmp/f.3mf -q
error: verify: A support part between z=21.85 and 21.87 mm floats in the air
exit=1
```
Moteur direct, profil 0.2 Dense : 3 erreurs sur la figurine, 12 sur `inclined_60deg.stl`,
1 sur `cavity.stl` et 1 sur `impossible_case.stl`. Dans la GUI, la figurine en 0.4 Normal
affiche « 1 error(s) ».

*Preuve* : chaque composante signalée est une coque fermée à **volume négatif** (coque
inversée = cavité) contenue dans un tronc qui touche le plateau.
```
floating comp [-0.77 -9.08 15.33] .. 28 faces  vol -0.0010609  inside a grounded component: True
floating comp [ 6.14 -9.1  15.94] .. 12 faces  vol -9.7e-06    inside a grounded component: True
floating comp [-6.44 -3.07 21.42] ..  4 faces  vol -8.9e-10    inside a grounded component: True
inclined_60deg : 12/12 composantes "flottantes" : volume <= 0, à l'intérieur d'un support posé
```
*Correctif proposé* : dans `verify._check_mesh`, ignorer les composantes de volume ≤ 0 (cavités)
et, mieux, les supprimer dans `build.mesh_tree` (un `split()` qui ne garde que les coques à
volume positif, ou `Manifold.decompose()` en jetant les coques de volume négatif ou inférieur
à 1e-3 mm³). *Vérification* : `test_dense02_figurine_has_no_false_floating_support` et
`test_cli_default_figurine_exits_zero` passent en XPASS. `test_verify_detects_floating_mesh_part`
doit continuer à passer.

### QA-2 : explosion mémoire avec `contact_spacing` petit

*Reproduction* : figurine, profil 0.4 Normal, `contact_spacing = 0.2`. C'est le minimum du
champ « Surface spacing » de la GUI, et `validate()` l'accepte.
```
sans limite : processus tué par l'OOM killer après 20 s (7,7 Go de RAM)
ulimit -v 4 Go : _ArrayMemoryError: Unable to allocate 221. MiB for an array with shape (28939331,)
  contacts.py:112  p = pts[pi, :2] - self.a[ti]   (VerticalCaster._test, appelé par cast())
```
Mesure du pic mémoire selon `contact_spacing` (figurine, 62 k triangles) :

| contact_spacing | temps | pic RSS |
|---|---|---|
| 3.0 (profil) | 2,3 s | 228 Mo |
| 1.0 | 3,8 s | 278 Mo |
| 0.6 | 5,2 s | 294 Mo |
| 0.4 | 6,9 s | 741 Mo |
| 0.2 | - | > 4 Go (MemoryError ou OOM) |

*Cause* : dans `VerticalCaster.cast`, la branche des « petits » triangles construit toutes les
paires (point, triangle) d'un coup sans découpage. La branche des grands triangles est
découpée, elle. Le réseau résiduel (`RESIDUAL_PITCH` = 0,2 × espacement) devient très dense.
*Correctif* : découper la branche `query_ball_point` en blocs de points (comme la branche
des grands triangles, environ 2 M de paires par bloc). Borner le nombre de points du réseau
résiduel, ou relever le minimum de `contact_spacing` (≥ 2 × diamètre de pointe). *Vérification* :
`test_min_contact_spacing_bounded_memory` (sous-processus limité à 3 Go) passe.

### QA-3 : modifications pendant un calcul perdues, résultat incohérent

*Reproduction* (GUI offscreen, figurine) : lancer « Generate supports ». Pendant le calcul,
supprimer un contact manuel avec le bouton « Delete contact » et passer `tip_diameter` à 0.6
dans Advanced. Ces deux widgets restent actifs : seul le viewport est verrouillé
(`edit_enabled`).
```
enabled while busy: delete btn True | enabled check True | adv field True | profile combo True | rot spin True | repair True
manual deleted during run: True undo text: Remove contact #87
after run: manual contact back? True | contacts_edited False | params tip 0.6
tip radii in result: [0.225]          -> géométrie construite avec 0.45 mm
3MF metadata tip_diameter: 0.6        -> métadonnées fausses
```
Sans calcul en cours le problème existe aussi : générer, changer un paramètre, puis exporter.
Les métadonnées `arbor:support_params` contiennent les paramètres courants et non ceux de la
génération, et aucun message n'apparaît (`test_gui_export_metadata_matches_generated_geometry`).
*Cause* : `Project.is_request_current` ne compare que `mesh_version`. `apply_result` remplace
la liste des contacts par celle du résultat. `_export` envoie `p.params`.
*Correctif* : (a) désactiver `LeftPanel`, `RightPanel` et `ContactEditor` pendant une tâche,
dans `_refresh_actions` ; (b) dans `is_request_current`, comparer aussi `contacts_version` et
les paramètres, sinon proposer de jeter ou d'appliquer ; (c) conserver les paramètres de la
génération dans `GenerationResult` et les exporter, ou avertir que le résultat est périmé.
*Vérification* : `test_gui_edits_during_generation_are_not_lost`,
`test_gui_export_metadata_matches_generated_geometry`.

### QA-4 : un support qui traverse le modèle n'est qu'un avertissement

*Reproduction* : `suspended_arm.stl`. On ajoute au maillage de supports un pilier de 2,4 mm
qui traverse tout le modèle, puis on appelle `verify`.
```
(c) pillar through model [('warning', 'model_collision',
  'Supports overlap the model by 132.67 mm3 (allowed for tips: 1.14 mm3): a branch may cut into the part')]
```
Avec 116 fois le budget des pointes, ce n'est toujours qu'un warning, donc code de sortie
CLI 0. Le volume est bien mesuré (`test_verify_measures_support_crossing_model` passe).
*Correctif* : passer en `error` au-delà d'un seuil, par exemple volume > 5 × budget ou
> 2 mm³. *Vérification* : `test_verify_support_crossing_model_is_an_error`.

### QA-5 : un pied « sur le modèle » n'est pas vérifié géométriquement

*Reproduction* : on ajoute à l'arbre un nœud `MODEL_FOOT` à (40, 40, 15) mm, à 40,8 mm du
modèle, et le cylindre correspondant au maillage de supports.
```
model distance of fake foot [40.81]
(f) model foot in the air []        <- aucune erreur
```
`_check_tree` accepte tout `MODEL_FOOT` quelle que soit sa position. `_check_mesh` exempte une
composante dès qu'un point `MODEL_FOOT` se trouve dans sa boîte englobante élargie de 1 mm.
Le moteur actuel place correctement ses pieds : distance max mesurée 0,05 mm en 0.4 Normal,
0,03 mm en 0.2 Dense. Le défaut est donc latent, mais la vérification ne contrôle pas ce
qu'elle prétend contrôler.
*Correctif* : dans `verify`, exiger `closest_point(mesh, foot).distance <= tip_penetration + 0.1`
pour chaque `MODEL_FOOT`, sinon `disconnected_branch` / `floating_support`. *Vérification* :
`test_verify_detects_model_foot_in_the_air`.

### QA-6 : maillage cassé non réparé accepté sans diagnostic

*Reproduction* : figurine avec 5 % de faces supprimées et 5 % retournées, puis
`pipeline.generate`. La GUI fait exactement cela : `_generate` ne regarde pas l'état du
maillage. Le CLI, lui, refuse sans `--repair`, ce qui est correct.
```
intact: contacts 90  issues {unstable_trunk:3, rests_on_model:1, geometric_only:1}
broken: contacts 196 {error:unrouted_contact: 99, error:unsupported_region: 19, warning:sub_layer_gap: 5,
                      ..., info:collision_check_skipped: 1}
```
Le nombre de contacts double, 99 erreurs apparaissent, et la seule allusion au maillage est
une *info*. *Correctif* : `verify` (ou `pipeline.generate`) ajoute une issue `invalid_model_mesh`
(warning ou erreur) quand le modèle n'est pas fermé ou pas orienté. La GUI propose « Repair »
avant de générer. *Vérification* : `test_unrepaired_broken_mesh_is_flagged`.

### QA-7 : plantage de la liste des issues avec des messages identiques

*Reproduction* : deux figurines identiques côte à côte. Cas réaliste, puisque le moteur
produit alors deux messages `unstable_trunk` identiques
(`test_two_identical_minis_produce_identical_issue_messages`, qui passe). Ensuite :
```
RightPanel.set_issues([a, b])   # même severity / code / message, positions différentes
ValueError: The truth value of an array with more than one element is ambiguous.
```
*Cause* : `self._issues.index(i)` compare des dataclasses qui contiennent des `np.ndarray`.
L'exception sort du listener du projet, si bien que la liste, le résumé et la barre d'état
ne sont qu'en partie mis à jour.
*Correctif* : `for idx, i in sorted(enumerate(self._issues), key=...)` puis
`item.setData(UserRole, idx)`, ou `@dataclass(eq=False)` sur `Issue`. *Vérification* :
`test_issue_list_with_identical_messages`.

## Défauts mineurs

**QA-8, pas de limite de taille.** Figurine × 25,4 (1,52 m, que l'on obtient avec 2540 % dans
le champ Scale, qui va jusqu'à 10 000 %) : `MemoryError` au bout de 26 s avec une limite de
6 Go, OOM sans limite. Sans garde-fou, un mauvais facteur d'échelle fait tomber
l'application au lieu de produire un refus clair. ×4 (240 mm) : 21,9 s et 861 Mo ; ×8 (480 mm) :
86 s et 2,6 Go. Correctif : refuser avec un message (`ValueError`) au-delà d'une emprise
configurable (par exemple 500 mm), et limiter le champ Scale. Test : `test_huge_model_is_refused_cleanly`.

**QA-9, le raft fait perdre un contact.** Figurine 0.4 Normal : 0 erreur sans raft. Avec
`raft_enabled` : `Contact 86 (surface) is not supported: blocked by the model, no landing
surface reachable without crossing the model`. Le raft lui-même est correct : étanche, de
z = 0 à 0,8, sans intersection avec le modèle, tous les pieds dans son contour, supports à
z_min = 0,8 (`test_raft_supports_stand_on_raft_and_avoid_model`). Piste : la zone d'exclusion
du raft (`_raft_keepout`, empreinte du modèle + `xy_clearance`) ou le relèvement de `z_base`
interdisent un atterrissage qui était possible sur le plateau. Le message `unstable_trunk`
conseille aussi « consider braces » alors que les entretoises sont actives, et ne tient pas
compte du raft. Test : `test_raft_does_not_lose_contacts`.

**QA-10, validation des paramètres incomplète.** `validate()` accepte `trunk_diameter=-2`,
`foot_diameter=0` et `segment_length=0` : le CLI sort en 0 et écrit un fichier. Il accepte
aussi `contact_spacing=nan` (car `nan <= 0` est faux), ce qui donne `error: arange: cannot
compute length`. Avec `raft_thickness=-1` et le raft actif : trace Python non capturée
`RuntimeError: manifold error: Error.InvalidConstruction` et code 1, alors que 1 signifie
« problèmes de supports » et que le cas relève de l'usage (2). Les mêmes valeurs peuvent
venir d'un `.arbor` modifié. Correctif : bornes pour tous les champs (reprendre `PARAM_META`
de la GUI) et `math.isfinite`. Tests : `test_validate_rejects_invalid_geometry_params[...]`,
`test_cli_negative_raft_thickness_is_usage_error`.

**QA-11, interactivité sur un gros maillage.** Mesures GUI offscreen sur 992 256 triangles :
chargement 2,5 s, génération 13,5 s (dans le worker, l'UI reste libre). En revanche
`pick_model` tourne sur le thread UI : 4,49 s au premier appel (construction de la structure
d'accélération des rayons), puis 120 ms par appel. Le survol relance un picking toutes les
25 ms, donc l'UI rame. La coupe par hauteur prend 565 ms par recalcul. Correctif : construire
l'accélérateur dans le worker après le chargement, limiter la fréquence du survol (≥ 100 ms)
ou faire le picking sur un maillage décimé, puis affiner sur le maillage complet.

**QA-12, unités.** Une figurine de 60 mm enregistrée en pouces devient 1,86 × 1,46 × 2,36 mm.
Le seuil d'avertissement (`_unit_warnings`, 2 mm) n'est pas atteint, et le moteur rend 0
contact avec seulement l'info `geometric_only`. Correctif : seuil de 10 mm environ, et une
issue « modèle très petit » dans `verify`. Test : `test_inch_sized_model_gets_scale_warning`.

**QA-13, avertissement d'export invisible.** Dans `MainWindow._export`, le message
« Contacts were edited ... rebuild the branches » est aussitôt remplacé par « Exporting... »
(`_start`), puis par « Exported ... ». La boîte finale ne le reprend pas. Correctif : l'ajouter
au texte de `_info` à la fin de l'export, ou demander une confirmation. Test :
`test_gui_export_warns_about_unrebuilt_edits`.

Observation (pas un défaut si c'est voulu) : un `.arbor` rouvert ne contient pas les supports
générés (`result None`, contacts conservés). Il faut relancer « Rebuild branches ». À écrire
dans la doc utilisateur.

---

## Ce qui a été vérifié et fonctionne

**GUI** (offscreen, `examples/figurine.stl`, vrais événements souris `QTest.mouseClick`) :
`test_gui_full_flow_real_picking_and_exports`, `test_gui_every_control_has_an_effect`.
- Clic gauche réel sur le modèle : contact manuel au point exact du rayon (écart à la surface
  < 1e-3 mm). « Rebuild branches * » le route : la pointe est à 0,047 mm du contact.
  Clic droit réel sur le marqueur : suppression. Undo/redo cohérents.
- Barre d'outils et menus : New, Open, Save, Undo, Redo, Generate, Rebuild, Clear (+ undo),
  Cancel (« Cancelled »), Export 3MF / STL supports / STL modèle+supports. Les fichiers sont
  relus : le 3MF passe `validate_export`, la pointe du contact manuel est présente dans le
  3MF, et faces(STL tout) - faces(STL supports) = faces(modèle).
- Panneau gauche : rotation X 90° (dimensions permutées), échelle 50 %, hauteur 45 mm,
  « Place on plate » (centré, z_min = 0), « Suggest orientation » (6 candidats ; un clic sur
  le 2e applique exactement sa rotation, entrée d'undo « Orientation #2 »), Check et Repair
  sur `broken_hole.stl` (copie réparée étanche, original intact, annulable), profils, champs
  simples.
- Panneau droit : **chacun des 43 paramètres avancés** modifie `project.params`, et son
  bouton reset rétablit la valeur du profil ; « Reset all » ; éditeur de contact (activer,
  forcer le diamètre de pointe, supprimer) ; clic sur une issue (la caméra se recentre).
- Viewport : modes Model / Supports / Model + supports (visibilité vérifiée), coupe par
  hauteur (z_max du maillage affiché = z de coupe), vues prédéfinies.
- Aucune fonction simulée ni bouton sans effet trouvé, en dehors de QA-13 (avertissement
  affiché puis effacé aussitôt).

**Moteur** : figurine 0.4 Normal, 0 erreur ; les 90 pointes touchent le support (≤ 0,05 mm)
et les pieds posés sur le modèle sont à ≤ 0,05 mm de sa surface. Modèle tourné de 37° et
réduit à 50 % : 0 erreur. `allow_on_model=False` : aucun pied sur le modèle, et chaque contact
non routé est signalé en erreur avec son id. Les entretoises s'activent et se désactivent bien.
Cinq contacts manuels espacés de 0,02 mm sont tous gardés, avec 10 warnings
`contacts_too_close`. Plaque de 0,2 mm et aiguille de 0,3 mm : supportées. Paramètres
extrêmes valides (angles 10/89, angle de branche 0/60, 6/64 segments, densité 0,1/5, merge
0/50, gros pieds) : maillages étanches, rien sous le plateau.

**Vérification** (`verify.py`), défauts fabriqués effectivement détectés : contact retiré de
l'arbre → `unrouted_contact` (error) ; pièce flottante dans le maillage → `floating_support` ;
branche pendante dans l'arbre → `disconnected_branch` ; maillage descendu de 1 mm →
`below_plate` ; aucun support → `unsupported_region` ; collision mesurée (132,67 mm³). Non
détectés : voir QA-4 et QA-5.

**Exports**
- 3MF : `[Content_Types].xml` (rels + model, bons types MIME), `_rels/.rels` (StartPart vers
  `/3D/3dmodel.model`), `unit="millimeter"`, un objet `type="model"` nommé par pièce, un item
  de build par objet avec une seule et même transformation (translation = décalage vers
  l'octant positif). Mode `components` : un objet assemblage et un seul item.
- **lib3mf 2.5.0 en mode strict** (validation indépendante) : 0 avertissement sur
  figurine+supports+raft, sur le mode components et sur le 3MF de 1 M de triangles ;
  `IsManifoldAndOriented() == True` pour chaque maillage ; unité MilliMeter.
- trimesh : toutes les pièces sont relues étanches et bien orientées, avec les mêmes volumes ;
  les pointes touchent encore le modèle après l'aller-retour (< 0,1 mm).
- STL binaire : taille = 84 + 50 n octets, relu étanche.
- CLI : fichier absent → 2 ; maillage cassé sans `--repair` → 2 (`exit_code: 2` dans le
  JSON) ; avec `--repair` → 0 ; sortie `.obj` → 2 ; paramètre inconnu → 2 ; valeur hors
  bornes → 2 ; cas nominal → 0. Le JSON est strict (aucun NaN/Infinity) et donne contacts,
  arbre, issues, temps, fichiers et problèmes de validation.

## Mesures de performance (réelles, cette machine)

Temps de `pipeline.generate`. Le pic RSS est celui du processus complet (imports et
chargement compris).

| Cas | Triangles | Temps | Pic RSS | Résultat |
|---|---|---|---|---|
| figurine 0.4 Normal | 62 016 | 2,33 s | 228 Mo | 90 contacts, 0 erreur |
| figurine 0.2 Dense | 62 016 | 4,65 s | 348 Mo | 284 contacts, 3 faux `floating_support` (QA-1) |
| figurine + raft | 62 016 | 2,02 s | 228 Mo | 1 contact non routé (QA-9) |
| figurine `allow_on_model=False` | 62 016 | 2,30 s | 230 Mo | 5 contacts non routés (signalés) |
| figurine tournée X 90° | 62 016 | 3,38 s | 278 Mo | 261 contacts, 0 erreur |
| figurine × 2 (120 mm) | 62 016 | 5,42 s | 398 Mo | 0 erreur, 37 entretoises |
| figurine × 4 (240 mm) | 62 016 | 21,9 s | 861 Mo | 713 contacts |
| figurine × 8 (480 mm) | 62 016 | 86,4 s | 2 610 Mo | 2 544 contacts |
| figurine × 25,4 (1,5 m) | 62 016 | - | > 6 Go | MemoryError (QA-8) |
| figurine subdivisée | **992 256** | **10,4 s** (10,9 s dans le test) | **1 089 Mo** | 89 contacts, 0 erreur |
| CLI complet 1 M (chargement, génération, export 3MF, validation) | 992 256 | 32,9 s | 2 247 Mo | code 0, 3MF de 12,8 Mo |
| GUI 1 M : chargement / génération / 1er picking / picking suivant / coupe | 992 256 | 2,5 s / 13,5 s / 4,49 s / 120 ms / 565 ms | 1 715 Mo | voir QA-11 |

`/usr/bin/time` n'est pas installé : le pic mémoire vient de `resource.getrusage(...).ru_maxrss`.

## Ce qui n'a PAS pu être testé

- **Windows** : ni l'application, ni le packaging PyInstaller, ni les chemins ou l'encodage.
- **Vrais slicers** (Bambu Studio, OrcaSlicer, PrusaSlicer, Cura) : aucun n'est installé.
  La conformité 3MF repose sur lib3mf (strict), trimesh et le validateur interne, pas sur
  l'import réel (dialogue « multi-part object », ordre des objets, supports automatiques du
  slicer).
- **Impressions physiques** : rien ne prouve que les pointes se détachent proprement, que les
  troncs tiennent ou que les profils sont bons. Les profils disent eux-mêmes « not validated ».
- **Rendu OpenGL** : en offscreen, `QOpenGLWidget: Failed to create context`. L'affichage
  réel, la profondeur, le shader, la fluidité (FPS) avec 1 M de triangles et l'aperçu fantôme
  au survol n'ont pas été vus. Le picking, la coupe et les visibilités ont été testés sur les
  données des items, pas sur l'image.
- Interactions à la souris autres que le clic : rotation et panoramique par glisser, molette,
  HiDPI, multi-écran.
- Gros maillages au-delà de 1 M de triangles, et 3MF multi-objets ou avec extensions
  importés depuis d'autres logiciels.
