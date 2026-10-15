# Guide utilisateur

L'interface est en anglais ; ce guide reprend les libellés tels qu'ils apparaissent. Captures dans `docs/screenshots/`.

## 1. Vue d'ensemble

- **Barre d'outils** : New, Open, Save, Undo, Redo, Generate supports, Rebuild branches, Clear supports, Export (3MF modèle + supports + raft, STL supports seuls, STL modèle + supports), Cancel (Échap) pendant un calcul.
- **Panneau gauche** : import, objet chargé, dimensions et nombre de triangles, échelle (mm ou %), rotation (champs numériques), Place on plate, Suggest orientation, Mesh check (Check / Repair), profil et paramètres simples.
- **Vue 3D centrale** : rotation (clic gauche + glisser), déplacement (clic milieu ou Maj), zoom (molette), Fit (touche F). Modes d'affichage modèle seul / supports seuls / les deux. Curseur vertical à droite : coupe par hauteur (seul ce qui est sous la hauteur choisie est affiché).
- **Panneau droit** : onglet Advanced (tous les paramètres du moteur, une valeur modifiée passe en ambre avec un bouton reset, « Reset all ») et onglet Contacts and issues (contact sélectionné, liste des erreurs et avertissements).
- **Barre de statut** : progression, nombre de contacts, d'arbres, durée, erreurs et avertissements.

## 2. Flux de travail

1. **Importer** un STL, OBJ ou 3MF. Le fichier est supposé en millimètres s'il ne déclare pas d'unité ; un modèle de moins de 5 mm ou de plus de 2 m déclenche un avertissement d'échelle.
2. **Vérifier le maillage.** Le diagnostic est lancé au chargement. Si le maillage a des erreurs, un bandeau rouge l'indique. « Repair » répare une copie (annulable) ; l'original est conservé. Si vous générez sans réparer, une fenêtre propose « Repair, then generate », « Generate anyway » ou « Cancel ».
3. **Orienter** : rotation à la main, ou « Suggest orientation » qui propose plusieurs orientations classées selon un coût explicite (volume de support estimé, moment des surplombs, surface en surplomb, hauteur, stabilité, empreinte au plateau ; formule dans `arbor/core/orientation.py`, pondérations heuristiques non calibrées). Cliquez une proposition pour l'appliquer.
4. **Choisir le profil** : buse 0,2 ou 0,4, densité Light / Normal / Dense. Ajuster si besoin l'angle de surplomb, la densité, l'autorisation de supports posés sur le modèle, le raft, les entretoises.
5. **Generate supports.** Le calcul tourne en arrière-plan ; l'interface reste utilisable. Pendant le calcul, les commandes qui modifient la géométrie du modèle sont verrouillées (bandeau bleu). Si vous modifiez contacts ou paramètres pendant le calcul, le résultat est marqué périmé (bandeau ambre, libellé « Generate supports * »).
6. **Inspecter** : contacts colorés par raison (îlot, point bas, coin, arête, surface, manuel ; légende et compteurs), problèmes en rouge dans la vue. Cliquer une ligne de la liste des problèmes centre la vue dessus.
7. **Éditer** : clic gauche sur le modèle ajoute un contact manuel (aperçu de la pointe au survol), clic droit sur un contact le supprime. Un contact sélectionné peut être désactivé ou recevoir un diamètre de pointe propre. Ensuite « Rebuild branches » reconstruit uniquement les arbres en gardant les contacts. Undo/Redo couvrent toutes ces étapes.
8. **Exporter** en 3MF (recommandé) ou STL. Le fichier est relu après écriture pour validation. Les métadonnées du 3MF contiennent les paramètres réellement utilisés pour construire les supports.

## 3. Lire les problèmes signalés

| Code | Gravité | Signification |
|---|---|---|
| `unsupported_region` | erreur (îlot ou grande zone) / avertissement | zone de la couche qui imprimerait dans le vide |
| `unrouted_contact` | erreur | contact impossible à relier au plateau ou au modèle |
| `floating_support` | erreur | partie de support en l'air, ou pied « sur le modèle » qui ne le touche pas |
| `disconnected_branch` | erreur | branche dont la chaîne ne finit pas sur un pied |
| `model_collision` | avertissement / erreur au-delà de 3x la tolérance des pointes | supports qui entrent dans le modèle |
| `below_plate`, `invalid_support_mesh` | erreur | géométrie de support invalide |
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
