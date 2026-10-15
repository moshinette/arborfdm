# Angle de surplomb : conventions des trancheurs, valeurs par défaut, choix d'ArborFDM

Périmètre : quel seuil de surplomb utiliser par défaut, comment les trancheurs le définissent et le détectent, et ce qui en a été tiré pour `arbor/engine/analysis.py`.

Légende : **[F]** fait vérifiable (source citée, ou mesure exécutée ici avec sa commande), **[R]** recommandation ou choix de conception.

Convention ArborFDM (rappel, `params.py`) : `overhang_angle_deg` = α est mesuré **depuis la verticale** (0 = paroi verticale, 90 = plafond plat). Une pente α fait déborder chaque couche de h·tan(α) par rapport à la couche du dessous (h = hauteur de couche). On note w la largeur de ligne.

## 1. Résumé

- Tous les trancheurs étudiés détectent les surplombs de la même façon : `couche_i − offset(couche_{i−1}, d)`. Seule change la façon de calculer d (le débord toléré par couche). ArborFDM fait exactement la même chose.
- OrcaSlicer, Bambu Studio et PrusaSlicer mesurent leur angle **depuis l'horizontale** et ajoutent 1° dans le code. Conversion : **α_ArborFDM = 90 − (θ_trancheur + 1)**. Cura mesure comme ArborFDM (depuis la verticale), sans conversion.
- Les modes automatiques de PrusaSlicer et d'OrcaSlicer ne se basent pas sur un angle mais sur la largeur de trait : **d = 0,5 × w**.
- Exprimés en « débord par couche en largeurs de ligne », les réglages par défaut des trancheurs vont de **0,34 à 0,79 w**, autour de 0,5 w. Le défaut d'ArborFDM (55°) donne **0,52 w** pour le profil 0,2 (0,22/0,08) et **0,41 w** pour le profil 0,4 (0,42/0,12).
- Pour l'utilisateur (A1 mini, buse 0,2, OrcaSlicer 2.4.2, profil « 0.08mm Optimal ») : Orca utilise 30° depuis l'horizontale, soit **59°** chez ArborFDM (0,133 mm par couche, 0,60 w). ArborFDM à 55° (0,114 mm, 0,52 w) est un peu plus prudent, ce qui convient à des dessous de figurines.
- Implémenté : (1) **d plafonné à une largeur de ligne** quand il vient de l'angle ; (2) **le masque de faces utilise l'angle effectif atan(d/h)**, pour que les deux détecteurs aient le même seuil. Défauts des profils inchangés (55°), désormais écrits explicitement dans `_nozzle_02.json` et `_nozzle_04.json`.
- Non retenu après mesure : lissage multi-couches à la Cura, filtre « petits surplombs » d'Orca, changement de l'angle par défaut.

## 2. Sources

Codes sources lus à des versions figées : OrcaSlicer `v2.4.2` (la version de l'utilisateur), PrusaSlicer `version_2.9.4`, Cura `main` au commit `5bea2f8`, CuraEngine `main` au commit `47b0388`.

| # | Source | Ce qu'elle dit | Convention |
|---|---|---|---|
| O1 | [OrcaSlicer `PrintConfig.cpp` L6391-6413](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/PrintConfig.cpp#L6391-L6413) | `support_threshold_angle` : « Support will be generated for overhangs whose slope angle is below the threshold », défaut **30**. Si 0 : les supports normaux utilisent `support_threshold_overlap` (défaut **50 %**), les supports arborescents reviennent à 30. | depuis l'horizontale |
| O2 | [OrcaSlicer `Support/SupportMaterial.cpp` L1388-1442](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Support/SupportMaterial.cpp#L1388-L1442) | `thresh_angle = angle + 1` (« +1 makes the threshold inclusive »), puis `lower_layer_offset = lower_layer.height / tan(threshold_rad)` ; en mode recouvrement : `fw − overlap × fw`, avec fw = largeur du périmètre externe. Surplomb = couche − offset(couche inférieure). | depuis l'horizontale |
| O3 | [OrcaSlicer `Support/TreeSupport.cpp` L697-700, L837](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Support/TreeSupport.cpp#L697-L700) | Supports arborescents (type `tree(auto)` du profil Bambu) : même formule `height / tan(angle + 1)`, angle 30 si 0 ; `overhangs = diff(couche, offset(couche inférieure))` ([L837-843](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Support/TreeSupport.cpp#L837-L843)). | depuis l'horizontale |
| O4 | [OrcaSlicer `Support/TreeSupportCommon.hpp` L61](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Support/TreeSupportCommon.hpp#L61) | Conversion explicite dans le code : `support_angle = π/2 − (threshold + 1)°`, c'est-à-dire l'angle depuis la verticale. | conversion |
| O5 | [OrcaSlicer `TreeSupport.cpp` L1003-1031](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Support/TreeSupport.cpp#L1003-L1031) et [`PrintConfig.cpp` L6125-6130](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/PrintConfig.cpp#L6125-L6130) | « Ignore small overhangs » (activé par défaut) : un groupe de surplombs, fusionné sur plusieurs couches, érodé d'une largeur de ligne, dont la boîte englobante fait moins de 2 w dans une direction, est ignoré, **sauf** s'il s'agit d'une « sharp tail » (partie qui commence dans le vide) ou d'un porte-à-faux (> 3 mm de la base, [L877-895](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/Support/TreeSupport.cpp#L877-L895)). | largeur de ligne |
| O6 | [OrcaSlicer `PerimeterGenerator.cpp` L1173-1177, L2548-2571](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/PerimeterGenerator.cpp#L2548-L2571) ; [`PrintConfig.cpp` L5003-5009](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/PrintConfig.cpp#L5003-L5009), [L1610-1650](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/src/libslic3r/PrintConfig.cpp#L1610-L1650) | Parois en surplomb (vitesse) : « Detect the overhang percentage relative to line width [...] For 100% overhang, bridge speed is used. » Classes de vitesse 10-25 %, 25-50 %, 50-75 %, 75-100 % de la largeur de ligne. Les polygones de référence sont la couche inférieure décalée de −w/2 à +buse/2 ; une paroi est en surplomb quand toute la buse n'est plus portée. | % de largeur de ligne |
| O7 | Profils Bambu dans OrcaSlicer v2.4.2 : [`BBL/process/fdm_process_common.json` L143-145](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/resources/profiles/BBL/process/fdm_process_common.json#L143-L145), [`fdm_process_single_0.08_nozzle_0.2.json`](https://github.com/SoftFever/OrcaSlicer/blob/v2.4.2/resources/profiles/BBL/process/fdm_process_single_0.08_nozzle_0.2.json) | Le profil « 0.08mm Optimal @BBL A1M 0.2 nozzle » hérite de `fdm_process_single_0.08_nozzle_0.2` (`line_width` 0,22, `layer_height` 0,08) puis de `fdm_process_common` : `support_threshold_angle` **30**, `support_type` **tree(auto)**. | depuis l'horizontale |
| P1 | [PrusaSlicer `PrintConfig.cpp` L3494-3506](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.4/src/libslic3r/PrintConfig.cpp#L3494-L3506) | `support_material_threshold` : « slope angle (90° = vertical) [...] the most horizontal slope (measured from the horizontal plane) that you can print without support material. Set to zero for automatic detection (recommended). » Défaut **0**. | depuis l'horizontale |
| P2 | [PrusaSlicer `Support/SupportMaterial.cpp` L1173-1175, L1226-1233](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.4/src/libslic3r/Support/SupportMaterial.cpp#L1226-L1233) ; [`Support/TreeSupport.cpp` L234-237, L257-267](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.4/src/libslic3r/Support/TreeSupport.cpp#L257-L267) ; [`TreeSupportCommon.cpp` L47](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.4/src/libslic3r/Support/TreeSupportCommon.cpp#L47) | Angle : `height / tan(threshold + 1)`. Automatique : « Overhang defined by half the extrusion width » : `0.5 × fw` (largeur du périmètre externe). Même conversion π/2 − (threshold + 1) pour l'arbre. | depuis l'horizontale / largeur |
| P3 | [PrusaSlicer `PrintConfig.cpp` L765-770](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.4/src/libslic3r/PrintConfig.cpp#L765-L770) | Vitesses dynamiques en surplomb : « percentage of overlap of the extrusion with the previous layer: 100% would be full overlap (no overhang), while 0% represents full overhang (floating extrusion, bridge) ». | % de recouvrement |
| P4 | [PrusaSlicer `resources/profiles/PrusaResearch.ini`](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.4/resources/profiles/PrusaResearch.ini) (L424, L475, L506, L3979, L5261) | Les profils Prusa ne gardent pas l'automatique : `*common*` 50, `*MK4*` 45, `*MK4IS_common*` 40, profils SPEED MK4S / CORE One 35. Largeur 0,45, couche 0,2 pour ces profils 0,4. | depuis l'horizontale |
| C1 | [Cura `fdmprinter.def.json` L5616-5629](https://github.com/Ultimaker/Cura/blob/5bea2f86153faed3a1c2d9081b8d6b753684678a/resources/definitions/fdmprinter.def.json#L5616-L5629) | `support_angle` : « At a value of 0° all overhangs are supported, 90° will not provide any support. » Défaut **50**. | depuis la verticale |
| C2 | [CuraEngine `src/support.cpp` L1641-1663](https://github.com/Ultimaker/CuraEngine/blob/47b03886dc200b00f17fd40d7739a9da5a7b8745/src/support.cpp#L1641-L1663) | `max_dist_from_lower_layer = tan(support_angle) × layer_height`, la même formule qu'ArborFDM. En plus, union des couches i−k décalées de k·d sur `smooth_height` = 0,4 mm, « to avoid generating support for textures on vertical surfaces ». | depuis la verticale |
| K1 | [Prusa Knowledge Base, Support material](https://help.prusa3d.com/article/support-material_1698) | « the most horizontal slope (measured from the horizontal plane) that you can print without support material (90=vertical) ». Pas de valeur recommandée. | depuis l'horizontale |
| K2 | [Slic3r manual, Support material](https://manual.slic3r.org/expert-mode/support-material) | L'angle imprimable « is determined by several factors, most notably layer height and extrusion width, and is usually around 45° ». Recommande 0 (automatique). | non précisé |
| K3 | [Bambu Lab Wiki, Support](https://wiki.bambulab.com/en/software/bambu-studio/support) | « The threshold angle is the maximum slope angle that needs support », mesuré par rapport à l'horizontale, défaut 30°. | depuis l'horizontale |
| K4 | [Bambu Lab Wiki, Overhang](https://wiki.bambulab.com/en/filament-acc/filament/print-quality/overhang) | Causes qualitatives : refroidissement insuffisant, vitesse (« Slow down for overhang »), température. Aucune valeur d'angle. | n/a |
| K5 | [Prusa Knowledge Base, PETG](https://help.prusa3d.com/en/article/petg_2059) | PETG : « bridging- and overhang-behavior is usually worse » que le PLA ; ventilation réduite pour la solidité. Pas de valeur d'angle. | n/a |
| A1 | Jiang J., Stringer J., Xu X., Zhong R., « Investigation of printable threshold overhang angle in extrusion-based additive manufacturing for reducing support waste », *Int. J. Computer Integrated Manufacturing* 31, 961-969 (2018), [doi:10.1080/0951192X.2018.1466398](https://doi.org/10.1080/0951192X.2018.1466398) ([notice](https://researchspace.auckland.ac.nz/handle/2292/44788)) | Le résumé dit que l'angle imprimable « varies substantially with printing conditions » (ventilation, vitesse, température), essais à 20, 30, 40, 50°. **Je n'ai lu que le résumé** : aucune valeur chiffrée reprise ici. | non vérifié |
| A2 | [PADT, Towards Self-Supporting Design for AM, part 1 (2017)](https://www.padtinc.com/2017/07/12/towards-self-supporting-design-for-additive-manufacturing-part-1-standard-guidelines/) | Règle des 45° (depuis l'horizontale) présentée comme règle empirique ; l'angle dépend de l'épaisseur de couche et de la largeur du contour, de la machine et de la matière ; essais Makerbot (0,2 mm) jusqu'à environ 65° avec retouche. | mixte (mal précisé) |
| A3 | Mazlan et al., « Overhang Analysis Fabricated Using FDM Technique », *JAITA* 1(1), 2020, [page](https://journal.uthm.edu.my/index.php/jaita/article/view/6479) | Surplomb admissible ≤ 45 (unité imprimée « mm » dans le résumé, probablement des degrés), matière et couche non précisées. Faible valeur probante. | non précisé |
| A4 | [MakerWorld, Fast Support Threshold Overhang Test](https://makerworld.com/en/models/2350066-fast-support-threshold-overhang-test) | Pièce de calibration (arc 10° à 80°, pas de 10°) pour trouver l'angle limite sur son imprimante. Aucun résultat publié. | non précisé |

**Ce que je n'ai pas trouvé** : aucune source chiffrée et vérifiable reliant l'angle limite à la hauteur de couche, à la buse ou à la matière (PLA, PETG, ABS) dans des conditions comparables. Les études lues (A1 à A3) ne donnent que des résumés qualitatifs ou des résultats sans conditions. Deux articles trouvés par la recherche (Ülkir et al. 2025, doi:10.1002/pol.20250752 ; Gong et al. 2025, *Materials Today Communications* 43, 111615) traitent de la rugosité ou de l'extrusion de tiges sans support : aucun ne donne d'angle limite exploitable ici.

## 3. Conversion des conventions

Formules tirées du code (O2, O3, P2, C2) :

| Trancheur, mode | Débord toléré par couche d | Angle équivalent ArborFDM α |
|---|---|---|
| ArborFDM, Cura (angle α depuis la verticale) | h · tan(α) | α |
| Orca, Bambu, Prusa (angle θ depuis l'horizontale) | h / tan(θ + 1°) | 90 − (θ + 1) |
| Prusa automatique, Orca « threshold overlap » 50 % | 0,5 · w (w = périmètre externe) | atan(0,5 · w / h) |

Valeurs par défaut converties (calculées avec ces formules) :

| Réglage | h (mm) | w (mm) | d (mm) | d / w | α ArborFDM |
|---|---|---|---|---|---|
| **Orca 2.4.2, A1 mini buse 0,2, « 0.08mm Optimal » (θ = 30)** | 0,08 | 0,22 | 0,133 | 0,60 | **59°** |
| Orca θ = 30, buse 0,4 en 0,12 | 0,12 | 0,42 | 0,200 | 0,48 | 59° |
| Orca θ = 30, buse 0,4 en 0,20 | 0,20 | 0,42 | 0,333 | 0,79 | 59° |
| Orca recouvrement 50 %, buse 0,2 | 0,08 | 0,22 | 0,110 | 0,50 | 54,0° |
| Prusa automatique, buse 0,4 en 0,20 | 0,20 | 0,45 | 0,225 | 0,50 | 48,4° |
| Prusa `*common*` (θ = 50) | 0,20 | 0,45 | 0,162 | 0,36 | 39° |
| Prusa `*MK4*` (θ = 45) | 0,20 | 0,45 | 0,193 | 0,43 | 44° |
| Prusa `*MK4IS_common*` (θ = 40) | 0,20 | 0,45 | 0,230 | 0,51 | 49° |
| Prusa SPEED MK4S / CORE One (θ = 35) | 0,20 | 0,45 | 0,275 | 0,61 | 54° |
| Cura défaut 50°, buse 0,2 en 0,08 | 0,08 | 0,22 | 0,095 | 0,43 | 50° |
| Cura défaut 50°, buse 0,4 en 0,20 | 0,20 | 0,40 | 0,238 | 0,60 | 50° |
| Cura défaut 50°, buse 0,4 en 0,12 | 0,12 | 0,42 | 0,143 | 0,34 | 50° |
| **ArborFDM 55°, profil 0,2** | 0,08 | 0,22 | 0,114 | **0,52** | 55° |
| **ArborFDM 55°, profil 0,4** | 0,12 | 0,42 | 0,171 | **0,41** | 55° |

Lecture : un angle fixe donne un débord par couche proportionnel à h, alors que ce que le cordon tolère dépend de w. C'est pourquoi les modes automatiques raisonnent en largeur de ligne (K2 le dit aussi en toutes lettres).

Angle conseillé par la règle 0,5 w, atan(0,5 · w / h), et angle où le cordon n'a plus de recouvrement, atan(w / h) :

| w | h | 0,5 w | w (plafond) | 55° donne |
|---|---|---|---|---|
| 0,22 | 0,04 | 70,0° | 79,7° | 0,26 w |
| 0,22 | 0,06 | 61,4° | 74,7° | 0,39 w |
| 0,22 | **0,08** | **54,0°** | **70,0°** | 0,52 w |
| 0,22 | 0,10 | 47,7° | 65,6° | 0,65 w |
| 0,22 | 0,12 | 42,5° | 61,4° | 0,78 w |
| 0,42 | **0,12** | **60,3°** | **74,1°** | 0,41 w |
| 0,42 | 0,16 | 52,7° | 69,1° | 0,54 w |
| 0,42 | 0,20 | 46,4° | 64,5° | 0,68 w |
| 0,42 | 0,28 | 36,9° | 56,3° | 0,95 w |

## 4. Facteurs qui font varier l'angle imprimable

- **Hauteur de couche et largeur de ligne [F]** : c'est géométrique (formules de la section 3). À w constant, une couche plus épaisse réduit l'angle tenable (K2, A2, modes automatiques P2 et O2).
- **Diamètre de buse [F]** : il agit par w. Orca considère une paroi en surplomb quand toute la buse n'est plus portée (O6). Avec une buse 0,2 et des couches de 0,08, le rapport w/h vaut 2,75, contre 3,5 pour 0,42/0,12 : à angle égal, la buse 0,2 travaille plus près de la limite.
- **Matière, refroidissement, vitesse, température [F, qualitatif seulement]** : A1, K4 et K5 citent la ventilation, la vitesse et la température comme facteurs, et le PETG comme moins bon que le PLA en surplomb. Aucune source lue ne donne d'écart chiffré. ArborFDM n'a pas de paramètre matière ; **[R]** pour le PETG ou l'ABS, baisser l'angle du profil (par exemple 45 à 50°) **après** une impression de la pièce de calibration A4. Ces chiffres ne sont pas sourcés : ce sont des points de départ à vérifier.
- **Petites surfaces, pointes, bords [F]** : Orca ignore les petits surplombs, mais garde toujours les parties qui commencent dans le vide et les porte-à-faux (O5). ArborFDM garde aussi toujours les îlots (contact obligatoire), et les pointes vers le bas reçoivent un contact `low_point` (`docs/research/geometry.md`, section 4).
- **Ponts [F]** : un débord d'une largeur de ligne complète correspond à 0 % de recouvrement, traité comme un pont par Prusa (P3) et par Orca (O6, « For 100% overhang, bridge speed is used »). ArborFDM ne modélise pas les ponts : une telle couche doit être soutenue.
- **Figurines [R]** : l'aspect du dessous compte. Rester un peu plus prudent que le trancheur (0,4 à 0,5 w par couche) est raisonnable tant qu'aucun essai physique ne justifie de relâcher.

## 5. Ce qui a été implémenté

### 5.1 Plafond du débord à une largeur de ligne

`analysis.max_layer_step(params)` renvoie (d, source) :
- `"explicit"` : `max_unsupported_overhang > 0`, pris tel quel (comportement inchangé, choix de l'utilisateur) ;
- `"angle"` : h · tan(`overhang_angle_deg`) ;
- `"line_width"` : la valeur précédente plafonnée à `MAX_STEP_LINE_WIDTHS` × w, avec `MAX_STEP_LINE_WIDTHS = 1.0`.

Justification [F] : au-delà d'une largeur de ligne par couche, le cordon extérieur n'a plus aucun recouvrement avec la couche du dessous (P3, O6). Aucun angle ne peut rendre cela imprimable sans pont. Le plafond ne joue qu'au-dessus de **70,0°** (profil 0,2) et **74,1°** (profil 0,4) : **les profils (55°) ne sont pas touchés**.

### 5.2 Même seuil pour le masque de faces et les couches

`analysis.effective_overhang_angle_deg(params)` = atan(d / h), égal à `overhang_angle_deg` quand d vient de l'angle (valeur exacte, sans arrondi). Le masque de faces (`-n_z > sin(angle)`) utilise maintenant cet angle. Avant, il utilisait toujours `overhang_angle_deg`, même quand d était donné explicitement : les deux détecteurs pouvaient se contredire.

Nouvelles statistiques d'analyse : `max_step_source`, `max_step_line_widths`, `effective_overhang_angle_deg` (en plus de `max_step`).

### 5.3 Profils

`overhang_angle_deg: 55.0` est écrit explicitement dans `_nozzle_02.json` et `_nozzle_04.json`. Les paramètres résolus ne changent pas : c'était déjà le défaut de `SupportParams`.

## 6. Mesures

Commande : script de mesure hors dépôt qui appelle `pipeline.generate` (génération complète, vérification comprise) sur `examples/*.stl` avec les profils `nozzle_02_normal` et `nozzle_04_normal`. Pour reproduire « avant », le plafond est désactivé (`analysis.MAX_STEP_LINE_WIDTHS = inf`), ce qui redonne exactement l'ancien code. Les temps varient de ±50 % d'une exécution à l'autre (machine partagée avec d'autres processus) : ils ne servent qu'à repérer un ordre de grandeur.

### 6.1 Défauts (55°) : identiques avant et après

| Modèle | Profil | Contacts (îlot / coin / arête / surface) | Régions | Avertissements |
|---|---|---|---|---|
| figurine | 0,2 | 126 (4 / 15 / 65 / 42) | 44 | 2 `unstable_trunk` |
| figurine | 0,4 | 90 (3 / 13 / 51 / 23) | 33 | 3 `unstable_trunk` |

Mêmes nombres sur shelf, inclined_60deg, sphere_on_plate, bridge, suspended_arm, cavity et floating_cube (la source du pas reste `"angle"`).

### 6.2 Sensibilité à l'angle, figurine (génération complète, avant les changements)

| α | d 0,2 (mm) | Contacts 0,2 | Régions 0,2 | d 0,4 (mm) | Contacts 0,4 | Régions 0,4 |
|---|---|---|---|---|---|---|
| 45 | 0,080 | 146 | 67 | 0,120 | 111 | 44 |
| 50 | 0,095 | 135 | 56 | 0,143 | 96 | 38 |
| 54 | 0,110 | 130 | 45 | 0,165 | 93 | 34 |
| **55** | 0,114 | **126** | 44 | 0,171 | **90** | 33 |
| 59 | 0,133 | 115 | 35 | 0,200 | 89 | 27 |
| 60 | 0,139 | 112 | 35 | 0,208 | 89 | 25 |

Seuls les contacts de surface varient : les arêtes suspendues et les coins sont des plis, ils ne dépendent pas de l'angle. Aucune régression de vérification (pas de `unsupported_region`) entre 45 et 60°.

### 6.3 Plafond (angles élevés), avant / après

| Cas | Avant | Après |
|---|---|---|
| figurine, 0,2, α = 89 | 84 contacts, 9 régions, **2 `unsupported_region`** | 100 contacts, 19 régions, aucune |
| figurine, 0,4, α = 89 | 66 contacts, 4 régions | 78 contacts, 16 régions |
| figurine, 0,2, α = 80 | 94 contacts, 13 régions | 100 contacts, 19 régions |
| figurine, 0,2, α = 75 | 99 contacts, 13 régions | 100 contacts, 19 régions |
| figurine, α = 70 (0,2 et 0,4) | 100 / 79 | 100 / 79 (sous le plafond, inchangé) |
| suspended_arm, 0,2, α = 89 | 1 contact, **1 erreur `unsupported_region`** | 8 contacts, aucune |
| suspended_arm, 0,4, α = 89 | 1 contact, 1 avertissement `unsupported_region` | 6 contacts, aucun |

Avec le plafond, tout angle au-delà de 70° (0,2) se comporte comme 70° : c'est voulu.

### 6.4 Pas explicite (`max_unsupported_overhang = 0,2 mm`), avant / après

| Cas | Avant | Après |
|---|---|---|
| inclined_60deg, 0,2 (0,139 mm/couche < 0,2) | 0 région mais **48 contacts** (masque à 55°) | 0 contact (angle effectif 68,2°) |
| inclined_60deg, 0,4 (0,208 mm/couche > 0,2) | 33 contacts | 33 contacts (angle effectif 59,0°, pente à 60° bien en surplomb) |
| figurine, 0,2 | 109 contacts | 104 contacts, identique à α = 68,2° (104) |

### 6.5 Temps

Les deux changements remplacent un calcul par un autre, sans travail ajouté. Temps de génération complète de la figurine mesurés entre 4,6 et 10 s (0,2) selon la charge de la machine, sans écart attribuable au changement.

## 7. Évalué et non retenu

| Piste | Mesure | Décision |
|---|---|---|
| Défaut 54° (règle 0,5 w) pour le profil 0,2 | figurine : 126 → 130 contacts, 44 → 45 régions | **Non.** 55° donne déjà 0,52 w, l'écart est dans le bruit. Comportement conservé. |
| Défaut 60° (règle 0,5 w, proche des 59° d'Orca) pour le profil 0,4 | figurine : 90 → 89 contacts, 33 → 25 régions | **Non.** 55° (0,41 w) est plus prudent pour des dessous de figurines, et aucun essai physique ne justifie de relâcher (`docs/PHYSICAL_TESTS.md` ne contient pas encore de mesure de surplomb). |
| Remplacer l'angle par d = 0,5 w (mode automatique Prusa/Orca) | | **Non.** Le curseur d'angle de l'interface deviendrait sans effet. L'angle reste le réglage, comme dans le mode par défaut d'Orca. |
| Lissage sur 0,4 mm à la Cura (C2) | figurine 0,2 : 126 → 124 contacts, analyse 0,85 → 4,6 s ; plafond de shelf : aire 297,7 → 288,6 mm² ; cavity : 120,6 → 98,7 mm² | **Non.** Analyse 3 à 5 fois plus lente, gain négligeable sur les contacts, et le bord des plafonds plats est rogné de 5 d au lieu de d. Code d'essai retiré. |
| « Ignore small overhangs » d'Orca (O5) | figurine : 35 régions sur 44 (0,2) et 29 sur 33 (0,4) passeraient le critère couche par couche | **Non.** Orca l'applique à des groupes fusionnés sur plusieurs couches. Appliqué couche par couche, le critère supprimerait les bandes de pente. Pour les détails fins (doigts, menton), ArborFDM s'appuie déjà sur les îlots et les points bas. |

## 8. Limites et suites

- `verify.py` (`support_layer_regions`, ligne 298) recalcule d sans le plafond pour le contrôle des supports tranchés seuls. C'est sans effet pratique : les branches sont limitées à min(`branch_angle_deg`, angle − 2°) ≤ 40° dans les profils. Pour être cohérent, il faudrait y appeler `analysis.max_layer_step` (fichier hors de mon périmètre).
- La coloration du modèle dans l'interface (`arbor/app/overhang.py`) et les suggestions d'orientation (`arbor/core/orientation.py`) utilisent l'angle brut. Elles divergent de l'analyse uniquement au-dessus du plafond (70° en 0,2/0,08) ou avec un pas explicite. Elles pourraient utiliser `analysis.effective_overhang_angle_deg` (hors périmètre).
- Si l'utilisateur change la hauteur de couche d'un profil, l'angle ne suit pas (55° en 0,12 avec une buse 0,2 donne 0,78 w). L'interface pourrait proposer l'angle conseillé atan(0,5 · w / h) de la section 3 (hors périmètre).
- Aucune valeur n'est validée par impression. Protocole proposé : imprimer la pièce A4 (ou des pentes à 45, 50, 55, 60, 65° depuis la verticale) en PLA, 0,08 mm, buse 0,2, puis consigner le résultat dans `docs/PHYSICAL_TESTS.md` avant de toucher aux défauts.
