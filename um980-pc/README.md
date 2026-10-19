# UM980 PC — Acquisition NMEA & NTRIP RTK

Script autonome pour enregistrer le flux NMEA et injecter les corrections RTK NTRIP lorsque le récepteur Unicore UM980 est connecté directement en USB à un PC.

## 1. Installation

Créez un environnement virtuel et installez les dépendances :

```bash
cd /home/vtk/Documents/um980-pc
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Configuration NTRIP

Modifiez le fichier `ntrip.conf` selon vos besoins (par défaut : réseau libre Centipede RTK avec le mountpoint `NEAR`).

## 3. Utilisation

### Lister les ports série disponibles
Pour identifier le port USB du UM980 (ex: `/dev/ttyUSB0` ou `/dev/ttyACM0`) :

```bash
./save_gps_nmea.py --list-ports
```

### Enregistrement avec corrections NTRIP RTK (recommandé)

```bash
# Détection automatique du port USB et mountpoint NEAR
./save_gps_nmea.py --ntrip

# En spécifiant un port particulier
./save_gps_nmea.py -p /dev/ttyUSB0 --ntrip

# Avec génération d'une trace GPX en plus du CSV/NMEA
./save_gps_nmea.py --ntrip --gpx
```

### Enregistrement autonome (sans corrections RTK)

```bash
./save_gps_nmea.py --no-ntrip
```

Les enregistrements sont sauvegardés par défaut dans le sous-dossier `enregistrements/`.
