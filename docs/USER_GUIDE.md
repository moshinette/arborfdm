# Guide utilisateur

L'interface est en anglais ; ce guide reprend les libellés tels qu'ils apparaissent. Captures dans `docs/screenshots/`.

## 1. Vue d'ensemble

Captures de l'interface : `ui_after_1920_*.png` (1920x1080) et `ui_after_1366_*.png` (1366x768) ; état précédent dans `ui_before_*.png`.

- **Barre d'outils** : New, Open, Save, Undo, Redo, Generate supports (bouton principal, en bleu), Rebuild branches, Clear supports, Export (menu : 3MF modèle + supports + raft, 3MF pour Orca / Bambu, STL supports seuls, STL modèle + supports, puis Open in OrcaSlicer), Open in OrcaSlicer (actif seulement quand des supports existent). Les infobulles indiquent les raccourcis.
- **Menu File** : New, Open, Save, Save as..., Export, **Preferences...** (Ctrl+,, section « Slicers »), Quit. Cancel (Échap) est dans le menu Supports.
- **Panneau gauche** : Import model..., objet chargé (ou « No model loaded »), taille X × Y × Z en mm et nombre de triangles, rotation en degrés, échelle (%), hauteur (mm), Place on plate, Suggest orientation, Mesh check (Check / Repair), profil, Save preset / Presets et paramètres simples (Raft et Braces côte à côte, Supports on model et Manual contacts only en dessous).
- **Vue 3D centrale** : rotation (clic gauche + glisser), déplacement (clic milieu ou Maj), zoom (molette), Fit (touche F). Barre au-dessus de la vue : groupe d'affichage (Model + supports / Model / Supports), groupe de vues (Iso, Front, Side, Top, Below), Fit, et à droite Overhangs. Sur un écran étroit (1366 px), les intitulés « Show » et « View » sont masqués, les boutons restent. Curseur vertical à droite : coupe par hauteur, la hauteur s'affiche en mm et en orange quand la coupe est active. Sans modèle, la vue affiche « No model loaded » et comment en ouvrir un. En bas à gauche, la légende « Contacts » donne le total puis le nombre par raison, la présence des supports et le nombre de zones non supportées (en rouge s'il y en a).
- **Panneau droit** : onglet Advanced (tous les paramètres du moteur, alignés sur une même colonne ; une valeur modifiée passe en ambre et son bouton reset, une flèche circulaire à droite du champ, devient actif ; « Reset all ») et onglet Contacts and issues (contact sélectionné, liste des erreurs et avertissements). Le nombre d'erreurs et d'avertissements apparaît dans le titre de l'onglet, par exemple « Contacts and issues (2) ».
- **Barre de statut** : message (texte complet dans l'infobulle s'il est coupé), progression et Cancel pendant un calcul, nombre de contacts, d'arbres, durée, erreurs et avertissements.
- **Unités** : angles en degrés (°), surfaces en mm², longueurs en mm.

## 2. Flux de travail

1. **Importer** un STL, OBJ ou 3MF. Le fichier est supposé en millimètres s'il ne déclare pas d'unité ; un modèle de moins de 5 mm ou de plus de 2 m déclenche un avertissement d'échelle.
2. **Vérifier le maillage.** Le diagnostic est lancé au chargement. Si le maillage a des erreurs, un bandeau rouge l'indique. « Repair » répare une copie (annulable) ; l'original est conservé. Si vous générez sans réparer, une fenêtre propose « Repair, then generate », « Generate anyway » ou « Cancel ».
3. **Orienter** : rotation à la main, ou « Suggest orientation » qui propose plusieurs orientations classées selon un coût explicite (volume de support estimé, moment des surplombs, surface en surplomb, hauteur, stabilité, empreinte au plateau ; formule dans `arbor/core/orientation.py`, pondérations heuristiques non calibrées). Cliquez une proposition pour l'appliquer.
4. **Choisir le profil** : buse 0,2 ou 0,4, densité Light / Normal / Dense. Ajuster si besoin l'angle de surplomb, la densité, l'autorisation de supports posés sur le modèle, le raft, les entretoises.
5. **Generate supports.** Le calcul tourne en arrière-plan ; l'interface reste utilisable. Une carte de progression s'affiche en haut de la vue : tâche, étape en cours, pourcentage, temps écoulé et bouton Cancel (aussi Échap, ou Cancel dans la barre de statut). Pendant le calcul, les commandes qui modifient la géométrie du modèle sont verrouillées (bandeau bleu, « locked: computing » sur la section Orientation and size). Si vous modifiez contacts ou paramètres pendant le calcul, le résultat est marqué périmé (bandeau ambre, libellé « Generate supports * »).
6. **Inspecter** : contacts colorés par raison (îlot, point bas, coin, arête, surface, manuel ; légende et compteurs), problèmes en rouge dans la vue. Dans la liste des problèmes, chaque ligne porte une icône de gravité (disque rouge : erreur, triangle orange : avertissement, cercle gris : info) ; l'infobulle donne le code (voir section 3). Cliquer une ligne centre la vue dessus.
7. **Éditer** : clic gauche sur le modèle ajoute un contact manuel (aperçu de la pointe au survol), clic droit sur un contact le supprime. Un contact sélectionné peut être désactivé ou recevoir un diamètre de pointe propre. Ensuite « Rebuild branches » reconstruit uniquement les arbres en gardant les contacts. Undo/Redo couvrent toutes ces étapes.
8. **Exporter** en 3MF (recommandé) ou STL. **Open in OrcaSlicer** (barre d'outils ou menu Export) ouvre le résultat dans OrcaSlicer ; le chemin du programme se règle dans File > Preferences..., section « Slicers ». Pour OrcaSlicer / Bambu Studio, « 3MF for Orca / Bambu: separate objects » garde le modèle et les supports (+ raft) en deux objets alignés, chacun pouvant recevoir sa propre hauteur de couche dans le slicer. Le fichier est relu après écriture pour validation. Les métadonnées du 3MF contiennent les paramètres réellement utilisés pour construire les supports.

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
| `rests_on_model`, `geometric_only` | info | marques sur le modèle ; rappel que les contrôles sont géométriques |

Aucun contrôle ne garantit qu'une impression réussira. L'absence d'erreur signifie seulement que les contrôles géométriques n'ont rien trouvé.

## 4. Dans le slicer

- Désactivez les supports du slicer.
- PrusaSlicer / OrcaSlicer / Bambu Studio : répondez **Oui** à « Multi-part object detected ».
- Supports en 3 parois conseillés (paramètre informatif `walls`).
- Voir `docs/SLICERS.md`.
