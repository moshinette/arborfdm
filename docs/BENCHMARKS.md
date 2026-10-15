# Benchmarks

Mesure du 2026-10-15 11:52, produite par `python scripts/bench.py` (aucune valeur saisie à la main).

## Machine

| Élément | Valeur |
|---|---|
| python | 3.13.16 |
| platform | Linux-6.18.44-fc-v114-x86_64-with-glibc2.39 |
| cpu_count | 2 |
| processor | Intel(R) Xeon(R) Processor @ 2.10GHz |
| memory | 7.8 GiB |
| numpy | 2.5.3 |
| scipy | 1.18.1 |
| trimesh | 5.1.1 |
| shapely | 2.2.0 |
| manifold3d | 3.5.4 |
| commit | b96299e |

## Protocole

- Modèle : figurine procédurale (`tests/models/synthetic.py::large_mesh`), raffinée à la taille cible. Déterministe, graine numpy 12345.
- Profil : `nozzle_04_normal`.
- Étapes chronométrées séparément, dans l'ordre de `pipeline.generate`. Médiane sur `runs` exécutions.
- Temps en secondes, un seul processus. `n.m.` = non mesuré.

## Résultats (s)

| Faces | Runs | génération modèle | analysis | contacts | tree | mesh | verify | total | nb contacts | arbres | faces support | étanche | erreurs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 11 978 | 2 | 0.04 | 0.21 | 0.04 | 0.36 | 0.73 | 0.32 | 1.67 | 86 | 14 | 55492 | oui | 0 |
| 119 612 | 2 | 0.28 | 0.85 | 0.18 | 0.41 | 0.71 | 0.79 | 2.94 | 82 | 14 | 50694 | oui | 0 |
| 541 228 | 1 | 0.90 | 2.70 | 0.43 | 0.84 | 0.75 | 2.76 | 7.48 | 88 | 15 | 54030 | oui | 0 |
| 982 054 | 1 | 1.36 | 4.88 | 0.75 | 0.87 | 0.79 | 6.21 | 13.50 | 87 | 18 | 52294 | oui | 0 |
