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
2. points bas ;
3. coins des surplombs plats ;
4. rangées le long des arêtes suspendues : segments chaînés en polylignes (coupées aux virages > 45°), échantillonnées régulièrement à `edge_spacing / contact_density`, indépendamment de la tessellation ;
5. remplissage des îlots et surplombs en grille hexagonale à `contact_spacing / contact_density` ;
6. remplissage résiduel : grille XY fixe projetée sur les faces en surplomb ; tout point à plus d'un espacement d'un contact en reçoit un.

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
