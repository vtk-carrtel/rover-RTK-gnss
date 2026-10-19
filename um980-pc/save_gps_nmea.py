#!/usr/bin/env python3
"""
GPS NMEA Data Logger & NTRIP RTK Client for UM980 / GNSS Receivers
Records raw NMEA stream, parses positions to CSV/GPX, and injects RTCM3 NTRIP corrections.
Works directly with .venv, no pygpsclient required.
"""

import argparse
import base64
import configparser
import csv
import datetime
import json
import math
import os
import shutil
import signal
import socket
import sys
import threading
import time
from pathlib import Path

# Auto-reexec with local .venv python if not currently running inside this venv
_SCRIPT_DIR = Path(__file__).resolve().parent
_VENV_DIR = _SCRIPT_DIR / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python3"

if _VENV_PYTHON.is_file() and Path(sys.prefix).resolve() != _VENV_DIR.resolve():
    try:
        os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
    except (OSError, TypeError, ValueError):
        pass

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write(
        "Error: 'pyserial' is not installed.\n"
        "Please run: .venv/bin/pip install pyserial\n"
    )
    sys.exit(1)

try:
    from pynmeagps import NMEAReader
    HAVE_PYNMEAGPS = True
except ImportError:
    HAVE_PYNMEAGPS = False

# Mapping of NMEA GGA quality indicators
GGA_QUALITY_MAP = {
    0: "No Fix",
    1: "Autonomous GNSS (SPS)",
    2: "Differential GNSS (DGPS)",
    3: "PPS Fix",
    4: "RTK Fixed",
    5: "RTK Float",
    6: "Estimated / Dead Reckoning",
    7: "Manual Input",
    8: "Simulation",
}

# Approximate reference coordinates for station distance search
DEFAULT_LAT_REF = 46.36
DEFAULT_LON_REF = 6.44

# ANSI colors for status display
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"
COLOR_GREEN = "\033[32m"
COLOR_YELLOW = "\033[33m"
COLOR_CYAN = "\033[36m"
COLOR_RED = "\033[31m"
COLOR_GRAY = "\033[90m"
COLOR_MAGENTA = "\033[35m"

DEFAULT_STATUS_FILE = Path("/tmp/rover-um980-status.json")


def format_bytes(num_bytes: int) -> str:
    """Format bytes into human readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def optional_float(value) -> float | None:
    """Convert a possibly empty NMEA field to float."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_status_file(path: Path, payload: dict):
    """Atomically publish recorder state without disrupting acquisition."""
    temp_path = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **payload,
        }
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with open(temp_path, "w", encoding="utf-8") as status_handle:
            json.dump(payload, status_handle, ensure_ascii=False, separators=(",", ":"))
            status_handle.write("\n")
        os.replace(temp_path, path)
    except OSError:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two points on Earth in km."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def list_serial_ports():
    """List all detected serial ports with hardware details."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports detected.")
        return
    print(f"{COLOR_BOLD}Available serial ports:{COLOR_RESET}")
    for p in ports:
        desc = p.description or "No description"
        hwid = p.hwid or ""
        print(f"  • {COLOR_CYAN}{p.device}{COLOR_RESET} : {desc} [{hwid}]")


def auto_detect_port(preferred: str | None = None) -> str | None:
    """Find the best matching serial port for GPS receiver."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None

    if preferred:
        for p in ports:
            if p.device == preferred:
                return p.device
        return preferred

    # Search for USB serial ports
    usb_ports = [p for p in ports if "ttyUSB" in p.device or "ttyACM" in p.device]
    if not usb_ports:
        return ports[0].device

    # Look for known GNSS or UART chips
    keywords = ["ftdi", "ft230x", "unicore", "um980", "gnss", "gps", "ch340", "cp210"]
    for p in usb_ports:
        haystack = f"{p.device} {p.description or ''} {p.hwid or ''}".lower()
        if any(k in haystack for k in keywords):
            return p.device

    return usb_ports[0].device


def fetch_and_display_ntrip_stations(
    server: str = "caster.centipede.fr",
    port: int = 2101,
    center_lat: float = DEFAULT_LAT_REF,
    center_lon: float = DEFAULT_LON_REF,
    location_name: str = "la position de référence",
    max_results: int = 12,
):
    """Query caster sourcetable and print closest mountpoints sorted by distance."""
    print(f"\n{COLOR_BOLD}Fetching NTRIP stations from {server}:{port}...{COLOR_RESET}")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(6.0)
        s.connect((server, port))
        s.sendall(b"GET / HTTP/1.0\r\nUser-Agent: NTRIP Rover/1.0\r\nAccept: */*\r\n\r\n")
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
    except Exception as err:
        sys.stderr.write(f"{COLOR_RED}Failed to connect to NTRIP caster: {err}{COLOR_RESET}\n")
        return

    stations = []
    for line in data.decode("latin1", errors="replace").splitlines():
        if line.startswith("STR;"):
            parts = line.split(";")
            if len(parts) > 10:
                mp = parts[1]
                city = parts[2]
                fmt = parts[3]
                nav_system = parts[6] if len(parts) > 6 else ""
                country = parts[8] if len(parts) > 8 else ""
                try:
                    lat = float(parts[9])
                    lon = float(parts[10])
                    dist = haversine_distance_km(center_lat, center_lon, lat, lon)
                    stations.append((dist, mp, city, country, fmt, nav_system, lat, lon))
                except (ValueError, TypeError):
                    continue

    stations.sort(key=lambda x: x[0])

    print(
        f"\n{COLOR_BOLD}Stations NTRIP les plus proches de {COLOR_GREEN}{location_name}{COLOR_RESET} "
        f"({center_lat:.4f}°N, {center_lon:.4f}°E) :{COLOR_RESET}\n"
    )
    header = f"{'Mountpoint':<12} {'Distance':<10} {'Ville / Lieu':<22} {'Format':<10} {'Systèmes'}"
    print(COLOR_BOLD + header + COLOR_RESET)
    print("-" * len(header))

    # Also display NEAR virtual mountpoint at top if available
    print(f"{COLOR_CYAN}{'NEAR':<12}{COLOR_RESET} {'Auto-VRS':<10} {'Plus proche auto (VRS)':<22} {'RTCM3':<10} {'Multi-GNSS'}")

    for dist, mp, city, country, fmt, nav, lat, lon in stations[:max_results]:
        dist_str = f"{dist:.1f} km"
        loc_str = f"{city} ({country})" if country else city
        color = COLOR_GREEN if mp.upper() == "CLOU" else COLOR_CYAN
        marker = " (Sélection par défaut)" if mp.upper() == "CLOU" else ""
        print(f"{color}{mp:<12}{COLOR_RESET} {dist_str:<10} {loc_str:<22} {fmt:<10} {nav}{marker}")

    print(f"\n{COLOR_GRAY}Pour utiliser une station, lancez : ./save_gps_nmea.py --ntrip <MOUNTPOINT>{COLOR_RESET}\n")


class PositionFix:
    """Aggregates position data from NMEA sentences (GGA, RMC)."""

    def __init__(self):
        self.time_obj: datetime.time | None = None
        self.date_obj: datetime.date | None = None
        self.lat: float | None = None
        self.lon: float | None = None
        self.alt_m: float | None = None
        self.sep_m: float | None = None
        self.quality: int = 0
        self.quality_name: str = "No Fix"
        self.num_sats: int = 0
        self.hdop: float | None = None
        self.speed_kmh: float | None = None
        self.speed_knots: float | None = None
        self.course_deg: float | None = None
        self.pos_mode: str | None = None
        self.diff_age_s: float | None = None

    @property
    def iso_timestamp(self) -> str:
        """Return formatted ISO timestamp (UTC)."""
        if self.date_obj and self.time_obj:
            dt = datetime.datetime.combine(self.date_obj, self.time_obj)
            return dt.strftime("%Y-%m-%dT%H:%M:%S") + (
                f".{self.time_obj.microsecond // 1000:03d}Z"
                if self.time_obj.microsecond
                else "Z"
            )
        if self.time_obj:
            t_str = self.time_obj.strftime("%H:%M:%S")
            if self.time_obj.microsecond:
                t_str += f".{self.time_obj.microsecond // 1000:03d}"
            return t_str + "Z"
        return ""

    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lon is not None


class NTRIPClient(threading.Thread):
    """Background NTRIP Client: fetches RTCM3 corrections and writes to serial port."""

    def __init__(
        self,
        ser: serial.Serial,
        ser_lock: threading.Lock,
        server: str = "caster.centipede.fr",
        port: int = 2101,
        mountpoint: str = "NEAR",
        user: str = "centipede",
        password: str = "centipede",
        gga_interval: float = 10.0,
        get_latest_gga=None,
    ):
        super().__init__(daemon=True)
        self.ser = ser
        self.ser_lock = ser_lock
        self.server = server
        self.port = port
        self.mountpoint = mountpoint.strip("/ ")
        self.user = user
        self.password = password
        self.gga_interval = gga_interval
        self.get_latest_gga = get_latest_gga

        self.stop_event = threading.Event()
        self.connected = False
        self.status = "Initialisation..."
        self.bytes_received = 0
        self.bytes_per_sec = 0.0
        self._last_rate_time = time.time()
        self._bytes_since_rate = 0

    def stop(self):
        self.stop_event.set()

    def run(self):
        auth_bytes = f"{self.user}:{self.password}".encode("latin1")
        auth_header = base64.b64encode(auth_bytes).decode("ascii")

        req_lines = [
            f"GET /{self.mountpoint} HTTP/1.0",
            "User-Agent: NTRIP Rover/1.0",
            f"Authorization: Basic {auth_header}",
            "Accept: */*",
            "Connection: close",
        ]
        http_request = "\r\n".join(req_lines) + "\r\n\r\n"

        while not self.stop_event.is_set():
            sock = None
            try:
                self.status = f"Connexion {self.server}:{self.port}..."
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(6.0)
                sock.connect((self.server, self.port))

                sock.sendall(http_request.encode("ascii"))

                # Read HTTP response header
                header_data = b""
                while b"\r\n\r\n" not in header_data and not self.stop_event.is_set():
                    chunk = sock.recv(512)
                    if not chunk:
                        break
                    header_data += chunk

                if b"200 OK" not in header_data and b"ICY 200" not in header_data:
                    first_line = (
                        header_data.split(b"\r\n")[0].decode("latin1", errors="replace")
                        if header_data
                        else "No response"
                    )
                    self.status = f"Erreur Caster ({first_line[:25]})"
                    sock.close()
                    time.sleep(4.0)
                    continue

                self.connected = True
                self.status = f"Connecté ({self.mountpoint})"

                last_gga_sent = 0.0
                sock.settimeout(3.0)

                # Send initial GGA if available
                if self.get_latest_gga:
                    init_gga = self.get_latest_gga()
                    if init_gga:
                        try:
                            sock.sendall(init_gga)
                            last_gga_sent = time.time()
                        except Exception:
                            pass

                # Main RTCM receiving loop
                while not self.stop_event.is_set():
                    now = time.time()

                    # Periodically send rover's live GGA sentence
                    if self.get_latest_gga and (now - last_gga_sent) >= self.gga_interval:
                        gga_bytes = self.get_latest_gga()
                        if gga_bytes:
                            try:
                                sock.sendall(gga_bytes)
                                last_gga_sent = now
                            except Exception:
                                break

                    try:
                        rtcm_chunk = sock.recv(2048)
                    except socket.timeout:
                        continue

                    if not rtcm_chunk:
                        break

                    # Update throughput stats
                    self.bytes_received += len(rtcm_chunk)
                    self._bytes_since_rate += len(rtcm_chunk)

                    if now - self._last_rate_time >= 1.0:
                        elapsed = now - self._last_rate_time
                        self.bytes_per_sec = self._bytes_since_rate / elapsed
                        self._bytes_since_rate = 0
                        self._last_rate_time = now

                    # Inject RTCM3 corrections into GPS receiver UART
                    with self.ser_lock:
                        self.ser.write(rtcm_chunk)

            except Exception as err:
                self.connected = False
                self.bytes_per_sec = 0.0
                self.status = f"Reconnexion..."
                time.sleep(3.0)
            finally:
                self.connected = False
                self.bytes_per_sec = 0.0
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass


class GPSRecorder:
    """Manages serial connection, files, NTRIP client, and NMEA stream parsing."""

    def __init__(
        self,
        port: str,
        baud: int,
        out_dir: Path,
        prefix: str = "gps_nmea",
        custom_name: str | None = None,
        save_raw: bool = True,
        save_csv: bool = True,
        save_gpx: bool = False,
        ntrip_enabled: bool = False,
        ntrip_server: str = "caster.centipede.fr",
        ntrip_port: int = 2101,
        ntrip_mountpoint: str = "NEAR",
        ntrip_user: str = "centipede",
        ntrip_password: str = "centipede",
        ntrip_gga_interval: float = 10.0,
        status_file: Path = DEFAULT_STATUS_FILE,
        quiet: bool = False,
        verbose: bool = False,
    ):
        self.port = port
        self.baud = baud
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.custom_name = custom_name
        self.save_raw = save_raw
        self.save_csv = save_csv
        self.save_gpx = save_gpx

        self.ntrip_enabled = ntrip_enabled
        self.ntrip_server = ntrip_server
        self.ntrip_port = ntrip_port
        self.ntrip_mountpoint = ntrip_mountpoint
        self.ntrip_user = ntrip_user
        self.ntrip_password = ntrip_password
        self.ntrip_gga_interval = ntrip_gga_interval
        self.status_file = Path(status_file)

        self.quiet = quiet
        self.verbose = verbose

        self.running = False
        self.ser: serial.Serial | None = None
        self.ser_lock = threading.Lock()
        self.ntrip_client: NTRIPClient | None = None

        self.raw_file = None
        self.csv_file = None
        self.csv_writer = None
        self.raw_path: Path | None = None
        self.csv_path: Path | None = None
        self.gpx_path: Path | None = None

        self.gpx_points: list[dict] = []
        self.total_sentences = 0
        self.total_fixes = 0
        self.start_time = 0.0
        self.stop_time = 0.0
        self.last_sentence_time = 0.0
        self.dashboard_state = "starting"
        self.dashboard_message = "Initialisation de l'enregistreur"
        self._last_status_publish = 0.0

        self.current_fix = PositionFix()
        self.last_epoch_time: datetime.time | None = None
        self.latest_date: datetime.date | None = None
        self.latest_valid_fix: PositionFix | None = None
        self.latest_raw_gga: bytes | None = None
        self.best_fix_quality: int = 0

        self._setup_files()
        self._publish_status(force=True)

    @staticmethod
    def _file_details(path: Path | None) -> dict | None:
        if not path:
            return None
        try:
            size = path.stat().st_size if path.exists() else 0
        except OSError:
            size = 0
        return {"path": str(path.resolve()), "name": path.name, "size_bytes": size}

    def _publish_status(
        self,
        state: str | None = None,
        message: str | None = None,
        force: bool = False,
    ):
        """Publish a snapshot for the independent Web dashboard."""
        now = time.time()
        if state is not None:
            self.dashboard_state = state
        if message is not None:
            self.dashboard_message = message
        if not force and (now - self._last_status_publish) < 1.0:
            return

        self._last_status_publish = now
        fix = self.latest_valid_fix or self.current_fix
        ntrip = self.ntrip_client
        elapsed = 0.0
        if self.start_time:
            end_time = self.stop_time or now
            elapsed = max(0.0, end_time - self.start_time)

        try:
            disk_free = shutil.disk_usage(self.out_dir).free
        except OSError:
            disk_free = None

        serial_open = bool(self.ser and self.ser.is_open)
        last_sentence_age = (
            max(0.0, now - self.last_sentence_time) if self.last_sentence_time else None
        )

        payload = {
            "state": self.dashboard_state,
            "message": self.dashboard_message,
            "gnss": {
                "connected": serial_open,
                "port": self.port,
                "baud": self.baud,
                "last_sentence_age_s": last_sentence_age,
                "sentences": self.total_sentences,
            },
            "ntrip": {
                "enabled": self.ntrip_enabled,
                "connected": bool(ntrip and ntrip.connected),
                "status": ntrip.status if ntrip else (
                    "Désactivé" if not self.ntrip_enabled else "En attente"
                ),
                "server": self.ntrip_server,
                "port": self.ntrip_port,
                "mountpoint": self.ntrip_mountpoint,
                "bytes_received": ntrip.bytes_received if ntrip else 0,
                "bytes_per_second": ntrip.bytes_per_sec if ntrip else 0.0,
            },
            "fix": {
                "quality_id": fix.quality,
                "quality": fix.quality_name,
                "satellites": fix.num_sats,
                "hdop": fix.hdop,
                "latitude": fix.lat,
                "longitude": fix.lon,
                "altitude_m": fix.alt_m,
                "speed_kmh": fix.speed_kmh,
                "course_deg": fix.course_deg,
                "timestamp": fix.iso_timestamp,
                "diff_age_s": fix.diff_age_s,
            },
            "session": {
                "elapsed_seconds": elapsed,
                "position_fixes": self.total_fixes,
                "best_quality_id": self.best_fix_quality,
                "best_quality": GGA_QUALITY_MAP.get(self.best_fix_quality, "Unknown"),
                "output_directory": str(self.out_dir.resolve()),
                "disk_free_bytes": disk_free,
                "files": {
                    "nmea": self._file_details(self.raw_path),
                    "csv": self._file_details(self.csv_path),
                    "gpx": self._file_details(self.gpx_path),
                },
            },
        }
        write_status_file(self.status_file, payload)

    def _setup_files(self):
        """Prepare raw NMEA, CSV, and GPX output files."""
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = self.custom_name if self.custom_name else f"{self.prefix}_{now_str}"

        if self.save_raw:
            self.raw_path = self.out_dir / f"{base_name}.nmea"
            self.raw_file = open(self.raw_path, "wb")

        if self.save_csv:
            self.csv_path = self.out_dir / f"{base_name}.csv"
            self.csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow([
                "utc_timestamp",
                "latitude",
                "longitude",
                "altitude_m",
                "geoid_sep_m",
                "fix_quality_id",
                "fix_quality",
                "num_satellites",
                "hdop",
                "speed_kmh",
                "speed_knots",
                "course_deg",
                "pos_mode",
                "diff_age_s",
            ])
            self.csv_file.flush()

        if self.save_gpx:
            self.gpx_path = self.out_dir / f"{base_name}.gpx"

    def get_latest_gga_for_ntrip(self) -> bytes | None:
        """Callback for NTRIP client to get live GGA sentence."""
        return self.latest_raw_gga

    def _flush_fix_to_csv(self, fix: PositionFix):
        """Write a completed fix record to CSV."""
        if not self.csv_writer or not self.csv_file:
            return
        if not fix.has_coordinates():
            return

        # Carry over fields if an epoch was split
        if fix.alt_m is None and self.latest_valid_fix and self.latest_valid_fix.alt_m is not None:
            fix.alt_m = self.latest_valid_fix.alt_m
            fix.sep_m = self.latest_valid_fix.sep_m
            if fix.num_sats == 0:
                fix.num_sats = self.latest_valid_fix.num_sats
            if fix.hdop is None:
                fix.hdop = self.latest_valid_fix.hdop

        self.csv_writer.writerow([
            fix.iso_timestamp,
            f"{fix.lat:.9f}" if fix.lat is not None else "",
            f"{fix.lon:.9f}" if fix.lon is not None else "",
            f"{fix.alt_m:.3f}" if fix.alt_m is not None else "",
            f"{fix.sep_m:.3f}" if fix.sep_m is not None else "",
            fix.quality,
            fix.quality_name,
            fix.num_sats,
            f"{fix.hdop:.2f}" if fix.hdop is not None else "",
            f"{fix.speed_kmh:.2f}" if fix.speed_kmh is not None else "",
            f"{fix.speed_knots:.2f}" if fix.speed_knots is not None else "",
            f"{fix.course_deg:.1f}" if fix.course_deg is not None else "",
            fix.pos_mode or "",
            f"{fix.diff_age_s:.1f}" if fix.diff_age_s is not None else "",
        ])
        self.csv_file.flush()
        self.total_fixes += 1
        self.latest_valid_fix = fix
        if fix.quality > self.best_fix_quality:
            self.best_fix_quality = fix.quality

        if self.save_gpx and fix.has_coordinates():
            self.gpx_points.append({
                "lat": fix.lat,
                "lon": fix.lon,
                "alt": fix.alt_m,
                "time": fix.iso_timestamp,
                "hdop": fix.hdop,
                "sats": fix.num_sats,
                "fix": fix.quality_name,
            })

    def _write_gpx_file(self):
        """Generate formatted GPX track file on exit."""
        if not self.save_gpx or not self.gpx_path or not self.gpx_points:
            return

        with open(self.gpx_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<gpx version="1.1" creator="save_gps_nmea.py" '
                    'xmlns="http://www.topografix.com/GPX/1/1">\n')
            f.write('  <trk>\n')
            f.write(f'    <name>{self.gpx_path.stem}</name>\n')
            f.write('    <trkseg>\n')
            for pt in self.gpx_points:
                f.write(f'      <trkpt lat="{pt["lat"]:.8f}" lon="{pt["lon"]:.8f}">\n')
                if pt["alt"] is not None:
                    f.write(f'        <ele>{pt["alt"]:.3f}</ele>\n')
                if pt["time"]:
                    f.write(f'        <time>{pt["time"]}</time>\n')
                if pt["hdop"] is not None:
                    f.write(f'        <hdop>{pt["hdop"]:.2f}</hdop>\n')
                if pt["sats"]:
                    f.write(f'        <sat>{pt["sats"]}</sat>\n')
                f.write('      </trkpt>\n')
            f.write('    </trkseg>\n')
            f.write('  </trk>\n')
            f.write('</gpx>\n')

    def _process_parsed(self, parsed, raw_bytes: bytes):
        """Update current position fix with incoming NMEA object."""
        if not parsed:
            return

        msg_id = getattr(parsed, "msgID", "")
        msg_time = getattr(parsed, "time", None)

        if msg_id == "GGA":
            self.latest_raw_gga = raw_bytes

        # Detect new epoch
        if msg_time is not None:
            if self.last_epoch_time is not None and msg_time != self.last_epoch_time:
                self._flush_fix_to_csv(self.current_fix)
                new_fix = PositionFix()
                new_fix.date_obj = self.latest_date
                self.current_fix = new_fix

            self.last_epoch_time = msg_time
            self.current_fix.time_obj = msg_time

        if msg_id == "RMC":
            date_val = getattr(parsed, "date", None)
            if date_val:
                self.latest_date = date_val
                self.current_fix.date_obj = date_val

            status = getattr(parsed, "status", None)
            if status == "A":
                lat = optional_float(getattr(parsed, "lat", None))
                lon = optional_float(getattr(parsed, "lon", None))
                if lat is not None and lon is not None:
                    self.current_fix.lat = lat
                    self.current_fix.lon = lon

            spd = optional_float(getattr(parsed, "spd", None))
            if spd is not None:
                self.current_fix.speed_knots = spd
                self.current_fix.speed_kmh = spd * 1.852

            cog = optional_float(getattr(parsed, "cog", None))
            if cog is not None:
                self.current_fix.course_deg = cog

            pos_mode = getattr(parsed, "posMode", None)
            if pos_mode:
                self.current_fix.pos_mode = str(pos_mode)
                if self.current_fix.quality == 0:
                    mode_map = {
                        "A": (1, "Autonomous GNSS (SPS)"),
                        "D": (2, "Differential GNSS (DGPS)"),
                        "F": (5, "RTK Float"),
                        "R": (4, "RTK Fixed"),
                        "E": (6, "Estimated / Dead Reckoning"),
                    }
                    if str(pos_mode).upper() in mode_map:
                        q_val, q_desc = mode_map[str(pos_mode).upper()]
                        self.current_fix.quality = q_val
                        self.current_fix.quality_name = q_desc

        elif msg_id == "GGA":
            lat = optional_float(getattr(parsed, "lat", None))
            lon = optional_float(getattr(parsed, "lon", None))
            if lat is not None and lon is not None:
                self.current_fix.lat = lat
                self.current_fix.lon = lon

            quality = getattr(parsed, "quality", 0)
            try:
                self.current_fix.quality = int(quality)
            except (ValueError, TypeError):
                self.current_fix.quality = 0
            self.current_fix.quality_name = GGA_QUALITY_MAP.get(
                self.current_fix.quality, f"Quality {quality}"
            )

            num_sv = getattr(parsed, "numSV", 0)
            try:
                self.current_fix.num_sats = int(num_sv)
            except (ValueError, TypeError):
                self.current_fix.num_sats = 0

            hdop = getattr(parsed, "HDOP", None)
            if hdop is not None:
                try:
                    self.current_fix.hdop = float(hdop)
                except (ValueError, TypeError):
                    pass

            alt = getattr(parsed, "alt", None)
            if alt is not None:
                try:
                    self.current_fix.alt_m = float(alt)
                except (ValueError, TypeError):
                    pass

            sep = getattr(parsed, "sep", None)
            if sep is not None:
                try:
                    self.current_fix.sep_m = float(sep)
                except (ValueError, TypeError):
                    pass

            diff_age = getattr(parsed, "diffAge", None)
            if diff_age:
                try:
                    self.current_fix.diff_age_s = float(diff_age)
                except (ValueError, TypeError):
                    pass

    def _render_status_line(self):
        """Update live status line in terminal."""
        if self.quiet or self.verbose:
            return

        elapsed = int(time.time() - self.start_time)
        hrs, rem = divmod(elapsed, 3600)
        mins, secs = divmod(rem, 60)
        time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"

        fix = self.latest_valid_fix or self.current_fix

        # Color based on fix quality
        q = fix.quality
        if q == 4:  # RTK Fixed
            q_color = COLOR_GREEN + COLOR_BOLD
        elif q == 5:  # RTK Float
            q_color = COLOR_YELLOW + COLOR_BOLD
        elif q == 2:  # DGPS
            q_color = COLOR_CYAN + COLOR_BOLD
        elif q == 1:  # GPS SPS
            q_color = COLOR_CYAN
        else:
            q_color = COLOR_RED

        q_label = f"{q_color}{fix.quality_name}{COLOR_RESET}"

        pos_str = (
            f"{abs(fix.lat):.6f}°{'N' if fix.lat >= 0 else 'S'}, "
            f"{abs(fix.lon):.6f}°{'E' if fix.lon >= 0 else 'W'}"
            if fix.has_coordinates()
            else f"{COLOR_GRAY}No Position{COLOR_RESET}"
        )

        alt_str = f"{fix.alt_m:.1f}m" if fix.alt_m is not None else "--.-m"
        spd_str = f"{fix.speed_kmh:.1f} km/h" if fix.speed_kmh is not None else "--.- km/h"
        sats_str = f"{fix.num_sats} sats" if fix.num_sats else "0 sats"
        hdop_str = f"HDOP {fix.hdop:.1f}" if fix.hdop is not None else "HDOP --"

        # NTRIP indicator
        ntrip_str = ""
        if self.ntrip_client:
            if self.ntrip_client.connected:
                rate_kb = self.ntrip_client.bytes_per_sec / 1024.0
                tot_kb = self.ntrip_client.bytes_received / 1024.0
                ntrip_str = f" | {COLOR_GREEN}NTRIP:{COLOR_RESET} {self.ntrip_client.mountpoint} ({rate_kb:.1f} KB/s)"
            else:
                ntrip_str = f" | {COLOR_YELLOW}NTRIP:{COLOR_RESET} {self.ntrip_client.status[:18]}"

        line = (
            f"\r\033[2K{COLOR_BOLD}[REC {time_str}]{COLOR_RESET} "
            f"Fix: {q_label} ({sats_str}, {hdop_str}){ntrip_str} | "
            f"Pos: {pos_str} | Alt: {alt_str} | Spd: {spd_str}"
        )
        sys.stdout.write(line)
        sys.stdout.flush()

    def run(self, max_duration: float | None = None, max_fixes: int | None = None):
        """Open serial port, launch NTRIP if enabled, and record stream."""
        print(f"{COLOR_BOLD}Connecting to {self.port} at {self.baud} baud...{COLOR_RESET}")
        self._publish_status("starting", f"Connexion à {self.port}", force=True)

        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1.5)
        except serial.SerialException as err:
            sys.stderr.write(f"\n{COLOR_RED}Failed to open {self.port}: {err}{COLOR_RESET}\n")
            self._publish_status("error", f"Impossible d'ouvrir le port GNSS : {err}", force=True)
            return 1

        print(f"{COLOR_GREEN}Connected to GNSS receiver! Recording active.{COLOR_RESET}")
        if self.raw_path:
            print(f"  • Raw NMEA file: {COLOR_CYAN}{self.raw_path}{COLOR_RESET}")
        if self.csv_path:
            print(f"  • Parsed CSV:    {COLOR_CYAN}{self.csv_path}{COLOR_RESET}")
        if self.gpx_path:
            print(f"  • GPX track:     {COLOR_CYAN}{self.gpx_path}{COLOR_RESET}")

        # Start NTRIP client if requested
        if self.ntrip_enabled:
            print(
                f"  • NTRIP RTK:     {COLOR_GREEN}Activé{COLOR_RESET} -> "
                f"{COLOR_CYAN}{self.ntrip_server}:{self.ntrip_port}/{self.ntrip_mountpoint}{COLOR_RESET}"
            )
            self.ntrip_client = NTRIPClient(
                ser=self.ser,
                ser_lock=self.ser_lock,
                server=self.ntrip_server,
                port=self.ntrip_port,
                mountpoint=self.ntrip_mountpoint,
                user=self.ntrip_user,
                password=self.ntrip_password,
                gga_interval=self.ntrip_gga_interval,
                get_latest_gga=self.get_latest_gga_for_ntrip,
            )
            self.ntrip_client.start()
        else:
            print(f"  • NTRIP RTK:     {COLOR_GRAY}Désactivé (mode autonome){COLOR_RESET}")

        print(f"Press {COLOR_BOLD}Ctrl+C{COLOR_RESET} to stop recording at any time.\n")

        self.running = True
        self.start_time = time.time()
        last_status_render = 0.0
        self._publish_status("recording", "Acquisition GNSS active", force=True)

        try:
            while self.running:
                now = time.time()
                self._publish_status()
                if max_duration and (now - self.start_time) >= max_duration:
                    print(f"\n\n{COLOR_YELLOW}Reached duration limit of {max_duration} seconds.{COLOR_RESET}")
                    break

                if max_fixes and self.total_fixes >= max_fixes:
                    print(f"\n\n{COLOR_YELLOW}Reached target count of {max_fixes} fixes.{COLOR_RESET}")
                    break

                # Read raw line from serial
                with self.ser_lock:
                    raw_bytes = self.ser.readline()

                if not raw_bytes:
                    continue

                stripped = raw_bytes.strip()
                if not stripped:
                    continue

                # Save raw NMEA if line starts with $ or !
                if self.raw_file and (raw_bytes.startswith(b"$") or raw_bytes.startswith(b"!")):
                    self.raw_file.write(raw_bytes)
                    self.raw_file.flush()

                self.total_sentences += 1
                self.last_sentence_time = time.time()

                # Parse sentence
                parsed = None
                if HAVE_PYNMEAGPS and (raw_bytes.startswith(b"$") or raw_bytes.startswith(b"!")):
                    try:
                        parsed = NMEAReader.parse(raw_bytes)
                    except Exception:
                        parsed = None

                if parsed:
                    self._process_parsed(parsed, raw_bytes)

                if self.verbose:
                    try:
                        print(raw_bytes.decode("ascii", errors="replace").strip())
                    except Exception:
                        pass
                elif now - last_status_render >= 0.2:
                    self._render_status_line()
                    last_status_render = now

        except KeyboardInterrupt:
            print(f"\n\n{COLOR_YELLOW}Recording stopped by user (Ctrl+C).{COLOR_RESET}")
        finally:
            self.running = False
            self.stop_time = time.time()

            # Stop NTRIP client
            if self.ntrip_client:
                self.ntrip_client.stop()
                self.ntrip_client.join(timeout=1.0)

            # Flush final fix
            if self.current_fix and self.current_fix.has_coordinates():
                self._flush_fix_to_csv(self.current_fix)

            # Close serial port
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass

            # Close files
            if self.raw_file:
                self.raw_file.close()
            if self.csv_file:
                self.csv_file.close()

            # Write GPX file if requested
            if self.save_gpx:
                self._write_gpx_file()

            self._publish_status("stopped", "Acquisition arrêtée", force=True)
            self._print_summary()

        return 0

    def _print_summary(self):
        """Print execution and recording summary."""
        duration = max(0.0, self.stop_time - self.start_time)
        hrs, rem = divmod(int(duration), 3600)
        mins, secs = divmod(rem, 60)
        time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"

        best_q_name = GGA_QUALITY_MAP.get(self.best_fix_quality, "Unknown")
        if self.best_fix_quality == 4:
            best_q_color = COLOR_GREEN + COLOR_BOLD
        elif self.best_fix_quality == 5:
            best_q_color = COLOR_YELLOW + COLOR_BOLD
        elif self.best_fix_quality in (1, 2):
            best_q_color = COLOR_CYAN
        else:
            best_q_color = COLOR_RED

        print(f"\n{COLOR_BOLD}{'=' * 65}{COLOR_RESET}")
        print(f"{COLOR_BOLD}RECORDING SUMMARY{COLOR_RESET}")
        print(f"{'=' * 65}")
        print(f"  Duration:          {time_str} ({duration:.1f} s)")
        print(f"  Sentences saved:   {self.total_sentences}")
        print(f"  Position fixes:    {self.total_fixes}")
        print(f"  Best fix quality:  {best_q_color}{best_q_name}{COLOR_RESET}")

        if self.ntrip_client:
            ntrip_vol = format_bytes(self.ntrip_client.bytes_received)
            print(
                f"  NTRIP Corrections: {COLOR_GREEN}{self.ntrip_mountpoint}{COLOR_RESET} "
                f"({self.ntrip_server}:{self.ntrip_port}) - {ntrip_vol} injectés"
            )

        if self.latest_valid_fix and self.latest_valid_fix.has_coordinates():
            fix = self.latest_valid_fix
            pos = (
                f"{abs(fix.lat):.7f}°{'N' if fix.lat >= 0 else 'S'}, "
                f"{abs(fix.lon):.7f}°{'E' if fix.lon >= 0 else 'W'}"
            )
            alt = f"{fix.alt_m:.2f} m" if fix.alt_m is not None else "N/A"
            diff_str = f", DiffAge: {fix.diff_age_s}s" if fix.diff_age_s else ""
            print(f"  Last coordinates:  {pos} (Alt: {alt})")
            print(f"  Last fix state:    {fix.quality_name} ({fix.num_sats} sats, HDOP {fix.hdop}{diff_str})")

        print(f"\n{COLOR_BOLD}Output Files:{COLOR_RESET}")
        if self.raw_path and self.raw_path.is_file():
            size_str = format_bytes(self.raw_path.stat().st_size)
            print(f"  • Raw NMEA:        {COLOR_GREEN}{self.raw_path}{COLOR_RESET} ({size_str})")
        if self.csv_path and self.csv_path.is_file():
            size_str = format_bytes(self.csv_path.stat().st_size)
            print(f"  • Parsed CSV:      {COLOR_GREEN}{self.csv_path}{COLOR_RESET} ({size_str})")
        if self.gpx_path and self.gpx_path.is_file():
            size_str = format_bytes(self.gpx_path.stat().st_size)
            print(f"  • GPX track:       {COLOR_GREEN}{self.gpx_path}{COLOR_RESET} ({size_str})")
        print(f"{'=' * 65}\n")


def load_config() -> dict:
    """Load default options from ntrip.conf if present."""
    cfg = {
        "ntrip_enabled": False,
        "ntrip_server": "caster.centipede.fr",
        "ntrip_port": 2101,
        "ntrip_mountpoint": "NEAR",
        "ntrip_user": "centipede",
        "ntrip_password": "centipede",
        "ntrip_gga_interval": 10.0,
    }
    conf_path = _SCRIPT_DIR / "ntrip.conf"
    if conf_path.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(conf_path)
            if "ntrip" in parser:
                s = parser["ntrip"]
                cfg["ntrip_enabled"] = s.getboolean("enabled", fallback=False)
                cfg["ntrip_server"] = s.get("server", fallback=cfg["ntrip_server"])
                cfg["ntrip_port"] = s.getint("port", fallback=cfg["ntrip_port"])
                cfg["ntrip_mountpoint"] = s.get("mountpoint", fallback=cfg["ntrip_mountpoint"])
                cfg["ntrip_user"] = s.get("user", fallback=cfg["ntrip_user"])
                cfg["ntrip_password"] = s.get("password", fallback=cfg["ntrip_password"])
                cfg["ntrip_gga_interval"] = s.getfloat("gga_interval", fallback=cfg["ntrip_gga_interval"])
        except Exception:
            pass
    return cfg


def parse_args():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Record GPS NMEA stream and inject NTRIP RTK corrections without pygpsclient.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Enregistrement standard avec NTRIP activé (sélection automatique NEAR) :
  ./save_gps_nmea.py --ntrip

  # Choisir une station spécifique (ex: CLOU, NEAR, JBCH...) :
  ./save_gps_nmea.py --ntrip CLOU
  ./save_gps_nmea.py --ntrip NEAR

  # Lister les stations Centipede RTK les plus proches de la position de référence :
  ./save_gps_nmea.py --ntrip-list

  # Enregistrer sans NTRIP (mode autonome SPS uniquement) :
  ./save_gps_nmea.py --no-ntrip

  # Enregistrer 60s avec trace GPX :
  ./save_gps_nmea.py -d 60 --gpx
        """,
    )

    parser.add_argument(
        "-p",
        "--port",
        help="Serial port of GPS receiver (e.g. /dev/ttyUSB0). If omitted, auto-detects.",
        default=None,
    )
    parser.add_argument(
        "-b",
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200 for UM980)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="enregistrements",
        help="Destination directory for recordings (default: 'enregistrements')",
    )
    parser.add_argument(
        "--prefix",
        default="gps_nmea",
        help="Prefix for generated filenames (default: 'gps_nmea')",
    )
    parser.add_argument(
        "-f",
        "--output-name",
        default=None,
        help="Explicit base filename (without extension). Overrides timestamp naming.",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=None,
        help="Recording duration in seconds. If omitted, records until Ctrl+C.",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=None,
        help="Stop after recording this number of position fixes.",
    )
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="Save only raw NMEA (.nmea), do not generate CSV.",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Do not save raw NMEA file (CSV only).",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Do not save parsed CSV file.",
    )
    parser.add_argument(
        "--gpx",
        action="store_true",
        help="Also export a GPX track file (.gpx) compatible with Google Earth / QGIS.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print raw NMEA sentences to terminal as they arrive.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode: suppress real-time console status line.",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit.",
    )
    parser.add_argument(
        "--status-file",
        default=str(DEFAULT_STATUS_FILE),
        help=f"JSON status file for the Web dashboard (default: {DEFAULT_STATUS_FILE})",
    )

    # NTRIP arguments
    parser.add_argument(
        "--ntrip",
        nargs="?",
        const=cfg["ntrip_mountpoint"],
        default=None,
        help=(
            f"Enable NTRIP RTK client. Optionally specify mountpoint directly "
            f"(default: {cfg['ntrip_mountpoint']})."
        ),
    )
    parser.add_argument(
        "--no-ntrip",
        action="store_true",
        help="Explicitly disable NTRIP client (even if enabled in ntrip.conf).",
    )
    parser.add_argument(
        "--ntrip-mountpoint",
        default=None,
        help=f"NTRIP mountpoint (e.g. CLOU, NEAR, JBCH). Default: {cfg['ntrip_mountpoint']}",
    )
    parser.add_argument(
        "--ntrip-server",
        default=cfg["ntrip_server"],
        help=f"NTRIP Caster hostname. Default: {cfg['ntrip_server']}",
    )
    parser.add_argument(
        "--ntrip-port",
        type=int,
        default=cfg["ntrip_port"],
        help=f"NTRIP Caster port. Default: {cfg['ntrip_port']}",
    )
    parser.add_argument(
        "--ntrip-user",
        default=cfg["ntrip_user"],
        help=f"NTRIP username. Default: {cfg['ntrip_user']}",
    )
    parser.add_argument(
        "--ntrip-password",
        default=cfg["ntrip_password"],
        help=f"NTRIP password. Default: {cfg['ntrip_password']}",
    )
    parser.add_argument(
        "--ntrip-gga-interval",
        type=float,
        default=cfg["ntrip_gga_interval"],
        help=f"Interval in seconds to send rover GGA to caster. Default: {cfg['ntrip_gga_interval']}",
    )
    parser.add_argument(
        "--ntrip-list",
        action="store_true",
        help="Query Centipede RTK sourcetable, display closest stations to the reference position, and exit.",
    )

    return parser.parse_args(), cfg


def main():
    args, cfg = parse_args()

    if args.list_ports:
        list_serial_ports()
        return 0

    if args.ntrip_list:
        fetch_and_display_ntrip_stations(
            server=args.ntrip_server,
            port=args.ntrip_port,
            center_lat=DEFAULT_LAT_REF,
            center_lon=DEFAULT_LON_REF,
            location_name="la position de référence",
        )
        return 0

    port = auto_detect_port(args.port)
    if not port:
        write_status_file(Path(args.status_file), {
            "state": "error",
            "message": "Aucun port série GNSS détecté",
            "gnss": {"connected": False, "port": args.port, "baud": args.baud},
            "ntrip": {"enabled": False, "connected": False},
            "fix": {"quality_id": 0, "quality": "No Fix"},
            "session": {},
        })
        sys.stderr.write(
            f"{COLOR_RED}Error: No serial port found. Check USB connection or specify with -p / --port.{COLOR_RESET}\n"
        )
        return 1

    save_raw = not args.no_raw
    save_csv = not (args.raw_only or args.no_csv)

    if not save_raw and not save_csv and not args.gpx:
        sys.stderr.write(f"{COLOR_RED}Error: All output formats disabled.{COLOR_RESET}\n")
        return 1

    # Determine NTRIP state & mountpoint
    ntrip_enabled = False
    if args.no_ntrip:
        ntrip_enabled = False
    elif args.ntrip is not None:
        ntrip_enabled = True
    else:
        ntrip_enabled = cfg.get("ntrip_enabled", False)

    mountpoint = args.ntrip_mountpoint or args.ntrip or cfg.get("ntrip_mountpoint", "NEAR")

    recorder = GPSRecorder(
        port=port,
        baud=args.baud,
        out_dir=Path(args.output_dir),
        prefix=args.prefix,
        custom_name=args.output_name,
        save_raw=save_raw,
        save_csv=save_csv,
        save_gpx=args.gpx,
        ntrip_enabled=ntrip_enabled,
        ntrip_server=args.ntrip_server,
        ntrip_port=args.ntrip_port,
        ntrip_mountpoint=mountpoint,
        ntrip_user=args.ntrip_user,
        ntrip_password=args.ntrip_password,
        ntrip_gga_interval=args.ntrip_gga_interval,
        status_file=Path(args.status_file),
        quiet=args.quiet,
        verbose=args.verbose,
    )

    return recorder.run(max_duration=args.duration, max_fixes=args.count)


if __name__ == "__main__":
    sys.exit(main())
