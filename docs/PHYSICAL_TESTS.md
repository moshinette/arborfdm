# Essais physiques : diamètre de pointe et densité

Ces essais ne sont **pas** automatisés : ils demandent une imprimante réelle. Aucun résultat n'est inscrit ici tant qu'une impression n'a pas été faite et mesurée. Les valeurs par défaut des profils (`profiles/*.json`) sont des points de départ, non validés.

## 1. Banc d'essai

Générateur : `scripts/make_tip_bench.py` (sortie dans `examples/test_bench/`).

- Socle 70 x 14 x 2 mm, quatre piliers.
- Chaque pilier porte une plaque plate en porte-à-faux de 10 x 10 x 1,2 mm, à 6, 9, 12 et 15 mm de hauteur.
- La plaque n° i est repérée par i petits plots sur le socle devant son pilier.
- Le dessous de chaque plaque est un surplomb horizontal : il doit être entièrement soutenu.

```
python scripts/make_tip_bench.py                                   # modèle nu
python scripts/make_tip_bench.py --supports --tips 0.3 0.4 0.5 0.6 # un 3MF par diamètre de pointe (buse 0,4)
python scripts/make_tip_bench.py --supports --profile nozzle_02_normal --tips 0.18 0.22 0.26 0.30   # buse 0,2
python scripts/make_tip_bench.py --supports --densities            # Light / Normal / Dense
```

## 2. Conditions à consigner (pour chaque série)

| Élément | Valeur |
|---|---|
| Imprimante, buse (diamètre, matériau) | |
| Trancheur et version | |
| Filament (marque, matière, couleur) | |
| Température buse / plateau | |
| Hauteur de couche (0,12 mm recommandé pour 0,4) | |
| Vitesse des parois externes | |
| Ventilation | |
| Version d'ArborFDM (commit) et profil | |

Importer le 3MF tel quel ; ne pas laisser le trancheur ajouter ses propres supports. Supports en tant que pièce normale (même matière).

## 3. Protocole

1. Imprimer une série complète (4 diamètres, puis 3 densités) avec les mêmes réglages.
2. Laisser refroidir 10 min sur le plateau.
3. Retirer les supports à la main, sans outil, plaque par plaque. Noter la note de retrait (1 = impossible sans outil, 5 = se détache seul).
4. Compter les pointes cassées avant la fin de l'impression (pointe non reliée à la plaque, fil visible sous la plaque).
5. Observer le dessous des plaques à la loupe (x10) : noter la marque (0 = invisible, 3 = cratère / arrachement de matière).
6. Mesurer l'affaissement du dessous de la plaque (jauge ou comparateur, mm sous la cote nominale) au centre et dans le coin le plus éloigné du pilier.
7. Photographier chaque plaque de dessous, nommer le fichier `serie_tip_0p40_plaque3.jpg`.
8. Répéter chaque série deux fois au minimum avant de conclure.

Critère d'acceptation proposé : aucune pointe cassée, affaissement < 0,2 mm, marque <= 1, retrait >= 3. Le diamètre retenu est le plus petit qui satisfait tous les critères sur les quatre plaques.

## 4. Résultats : diamètre de pointe

Buse : ____ Profil : ____ Date : ____

| Pointe (mm) | Plaque | Pointes cassées | Affaissement centre (mm) | Affaissement coin (mm) | Marque (0-3) | Retrait (1-5) | Remarques |
|---|---|---|---|---|---|---|---|
| 0,30 | 1 | | | | | | |
| 0,30 | 2 | | | | | | |
| 0,30 | 3 | | | | | | |
| 0,30 | 4 | | | | | | |
| 0,40 | 1 | | | | | | |
| 0,40 | 2 | | | | | | |
| 0,40 | 3 | | | | | | |
| 0,40 | 4 | | | | | | |
| 0,50 | 1 | | | | | | |
| 0,50 | 2 | | | | | | |
| 0,50 | 3 | | | | | | |
| 0,50 | 4 | | | | | | |
| 0,60 | 1 | | | | | | |
| 0,60 | 2 | | | | | | |
| 0,60 | 3 | | | | | | |
| 0,60 | 4 | | | | | | |

## 5. Résultats : densité

| Profil | Nb de contacts (sortie du script) | Pointes cassées | Affaissement max (mm) | Marque (0-3) | Retrait (1-5) | Temps d'impression | Masse supports (g) |
|---|---|---|---|---|---|---|---|
| Light | | | | | | | |
| Normal | | | | | | | |
| Dense | | | | | | | |

## 6. Exploitation

Reporter le diamètre et la densité retenus dans le profil concerné (`profiles/nozzle_XX_*.json`), avec en description la référence de l'essai (date, imprimante, matière). Ne jamais présenter une valeur comme « calibrée » sans tableau rempli ci-dessus.

## Résultats

### Essai 1 : 2026-10-15, figurine d'exemple

| Élément | Valeur |
|---|---|
| Imprimante, buse | Bambu Lab A1 mini, 0,2 mm acier inox |
| Trancheur | OrcaSlicer 2.4.2, préréglage « 0.08mm Optimal » |
| Profil ArborFDM | `nozzle_02_normal`, raft activé |
| Résultat | échec vers 50 % : un support s'est décroché, puis fils dans le vide autour de la zone qu'il tenait |

Observations de l'utilisateur : un support décroché vers la moitié de l'impression ; raft trop épais (0,8 mm) ; souhait d'un raft d'environ 0,15 mm.

Analyse de la génération correspondante : l'arbre le plus haut posé sur le plateau mesurait 49 mm sur un pied de 3,5 mm (élancement 28, avertissement `unstable_trunk` émis), branches de 0,7 mm, aucune entretoise ; le raft était coupé en 3 morceaux par l'empreinte du socle (deux pads de 73 et 42 mm²).

Corrections (commit suivant cet essai) :
- profils 0,2 : `branch_min_diameter` 0,7 -> 0,9 mm, `trunk_diameter` 2,0 -> 2,4 mm, `foot_diameter` 5 -> 6 mm, `raft_thickness` 0,8 -> 0,16 mm (2 couches de 0,08) ; profils 0,4 : `raft_thickness` 0,8 -> 0,24 mm ;
- les pieds des arbres hauts sont agrandis en proportion de leur hauteur (hauteur / 14), dans la limite de la place laissée par le modèle ;
- le raft est rejoint en une seule plaque par un anneau autour de l'empreinte du modèle ;
- fragments de support de volume quasi nul supprimés après l'union.

Limite : sur cette figurine, les deux arbres les plus hauts sont collés au socle et leur pied ne peut dépasser environ 4 mm ; sans raft ils restent signalés `unstable_trunk`. Avec le raft (désormais d'une seule pièce) ils sont ancrés à la plaque. À réimprimer pour confirmer.

### Essai 2 : 2026-10-15, figurine d'exemple (mains)

| Élément | Valeur |
|---|---|
| Imprimante, buse | Bambu Lab A1 mini, 0,2 mm |
| Trancheur | OrcaSlicer 2.4.2, couches de 0,08 mm |
| Modèle, profil | `examples/figurine.stl`, `nozzle_02_normal`, raft activé (version 2db7b96) |
| Résultat | figurine entière (torse, bras, raft, grands arbres) **sauf les deux mains** : nid de fils à leur place |

Les mains sont des sphères de r 2,6 mm au bout des bras (centre x = ±21, y = 0, bas à z = 39,4), des îlots qui commencent en un point dans le vide.

**Mesure dans le G-code d'Orca** (CLI 2.4.2, A1 mini buse 0,2, couches 0,08, `--no-check`, script de lecture du G-code : longueur des trajets extrudés par objet et par couche). Main gauche, dans un rayon de 3,2 mm de son axe, fichier `--layout objects` : à z = 39,46 (1re couche de la main) trajet modèle 0,8 mm / supports 3,0 mm ; z = 39,78 : 19,4 / 0,7 ; z = 40,10 : 39 / 0,3. Le bas de la main passe de presque rien à environ 40 mm de trajet par couche en 0,6 mm, porté par 7 pointes de 0,26 mm dans ce rayon (1 ISLAND + 6 SURFACE), dont 3 seulement sous la main elle-même, 0,4 mm plus haut que son point bas (les autres sont à la jonction avec le bras) : les premières couches ne s'ancrent pas et la buse les entraîne.

Causes trouvées :
1. **Placement** : le contact du point bas « couvrait » 1,3 mm autour de lui (0,55 x `contact_spacing`), donc rien sous les 5 premières couches de la main hors de ce point.
2. **Pointes trop fines pour le trancheur** : avec les parois « classic » et `detect_thin_wall` désactivé (réglages A1 mini), Orca n'extrude pas en boucle une section plus étroite qu'environ 2 largeurs de ligne. Le haut de la pointe de 0,26 mm du point bas ne sort qu'en fragments (0,15 à 0,5 mm de trajet par couche).
3. **Disposition du 3MF** : le G-code mesuré ci-dessus vient de la disposition `objects` (3 objets : modèle, supports, radeau). Orca pose l'objet supports sur le plateau (`ensure_on_bed`, `docs/SLICERS.md`) : tous les supports descendent de l'épaisseur du raft (0,16 mm, 2 couches), il reste 2 couches d'air sous chaque pointe. Les grandes surfaces (torse, bras, cape) le supportent, pas les premières couches minuscules des mains. **Fichier à utiliser avec Orca : `--layout separate` ou « Open in OrcaSlicer »** (alignement conservé). On ne sait pas laquelle des deux dispositions a servi à l'impression.

Correction : ancrage des débuts d'îlots (`docs/ALGORITHM.md`, section 2, étape 1b) : couronne de 6 contacts à 1,0 mm du point bas, pointes de 0,6 mm (2,5 largeurs de ligne) pour la couronne et le contact du point bas, base élargie à 1,04 mm.

**Mesure avant / après** (même chaîne Orca, mêmes préréglages). Métrique « sous les cordons » : longueur de trajet de support dans la couche juste en dessous, à moins de 0,3 mm d'un cordon du modèle de la couche, cumulée sur les 3 premières couches de la pièce. « Supports r 3,2 » : trajet de support de la même couche à moins de 3,2 mm de l'axe de la main (métrique du premier relevé). Trajet modèle des 3 premières couches de chaque main : 12 à 16 mm.

| Buse 0,2, `--layout separate` | avant | après |
|---|---|---|
| main gauche : sous les cordons (couches 1 / 2 / 3) | 0,23 / 0,15 / 0 (total 0,38) | 0,82 / 0,87 / 4,02 (total 5,72) |
| main droite : sous les cordons (total) | 0,38 | 5,47 |
| main gauche : supports r 3,2 (3 couches) | 10,3 | 24,8 |
| bras gauche / droit : sous les cordons | 0,74 / 0,25 | 0,74 / 0,74 |
| torse (dessous à z = 25) | 5,08 | 5,07 |
| cape (îlot plat à z = 18) | 1,90 | 1,90 |

| Buse 0,2, `--layout objects` (supports descendus de 0,16 mm) | avant | après |
|---|---|---|
| mains : sous les cordons, couche juste en dessous | 0 / 0 | 0 / 0 |
| mains : sous les cordons, 2 couches en dessous | 0,15 / 0,15 | 4,19 / 4,62 |
| torse, bras, cape : sous les cordons, 2 couches | 1,27 ; 0 ; 0 | 1,27 ; 0 ; 0 |

| Buse 0,4 (`nozzle_04_normal`, couches 0,12, A1 mini 0,4), `--layout separate` | avant | après |
|---|---|---|
| main gauche / droite : sous les cordons (3 couches) | 0,19 / 0 | 9,72 / 8,59 |
| torse ; cape | 2,52 ; 1,30 | 2,52 ; 1,31 |

Contacts et marques (`GenerationResult.stats`, radeau activé) : 0,2 Normal 124 -> 130 contacts, `contact_area_mm2` 7,08 -> 10,61 ; 0,4 Normal 90 -> 101, 15,15 -> 25,66. Le supplément est entièrement sous les mains (7 pointes épaisses par main) ; les contacts à plus de 5 mm des mains sont identiques (test `tests/test_island_anchor.py`).

Ce qui reste à vérifier par impression : tenue des mains avec le fichier `--layout separate`, retrait des 7 pointes épaisses et marque laissée sous chaque main.

### Essai 3 : 2026-10-16, figurine d'exemple (bouts des bras, 2e impression)

| Élément | Valeur |
|---|---|
| Imprimante, buse | Bambu Lab A1 mini, 0,2 mm acier inox |
| Trancheur | OrcaSlicer 2.4.2, préréglage « 0.08mm Optimal @BBL A1M 0.2 nozzle », supports d'Orca désactivés |
| Modèle, profil | `examples/figurine.stl`, `nozzle_02_normal`, raft activé, PLA noir |
| Résultat | impression complète ; **aux bouts des bras** (épée côté x > 17, main côté x < -17, z = 39 à 42 mm), en haut des deux colonnes d'environ 40 mm, un filet de fils déposé dans le vide entre le haut de la colonne et la pièce. Dessous du corps et des bras corrects |

On ne sait pas si le fichier imprimé contenait déjà l'ancrage des mains (essai 2, non commité à ce moment) ; les mesures ci-dessous sont faites avec le code qui l'inclut.

**Chaîne de mesure** (reproductible, scripts hors dépôt) : génération avec le code du dépôt, export comme « Open in OrcaSlicer » (`export_for_slicer`, disposition `separate`, couches 0,08 par objet), tranchage par Orca 2.4.2 en ligne de commande (`--no-check` : supports et modèle s'interpénètrent volontairement de la pénétration des pointes), préréglages système A1 mini 0,2 aplatis. Lecture du G-code de l'objet supports (arcs G2/G3 compris : `enable_arc_fitting = 1` sur l'A1 mini ; un premier relevé qui les ignorait faisait croire, à tort, que le haut des pointes n'était pas imprimé), coordonnées ramenées à celles du modèle par la transformation de l'objet dans le 3MF. Par couche : îlots (tracés séparés), trajets de moins de 1 mm entre deux déplacements, rétractions, déplacements, remplissage d'espace (« Gap infill »), trajet de support sans rien dessous à 0,25 mm dans la couche précédente (« dans le vide »), et pour chaque contact la dernière couche de support imprimée à moins de 0,3 mm de lui.

**Constat** (code avant correction) :
- côté épée, 40 contacts sur environ 6 x 25 mm (lame horizontale de 0,4 à 3 mm de large et 18 mm de long, garde de 0,8 mm, main) ; côté main, 9 (couronne d'ancrage) ;
- toutes les pointes vont jusqu'au modèle (écart au contact <= 0,02 mm) et aucun support n'est imprimé dans le vide (0,6 mm sur 157 couches) : ce n'est ni un manque de contacts ni une pointe qui disparaît ;
- sous la lame, chaque pointe de 0,26 mm est une boucle de 0,1 à 0,8 mm de trajet sur ses 15 dernières couches : 21 à 30 micro-îlots par couche, presque tous de moins de 1 mm, 20 à 25 rétractions par couche entre z = 40,5 et 41,9 ;
- deux rangées d'arêtes parallèles sous la lame (une par bord, 1,1 à 3 mm d'écart), garde de 0,82 mm portant 4 coins à 0,82 mm les uns des autres : 6 paires de pointes plus proches que leur base (0,7 mm) + un trait (0,22 mm) ;
- densité : 2,7 voisins à moins de 1,6 mm (partageant des couches) par pointe côté épée, 3,1 côté main, 1,3 sous le corps. Sous le corps, le nombre d'îlots par couche est pourtant du même ordre (29 à 35 à z = 17,5 et 24,5) : la différence mesurable est la concentration des pointes sur une petite surface, au sommet d'une colonne de 40 mm ;
- la convergence des pointes n'est pas en cause d'après le G-code : longueur libre avant la première fusion 2,6 mm (médiane) sous les bras, 2,9 mm sous le corps.

**Correction** (`docs/ALGORITHM.md`, section 2) :
1. espacement effectif entre pointes automatiques = max(`min_contact_distance`, `tip_base_diameter` + `line_width`) : 0,92 mm en 0,2, 1,42 mm en 0,4 (couronne d'ancrage et contact obligatoire d'îlot exemptés) ;
2. pièces étroites (largeur <= 2,10 mm en 0,2 Normal, 2,62 mm en 0,4 Normal, limite qui garde le rayon de couverture du remplissage) : une rangée centrale au lieu d'une par arête.
Profils inchangés (le maximum est appliqué dans le code, il couvre aussi les préréglages utilisateur). Pas de fusion retardée des pointes (rien ne la justifie dans les mesures).

**Mesures G-code avant / après** (Orca 2.4.2, A1 mini 0,2, 0.08mm Optimal ; sommes sur les couches de la zone) :

| Côté épée (x > 17), buse 0,2 | avant | après |
|---|---|---|
| contacts | 40 | 32 |
| paires < 0,92 mm ; voisins à < 1,6 mm par pointe | 6 ; 2,7 | 0 ; 2,0 |
| couronne z 40,45-42 : îlots (max par couche) | 409 (30) | 312 (22) |
| couronne : trajets < 1 mm (nombre / longueur) | 309 / 170 mm | 225 / 124 mm |
| couronne : rétractions ; déplacements | 319 ; 4051 | 267 ; 3359 |
| couronne : remplissage d'espace | 4,6 mm | 0,2 mm |
| fusion z 38-40,45 : trajets < 1 mm ; remplissage d'espace ; rétractions | 516 ; 62 mm ; 270 | 389 ; 46 mm ; 294 |
| z 30-42,5 : supports dans le vide ; modèle sans rien dessous | 0,6 ; 178 mm | 0,5 ; 179 mm |
| pointes qui n'atteignent pas le modèle | 0 | 0 |

| Autres zones | avant | après |
|---|---|---|
| côté main (x < -17), 0,2 : contacts ; îlots couronne ; rétractions z 30-42,5 | 9 ; 87 ; 115 | 9 ; 87 ; 85 |
| sous le corps (abs(x) <= 17), 0,2 : contacts ; trajets < 1 mm ; rétractions ; dans le vide | 81 ; 3125 ; 2166 ; 1,9 mm | 70 ; 3047 ; 2337 ; 0,9 mm |
| buse 0,4 (`nozzle_04_normal`, A1 mini 0,4, couches 0,12), côté épée : contacts ; îlots couronne ; trajets < 1 mm couronne ; rétractions couronne | 30 ; 214 ; 153 ; 187 | 24 ; 153 ; 107 ; 140 |
| buse 0,4, sous le corps : contacts ; rétractions ; dans le vide | 63 ; 3619 ; 140 mm | 48 ; 3152 ; 138 mm |

Couverture des faces en surplomb (échantillons tous les 0,1 mm, distance au contact le plus proche), avant -> après : 0,2 Normal max 2,29 -> 2,29 mm, côtés bras 1,42 -> 1,42 mm, p99 1,62 -> 1,60 ; 0,4 Normal max 2,38 -> 2,13 mm. Aucune région non soutenue (`unsupported_region`) sur la figurine ; les autres exemples ne changent pas (mêmes contacts, mêmes avertissements). Tests : `tests/test_tip_crowns.py`.

Limites : la couronne est réduite d'environ un quart, pas supprimée : chaque pointe de 0,26 mm reste une suite de micro-boucles sur ses dernières couches, c'est inhérent à une pointe fine. Côté main, la couronne d'ancrage (7 pointes épaisses à 1 mm) est inchangée, par choix (elle tient la main, essai 2). La colonne de 40 mm qui porte la grappe n'est pas modifiée. Le lien entre ces chiffres et les fils observés n'est pas prouvé : à confirmer par impression.

À regarder dans l'aperçu d'Orca avant d'imprimer : couches z = 40,5 à 42 côté épée (type de ligne « Support » de l'objet supports) ; une seule rangée de petites pastilles sous la partie fine de la lame, pas de pastilles soudées deux à deux ; dans la légende « Rétractions », le nombre de marques par couche dans cette zone. Si les fils persistent : réglages d'Orca sur l'objet supports seulement (rétraction, « Avoid crossing walls », ventilation) ; parois Arachne non testées ici.
