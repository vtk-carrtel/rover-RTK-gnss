# Plan de déploiement autonome — Raspberry Pi et UM980

## Objectif

Mettre en place un rover GNSS autonome utilisable sur le terrain sans écran ni clavier.

Le fonctionnement final doit être le suivant :

1. l'iPhone ou le Samsung crée un hotspot Wi-Fi avec accès Internet ;
2. le Raspberry Pi s'y connecte automatiquement ;
3. le Raspberry Pi lance automatiquement l'acquisition du UM980 ;
4. le client NTRIP récupère et transmet les corrections RTK ;
5. les fichiers NMEA, CSV et éventuellement GPX sont enregistrés localement ;
6. une page Web locale permet de surveiller le rover depuis le téléphone.

```text
iPhone ou Samsung
    │ Hotspot + Internet
    ▼
Raspberry Pi
    ├── acquisition UM980
    ├── corrections NTRIP
    ├── fichiers CSV/NMEA/GPX
    └── page Web http://rover.local:8080
```

Sur le terrain, l'utilisation doit se limiter à :

1. activer le hotspot du téléphone ;
2. alimenter le Raspberry Pi ;
3. attendre son démarrage ;
4. ouvrir la page Web depuis le téléphone ;
5. vérifier que l'acquisition est active et que NTRIP est connecté.

## Hypothèses

- Raspberry Pi avec Wi-Fi intégré ou adaptateur Wi-Fi compatible ;
- Raspberry Pi OS Bookworm ou plus récent ;
- récepteur Unicore UM980 connecté en USB ;
- projet installé dans `/home/vtk/Documents/rover-um980` ;
- utilisateur Linux `vtk` ;
- NetworkManager utilisé pour gérer le réseau.

## Étape 1 — Vérifier le Raspberry Pi

Afficher la version du système :

```bash
cat /etc/os-release
```

Vérifier NetworkManager :

```bash
nmcli --version
```

Vérifier Python :

```bash
python3 --version
```

NetworkManager est utilisé par défaut depuis Raspberry Pi OS Bookworm.

Documentation : <https://www.raspberrypi.com/documentation/computers/configuration.html>

## Étape 2 — Préparer les hotspots

### Samsung

Ouvrir :

```text
Paramètres
→ Connexions
→ Point d'accès mobile et modem
→ Point d'accès mobile
```

Configurer :

- un nom de réseau fixe ;
- un mot de passe fixe ;
- la bande `2,4 GHz` ;
- le réseau masqué désactivé ;
- l'économie d'énergie désactivée ;
- l'arrêt automatique du hotspot désactivé.

Documentation : <https://www.samsung.com/us/support/answer/ANS10002546/>

### iPhone

Ouvrir :

```text
Réglages
→ Partage de connexion
```

Configurer :

- `Autoriser d'autres utilisateurs` activé ;
- un mot de passe Wi-Fi fixe ;
- `Compatibilité maximale` activée si l'option est disponible.

Le nom du hotspot correspond généralement au nom de l'iPhone.

Documentation : <https://support.apple.com/fr-fr/111785>

## Étape 3 — Enregistrer les deux réseaux dans le Pi

Activer uniquement le hotspot Samsung, puis afficher les réseaux :

```bash
nmcli device wifi list
```

Connecter le Pi au Samsung :

```bash
sudo nmcli --ask device wifi connect "NOM_DU_SAMSUNG" name rover-samsung
```

Désactiver ensuite le Samsung, activer le hotspot iPhone, puis exécuter :

```bash
sudo nmcli --ask device wifi connect "NOM_DE_L_IPHONE" name rover-iphone
```

Configurer l'autoconnexion :

```bash
sudo nmcli connection modify rover-samsung \
  connection.autoconnect yes \
  connection.autoconnect-priority 20 \
  connection.autoconnect-retries 0
```

```bash
sudo nmcli connection modify rover-iphone \
  connection.autoconnect yes \
  connection.autoconnect-priority 10 \
  connection.autoconnect-retries 0
```

Dans cet exemple, le Samsung est prioritaire. Si les deux hotspots sont visibles, le Pi essaie le Samsung en premier.

La valeur `0` de `connection.autoconnect-retries` demande à NetworkManager de continuer à réessayer.

Vérifier les profils :

```bash
nmcli --fields NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
```

Documentation : <https://www.networkmanager.dev/docs/api/latest/nm-settings-nmcli.html>

## Étape 4 — Donner un nom réseau au Pi

Définir le nom `rover` :

```bash
sudo hostnamectl set-hostname rover
```

Installer et activer la découverte locale :

```bash
sudo apt update
sudo apt install avahi-daemon
sudo systemctl enable --now avahi-daemon
```

Redémarrer :

```bash
sudo reboot
```

Après le redémarrage, le Pi devrait être accessible sous le nom :

```text
rover.local
```

La découverte `.local` peut toutefois être irrégulière avec certains hotspots. Une adresse IP directe doit rester disponible comme solution de secours.

## Étape 5 — Préparer Python

Se placer dans le projet :

```bash
cd /home/vtk/Documents/rover-um980
```

Créer l'environnement virtuel s'il n'existe pas encore :

```bash
python3 -m venv .venv
```

L'activer :

```bash
source .venv/bin/activate
```

Installer les dépendances :

```bash
python -m pip install -r requirements.txt
```

Vérifier les dépendances :

```bash
python -c "import serial, pynmeagps; print('Dépendances OK')"
```

## Étape 6 — Préparer l'accès au UM980

Ajouter l'utilisateur au groupe série :

```bash
sudo usermod -aG dialout vtk
```

Redémarrer pour appliquer l'appartenance au groupe :

```bash
sudo reboot
```

Connecter le UM980 et rechercher son port :

```bash
cd /home/vtk/Documents/rover-um980
.venv/bin/python save_gps_nmea.py --list-ports
```

Rechercher également un identifiant USB stable :

```bash
ls -l /dev/serial/by-id/
```

Utiliser de préférence un chemin de la forme :

```text
/dev/serial/by-id/usb-...
```

Ce chemin est plus stable que `/dev/ttyUSB0`, dont le numéro peut changer.

## Étape 7 — Tester l'acquisition manuellement

Remplacer `IDENTIFIANT_UM980` par le nom réellement trouvé.

Tester d'abord sans NTRIP :

```bash
.venv/bin/python save_gps_nmea.py \
  --port /dev/serial/by-id/IDENTIFIANT_UM980 \
  --no-ntrip \
  --duration 30
```

Vérifier les fichiers :

```bash
ls -lh enregistrements/
```

Tester ensuite avec NTRIP :

```bash
.venv/bin/python save_gps_nmea.py \
  --port /dev/serial/by-id/IDENTIFIANT_UM980 \
  --ntrip \
  --duration 60
```

Vérifier que :

- des trames NMEA sont reçues ;
- le CSV contient des coordonnées ;
- NTRIP se connecte ;
- la qualité passe idéalement à `RTK Float`, puis `RTK Fixed`.

## Étape 8 — Ajouter la page Web

Cette fonctionnalité est maintenant implémentée dans `rover_dashboard.py`.

Architecture recommandée :

- `save_gps_nmea.py` continue de gérer le GNSS et NTRIP ;
- il publie régulièrement son état dans un petit fichier JSON ;
- le programme `rover_dashboard.py` lit cet état ;
- `rover_dashboard.py` fournit une page Web sur le port `8080` ;
- le tableau de bord reste disponible même si l'enregistreur redémarre ou rencontre une erreur.

Le tableau de bord doit notamment afficher :

- état de l'acquisition ;
- état du port UM980 ;
- état et mountpoint NTRIP ;
- qualité du fix : autonome, DGPS, RTK Float ou RTK Fixed ;
- nombre de satellites ;
- HDOP ;
- coordonnées actuelles ;
- durée de la session ;
- nombre de positions enregistrées ;
- nom et taille du fichier en cours ;
- espace disque disponible ;
- date de la dernière mise à jour reçue.

Exemple :

```text
Rover UM980
────────────────────────
Acquisition : active
Port UM980   : connecté
NTRIP        : connecté — NEAR
Fix          : RTK Fixed
Satellites   : 19
HDOP         : 0.90
Durée        : 00:14:32
Positions    : 873
Fichier      : gps_nmea_....csv
Espace libre : 24.8 GB
```

Le serveur Web doit écouter sur toutes les interfaces :

```text
0.0.0.0:8080
```

Il ne doit pas écouter uniquement sur `127.0.0.1`, car cela empêcherait l'accès depuis le téléphone.

Lancement manuel :

```bash
.venv/bin/python rover_dashboard.py
```

Le fichier d'état partagé par défaut est :

```text
/tmp/rover-um980-status.json
```

## Étape 9 — Lancement automatique avec systemd

Après validation manuelle, créer deux services :

```text
rover-gps.service
rover-dashboard.service
```

Les deux services sont disponibles dans le dossier `systemd/`, liés à systemd,
activés au démarrage et actuellement en cours d'exécution.

Le service d'acquisition pourra suivre ce modèle :

```ini
[Unit]
Description=Rover UM980 GPS recorder
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
User=vtk
SupplementaryGroups=dialout
WorkingDirectory=/home/vtk/Documents/rover-um980
ExecStart=/home/vtk/Documents/rover-um980/.venv/bin/python /home/vtk/Documents/rover-um980/save_gps_nmea.py --port /dev/serial/by-id/IDENTIFIANT_UM980 --ntrip --quiet
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Le service ne doit pas dépendre strictement de `network-online.target`. L'acquisition GNSS pourra ainsi commencer même si le hotspot n'est pas encore disponible. Le client NTRIP se reconnectera lorsque l'accès Internet apparaîtra.

Le service du tableau de bord lance `rover_dashboard.py` sur le port 8080.

Après création des services :

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rover-gps.service
sudo systemctl enable --now rover-dashboard.service
```

Vérifier leur état :

```bash
systemctl status rover-gps.service
systemctl status rover-dashboard.service
```

Afficher les journaux en direct :

```bash
journalctl -u rover-gps.service -f
```

## Étape 10 — Accéder à la page depuis le téléphone

Essayer d'abord :

```text
http://rover.local:8080
```

Si cela ne fonctionne pas, utiliser l'adresse IP du Pi.

Pour l'afficher sur le Pi :

```bash
hostname -I
```

Exemples :

```text
iPhone  : http://172.20.10.2:8080
Samsung : http://192.168.x.x:8080
```

Sur Samsung, l'adresse du Pi apparaît souvent dans la liste des appareils connectés. Sur iPhone, essayer d'abord `rover.local`, puis l'adresse IP directe.

La topologie reste identique avec les deux téléphones : le téléphone fournit le hotspot et le Raspberry Pi s'y connecte. Le téléphone tente ensuite d'ouvrir la page Web du Pi à travers le réseau local qu'il a créé.

## Étape 11 — Tests avant utilisation sur le lac

Valider au minimum les scénarios suivants :

- démarrage avec le hotspot déjà actif ;
- démarrage sans hotspot, puis activation ultérieure ;
- perte et retour de la connexion Internet ;
- extinction et redémarrage du hotspot ;
- UM980 branché avant le démarrage ;
- UM980 branché après le démarrage ;
- débranchement temporaire du UM980 ;
- redémarrage complet du Raspberry Pi ;
- accès à la page depuis le Samsung ;
- accès à la page depuis l'iPhone ;
- passage du Samsung vers l'iPhone et inversement ;
- arrêt propre du Raspberry Pi ;
- reprise après une coupure d'alimentation ;
- espace disque suffisant pour une longue session ;
- présence et contenu des fichiers CSV/NMEA/GPX après chaque test.

## Comportement attendu en cas de panne

- **Hotspot absent** : l'acquisition GNSS locale continue ; NTRIP attend et réessaie.
- **Internet perdu** : l'enregistrement continue sans corrections ; NTRIP se reconnecte automatiquement.
- **UM980 absent au démarrage** : systemd relance périodiquement le service.
- **UM980 débranché** : le service signale l'erreur puis redémarre.
- **Programme arrêté anormalement** : systemd le redémarre après cinq secondes.
- **Nom `rover.local` indisponible** : utiliser l'adresse IP directe.
- **Page Web sans mise à jour** : elle signale un état périmé ou un enregistreur arrêté.

## Ordre de réalisation

1. valider Raspberry Pi OS, NetworkManager et Python ;
2. configurer et tester les hotspots Samsung et iPhone ;
3. valider l'autoconnexion après redémarrage ;
4. installer les dépendances Python ;
5. identifier le chemin stable du UM980 ;
6. tester l'acquisition sans NTRIP ;
7. tester l'acquisition avec NTRIP ;
8. développer l'état JSON et le tableau de bord Web ;
9. créer les services systemd ;
10. réaliser tous les essais de panne et de récupération ;
11. effectuer un essai complet sur le terrain avant une vraie campagne de mesure.
