# Algorithme du moteur de supports

Repère : millimètres, Z vers le haut, plateau à z = 0. Le moteur (`arbor/engine`) n'importe jamais Qt. Point d'entrée unique : `pipeline.generate(mesh, params, ...)`, en cinq étapes chronométrées séparément.

```
analysis.analyze  ->  contacts.place_contacts  ->  tree.build_tree  ->  build.mesh_tree / mesh_raft  ->  verify.verify
```

Garde-fous en entrée : `params.validate()` (bornes, valeurs finies) et `check_model_size` (refus au-delà de 1000 mm, presque toujours une erreur d'unité).

## 1. Analyse (`analysis.py`)

Deux détecteurs complémentaires.

**Slicing virtuel (référence).** Le modèle est coupé au centre de chaque couche (manifold3d `slice`, ou un slicer numpy de secours si le maillage n'est pas manifold). Pour la couche i, avec d = hauteur de couche x tan(angle de surplomb) :

```
non_soutenu_i = couche_i - offset(couche_{i-1}, d)
```

Le pas d est plafonné à une largeur de ligne (`MAX_STEP_LINE_WIDTHS`) : au-delà, le cordon extérieur n'a plus aucun recouvrement avec la couche du dessous (le plafond ne joue qu'au-dessus de 70° pour 0,22/0,08 et 74° pour 0,42/0,12 ; les profils restent à 55°). Une valeur explicite de `max_unsupported_overhang` est prise telle quelle. Détails et sources : `docs/research/overhang_angles.md`.

Les composantes qui ne touchent pas du tout la couche précédente sont des **îlots** ; les autres morceaux non soutenus sont des **surplombs**. Les morceaux plus petits que `min_region_area` sont ignorés (bruit, ponts courts). Les résultats sont consultables couche par couche (`AnalysisResult.layer_polygons`, coupe par hauteur dans la vue).

**Analyse des faces.** Une face dont l'inclinaison vers le bas dépasse l'angle effectif atan(d / hauteur de couche) (-n_z > sin(angle)) est en surplomb. Cet angle est égal à `overhang_angle_deg` sauf si d est plafonné ou explicite : les deux détecteurs gardent ainsi le même seuil. On en dérive les arêtes suspendues, les coins des surplombs plats et les points bas locaux (bout d'un bras, menton). Cette analyse attrape les pentes qui n'apparaissent que comme des bandes très fines dans les couches.

**Sévérité des surplombs** (`severe_layer_step`, `severe_overhang_angle_deg`, version 0.4). Un surplomb dont chaque couche dépasse la précédente de plus que le seuil mais de moins de `SEVERE_STEP_LINE_WIDTHS` = 0,75 largeur de ligne est **modéré** : le cordon extérieur garde plus de 25 % de recouvrement (classes 25-50 % et 50-75 % d'OrcaSlicer), il s'imprime avec un peu d'affaissement. Au-delà il est **sévère** (classe 75-100 %, pratiquement dans le vide). Angle correspondant : atan(0,75 x largeur / couche), soit 64,1° en buse 0,2 (0,22 / 0,08) et 69,2° en 0,4 (0,42 / 0,12) ; seuil de surplomb 55° dans les deux cas. Chaque région `overhang` porte `severity` (`severe` si la partie qui dépasse 0,75 largeur fait au moins la moitié de `min_region_area`, sinon `mild`), et `AnalysisResult.severe_face_mask` marque les faces au-delà de l'angle sévère. Les îlots sont toujours sévères. `mild_overhang_spacing` = 1 supprime la distinction (comportement 0.3). La vue colore les deux bandes (orange : modéré, contacts plus espacés ; rouge : sévère) avec ces mêmes angles.

## 2. Placement des contacts (`contacts.py`)

Ordre de priorité (un candidat est ignoré si un contact accepté le couvre déjà) :

0. contacts manuels (jamais modifiés) ;
1. un contact obligatoire par îlot ;
1b. **ancrage des îlots qui commencent en un point** (ci-dessous) : couronne de 6 contacts à pointe épaisse autour du point bas ;
2. points bas ;
3. coins des surplombs plats ;
4. rangées le long des arêtes suspendues : segments chaînés en polylignes (coupées aux virages > 45°), échantillonnées régulièrement à `edge_spacing / contact_density`, indépendamment de la tessellation ; sur une **pièce étroite** (lame, sangle, ailette), une seule rangée centrale au lieu d'une par arête (ci-dessous) ;
5. remplissage des îlots et surplombs en grille hexagonale à `contact_spacing / contact_density` ;
6. remplissage résiduel : grille XY fixe projetée sur les faces en surplomb ; tout point à plus d'un espacement d'un contact en reçoit un.

Aux étapes 5 et 6, les candidats posés sur une face **modérée** (section 1) sont traités après les autres avec un rayon de couverture multiplié par `mild_overhang_spacing` (2 dans les profils) : contacts deux fois plus espacés, note « moderate overhang, contacts 2x sparser ». Les îlots, leur ancrage, les points bas, les coins et les arêtes gardent la densité normale. `verify` (`unsupported_region`) applique la même portée multipliée aux régions modérées.

**Espacement effectif entre pointes** (`effective_min_contact_distance`) : deux contacts automatiques restent à au moins max(`min_contact_distance`, `tip_base_diameter` + `line_width`), 0,92 mm en buse 0,2 (au lieu de 0,45 à 0,75 selon le profil), 1,42 mm en buse 0,4 (au lieu de 0,8). En dessous, les bases des deux pointes se chevauchent ou laissent une fente plus étroite qu'un trait : le trancheur les imprime en éclats soudés et micro-îlots (remplissage d'espace, rétractions en plus) au lieu de deux cônes nets. Exceptions voulues : le contact obligatoire d'un îlot et la couronne d'ancrage (étape 1b) gardent `min_contact_distance` (grappe volontaire de pointes épaisses dont le haut est extrudé en boucle). `verify` (`contacts_too_close`) utilise le même seuil pour les contacts manuels.

**Pièces étroites** (`_centre_narrow_edges`, étape 4) : pour chaque échantillon d'arête, la largeur de la pièce est mesurée en travers de l'arête sur la tranche du modèle juste au-dessus (`layer_polygons`). Si elle est au plus `narrow_width_limit` = 2 sqrt(r² - (g/2)²), avec r = 0,55 x `contact_spacing` (rayon de couverture du remplissage) et g l'écart le long de l'arête, des deux côtés de l'échantillon le long de l'arête (à ±2 couches, pour qu'un bout de lame qui rejoint une garde ou un pilier garde ses deux contacts), l'échantillon est déplacé au milieu de la largeur et reprojeté sur le modèle (face en surplomb, même hauteur à une demi-largeur près). Les échantillons de la seconde arête tombent alors entre ceux de la première et sont couverts par eux : une rangée centrale. Par construction, tout point de la bande reste à moins de r d'un contact (bords compris) : 2,10 mm en 0,2 Normal, 2,62 mm en 0,4 Normal. Une pièce plus large garde ses deux rangées (la consigne « < 2 x `edge_spacing` », 3,2 mm, laisserait les bords d'une lame de 3 mm à 1,7 mm du contact le plus proche, plus que r = 1,32 mm). Note du contact : « centre line of a part ... mm wide ».

Effet (figurine, radeau activé, `docs/PHYSICAL_TESTS.md` essai 3) : 0,2 Normal 130 -> 111 contacts (côté épée 40 -> 32, plus aucune paire < 0,92 mm ; voisins à moins de 1,6 mm partageant des couches 2,7 -> 2,0 par pointe), 0,4 Normal 101 -> 80. Couverture des faces en surplomb (distance d'un échantillon tous les 0,1 mm au contact le plus proche) inchangée en 0,2 (max 2,29 mm, côtés bras 1,42 mm), 2,38 -> 2,13 mm en 0,4 (p99 1,81 -> 1,71 ; côté épée p99 1,42 -> 1,46).

Non retenu : faire fusionner les pointes seulement après une descente minimale (ou par paires). Mesuré, la longueur libre d'une pointe avant sa première fusion est la même sous les bras et sous le corps (médiane 2,6 contre 2,9 mm), et aucun trajet de support n'est imprimé dans le vide (0,6 mm sur 157 couches côté épée) : rien dans le G-code n'indique que la convergence sous le modèle pose problème. Les pointes de remplissage des régions fines (étape 5, `_outline_samples`) ne sont pas recentrées.

### Ancrage des débuts d'îlots (étape 1b)

Origine : essai 2 (`docs/PHYSICAL_TESTS.md`), les mains de la figurine (sphères r 2,6 mm) sont sorties en nid de fils. Le bas d'une forme ronde passe de presque rien à environ 40 mm de trajet par couche en 0,6 mm de hauteur ; il n'était tenu que par la pointe de 0,26 mm du point bas, car le contact d'îlot « couvrait » un rayon de 0,55 x `contact_spacing` = 1,3 mm et les premiers contacts de remplissage n'arrivaient que 0,4 mm plus haut. Dans OrcaSlicer (parois « classic », `detect_thin_wall` désactivé, réglages A1 mini), une section de support plus étroite qu'environ 2 largeurs de ligne n'est pas extrudée en boucle : le haut d'une pointe de 0,26 mm ne sort qu'en fragments (0,15 à 0,5 mm de trajet par couche, mesuré dans le G-code).

Règle :
- **Empreinte** (`island_anchor_footprint`) : l'îlot est suivi couche par couche (composantes qui touchent la sélection de la couche précédente) sur `island_anchor_height` (1 mm) au-dessus de sa première couche. Le suivi s'arrête avant la couche où l'îlot rejoint une autre matière (bras, autre îlot) : à partir de là il est tenu par elle. Sur la figurine, la main rejoint le bras vers z = 40,0 : empreinte jusqu'à z = 39,96.
- **Critère « commence en un point »** : aire de la première couche <= `ANCHOR_START_RATIO` (0,5) x aire de l'empreinte. Un dessous plat (première couche = empreinte) n'est pas concerné : contacts et pointes inchangés.
- **Couronne** : 6 points sur un hexagone régulier de côté `island_anchor_spacing` autour du point bas (retenté à 0,75 x si moins de 3 points conviennent, jamais sous `min_contact_distance`), gardés s'ils sont dans l'empreinte (une demi-largeur de ligne à l'intérieur), hors de la première couche, sous la hauteur d'ancrage et sur une face du masque de surplomb (la partie plate du bas rond, là où chaque couche grandit plus vite qu'elle ne peut s'imprimer sans support ; sur une sphère, à moins de R x sin(35°) de l'axe). Raison `ISLAND`, note « island anchor: ... ».
- **Pointes** : les contacts de la couronne et le contact obligatoire de l'îlot reçoivent `Contact.tip_diameter = island_tip_diameter`. Le routage (`tree.tip_base_r`) garde la conicité du profil pour toute pointe plus épaisse que `tip_diameter` : base = pointe + (`tip_base_diameter` - `tip_diameter`), soit 0,6 -> 1,04 mm en buse 0,2 ; la pointe est plus raide sous le contact au lieu de devenir un cylindre. Le budget de pénétration de `model_collision` est calculé contact par contact avec cette base.
- **Au plus 7 pointes épaisses par îlot** : sur une grosse boule, les parties plus hautes gardent les contacts ordinaires.

Valeurs (profils `_nozzle_02.json` / `_nozzle_04.json`) :

| Paramètre | Buse 0,2 | Buse 0,4 | Justification |
|---|---|---|---|
| `island_anchor_height` | 1,0 | 1,0 | sur une sphère de 2,6 mm, la pente atteint l'angle de surplomb (55°) à 0,5 mm du bas ; 1 mm couvre ce bas plat avec une marge |
| `island_anchor_spacing` | 1,0 | 1,2 | couronne à 2 à 4 couches au-dessus du point bas d'une main de 2,6 mm (z + 0,2 mm en 0,2) ; 1,6 mm en 0,4 sortait de l'empreinte de la main |
| `island_tip_diameter` | 0,6 | 1,0 | environ 2,5 largeurs de ligne. Mesuré dans Orca 2.4.2 : sous la 1re couche de la main gauche, 0,15 mm de trajet de support avec 0,45 (2 largeurs), 0,8 mm avec 0,6 ; sur les 3 premières couches 2,8 mm avec 0,45, 5,7 mm avec 0,6 ; en 0,4, 1,0 ancre la 2e couche de la main (3,7 mm de trajet sous les cordons contre 0,7 avec 0,8) |

`island_anchor_height = 0` désactive l'ancrage. Le contrôle « contacts trop denses » (`hard_to_remove`, section 5) ignore les contacts d'ancrage : leur densité est voulue (7 pointes de 1,0 mm dans un disque de 1,5 mm couvrent 78 % en buse 0,4) ; ils restent comptés dans `contact_area_mm2`.

Effet sur les exemples (radeau activé) : figurine 0,2 Normal 124 -> 130 contacts, marques 7,08 -> 10,61 mm² ; 0,4 Normal 90 -> 101, 15,15 -> 25,66 mm² ; `suspended_arm` 0,2 17 -> 19, 0,90 -> 2,62 mm². Aucun changement sur `shelf`, `floating_cube`, `sphere_on_plate`. Mesures G-code : `docs/PHYSICAL_TESTS.md`, essai 2.

Limites : non validé par une nouvelle impression ; plus de marques sous les bas ronds (7 pointes épaisses par îlot, 5,5 mm² par main en buse 0,4). Seuls les îlots de l'analyse sont ancrés : un bas rond qui rejoint une partie soutenue avant d'atteindre `min_region_area` (îlot jamais promu) garde son seul contact de point bas. Les pointes ordinaires (0,26 mm en 0,2) restent plus fines que ce que le générateur de parois « classic » d'Orca extrude : le haut de ces pointes n'est imprimé qu'en fragments partout (à traiter par profil ou avec `detect_thin_wall` / Arachne, non mesuré ici). Pointes plus courtes envisagées, non retenues : `tip_length` est global au routage (dégagement, plancher, direction) ; la base élargie donne déjà la raideur.

Les positions sont projetées verticalement sur le modèle (lancer de rayons vers le haut, par blocs pour borner la mémoire). **Évitement des détails** : la rugosité locale (1 - |normale moyenne|) des faces vers le bas dans un rayon de 0,4 mm est mesurée ; sur un point rugueux (écailles, cheveux, doigts), six positions voisines sont essayées et la plus lisse est retenue si elle reste en surplomb et dans la même couche. Chaque contact porte une raison (`ContactReason`) et une note lisible.

### Jeu d'interface (`interface_gap_layers`, version 0.4)

Origine : essai 5 (`docs/PHYSICAL_TESTS.md`) : toutes les pointes étaient soudées au modèle (pénétration de 0,03 mm, aucun jeu), d'où des marques profondes et une pointe restée collée à une main. Les supports des trancheurs laissent au contraire un jeu vertical (« top Z distance ») entre le haut du support et le modèle.

Règle (`tree.contact_gap`) : les pointes des contacts `edge`, `corner` et `surface` s'arrêtent `interface_gap_layers` couches (1 dans les profils) sous le contact, sans pénétration ; le trancheur laisse une couche d'air et pose la première couche du surplomb sur le haut des pointes sans les y fondre. Les contacts d'îlot (et leur couronne d'ancrage), les points bas et les contacts manuels restent soudés (`tip_penetration`, `z_gap`) : une pièce qui commence dans le vide doit être tenue dès ses premières couches (essai 2). Un contact trop proche de la surface du dessous pour une couche d'air plus une couche de support garde la pointe soudée (support court).

Deux corrections géométriques, mesurées :
- le disque du haut de la pointe est perpendiculaire à son axe ; sous une pente, ou si l'axe est incliné, son bord remonte dans la couche d'air. `_clear_gap` abaisse la pointe jusqu'à ce que tout le disque (centre et 12 points du bord) soit au moins à un jeu sous le modèle, mesuré verticalement comme le voit le trancheur (avant : 26 pointes sur 87 à moins de 0,06 mm, certaines au contact) ;
- une pointe dont l'axe est incliné de plus de 10° reçoit un **col vertical** de 2 couches sous son sommet (`NECK_LAYERS`) : sinon le bord relevé obligeait à descendre toute la pointe d'une couche de plus (2 couches d'air sur 19 des 61 contacts plats de la figurine, mesuré dans le G-code d'Orca).

Mesure (figurine, 0,2 Normal, export « Open in OrcaSlicer », Orca 2.4.2, A1 mini 0,2, 0.08mm Optimal ; contacts sous des faces presque horizontales, extrusion à moins de 0,12 mm du contact) : avant, 63 contacts sur 63 sans aucune couche vide entre support et modèle ; après, 61 pointes à jeu sur 61 avec exactement une couche vide, les pointes soudées (mains, îlots) toujours sans couche vide. `GenerationResult.stats` : `interface_gap_contacts` (87 sur 103 contacts) et `welded_contact_area_mm2` (4,43 mm² contre 9,56 avant pour `contact_area_mm2`).

## 3. Routage des arbres (`tree.py`)

1. **Grille du modèle** : voxelisation en intervalles d'occupation verticaux par colonne XY ; champ de distance 2D au modèle par tranche de hauteur `segment_length` (scipy EDT).
2. **Accessibilité** (de bas en haut) : pour chaque niveau, cellules depuis lesquelles le plateau est atteignable en respectant le dégagement et l'angle maximal de branche.
3. **Pointes** : direction issue de la normale ; si la base de pointe manque de dégagement, la pointe s'incline (au plus de l'angle de branche) vers le côté le plus libre. Contacts à faible dégagement vertical (moins d'une pointe) : **support court**, cône vertical posé sur le plateau ou sur le modèle ; en dessous d'une couche, rien n'est construit (`sub_layer_gap`).
4. **Descente niveau par niveau** : chaque nœud descend d'un niveau et se déplace d'au plus dz x tan(angle) vers son voisin ; les nœuds qui peuvent se rejoindre fusionnent (regroupement glouton sur KD-tree). Le dégagement est vérifié sur deux tranches (celle du nœud et celle que traverse le segment). Le rayon grandit avec le nombre de pointes fusionnées et la hauteur descendue (`trunk_growth_per_mm`).
5. **Atterrissage** : sur le plateau (pied large + collerette), ou sur le modèle si autorisé et si le trajet ne traverse pas le modèle (`MODEL_FOOT`), sinon échec signalé avec sa raison.
6. **Entretoises** entre troncs hauts voisins, zone d'exclusion du raft.
   - Passe régulière : diagonales entre chaque tronc non ramifié plus haut que `brace_min_height` et ses 2 plus proches voisins, une tous les `brace_spacing_z`, à l'angle de branche.
   - **Groupes isolés** (essai 4 : trois arbres de 42 mm alignés, reliés entre eux seulement, renversés d'un bloc) : les pieds reliés par des entretoises forment des groupes ; un groupe (ou un arbre seul) contenant un arbre > `brace_min_height` est stable si sa largeur dans sa direction la plus étroite (centres des pieds + un diamètre de pied) vaut au moins hauteur / 5 (`BRACE_GROUP_MAX_ASPECT` : poussée F à la hauteur H, traction ≈ F·H/w aux pieds ; le groupe tombé était à 6,6). Deux pieds, ou des pieds alignés, ne sont larges que d'un pied : ce critère remplace un nombre minimal de pieds. Un groupe instable est relié au tronc principal (enfant tronc le plus haut à chaque fourche) voisin le plus proche, puis on recommence jusqu'à stabilité. Ces liaisons : course horizontale jusqu'à 1,5 x `brace_max_length` (l'écart bras/corps de la figurine est d'environ 1,3 x), départ juste au-dessus des collerettes, niveaux essayés tous les 2 mm puis un tous les `brace_spacing_z`, chaque extrémité sous le sommet de son propre tronc (un voisin court peut porter le bas d'une diagonale vers un tronc haut), inclinaison de l'angle de branche et contrôle de l'angle imprimable, diamètre porté à longueur / 18 si nécessaire (jamais au-delà de 0,85 x le rayon des troncs), dégagement au modèle `xy_clearance` + rayon comme les branches. Rien n'est ajouté si aucun groupe n'est instable. Un groupe qui reste instable (trop loin, modèle au milieu) est signalé par `isolated_tall_tree` (avertissement, position du pied le plus haut).

## 4. Maillage (`build.py`)

Chaque chaîne non ramifiée devient un tube lissé ; sphères aux jonctions (rayon x `junction_radius_factor`), pieds, collerettes, plots sur le modèle, entretoises : primitives manifold3d fusionnées par un seul `batch_boolean(Add)`. Le résultat est fermé par construction, puis coupé par le plan de base des supports (`tree.z_base` : plateau ou dessus du raft). Raft : section 4b. Supports creux : section 4c.

## 4b. Raft et surélévation (`params.raft_*`, `placement.py`, `build.raft_polygon`)

Le raft est activé par défaut (paramètres et profils intégrés). Les projets `.arbor` enregistrés avant `raft_coverage` gardent leur comportement (`project._legacy_param_defaults` : raft « feet »), comme les presets utilisateur au format 1 (voir plus bas).

**Repères.** Le modèle n'est jamais déplacé ni modifié : il reste à z = 0 dans le repère de l'utilisateur, tout le reste est placé dessous. `GenerationResult.model_lift` (surélévation) et `plate_z` (plateau dans le repère du modèle) décrivent le placement ; l'export 3MF (`positive_octant`) remonte la scène entière, les positions relatives sont conservées.

| `raft_coverage` | Contour | Modèle | Base des supports (`z_base`) | Raft |
|---|---|---|---|---|
| `feet` (v0.1) | enveloppe de chaque grappe de pieds + `raft_margin`, moins l'empreinte du modèle, morceaux reliés par un anneau | sur le plateau | `raft_thickness` | [0, t] |
| `footprint` (défaut) | une seule plaque : enveloppe convexe de la base du modèle (sommets à moins d'une couche du sol) et des disques des pieds, + `raft_margin` | posé sur le raft, jeu g | -g | [-g-t, -g] |
| `bed` | rectangle englobant du modèle (projection XY entière) et des pieds, + `raft_margin` ; jamais la surface du plateau | posé sur le raft, jeu g | -g | [-g-t, -g] |

Mesures (figurine, 0,2 Normal, A1 mini, OrcaSlicer 2.4.2 en ligne de commande, 0,08 mm, export « Open in OrcaSlicer ») :

| Mode | Surface du raft | Volume du raft | Supports | Scène entière (Orca) |
|---|---|---|---|---|
| `feet` | 800 mm² | 128 mm³ | 2973 mm³ | 9,58 g, 3 h 41 min 34 s |
| `footprint` | 1587 mm² | 254 mm³ | 2919 mm³ | 9,56 g, 3 h 39 min 38 s |
| `bed` | 2331 mm² | 373 mm³ | 2919 mm³ | 9,73 g, 3 h 42 min 32 s |

**Jeu de décollement** g = `raft_release_gap` (une couche dans les profils : 0,08 / 0,12 mm ; 0 = soudé) : le dessus du raft est g sous le fond du modèle, sur toute l'empreinte. L'analyse et les contacts gardent le sol du modèle à z = 0 (la base n'est pas supportée) ; les supports partent du dessus du raft. Mesure G-code (figurine, Orca 2.4.2, A1 mini 0,2, 0.08mm Optimal, disque de 12 mm sous le socle) : raft aux couches z = 0,10 et 0,18 ; couche 0,26 vide sous le socle ; première couche du modèle à z = 0,34 imprimée en « Bridge » (3156 mm de trajet) au-dessus du raft, comme une interface de support. Avec g = 0 : remplissage solide continu de 0,10 à 0,42 (socle soudé). Tranchage accepté (retour 0, sans « empty first layer »), 9,57 g, 3 h 37 min ; pointes à 0,03 mm du modèle au plus.

Mode `feet` : un contact plus bas que le dessus du raft + une couche (`raft_thickness` + `layer_height` au-dessus du plateau) n'a pas la place d'un support (il partirait dans le raft) : il est signalé (`unrouted_contact`, raison « below the raft top ... use raft_coverage 'footprint' ») au lieu de rester sans pointe ni explication. Router un support court posé sur le plateau dans le trou du raft demanderait de couper les supports sous `z_base` localement ; non retenu (1 à 2 couches de support, le mode `footprint` les supporte normalement).

Presets utilisateur au format 1 (avant v0.2, raft désactivé dans les profils) : sans mention du raft, il reste désactivé ; activé, il garde `raft_coverage = feet` (`presets.LEGACY_V1_VALUES`, format 2 depuis).

Le surcoût du raft commun est faible (+0,2 mm³ de raft par mm² ; le mode `footprint` coûte même un peu moins au total, car les supports partent 0,16 mm plus bas sans contourner l'empreinte). Retrait : en `feet`, le modèle est imprimé sur le plateau et le raft ne touche que les pieds ; en `footprint` / `bed`, la base repose sur le raft à travers le jeu de décollement (pont d'une couche, détachable comme une interface) ; avec `raft_release_gap = 0` elle est soudée et le raft se découpe au ras du socle. Les mesures du tableau sont sans jeu (g = 0) ; avec le jeu : 9,57 g, 3 h 37 min. Le mode `bed` ajoute surtout de la surface (adhérence) sans tenir mieux les pieds que `footprint`.

Conséquence export : un modèle posé sur le raft ne touche pas le plateau ; la disposition `separate` écrit alors un seul objet « ... with supports » (sinon le trancheur le poserait sur le plateau et l'alignement serait perdu), donc les réglages par objet (`layer_heights`, `object_settings`) s'appliquent au modèle et aux supports ensemble.

**Surélévation automatique** (`raft_lift_auto`, modèle posé sur le raft uniquement). Dégagement requis c = `tip_length` + `branch_min_diameter`/2 + 0,1 + 2 couches (1,91 mm en 0,2 ; 2,44 mm en 0,4), la même portée que le test « support court » du routage plus deux couches de pied. Si des faces en surplomb (angle effectif de l'analyse, hors première couche) ont leur point bas à moins de c du sol, pour au moins `min_region_area`, le modèle est surélevé de L = c - (point bas le plus bas des faces en surplomb, base comprise), arrondi à 0,01 mm, borné à `RAFT_LIFT_MAX` = 5 mm. Son fond devient un îlot de l'analyse et est supporté par la détection existante (contact d'îlot, couronne d'ancrage, remplissage). Refus (L = 0, avertissement `raft_lift_refused` avec la raison) si la base plate à supporter dépasse `LIFT_MAX_BASE_AREA` = 25 mm², si elle dépasse la surface de surplomb bas qu'elle corrigerait, ou si L > 5 mm. `raft_lift_auto = False` : `raft_lift` est appliqué tel quel (0 à 5 mm, sans garde-fou). Info `model_lifted` quand L > 0.

Mise en œuvre : `pipeline.generate` calcule le plan, travaille sur une copie du modèle translatée de +L (analyse, contacts, routage, maillage, vérification dans ce repère), puis translate de -L les supports, le raft, l'arbre, les contacts créés, l'analyse et les positions des problèmes. Les contacts fournis (`manual_contacts`, `contacts`) sont déplacés pour le calcul puis restaurés à l'identique. Une analyse réutilisée n'est acceptée que si elle a été calculée avec la même surélévation (`analysis.stats["model_lift"]`).

Exemples (0,2 Normal) : sphère r 5 posée sur un point (`floating_sphere` descendue à z = 0) : L = 1,91 mm, 13 contacts, 27,6 mm³ de supports au lieu de 0,4 mm³ de cônes courts ; colonne 3 x 3 mm avec un bras à 1,8 mm du plateau : L = 1,91 mm, fond de la colonne supporté ; même bras sur une colonne 10 x 10 : refusé (base de 100 mm²) ; cylindre r 4 couché : refusé (bande de contact de 31 mm²) ; figurine (socle de 707 mm², surplombs à 18 mm) : aucun surplomb bas, pas de surélévation. Les deux modèles surélevés se tranchent dans Orca (un seul objet, retour 0).

Limites : la stabilité d'un modèle surélevé (centre de gravité, nombre de pieds) n'est pas évaluée au-delà du critère de base plate ; le seuil de 25 mm² est empirique, non validé par impression ; la surélévation ne s'applique pas au mode `feet` (modèle sur le plateau).

## 4c. Supports creux (`hollow_supports`, désactivé par défaut)

**(a) Creux géométrique** (`build.hollow_cores`) : le long de chaque chaîne de tube, les suites de segments dont le rayon reste >= `hollow_wall` + `line_width` (paroi plus un trou d'au moins 2 lignes) reçoivent un noyau de même axe, de rayon r - `hollow_wall`, raccourci de `hollow_wall` à chaque bout et, sur un pied, commencé au-dessus du disque et de la collerette. Chaque noyau est à au moins `hollow_wall` à l'intérieur de son propre solide, donc de l'union : le vide est fermé (pas de trou en haut, le trancheur fait les parois et un petit pont). Pointes, plots, branches fines, sphères de jonction, pieds, collerettes et raft restent pleins. Le contrôle `support_floating_region` traite le plafond d'un trou fermé de la couche du dessous (<= `VOID_BRIDGE_MAX_AREA` = 4 mm²) comme un pont. `hollow_wall` = 3 largeurs de ligne (0,66 / 1,26 mm) : en buse 0,4 aucun tronc (2,6 mm) n'est assez large, l'option n'a pas d'effet.

**(b) Remplissage faible réglé dans le trancheur** pour l'objet supports : plus efficace et sans géométrie en plus. Dans Orca, une section de tronc (2,4 à 2,8 mm) est presque entièrement en parois et, ce qui reste, plus petit que `minimum_sparse_infill_area` (15 mm² dans le profil A1 mini), est rempli en plein : baisser seulement `wall_loops` et `sparse_infill_density` ne change rien.

Mesures Orca 2.4.2 (supports + raft seuls de la figurine, mode `footprint`, 0,2 Normal, profil A1 mini 0,08 mm) :

| Variante | Volume maillage | Filament | Temps |
|---|---|---|---|
| plein, profil (4 parois, 15 %) | 2919 mm³ | 3,86 g | 2 h 15 min 45 s |
| creux (a), profil | 2500 mm³ | 3,37 g (-13 %) | 2 h 10 min 10 s (-4 %) |
| plein, `wall_loops` 2 + 10 % | 2919 mm³ | 3,86 g (0 %) | 2 h 16 min 15 s |
| plein, `wall_loops` 2 + 10 % + `minimum_sparse_infill_area` 0 (b) | 2919 mm³ | 2,67 g (-31 %) | 1 h 43 min 4 s (-24 %) |
| creux (a) + (b) | 2500 mm³ | 3,04 g | 1 h 59 min 54 s |

Figurine entière avec (a) : 9,56 -> 9,06 g, 3 h 39 -> 3 h 32. (a) et (b) ne se cumulent pas : les parois autour du vide coûtent plus que le remplissage faible qu'elles remplacent. (b) est l'option recommandée ; elle demande que l'export écrive les réglages pour l'objet supports seul, ce qui n'est possible que si le modèle et les supports sont deux objets (raft `feet`, ou modèle et supports en parties distinctes d'un même objet, non implémenté dans `arbor/io/export.py`). Rigidité : le creux (a) garde 96 % du moment quadratique d'un tronc de 2,4 mm (1 - (0,54/1,2)^4) ; `fragile_support` ne tient pas compte du creux. Non validé par impression.

## 5. Vérification (`verify.py`)

Contrôles indépendants de la logique de routage (ils ne font pas confiance aux étiquettes de l'arbre) : contacts non routés ; régions de l'analyse sans contact routé à portée ; arbres dont la racine n'est ni sur le plateau ni sur le modèle ; pieds « sur le modèle » trop loin de sa surface ; composantes du maillage en l'air (les vides internes fermés sont ignorés) ; troncs élancés sans entretoise ; volume d'intersection supports/modèle comparé à la tolérance de pénétration des pointes ; validité du maillage des supports et du modèle ; contacts manuels trop proches ; **supports tranchés seuls** (`support_floating_region`) : le maillage des supports est découpé couche par couche comme par un slicer, et toute partie qui commencerait dans le vide ou dépasserait l'angle de surplomb est signalée. Le routage limite l'inclinaison des branches, pointes et entretoises à l'angle de surplomb moins 2°, et le bas des sphères de jonction est prolongé par une jupe conique noyée dans la branche.

### Supports fragiles ou difficiles à retirer

Deux contrôles ajoutés après le premier essai d'impression (arbre de 49 mm décroché, entretoise de 0,5 x 14 mm jugée fragile) et un retour d'utilisateur d'un outil comparable (supports faciles à retirer mais marques visibles). Ce sont des **règles empiriques, non calibrées par impression** : les seuils (constantes en tête de `verify.py`) ont été placés au-dessus des valeurs mesurées sur les modèles d'exemple avec les six profils, raft ou non, et ne se prononcent que sur la géométrie.

**`fragile_support`** (avertissement, position, `contact_id` d'une pointe de l'arbre). Élancement d'une section = bras de levier / diamètre. Le bras de levier est la hauteur de tout ce que la section porte (sommet de son sous-arbre moins la hauteur de la section) : pendant l'impression la buse pousse latéralement le haut du support, la section travaille comme l'encastrement d'une console (contrainte en levier / d³, flèche en levier³ / d⁴). Le rayon et le levier sont linéaires le long d'un segment, le maximum est donc à un nœud : on évalue tous les nœuds branche/tronc, la section d'un tronc posé sur un pied est prise au-dessus de la collerette (`foot_height + collar_height`), les pointes ne sont pas évaluées. Une seule alerte par arbre (sa pire section), et les arbres voisins de moins de 10 mm ayant le même défaut sont regroupés (« N supports like this nearby »).

| Cas | Seuil | Justification | Max mesuré sur les exemples |
|---|---|---|---|
| section >= 3 largeurs de ligne | levier / d > 25 | au moins 2 périmètres par côté | arbres sur plateau 20,0 ; arbres sur le modèle 23,1 |
| section < 3 largeurs de ligne | levier / d > 10 | une seule boucle de périmètre, sans remplissage (0,66 mm en buse 0,2 ; 1,26 mm en buse 0,4) | 7,5 |
| colonne posée sur le modèle | hauteur / diamètre du plot > 20 | ancrage ponctuel (`on_model_tip_diameter`) : 4,8 mm maxi sur un plot de 0,24, 8,4 mm sur 0,42 | voir ci-dessous |
| entretoise | longueur / d > 20, ou d < 2 largeurs de ligne | 0,5 x 14 mm (28) jugée fragile ; aucune boucle fermée sous 2 largeurs | 16,4 (liaisons de groupes isolés : 18, épaissies) |

Le pied sur le plateau (adhérence du disque) reste du ressort de `unstable_trunk` / `tall_tree_on_raft` : `fragile_support` ne le compte pas une deuxième fois. L'arbre de 49 mm du premier essai avait un tronc de 2,0 mm (élancement de section 23,6) : il est signalé par `unstable_trunk` (pied), pas par ce contrôle.

**`hard_to_remove`** (avertissement), deux cas :
- *support posé sur le modèle difficile d'accès* : depuis deux points de la colonne (mi-hauteur et 90 %), 65 rayons (16 azimuts x élévations -30°, 0°, 30°, 60°, plus la verticale) sont lancés dans une grille en colonnes du modèle (`tree.ModelGrid`, 0,3 mm) jusqu'à 20 mm. **Enfermé** si moins de 10 % des directions sont libres sur 20 mm ; **coincé** si, sur un axe horizontal, les deux parois opposées sont à moins de 2,5 mm l'une de l'autre (place d'une pince fine). Regroupement par 10 mm. Mesures : figurine (supports posés sur les épaules) 34 % de directions libres au minimum, largeur minimale 3,1 mm, non signalés ;
- *contacts trop denses* : pour chaque contact (pointes et plots sur le modèle), part d'un disque de 1,5 mm de rayon couverte par les marques (diamètre de marque = max(diamètre de pointe, largeur de ligne)) ; signalé au-delà de 30 % avec au moins 4 contacts. Maximum sur les exemples : 24 % (profil `nozzle_04_dense`, plan incliné).

Résultats sur les exemples avec les profils par défaut (`tests/test_support_checks.py`, liste `EXPECTED`) : aucun arbre sur le plateau, aucune entretoise ni zone de contacts denses signalés. Les seules alertes concernent des supports posés sur le modèle et sont de vrais défauts :
- `crossing_case` (buse 0,2) : colonnes de 12,1 mm sur des plots de 0,24 mm (50) ;
- `figurine` (buse 0,2, Normal et Dense) : une colonne de 22,1 mm posée sur le socle sur un plot de 0,24 mm (92) ;
- `impossible_case` : colonnes de 9,1 mm au fond du bol (38 en 0,2 ; 21,7 en 0,4), et enfermées (0 à 12 % de directions libres : seul un anneau de 3 mm au-dessus donne accès) ;
- `cavity` (tous profils sauf `nozzle_04_light`) : supports au fond de la fente de la « bouche » (3 mm de haut, 6 mm de profondeur), 5 % de directions libres.

Limites : élancement statique, sans matériau, température ni vitesse ; pas de calcul des efforts réels de la buse. L'accès est évalué en lignes droites (un outil courbe ou une ouverture plus étroite que la grille de 0,3 mm ne sont pas modélisés), seulement pour les supports posés sur le modèle (ceux qui partent du plateau s'arrachent par le bas). Les marques sont estimées par le diamètre des contacts, pas par la qualité de surface réelle (matière, `z_gap`, interface). Une colonne sur le modèle trop haute est signalée, pas corrigée : élargir le plot réduirait le risque mais agrandirait la marque, à décider par profil.

### Statistiques (`GenerationResult.stats`)

Calculées par `verify.support_stats` à chaque génération (comptées dans le temps `verify`), pour l'utilisateur et la comparaison de configurations (`arbor/core/compare.py`) :

| Clé | Sens |
|---|---|
| `support_volume_mm3` | volume du maillage des supports (raft exclu) |
| `support_mass_g` | volume x 1,24 g/cm³ (PLA plein : majorant, le trancheur remplit partiellement les gros troncs) |
| `raft_volume_mm3`, `raft_mass_g` | idem pour le raft (0 sans raft) |
| `contact_area_mm2` | somme des aires π r² des pointes et des plots posés sur le modèle (indicateur des marques) |
| `welded_contact_area_mm2` | la part de cette aire soudée au modèle (pointes sans jeu d'interface, plots) : les marques à casser |
| `interface_gap_contacts` | nombre de pointes séparées du modèle par le jeu d'interface |
| `model_contacts` | nombre de points de contact avec le modèle = `tip_contacts` + `model_feet` |
| `max_tree_height_mm` | hauteur maximale d'un arbre au-dessus de sa racine (pied ou plot) |
| `max_slenderness` | élancement de section maximal (plots compris) |
| `max_fragility` | max(élancement / seuil applicable) ; > 1 : `fragile_support` émis |
| `issues_by_code` | nombre de problèmes par code (toutes sévérités) |

## 6. Pointe paramétrique

| Paramètre | Rôle |
|---|---|
| `tip_diameter` | diamètre au contact |
| `tip_base_diameter` | diamètre où la pointe rejoint la branche |
| `tip_length` | longueur du cône |
| `tip_shape` | `cone` ou `ball` |
| `tip_penetration` | pénétration volontaire dans le modèle (adhérence de la pointe) |
| `z_gap` | jeu vertical retiré de la pointe |
| `interface_gap_layers` | couches d'air entre le modèle et les pointes d'arêtes, de coins et de surfaces (îlots et points bas soudés) |
| `xy_clearance` | jeu horizontal minimal des branches |
| `on_model_tip_diameter` | pointe des supports posés sur le modèle |
| `island_anchor_height` | hauteur suivie au début d'un îlot pour l'ancrage (0 = désactivé) |
| `island_anchor_spacing` | rayon de la couronne d'ancrage autour du point bas |
| `island_tip_diameter` | pointe des contacts d'ancrage et du contact obligatoire d'un îlot ancré |

Compromis : une pointe plus fine laisse moins de marques mais casse plus facilement et s'imprime moins bien (en dessous d'environ une largeur de ligne, le slicer peut ne rien extruder). Les profils partent de la largeur de ligne de la buse ; **ces valeurs ne sont pas encore validées par impression** (protocole : `docs/PHYSICAL_TESTS.md`).

## 7. Profils

`profiles/*.json` avec héritage (`_nozzle_02.json`, `_nozzle_04.json` abstraits). Les niveaux Light / Normal / Dense modifient de vrais paramètres : espacements des contacts, densité, diamètre de pointe.

| Profil | Couche | Pointe | Base | Espacement surface / arête |
|---|---|---|---|---|
| nozzle_02_light | 0,08 | 0,234 | 0,7 | 3,36 / 2,24 |
| nozzle_02_normal | 0,08 | 0,26 | 0,7 | 2,40 / 1,60 |
| nozzle_02_dense | 0,08 | 0,299 | 0,7 | 1,76 / 1,12 |
| nozzle_04_light | 0,12 | 0,405 | 1,0 | 4,20 / 2,80 |
| nozzle_04_normal | 0,12 | 0,45 | 1,0 | 3,00 / 2,00 |
| nozzle_04_dense | 0,12 | 0,517 | 1,0 | 2,20 / 1,40 |

Pour ajouter un profil : un fichier JSON avec `inherits` et les valeurs à changer.
