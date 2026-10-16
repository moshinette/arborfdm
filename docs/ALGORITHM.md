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

## 3. Routage des arbres (`tree.py`)

1. **Grille du modèle** : voxelisation en intervalles d'occupation verticaux par colonne XY ; champ de distance 2D au modèle par tranche de hauteur `segment_length` (scipy EDT).
2. **Accessibilité** (de bas en haut) : pour chaque niveau, cellules depuis lesquelles le plateau est atteignable en respectant le dégagement et l'angle maximal de branche.
3. **Pointes** : direction issue de la normale ; si la base de pointe manque de dégagement, la pointe s'incline (au plus de l'angle de branche) vers le côté le plus libre. Contacts à faible dégagement vertical (moins d'une pointe) : **support court**, cône vertical posé sur le plateau ou sur le modèle ; en dessous d'une couche, rien n'est construit (`sub_layer_gap`).
4. **Descente niveau par niveau** : chaque nœud descend d'un niveau et se déplace d'au plus dz x tan(angle) vers son voisin ; les nœuds qui peuvent se rejoindre fusionnent (regroupement glouton sur KD-tree). Le dégagement est vérifié sur deux tranches (celle du nœud et celle que traverse le segment). Le rayon grandit avec le nombre de pointes fusionnées et la hauteur descendue (`trunk_growth_per_mm`).
5. **Atterrissage** : sur le plateau (pied large + collerette), ou sur le modèle si autorisé et si le trajet ne traverse pas le modèle (`MODEL_FOOT`), sinon échec signalé avec sa raison.
6. **Entretoises** entre troncs hauts voisins, zone d'exclusion du raft.

## 4. Maillage (`build.py`)

Chaque chaîne non ramifiée devient un tube lissé ; sphères aux jonctions (rayon x `junction_radius_factor`), pieds, collerettes, plots sur le modèle, entretoises : primitives manifold3d fusionnées par un seul `batch_boolean(Add)`. Le résultat est fermé par construction, puis coupé par le plan du plateau (rien sous z = 0). Le raft est l'enveloppe des pieds, élargie de `raft_margin`, moins l'empreinte du modèle.

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
| entretoise | longueur / d > 20, ou d < 2 largeurs de ligne | 0,5 x 14 mm (28) jugée fragile ; aucune boucle fermée sous 2 largeurs | 16,4 |

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
