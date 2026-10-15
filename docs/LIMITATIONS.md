# Limitations connues

Liste tenue à jour à partir des rapports des sous-agents et de la revue QA (`docs/QA_REVIEW.md`).

## Non validé

- **Aucune impression physique** : diamètres de pointe, densités, pieds et profils sont des points de départ raisonnés, pas des valeurs mesurées. Protocole : `docs/PHYSICAL_TESTS.md`.
- **Slicers** : l'import dans Bambu Studio, OrcaSlicer, PrusaSlicer et Cura n'a pas été testé dans ces logiciels. Le 3MF est validé par trimesh et lib3mf (mode strict, 0 avertissement). Analyse du comportement attendu : `docs/SLICERS.md`.
- **Windows** : `scripts/build_windows.bat` et `run_dev.bat` n'ont pas été exécutés sous Windows. Le spec PyInstaller a été validé par un build Linux (CLI gelée exécutée avec succès).
- **Rendu OpenGL** : testé seulement sous xvfb (captures) et en offscreen ; fluidité non mesurée sur une vraie carte graphique.

## Moteur

- Le routage est discrétisé en tranches de hauteur `segment_length` ; les contrôles de dégagement peuvent compter de la matière jusqu'à une tranche sous le segment réel, et le décalage dépend de z_base (raft).
- Le champ de distance de la grille est approximatif : il reste typiquement 1 à 2 mm³ de contact rasant le long d'un rebord bas (sous la tolérance des pointes, et signalé au-delà).
- Les sphères de jonction ne sont pas recalées par rapport au modèle.
- Supports courts (dégagement inférieur à une pointe) : toujours verticaux, une pointe par contact, sans fusion avec les voisins.
- `tip_shape = "ball"` : la bille pénètre d'environ une demi-sphère de plus qu'une pointe conique.
- Points bas et coins dépendent encore des sommets du maillage (légère dépendance à la tessellation) ; les rangées d'arêtes et le remplissage n'en dépendent plus.
- Au-delà de 2 M points de grille de remplissage, le pas est élargi (signalé dans les stats et dans la note des contacts).
- Le contrôle « les supports n'emprisonnent pas une partie du modèle » n'est pas implémenté : seul un avertissement informatif accompagne les supports posés sur le modèle.
- Les contacts automatiques modifiés pendant une génération complète en cours ne peuvent pas être rejoués : ils sont signalés comme perdus.
- Modèles de plus de 1000 mm refusés ; au-delà d'environ 1 M de triangles, compter environ 2 Go de mémoire pour un passage complet par la CLI (mesuré par la QA).

## Interface

- La coupe par hauteur recalcule la géométrie affichée (environ 0,5 s sur 1 M de triangles).
- Rotation par champs numériques ; pas de poignées de rotation directement dans la vue 3D.
- Interface en anglais uniquement.
