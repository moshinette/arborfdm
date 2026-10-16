# Limitations connues

Liste tenue à jour à partir des rapports des sous-agents et de la revue QA (`docs/QA_REVIEW.md`).

## Non validé

- **Aucune impression physique** : diamètres de pointe, densités, pieds et profils sont des points de départ raisonnés, pas des valeurs mesurées. Protocole : `docs/PHYSICAL_TESTS.md`.
- **Slicers** : OrcaSlicer 2.4.2 essayé (aperçu seulement, A1 mini buse 0,2 : voir `docs/SLICERS.md`) ; Bambu Studio, PrusaSlicer et Cura pas encore. La buse réglée dans le slicer doit être celle du profil ArborFDM, sinon les pointes et branches fines disparaissent. Le 3MF est validé par trimesh et lib3mf (mode strict, 0 avertissement). Analyse du comportement attendu : `docs/SLICERS.md`.
- **Windows** : `scripts/build_windows.bat` a réussi sous Windows le 2026-10-15 avec Python 3.12.10 (tests CLI/export, PyInstaller, test de la CLI gelée sur `examples/shelf.stl`, zip). L'interface graphique gelée et `run_dev.bat` restent à essayer. Python 3.10 et 3.11 (plantage de PySide6 dans l interface) et 3.15+ (pas de paquet `manifold3d`) ne sont pas utilisables pour le build.
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

## Raft, surélévation et supports (0.3)

- Stabilité d'un modèle surélevé au-dessus du raft : seul le critère de base plate (25 mm² au maximum) est évalué ; ni centre de gravité ni nombre de pieds.
- Le seuil de 25 mm² (`LIFT_MAX_BASE_AREA`) est empirique, non validé par impression.
- Réglages par objet du trancheur (hauteur de couche, `object_settings`) impossibles quand le modèle repose sur le raft : le 3MF ne contient alors qu'un objet, donc « lighter supports » ne s'applique pas.
- Supports creux et supports allégés : mesurés dans OrcaSlicer 2.4.2 seulement, non validés par impression ; ils ne se cumulent pas ; le creux ne tient pas compte de `fragile_support` et n'a pas d'effet avec la buse 0,4.
- Raft `footprint` par défaut : retrait du modèle sur impression non validé (mesures au trancheur seulement).
- Pourcentages et couleurs de surface en surplomb par orientation : indication, pas garantie ; leurs seuils (relatifs et absolus) sont empiriques.

## Interface

- La coupe par hauteur recalcule la géométrie affichée (environ 0,5 s sur 1 M de triangles).
- Rotation par champs numériques ; pas de poignées de rotation directement dans la vue 3D.
- Interface en anglais uniquement.
