# Détection des zones à soutenir : note de recherche

Périmètre : `arbor/engine/analysis.py` (où faut-il du support ?) et `arbor/engine/contacts.py` (où poser les pointes ?).
Repère : mm, Z vers le haut, plateau z = 0. Convention d'angle (params.py) : `overhang_angle_deg` est mesuré depuis la verticale, 0 = paroi verticale, 90 = plafond plat.

Légende : **[F]** fait vérifiable (source citée ou mesure exécutée ici), **[R]** recommandation / choix de conception.

## 1. Deux familles de détection

| | Normales de faces | Slicing couche par couche |
|---|---|---|
| Principe | une face est en surplomb si `-n_z > sin(angle)` | couche_i moins (couche_{i-1} dilatée de d), d = h·tan(angle) |
| Coût | O(F), vectorisable | O(L · taille des contours) + booléens 2D |
| Îlots (pièce qui commence dans le vide) | non : le fond d'une sphère flottante a des faces inclinées, rien ne dit qu'il n'y a rien dessous | **oui** : composante sans recouvrement avec la couche précédente |
| Plafond plat | toutes ses faces, une seule fois | apparaît dans **une** couche (aire entière) |
| Pente raide (proche du seuil) | faces marquées, continu | bandes fines dans beaucoup de couches (largeur ≈ h·tan(pente) − d) |
| Cavités internes | oui (faces du toit) | oui (le trou se referme) |
| Bruit / maillage sculpté | sensible (mouchetis de faces) | filtré naturellement par l'aire minimale |
| Ce que voit l'imprimante | approximation | **exactement** ce que fait un slicer |

**[F]** Les slicers FDM définissent le seuil de surplomb par un angle : PrusaSlicer le mesure depuis l'horizontale (90 = vertical) ([Prusa Knowledge Base, Support material](https://help.prusa3d.com/article/support-material_1698)). Notre convention (depuis la verticale) est l'inverse, il faut convertir les valeurs de profil importées : `angle_arbor = 90 − seuil_prusa`.
**[F]** La différence couche / couche dilatée est la méthode classique des slicers pour obtenir les zones de surplomb (CuraEngine, `support.cpp`, fonction de calcul des surplombs de base). Je n'ai pas pu relire le source pendant cette session (l'accès à raw.githubusercontent.com/Ultimaker/CuraEngine a été bloqué), donc ce point reste à vérifier.
**[F]** Pour les procédés poudre et SLA, PySLM identifie d'abord les régions de surface en surplomb sur le maillage selon l'angle choisi, puis projette les supports ([PySLM BlockSupportGenerator](https://pyslm.readthedocs.io/en/latest/api/pyslm.support.BlockSupportGenerator.html)). La page ne détaille pas la méthode et parle d'une « approximation » pour l'estimation de surface ([getApproximateSupportArea](https://pyslm.readthedocs.io/en/latest/api/pyslm.support.getApproximateSupportArea.html)).

**[R] Choix : les deux, avec des rôles distincts.**
- Le slicing fait foi pour les **îlots** et les **aires non soutenues** (régions `SupportRegion`).
- Le masque de faces sert à **placer** les pointes et à filtrer les artefacts du slicing. Un échantillon de remplissage dont la projection ne tombe pas sur une face du masque est rejeté, ce qui empêche les bandes fines d'une pente de multiplier les contacts. Il sert aussi à rattraper les pentes dont les bandes par couche sont toutes sous `min_region_area`.

## 2. Slicing

- Plans au centre des couches : z_i = h·(i + 0,5), comme un slicer. Cela évite les plans pile sur des faces horizontales (sommets dégénérés).
- **[F] Backend principal : `manifold3d.Manifold.slice`.** La bibliothèque garantit une sortie manifold ([PyPI manifold3d](https://pypi.org/project/manifold3d/), Apache-2.0) et ses sections 2D (`CrossSection`) reposent sur Clipper2 (même source). Mesure ici : sphère de 1 310 720 faces, 333 coupes en **0,28 s**, contre **2,25 s pour seulement 50 coupes** avec `trimesh.intersections.mesh_multiplane`.
- **Booléens et offsets 2D** : `CrossSection` (Clipper2, entiers, robuste), offset en jonction Miter. Shapely sert uniquement aux sorties (`SupportRegion.polygon`, `layer_polygons`), aux requêtes STRtree et à l'échantillonnage.
- **Maillages non manifold ou ouverts [R]** : si `Manifold(mesh)` échoue (même après `Mesh.merge()`), un slicer numpy vectorisé prend le relais. Les points d'intersection sont calculés par arête en ordre canonique, pour que deux faces voisines donnent des points identiques au bit près. Les segments sont chaînés par `shapely.line_merge` et remplis en EvenOdd. Une chaîne ouverte est refermée si son écart est inférieur à max(1 mm, 4 × largeur de ligne), sinon elle est abandonnée et comptée dans `stats["open_chains_dropped"]`. `stats["slicer"]` vaut `"manifold"` ou `"fallback"`, accompagné de `n_boundary_edges` et `n_nonmanifold_edges`. Aucun crash, même sur une « soupe » de triangles aléatoires (testé).

## 3. Îlots, surplombs

Pour chaque couche i ≥ 1, chaque composante connexe reçoit un statut :
- *ancrée* si elle intersecte une composante ancrée de la couche i−1 ;
- *île en attente* si elle ne touche rien, ou seulement des îles en attente.

Une île en attente devient une région `island` dès que son aire atteint `min_region_area`, avec z = sa **première** couche. **[R]** Sans ce suivi, une sphère flottante maillée finement commence par une section de 0,1 mm², sous le seuil, et ses couches suivantes passaient pour des « overhang » (bug constaté puis corrigé, couvert par le test 655k faces).
Les îles sont retirées de la couche avant le calcul `couche − offset(couche_{i−1}, d)`. Les pièces de surplomb sous `min_region_area` sont ignorées. `d = max_unsupported_overhang` s'il est > 0, sinon `h·tan(angle)` (0,171 mm pour 0,12 mm et 55°).
Conséquence voulue : un trou de plafond est rétréci de d sur chaque bord (cavité 10×10 mm : région d'environ 93 mm², testé).

## 4. Arêtes suspendues, coins, points bas (sur le maillage)

- **Masque de faces** : `-n_z > sin(angle)`, en excluant les faces dont tous les sommets sont sous z = h (posées sur le plateau). **[R] Anti-bruit** : les petits trous du masque (composantes non-masque d'aire < `min_region_area` entourées de masque, faces descendantes seulement) sont comblés, puis les îlots du masque d'aire < `min_region_area` sont supprimés. Mesuré sur une sphère de 81 920 faces, bruit gaussien σ = 0,03 mm (environ 10 % de l'arête) : 300 arêtes suspendues parasites avant filtrage, 59 après les filtres et la règle de pli ci-dessous.
- **Arête suspendue** : arête de bord d'une zone du masque telle que (a) on ne descend pas en entrant dans la zone, (b) la surface voisine *monte* depuis l'arête (ou le maillage est ouvert), (c) l'angle dièdre dépasse 25° : c'est un vrai pli, pas la frontière lisse d'une sphère (**[R]** sur une sphère lisse, la limite du masque n'est pas une arête physique).
- **Coin** : sommet touché par au moins 2 arêtes suspendues, dont toutes les faces du masque sont quasi horizontales (< 20° de l'horizontale), avec une somme d'angles intérieurs sur le masque < 150° (coin convexe).
- **Point bas** : minimum strict sur le 1-anneau (tous les voisins plus hauts), z > h, normale de sommet tournée vers le bas (n_z < −0,5) **et** plus bas sommet dans un rayon 3D de 1 mm. Sur le maillage bruité de 655k faces, 7 830 candidats bruts se réduisent à 1 point retenu (le bas de la sphère flottante). Un fond plat n'a pas de minimum strict : il est couvert par les coins, les arêtes et le remplissage.

## 5. Placement des contacts

> Section dépassée sur l'échantillonnage des arêtes et le remplissage résiduel : la version à jour est dans `docs/ALGORITHM.md` (section 2).

Ordre de priorité, avec le premier arrivé qui couvre son voisinage : manuel > îlot (≥ 1 par île, posé sur le point bas de l'île s'il existe) > point bas > coin > arête (pas `edge_spacing/density`) > remplissage hexagonal des îles et surplombs (pas `contact_spacing/density`, échantillonnage du contour pour les bandes trop fines) > faces du masque restantes.
- Couverture : un candidat est sauté s'il existe un contact à moins de `min_contact_distance`, ou à moins de 0,55 × pas pour les arêtes et surfaces. Les voisins de grille (distance = pas) survivent. Écart maximal mesuré entre un point de la surface et le contact le plus proche : 2,2 à 2,8 mm pour un pas de 3 mm.
- Projection : rayon vertical +Z depuis sous la région, testé contre les seules faces descendantes, indexées dans un KD-tree XY avec liste brute pour les grands triangles. Normale = normale de la face touchée. Les coins et arêtes sont décalés vers l'intérieur de la face du rayon de pointe (`tip_diameter/2`), pour que la pointe ne déborde pas.
- **Évitement des détails [R]** : rugosité locale = 1 − |normale moyenne pondérée par l'aire| des faces descendantes dans un rayon de 0,4 mm. Elle vaut 0 sur une zone plate ou doucement courbe, et grandit sur le relief fin. Au-delà de 0,02, on essaie 6 positions sur un cercle de rayon min(0,25 × pas, 0,6 mm) et on garde la plus lisse si le gain dépasse 0,02, qu'elle reste dans le masque et à moins d'une couche en z. Le déplacement reste inférieur à la moitié du rayon de couverture, donc la couverture est conservée.
- Les contacts dont le rayon descendant touche le modèle sont conservés ; c'est le constructeur d'arbres qui gère l'appui sur le modèle.
- Ids : les manuels gardent les leurs, les automatiques partent de max(id manuel) + 1, dans un ordre déterministe.

## 6. Performances mesurées (2 cœurs, cette machine)

| Maillage | Faces | analyze | place_contacts |
|---|---|---|---|
| 2 icosphères bruitées (σ = 0,03), dont 1 flottante | 655 360 | 3,5 à 4,1 s | 0,6 s |
| icosphère fermée r = 20 | 1 310 720 | 4,3 s (slicing 0,34 s) | 1,0 s |
| même sphère ouverte (100 faces retirées, slicer de secours) | 1 310 620 | 6,9 s (slicing 3,1 s) | 1,0 s |

Pic RSS : 1,4 Go pour le cas à 1,3M faces.

## 7. Limites connues

- Le masque de faces dépend de l'orientation des normales : un maillage aux normales inversées doit être réparé en amont (`core/repair`).
- Le slicer de secours ne reconstruit pas les grands trous (au-delà de 1 mm) : la couche concernée perd ce contour, et le stat le signale.
- Sur une surface très bruitée (σ ≈ 10 % de l'arête), des arêtes suspendues parasites subsistent (59 sur 81 920 faces).
- L'heuristique de détail n'a pas de test dédié sur un modèle sculpté réel.
- Les régions sont stockées couche par couche, sans fusion verticale : un plafond incliné juste au-dessus du seuil produit beaucoup de petites `SupportRegion` (le placement des contacts reste borné grâce au filtrage par le masque).
