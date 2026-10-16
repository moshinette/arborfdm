# Guide utilisateur

L'interface est en anglais ; ce guide reprend les libellés tels qu'ils apparaissent. Captures dans `docs/screenshots/`.

## 1. Vue d'ensemble

Captures de l'interface : `ui_after_1920_*.png` (1920x1080) et `ui_after_1366_*.png` (1366x768) ; état précédent dans `ui_before_*.png`.

- **Barre d'outils** : New, Open, Save, Undo, Redo, Generate supports (bouton principal, en bleu), Rebuild branches, Clear supports, Export (menu : 3MF modèle + supports + raft, 3MF pour Orca / Bambu, STL supports seuls, STL modèle + supports, puis Open in OrcaSlicer), Open in OrcaSlicer (actif seulement quand des supports existent). Les infobulles indiquent les raccourcis.
- **Menu File** : New, Open, Save, Save as..., Export, **Preferences...** (Ctrl+,, section « Slicers » : chemin d'OrcaSlicer), Quit. Cancel (Échap pendant un calcul) est dans le menu Supports.
- **Menu Edit** : Undo, Redo, puis l'édition des contacts : Delete selected contact (Suppr), Deselect contact (Échap), Delete all '<raison>' contacts, Delete all manual contacts (voir section 2 quater).
- **Panneau gauche** : Import model..., objet chargé (ou « No model loaded »), taille X × Y × Z en mm et nombre de triangles, rotation en degrés, échelle (%), hauteur (mm), Place on plate, Suggest orientation, Mesh check (Check / Repair), profil, Save preset / Presets et paramètres simples (Raft et Braces côte à côte, Supports on model et Manual contacts only en dessous).
- **Vue 3D centrale** : rotation (clic gauche + glisser), déplacement (clic milieu ou Maj), zoom (molette), Fit (touche F). Barre au-dessus de la vue : groupe d'affichage (Model + supports / Model / Supports), groupe de vues (Iso, Front, Side, Top, Below), Fit, et à droite Overhangs. Sur un écran étroit (1366 px), les intitulés « Show » et « View » sont masqués, les boutons restent. Curseur vertical à droite : coupe par hauteur, la hauteur s'affiche en mm et en orange quand la coupe est active. Sans modèle, la vue affiche « No model loaded » et comment en ouvrir un. En bas à gauche, la légende « Contacts » donne le total puis le nombre par raison, la présence des supports et le nombre de zones non supportées (en rouge s'il y en a). À droite de la légende, une carte décrit ce que pointe la souris (contact : numéro, raison, état, hauteur, note ; marqueur de problème : titre, gravité, code et message) ou, à défaut, le contact sélectionné (captures `ui_after_*_04_supports_generated.png`, `ui_after_*_08_hover_issue.png`).
- **Panneau droit** : onglet Advanced (tous les paramètres du moteur, alignés sur une même colonne ; une valeur modifiée passe en ambre et son bouton reset, une flèche circulaire à droite du champ, devient actif ; « Reset all ») et onglet Contacts and issues (contact sélectionné, liste des erreurs et avertissements). Le nombre d'erreurs et d'avertissements apparaît dans le titre de l'onglet, par exemple « Contacts and issues (2) ». Quand un même code revient au moins 4 fois, ses problèmes sont regroupés sous une ligne « ▸ Titre × N » (clic : déplier / replier) ; un code inconnu de cette version s'affiche avec un titre lisible tiré de son nom.
- **Barre de statut** : message (texte complet dans l'infobulle s'il est coupé), progression et Cancel pendant un calcul, nombre de contacts, d'arbres, durée, erreurs et avertissements. L'infobulle de la durée donne le temps par étape, puis les statistiques fournies par le moteur (jamais additionnées à la durée).
- **Unités** : angles en degrés (°), surfaces en mm², longueurs en mm.

## 2. Flux de travail

1. **Importer** un STL, OBJ ou 3MF. Le fichier est supposé en millimètres s'il ne déclare pas d'unité ; un modèle de moins de 5 mm ou de plus de 2 m déclenche un avertissement d'échelle.
2. **Vérifier le maillage.** Le diagnostic est lancé au chargement. Si le maillage a des erreurs, un bandeau rouge l'indique. « Repair » répare une copie (annulable) ; l'original est conservé. Si vous générez sans réparer, une fenêtre propose « Repair, then generate », « Generate anyway » ou « Cancel ».
3. **Orienter** : rotation à la main, ou « Suggest orientation » qui propose plusieurs orientations classées selon un coût explicite (volume de support estimé, moment des surplombs, surface en surplomb, hauteur, stabilité, empreinte au plateau ; formule dans `arbor/core/orientation.py`, pondérations heuristiques non calibrées). Cliquez une proposition pour l'appliquer.
   Chaque proposition affiche **« overhang surface »** : aire des faces du maillage tourné dont l'inclinaison dépasse l'angle de surplomb effectif du moteur (angle réglé, ou `atan(d/h)` quand le décalage par couche est plafonné à une largeur de ligne), faces posées sur le plateau exclues, divisée par l'aire totale du maillage, en % et en mm². C'est le même masque que l'analyse (`overhang_face_mask`), calculé sur toutes les faces, identique pour toutes les orientations ; ce n'est ni un % de faces ni un % de volume. Elle n'entre pas dans le coût (l'ordre de tri est inchangé). La pastille de couleur est relative : tiers inférieur de l'écart meilleure-pire candidate = vert, milieu = orange, tiers supérieur = rouge (la part de surplomb dépend du modèle). Garde-fous absolus : <= 5 % toujours vert, >= 40 % toujours rouge ; si toutes les candidates sont à moins de 5 points l'une de l'autre, seuils absolus (< 10 % vert, < 25 % orange, sinon rouge). Seuils rappelés dans l'infobulle.
   **Curseur « Cut »** : la molette sur le curseur déplace la coupe d'une couche par cran (hauteur de couche courante), Maj + molette de 10 couches ; haut = couches supérieures, tout en haut = « all ». Le libellé indique le numéro de couche et la hauteur (ex. « L 493 · 39.46 mm »). La molette sur la vue 3D reste le zoom.
4. **Choisir le profil** : buse 0,2 ou 0,4, densité Light / Normal / Dense. Ajuster si besoin l'angle de surplomb, la densité, l'autorisation de supports posés sur le modèle, le raft, les entretoises.
5. **Generate supports.** Le calcul tourne en arrière-plan ; l'interface reste utilisable. Une carte de progression s'affiche en haut de la vue : tâche, étape en cours, pourcentage, temps écoulé et bouton Cancel (aussi Échap, ou Cancel dans la barre de statut). Pendant le calcul, les commandes qui modifient la géométrie du modèle sont verrouillées (bandeau bleu, « locked: computing » sur la section Orientation and size). Si vous modifiez contacts ou paramètres pendant le calcul, le résultat est marqué périmé (bandeau ambre, libellé « Generate supports * »).
6. **Inspecter** : contacts colorés par raison (îlot, point bas, coin, arête, surface, manuel ; légende et compteurs), problèmes en rouge dans la vue. Dans la liste des problèmes, chaque ligne porte une icône de gravité (disque rouge : erreur, triangle orange : avertissement, cercle gris : info) ; l'infobulle donne le code (voir section 3). Cliquer une ligne centre la vue dessus.
7. **Éditer** : clic gauche sur le modèle ajoute un contact manuel (aperçu de la pointe au survol), clic droit sur un contact le supprime, Suppr supprime le contact sélectionné, Échap le désélectionne. Un contact sélectionné peut être désactivé ou recevoir un diamètre de pointe propre. Suppressions groupées : section 2 quater. Ensuite « Rebuild branches » reconstruit uniquement les arbres en gardant les contacts. Undo/Redo couvrent toutes ces étapes.
8. **Exporter** en 3MF (recommandé) ou STL. **Open in OrcaSlicer** (barre d'outils ou menu Export) ouvre le résultat dans OrcaSlicer (section 2 quinquies). Pour OrcaSlicer / Bambu Studio, « 3MF for Orca / Bambu: separate objects » garde le modèle et les supports (+ raft) en deux objets alignés, chacun pouvant recevoir sa propre hauteur de couche dans le slicer. Le fichier est relu après écriture pour validation. Les métadonnées du 3MF contiennent les paramètres réellement utilisés pour construire les supports.

## 2 bis. Surplombs colorés selon l'angle

Bouton **Overhangs** dans la barre de vue (capture `14_overhang_colors.png`). Le modèle est coloré face par face selon son angle de surplomb, mesuré depuis la verticale comme le paramètre « Overhang angle » (0° = paroi verticale, 90° = plafond), en prenant la valeur courante du seuil S :

| Couleur | Plage | Sens |
|---|---|---|
| couleur normale | angle < S - 10° (et toute face orientée vers le haut) | pas de support nécessaire |
| jaune | S - 10° à S | proche du seuil |
| orange | S à S + (90 - S) / 2 | au-delà du seuil : support nécessaire |
| rouge | S + (90 - S) / 2 à 90° | surplomb fort, jusqu'au plafond |

Exemple avec S = 55° : jaune 45 à 55°, orange 55 à 72°, rouge 72 à 90°. La légende en bas à droite de la vue rappelle les plages en degrés et la surface (mm²) de chaque plage. Les faces posées sur le plateau (sous une hauteur de couche) ne sont jamais colorées, comme dans la détection.

- **Automatique par défaut** : actif tant qu'aucun support n'est généré, désactivé ensuite pour laisser voir les supports. Un clic sur le bouton force l'état (activé ou non) et ce choix est conservé ; **View > Overhang colors: automatic** revient au mode automatique.
- Les couleurs suivent le seuil (panneau simple ou Advanced) et sont recalculées quand le modèle tourne ou est mis à l'échelle, ainsi qu'avec la coupe par hauteur.
- Affichage seulement : la géométrie n'est pas modifiée. Le clic gauche pour ajouter un contact et le clic droit pour le supprimer fonctionnent normalement dans ce mode.
- C'est une indication par face ; la détection réelle (« Generate supports ») tient aussi compte des couches, des îlots et des arêtes pendantes.

## 2 ter. Presets personnalisés

Capture `15_save_preset.png`.

- **Enregistrer** : réglez les paramètres (panneau simple et onglet Advanced), puis **Save preset...** (panneau gauche sous le profil, en haut de l'onglet Advanced, ou menu Supports > Presets). Donnez un nom ; si le nom existe déjà, une confirmation est demandée avant de le remplacer. Le preset devient le profil courant (annulable par Undo).
- **Liste des profils** : les presets apparaissent sous l'en-tête « My presets », après les profils intégrés, précédés de ★.
- **Bouton Presets** (ou menu Supports > Presets) : Rename, Delete (les valeurs courantes sont conservées et apparaissent comme modifiées par rapport au profil intégré de départ), Export (fichier `.json` à partager), Import, Open presets folder. Rename, Delete et Export agissent sur le preset sélectionné.
- **Stockage** : un fichier `.json` par preset dans `%APPDATA%\ArborFDM\presets` sous Windows, `~/.config/arborfdm/presets` ailleurs (`$XDG_CONFIG_HOME` respecté). La variable d'environnement `ARBOR_USER_PRESET_DIR` remplace ce dossier (installation portable, tests). Les profils intégrés ne sont jamais modifiés.
- **Contenu** : le profil intégré de départ (`inherits`) et seulement les valeurs qui en diffèrent. Un preset enregistré depuis un autre preset reprend le profil intégré de celui-ci.

```json
{"format": "arborfdm-preset", "version": 1, "label": "Figurines 28mm",
 "description": "", "inherits": "nozzle_04_normal",
 "values": {"contact_density": 1.2, "overhang_angle_deg": 50.0}}
```

- **Validation** : chaque fichier est vérifié au démarrage et à l'import (JSON lisible, noms de paramètres connus, types, profil de départ existant, mêmes règles que le moteur). Un fichier invalide n'est pas chargé : au démarrage, la barre de statut indique les fichiers ignorés et la raison ; à l'import, un message explique l'erreur (par exemple `overhang_angle_deg out of range [10, 89]`). Un nom déjà pris à l'import reçoit le suffixe « (2) ».
- **Projets** : un projet `.arbor` enregistre le nom du preset et toutes les valeurs. Rouvert sur un poste qui n'a pas ce preset, le projet garde toutes ses valeurs et se rattache au profil par défaut (les valeurs apparaissent alors comme modifiées).

## 2 quater. Éditer les contacts

- **Sélection** : clic gauche sur un contact. La carte en bas de la vue et le panneau « Selected contact » donnent sa raison (couleur de la légende), « manual » / « disabled » / diamètre de pointe propre, sa hauteur et la note du moteur (pourquoi ce contact existe). Au survol, la carte décrit le contact pointé sans changer la sélection.
- **Clavier** : Suppr (Delete) supprime le contact sélectionné ; Échap le désélectionne. Pendant un calcul, Échap annule d'abord le calcul. Dans un champ de saisie, Suppr garde son rôle d'édition de texte.
- **Suppressions groupées** (menu Edit, ou bouton « Delete several contacts » du panneau Contacts and issues) : « Delete all '<raison>' contacts (N) » supprime tous les contacts de la raison du contact sélectionné (par exemple tous les contacts Edge) ; « Delete all manual contacts (N) » supprime les contacts placés à la main. Chaque action est **une seule étape d'annulation** : Ctrl+Z remet tous les contacts à leur place.
- Les contacts supprimés ne disparaissent des arbres qu'après **Rebuild branches** (le bandeau ambre le rappelle).

## 2 quinquies. Ouvrir dans OrcaSlicer

Format du fichier vérifié avec OrcaSlicer 2.4.2 (détails dans `docs/SLICERS.md`). Le lancement depuis l'interface est couvert par des tests automatiques (faux programme) ; il reste à l'essayer sur un vrai poste Windows.

1. **Open in OrcaSlicer** (barre d'outils, ou Export > Open in OrcaSlicer), actif dès que des supports existent. L'export se fait en arrière-plan (carte de progression) avec les paramètres réellement utilisés pour construire les supports ; si des contacts ou paramètres ont changé depuis, l'avertissement habituel est affiché et conservé.
2. Le fichier est un 3MF « separate objects » (modèle + supports/raft, deux objets alignés) écrit dans un dossier dédié : `%LOCALAPPDATA%\ArborFDM\to_slicer` sous Windows (`~/.local/share/arborfdm/to_slicer` sous Linux, `~/Library/Application Support/ArborFDM/to_slicer` sous macOS ; variable `ARBOR_TO_SLICER_DIR` pour changer). Nom `<modèle>_<date-heure>.3mf` : jamais d'écrasement du modèle d'origine ni d'un fichier existant ; seuls les 20 derniers fichiers écrits par ArborFDM sont gardés. Les supports d'OrcaSlicer sont désactivés pour ces deux objets dans le fichier.
3. **Programme** : le chemin choisi dans File > Preferences... > Slicers s'il est valide, sinon détection automatique (registre Windows, `Program Files\OrcaSlicer\orca-slicer.exe`, PATH...). Dans Preferences, « Browse... » choisit le programme ; l'état s'affiche dessous (✓ valide en vert, ✗ invalide en rouge avec la raison, en ambre « not found » si la détection automatique ne trouve rien). Un chemin invalide n'est pas enregistré ; un champ vide revient à la détection automatique (capture `ui_after_*_07_preferences.png`).
4. **OrcaSlicer introuvable** (ou qui ne démarre pas) : une fenêtre l'indique, donne le chemin du fichier déjà exporté et propose « Choose the executable... » (choisir `orca-slicer.exe` ; il est vérifié, mémorisé, puis OrcaSlicer est lancé), « Preferences... », « Open folder » (ouvre le dossier du fichier) ou « Cancel » (capture `ui_after_*_09_orca_not_found.png`).
5. Au lancement, un message rappelle de **garder les supports d'OrcaSlicer désactivés** (les supports sont dans le fichier), la hauteur de couche du profil pour les supports, le nombre de parois conseillé, et de ne pas déplacer les deux objets l'un par rapport à l'autre.

## 2 sexies. Raft, surélévation et supports allégés (0.3)

Ces réglages sont dans l'onglet Advanced (libellés et infobulles). Aucun n'est validé par impression : voir `docs/LIMITATIONS.md`.

- **Couverture du raft** (`raft_coverage`) :
  - `footprint` (défaut) : une seule plaque sous la base du modèle et les pieds. Le modèle repose dessus, séparé par un jeu de décollement (`raft_release_gap`, une couche) ; sa base se détache comme une interface.
  - `bed` : rectangle englobant le modèle et les pieds, jamais la surface du plateau. Plus de matière, sans meilleure tenue : non retenu par défaut.
  - `feet` : raft sous les pieds seulement, modèle posé sur le plateau (comportement de la version 0.1, gardé pour les anciens projets). Avec `raft_release_gap = 0`, la base est soudée au raft.
- **Surélévation du modèle** (`raft_lift_auto`, `raft_lift`) : n'agit que si le modèle repose sur le raft. En automatique, le modèle est remonté au minimum (5 mm au maximum) si une face en surplomb bas laisserait trop peu de place aux pointes. Refusé avec l'avertissement `raft_lift_refused` si la base plate à supporter dépasse 25 mm². En manuel, `raft_lift` de 0 à 5 mm, sans contrôle.
- **Supports creux** (`hollow_supports`, désactivé par défaut) : vide à l'intérieur des troncs assez larges pour une paroi de 3 largeurs de ligne. Gain mesuré sur les supports : -13 % de filament, -4 % de temps. Sans effet avec la buse 0,4.
- **Supports allégés dans le trancheur** (`light_slicer_supports`, désactivé par défaut) : l'export écrit pour l'objet supports seul 2 parois, 10 % de remplissage et pas de minimum de remplissage faible. Gain mesuré sur les supports : -31 % de filament, -24 % de temps. Demande un raft `feet` ou pas de raft. Si le modèle repose sur le raft, l'export indique « Lighter supports not applied... ».
- Les supports creux et les supports allégés ne se cumulent pas : n'en activer qu'un.

## 3. Lire les problèmes signalés

| Code | Gravité | Signification |
|---|---|---|
| `unsupported_region` | erreur (îlot ou grande zone) / avertissement | zone de la couche qui imprimerait dans le vide |
| `unrouted_contact` | erreur | contact impossible à relier au plateau ou au modèle |
| `floating_support` | erreur | partie de support en l'air, ou pied « sur le modèle » qui ne le touche pas |
| `disconnected_branch` | erreur | branche dont la chaîne ne finit pas sur un pied |
| `model_collision` | avertissement / erreur au-delà de 3x la tolérance des pointes | supports qui entrent dans le modèle |
| `below_plate`, `invalid_support_mesh` | erreur | géométrie de support invalide |
| `support_floating_region` | erreur (îlot) / avertissement (surplomb) | une partie des supports eux-mêmes commencerait dans le vide une fois tranchée |
| `unstable_trunk` | avertissement | arbre haut et fin sans entretoise proche |
| `contacts_too_close` | avertissement | contacts manuels trop proches (pointes fusionnées) |
| `sub_layer_gap` | avertissement | contact à moins d'une couche d'une surface, rien à construire |
| `invalid_model_mesh` | avertissement | modèle non fermé : détection et collisions peu fiables |
| `fragile_support` | avertissement | partie de support qui pourrait plier ou casser à l'impression (section trop fine pour sa hauteur, colonne sur le modèle avec un appui trop petit, entretoise trop fine ou trop longue) |
| `hard_to_remove` | avertissement | supports difficiles à retirer ou qui marqueront la pièce (appui sur le modèle dans un endroit étroit ou fermé, contacts très denses sur une petite zone) |
| `rests_on_model`, `geometric_only` | info | marques sur le modèle ; rappel que les contrôles sont géométriques |

Un code absent de ce tableau (version plus récente du moteur) s'affiche quand même : titre lisible tiré du code, gravité inconnue traitée comme un avertissement, marqueur dans la vue s'il a une position. Les codes nombreux (4 ou plus) sont regroupés avec un compteur.

Aucun contrôle ne garantit qu'une impression réussira. L'absence d'erreur signifie seulement que les contrôles géométriques n'ont rien trouvé.

## 4. Dans le slicer

- Désactivez les supports du slicer.
- PrusaSlicer / OrcaSlicer / Bambu Studio : répondez **Oui** à « Multi-part object detected ».
- Supports en 3 parois conseillés (paramètre informatif `walls`).
- Voir `docs/SLICERS.md`.
