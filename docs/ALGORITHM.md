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

Les composantes qui ne touchent pas du tout la couche précédente sont des **îlots** ; les autres morceaux non soutenus sont des **surplombs**. Les morceaux plus petits que `min_region_area` sont ignorés (bruit, ponts courts). Les résultats sont consultables couche par couche (`AnalysisResult.layer_polygons`, coupe par hauteur dans la vue).

**Analyse des faces.** Une face dont l'inclinaison vers le bas dépasse l'angle (-n_z > sin(angle)) est en surplomb. On en dérive les arêtes suspendues, les coins des surplombs plats et les points bas locaux (bout d'un bras, menton). Cette analyse attrape les pentes qui n'apparaissent que comme des bandes très fines dans les couches.

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
