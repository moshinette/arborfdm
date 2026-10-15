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
