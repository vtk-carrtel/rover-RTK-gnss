# Rover UM980 — Enregistreur NMEA GPS & Client NTRIP RTK

Script Python autonome pour enregistrer le flux NMEA et les positions GPS d’un récepteur GNSS **Unicore UM980**, avec corrections différentielles **NTRIP RTK**. Il n’utilise pas pygpsclient.

Les sessions sont enregistrées par défaut dans `enregistrements/` (CSV + NMEA, GPX optionnel).

## Matériel

- Récepteur Unicore UM980 (UART USB, 115200 bauds par défaut)
- Connexion Internet pour les corrections NTRIP (réseau [Centipede RTK](https://centipede.fr) par défaut)

## Installation

```bash
git clone https://github.com/vtk-carrtel/rover-RTK-gnss.git
cd rover-RTK-gnss/rover-um980

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Le script relance automatiquement l’interpréteur du `.venv` s’il est présent.

## Configuration NTRIP

Les paramètres par défaut sont dans [`ntrip.conf`](ntrip.conf) (caster Centipede, mountpoint automatique `NEAR`). Identifiants publics Centipede : `centipede` / `centipede`.

Pour un caster privé, modifiez `ntrip.conf` ou passez les options `--ntrip-server`, `--ntrip-port`, `--ntrip-user`, `--ntrip-password`.

## Commandes utiles

### Enregistrement avec corrections NTRIP RTK (recommandé)

```bash
# Sélection automatique par le caster (NEAR, valeur par défaut)
./save_gps_nmea.py --ntrip

# Station fixe (Évian-les-Bains)
./save_gps_nmea.py --ntrip CLOU

# Station spécifique (ex. Annemasse)
./save_gps_nmea.py --ntrip JBCH

# 60 secondes avec trace GPX
./save_gps_nmea.py --ntrip -d 60 --gpx
```

### Stations et réseau NTRIP

```bash
# Stations Centipede les plus proches de la position de référence
./save_gps_nmea.py --ntrip-list

# Caster privé
./save_gps_nmea.py --ntrip --ntrip-server mon-caster.org --ntrip-port 2101 --ntrip-mountpoint MABASE
```

### Enregistrement autonome (sans NTRIP)

```bash
./save_gps_nmea.py --no-ntrip
./save_gps_nmea.py --no-ntrip --gpx
```

### Fichiers et répertoires

```bash
./save_gps_nmea.py --ntrip -f parcours_test --gpx
./save_gps_nmea.py --ntrip -o /chemin/vers/dossier
```

### Arrêt automatique

```bash
./save_gps_nmea.py --ntrip -d 120
./save_gps_nmea.py --ntrip -n 50
```

### Matériel et diagnostic

```bash
./save_gps_nmea.py --list-ports
./save_gps_nmea.py -p /dev/ttyUSB0 -b 115200 --ntrip
./save_gps_nmea.py -v
```

## Tableau de bord Web

Le tableau de bord mobile fonctionne sans dépendance Python supplémentaire. Pendant
l'acquisition, `save_gps_nmea.py` publie son état dans
`/tmp/rover-um980-status.json`. Le serveur Web lit ce fichier sans intervenir dans
le flux GNSS.

Lancer le tableau de bord :

```bash
.venv/bin/python rover_dashboard.py
```

Lancer l'acquisition dans un autre terminal :

```bash
.venv/bin/python save_gps_nmea.py --ntrip
```

Depuis un téléphone connecté au même hotspot, ouvrir :

```text
http://rover.local:8080
```

Si le nom `.local` n'est pas résolu, afficher l'adresse du Pi avec `hostname -I`,
puis ouvrir par exemple `http://10.153.251.58:8080`.

Le tableau de bord affiche l'état GNSS et NTRIP, la qualité du fix, les satellites,
le HDOP, la position, la durée, les compteurs, les fichiers et l'espace disque. Le
bouton **Ouvrir la carte** affiche la dernière position valide dans Google Maps.

Options du tableau de bord :

| Option | Description | Valeur par défaut |
|---|---|---|
| `--host` | Adresse d'écoute HTTP | `0.0.0.0` |
| `--port` | Port HTTP | `8080` |
| `--status-file` | Fichier JSON produit par l'enregistreur | `/tmp/rover-um980-status.json` |

### Démarrage automatique avec systemd

Les unités prêtes à installer se trouvent dans `systemd/`. Elles utilisent le port
stable du UM980 et lancent automatiquement le tableau de bord et l'acquisition NTRIP.

```bash
sudo systemctl link "$PWD/systemd/rover-dashboard.service"
sudo systemctl link "$PWD/systemd/rover-gps.service"
sudo systemctl enable --now rover-dashboard.service rover-gps.service
```

Vérification :

```bash
systemctl status rover-dashboard.service rover-gps.service
journalctl -u rover-gps.service -f
```

## Stations Centipede proches de la zone de référence

[`ntrip.conf`](ntrip.conf) est pré-configuré pour cette région :

| Mountpoint | Distance | Localisation | Format | Constellations |
|---|---|---|---|---|
| **`CLOU`** | **12.0 km** | **Évian-les-Bains** | RTCM3 | GPS + GLO + GAL + BDS |
| **`NEAR`** | Auto-VRS | Plus proche auto via caster *(défaut)* | RTCM3 | Multi-GNSS |
| `WAHB` | 15.1 km | France (FRA) | RTCM3 | Multi-GNSS |
| `JAGGI` | 17.2 km | Suisse (CHZ) | RTCM3 | Multi-GNSS |
| `DELMO` | 19.5 km | Suisse (CHZ) | RTCM3 | Multi-GNSS |
| `MRON` | 19.8 km | Gros Morond (FRA) | RTCM3 | Multi-GNSS |
| `JBCH` | 24.1 km | Annemasse | RTCM3 | Multi-GNSS |

## Options de la ligne de commande

| Option | Description | Valeur par défaut |
|---|---|---|
| `-p`, `--port` | Port série du récepteur (ex. `/dev/ttyUSB0`) | Détection automatique |
| `-b`, `--baud` | Vitesse série | `115200` |
| `-o`, `--output-dir` | Dossier des enregistrements | `enregistrements` |
| `--prefix` | Préfixe des fichiers | `gps_nmea` |
| `-f`, `--output-name` | Nom de base (sans extension) | Horodatage |
| `-d`, `--duration` | Durée max en secondes | Infini (`Ctrl+C`) |
| `-n`, `--count` | Nombre max de positions | Infini |
| `--ntrip [STATION]` | Activer NTRIP RTK | `NEAR` si activé |
| `--ntrip-mountpoint` | Point de montage | `ntrip.conf` |
| `--ntrip-server` | Hôte du caster | `caster.centipede.fr` |
| `--ntrip-port` | Port du caster | `2101` |
| `--ntrip-user` | Identifiant NTRIP | `centipede` |
| `--ntrip-password` | Mot de passe NTRIP | `centipede` |
| `--ntrip-gga-interval` | Intervalle d'envoi GGA au caster | `10.0` s |
| `--ntrip-list` | Lister les stations proches de la position de référence | — |
| `--no-ntrip` | Forcer le mode autonome | Désactivé |
| `--gpx` | Exporter aussi un GPX | Désactivé |
| `--raw-only` | NMEA brut uniquement | Désactivé |
| `--no-csv` | Ne pas écrire le CSV | Désactivé |
| `--no-raw` | Ne pas écrire le NMEA brut | Désactivé |
| `-v`, `--verbose` | Afficher chaque trame NMEA | Désactivé |
| `-q`, `--quiet` | Masquer la ligne d’état | Désactivé |
| `--list-ports` | Lister les ports série et quitter | — |
| `--status-file` | Fichier d'état JSON pour le tableau de bord | `/tmp/rover-um980-status.json` |
| `-h`, `--help` | Afficher l'aide et quitter | — |

## Structure du CSV

Chaque session génère un CSV horodaté dans `enregistrements/` :

| Colonne | Exemple | Description |
|---|---|---|
| `utc_timestamp` | `2026-09-14T00:02:04Z` | Date/heure UTC ISO 8601 |
| `latitude` | `45.123456789` | Latitude (degrés décimaux) |
| `longitude` | `5.123456789` | Longitude (degrés décimaux) |
| `altitude_m` | `404.005` | Altitude antenne (m) |
| `geoid_sep_m` | `50.726` | Séparation géoïde (m) |
| `fix_quality_id` | `2` / `4` | Code qualité NMEA GGA |
| `fix_quality` | `RTK Fixed` / `Differential GNSS (DGPS)` | Qualité lisible |
| `num_satellites` | `19` | Satellites utilisés |
| `hdop` | `0.90` | HDOP |
| `speed_kmh` | `0.63` | Vitesse sol (km/h) |
| `speed_knots` | `0.34` | Vitesse sol (nœuds) |
| `course_deg` | `329.3` | Cap (degrés) |
| `pos_mode` | `D` / `R` | Mode NMEA (A=autonome, D=DGPS, R=RTK Fix, F=RTK Float) |
| `diff_age_s` | `1.0` | Âge des corrections (s) |

Les traces de session (CSV, NMEA, GPX) et les cartes générées (`carte_*.html`) ne sont pas versionnées.

## Niveaux de précision GNSS / RTK

- `0` : **No Fix**
- `1` : **Autonomous GNSS (SPS)** — ~1.5 m à 3 m
- `2` : **Differential GNSS (DGPS)** — ~0.5 m à 1 m
- `5` : **RTK Float** — ~10 cm à 30 cm
- `4` : **RTK Fixed** — ~1 cm à 2 cm
