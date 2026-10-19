#!/usr/bin/env python3
"""Read-only mobile Web dashboard for the UM980 rover recorder."""

import argparse
import datetime
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_STATUS_FILE = Path("/tmp/rover-um980-status.json")
STALE_AFTER_SECONDS = 5.0


DASHBOARD_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#07111f">
  <title>Rover UM980</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07111f;
      --panel: #101d2e;
      --panel-2: #14243a;
      --line: #263850;
      --text: #f3f7fc;
      --muted: #91a3b8;
      --green: #43d17d;
      --amber: #ffc857;
      --red: #ff6274;
      --blue: #58b8ff;
      --cyan: #55d8e6;
      --gray: #738399;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 80% -10%, rgba(44, 129, 196, .24), transparent 38%),
        var(--bg);
      color: var(--text);
      font: 16px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { width: min(920px, 100%); margin: 0 auto; padding: 24px 16px 48px; }
    header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 22px; }
    h1 { margin: 0; font-size: clamp(1.65rem, 5vw, 2.35rem); letter-spacing: -.035em; }
    .subtitle { margin-top: 3px; color: var(--muted); font-size: .9rem; }
    .live { display: flex; align-items: center; gap: 8px; color: var(--muted); white-space: nowrap; }
    .dot { width: 11px; height: 11px; border-radius: 50%; background: var(--gray); box-shadow: 0 0 0 4px rgba(115,131,153,.12); }
    .dot.ok { background: var(--green); box-shadow: 0 0 0 4px rgba(67,209,125,.14), 0 0 16px rgba(67,209,125,.45); }
    .dot.warn { background: var(--amber); box-shadow: 0 0 0 4px rgba(255,200,87,.14); }
    .dot.error { background: var(--red); box-shadow: 0 0 0 4px rgba(255,98,116,.14); }
    .summary {
      display: flex; justify-content: space-between; align-items: center; gap: 14px;
      padding: 17px 18px; margin-bottom: 14px; border: 1px solid var(--line);
      border-radius: 16px; background: linear-gradient(135deg, var(--panel-2), var(--panel));
    }
    .summary strong { display: block; font-size: 1.05rem; }
    .summary span { color: var(--muted); font-size: .88rem; }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .card { background: rgba(16,29,46,.94); border: 1px solid var(--line); border-radius: 16px; padding: 17px; min-width: 0; }
    .label { color: var(--muted); font-size: .76rem; font-weight: 700; text-transform: uppercase; letter-spacing: .09em; }
    .value { margin-top: 8px; font-size: 1.23rem; font-weight: 750; overflow-wrap: anywhere; }
    .detail { margin-top: 5px; color: var(--muted); font-size: .86rem; min-height: 1.2em; overflow-wrap: anywhere; }
    .green { color: var(--green); }
    .amber { color: var(--amber); }
    .red { color: var(--red); }
    .blue { color: var(--blue); }
    .cyan { color: var(--cyan); }
    .gray { color: var(--gray); }
    .wide { grid-column: span 2; }
    .position { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
    .map-link {
      display: inline-flex; min-height: 44px; align-items: center; justify-content: center;
      padding: 10px 14px; border-radius: 11px; background: #1d70b7; color: white;
      font-weight: 700; text-decoration: none; white-space: nowrap;
    }
    .map-link[hidden] { display: none; }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; padding: 0; overflow: hidden; }
    .metric { padding: 16px; background: var(--panel); }
    .metric .value { font-size: 1.08rem; }
    .files { grid-column: 1 / -1; }
    .file-list { list-style: none; margin: 10px 0 0; padding: 0; }
    .file-list li { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-top: 1px solid var(--line); }
    .file-list span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file-list span:last-child { color: var(--muted); white-space: nowrap; }
    footer { margin-top: 18px; text-align: center; color: var(--muted); font-size: .78rem; }
    @media (max-width: 700px) {
      main { padding-top: 18px; }
      .grid { grid-template-columns: 1fr 1fr; }
      .wide, .files { grid-column: 1 / -1; }
      .metrics { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 430px) {
      .grid { grid-template-columns: 1fr; }
      .wide, .files { grid-column: auto; }
      .position { align-items: flex-start; flex-direction: column; }
      .map-link { width: 100%; }
      header { align-items: flex-start; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div><h1>Rover UM980</h1><div class="subtitle">Surveillance GNSS &amp; NTRIP</div></div>
      <div class="live"><i id="liveDot" class="dot"></i><span id="liveText">Connexion…</span></div>
    </header>

    <section class="summary">
      <div><strong id="headline">Chargement de l’état…</strong><span id="message">Veuillez patienter</span></div>
      <div id="elapsed" class="value">00:00</div>
    </section>

    <section class="grid">
      <article class="card">
        <div class="label">Acquisition</div><div id="gnssValue" class="value gray">Inconnue</div>
        <div id="gnssDetail" class="detail">—</div>
      </article>
      <article class="card">
        <div class="label">NTRIP</div><div id="ntripValue" class="value gray">Inconnu</div>
        <div id="ntripDetail" class="detail">—</div>
      </article>
      <article class="card">
        <div class="label">Qualité du fix</div><div id="fixValue" class="value gray">No Fix</div>
        <div id="fixDetail" class="detail">—</div>
      </article>

      <article class="card wide">
        <div class="label">Position actuelle</div>
        <div class="position">
          <div><div id="coordinates" class="value">Position indisponible</div><div id="positionDetail" class="detail">—</div></div>
          <a id="mapLink" class="map-link" target="_blank" rel="noopener" hidden>Ouvrir la carte</a>
        </div>
      </article>
      <article class="card">
        <div class="label">Espace disponible</div><div id="diskFree" class="value">—</div>
        <div class="detail">Stockage des mesures</div>
      </article>

      <section class="card metrics wide">
        <div class="metric"><div class="label">Satellites</div><div id="satellites" class="value">0</div></div>
        <div class="metric"><div class="label">HDOP</div><div id="hdop" class="value">—</div></div>
        <div class="metric"><div class="label">Positions</div><div id="fixCount" class="value">0</div></div>
        <div class="metric"><div class="label">RTCM reçus</div><div id="rtcmBytes" class="value">0 B</div></div>
      </section>

      <article class="card files">
        <div class="label">Fichiers de la session</div><ul id="fileList" class="file-list"><li><span>Aucun fichier</span></li></ul>
      </article>
    </section>
    <footer>Actualisation automatique chaque seconde · <span id="lastUpdate">aucune donnée</span></footer>
  </main>

  <script>
    const $ = id => document.getElementById(id);
    const set = (id, text) => { $(id).textContent = text; };
    const paint = (id, color) => { $(id).className = `value ${color}`; };

    function formatBytes(value) {
      if (value === null || value === undefined) return '—';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let n = Number(value), i = 0;
      while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
      return `${i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
    }
    function formatDuration(value) {
      const seconds = Math.max(0, Math.floor(Number(value) || 0));
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      return h ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
               : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }
    function number(value, digits, suffix='') {
      return value === null || value === undefined ? '—' : `${Number(value).toFixed(digits)}${suffix}`;
    }

    function render(data) {
      const fresh = data.dashboard?.fresh !== false;
      const state = data.state || 'waiting';
      const gnss = data.gnss || {};
      const ntrip = data.ntrip || {};
      const fix = data.fix || {};
      const session = data.session || {};

      $('liveDot').className = `dot ${!fresh || state === 'error' ? 'error' : state === 'recording' ? 'ok' : 'warn'}`;
      set('liveText', !fresh ? 'Hors ligne' : state === 'recording' ? 'En direct' : state === 'error' ? 'Erreur' : 'En attente');
      set('headline', !fresh ? 'Enregistreur sans réponse' : state === 'recording' ? 'Acquisition en cours' : state === 'error' ? 'Erreur du rover' : state === 'stopped' ? 'Acquisition arrêtée' : 'Démarrage');
      set('message', data.message || 'Aucun détail disponible');
      set('elapsed', formatDuration(session.elapsed_seconds));

      set('gnssValue', gnss.connected ? 'Connecté' : 'Déconnecté');
      paint('gnssValue', gnss.connected && fresh ? 'green' : 'red');
      const sentenceAge = gnss.last_sentence_age_s == null ? 'aucune trame' : `dernière trame ${number(gnss.last_sentence_age_s, 1, ' s')}`;
      set('gnssDetail', `${gnss.port || 'port inconnu'} · ${sentenceAge}`);

      if (!ntrip.enabled) {
        set('ntripValue', 'Désactivé'); paint('ntripValue', 'gray');
      } else if (ntrip.connected && fresh) {
        set('ntripValue', 'Connecté'); paint('ntripValue', 'green');
      } else {
        set('ntripValue', 'Reconnexion'); paint('ntripValue', 'amber');
      }
      set('ntripDetail', `${ntrip.mountpoint || '—'} · ${ntrip.status || '—'}`);

      const quality = Number(fix.quality_id || 0);
      const fixColor = quality === 4 ? 'green' : quality === 5 ? 'amber' : quality === 2 ? 'cyan' : quality === 1 ? 'blue' : 'red';
      set('fixValue', fix.quality || 'No Fix'); paint('fixValue', fixColor);
      set('fixDetail', fix.timestamp || 'Pas encore de position valide');

      const validPosition = Number.isFinite(Number(fix.latitude)) && Number.isFinite(Number(fix.longitude)) && fix.latitude !== null && fix.longitude !== null;
      if (validPosition) {
        const lat = Number(fix.latitude), lon = Number(fix.longitude);
        set('coordinates', `${lat.toFixed(7)}, ${lon.toFixed(7)}`);
        set('positionDetail', `Altitude ${number(fix.altitude_m, 2, ' m')} · Vitesse ${number(fix.speed_kmh, 1, ' km/h')}`);
        $('mapLink').href = `https://www.google.com/maps?q=${encodeURIComponent(lat + ',' + lon)}`;
        $('mapLink').hidden = false;
      } else {
        set('coordinates', 'Position indisponible'); set('positionDetail', 'En attente d’un fix GNSS'); $('mapLink').hidden = true;
      }

      set('diskFree', formatBytes(session.disk_free_bytes));
      set('satellites', String(fix.satellites ?? 0));
      set('hdop', number(fix.hdop, 1));
      set('fixCount', String(session.position_fixes ?? 0));
      set('rtcmBytes', formatBytes(ntrip.bytes_received));

      const files = Object.values(session.files || {}).filter(Boolean);
      const list = $('fileList'); list.replaceChildren();
      if (!files.length) {
        const li = document.createElement('li'); const name = document.createElement('span'); name.textContent = 'Aucun fichier'; li.append(name); list.append(li);
      } else {
        files.forEach(file => {
          const li = document.createElement('li'); const name = document.createElement('span'); const size = document.createElement('span');
          name.textContent = file.name || 'fichier'; size.textContent = formatBytes(file.size_bytes); li.append(name, size); list.append(li);
        });
      }
      const age = data.dashboard?.age_seconds;
      set('lastUpdate', age == null ? 'aucune donnée' : `mise à jour il y a ${Number(age).toFixed(1)} s`);
    }

    async function refresh() {
      try {
        const response = await fetch('/api/status', {cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        render(await response.json());
      } catch (error) {
        $('liveDot').className = 'dot error'; set('liveText', 'Hors ligne');
        set('headline', 'Tableau de bord inaccessible'); set('message', error.message);
      }
    }
    refresh(); setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


def load_status(status_file: Path) -> dict:
    """Load recorder state and annotate its freshness."""
    try:
        with open(status_file, "r", encoding="utf-8") as status_handle:
            data = json.load(status_handle)
    except FileNotFoundError:
        data = {
            "state": "waiting",
            "message": "En attente du démarrage de l'enregistreur",
            "gnss": {"connected": False},
            "ntrip": {"enabled": False, "connected": False},
            "fix": {"quality_id": 0, "quality": "No Fix"},
            "session": {},
        }
    except (OSError, ValueError) as err:
        data = {
            "state": "error",
            "message": f"État illisible : {err}",
            "gnss": {"connected": False},
            "ntrip": {"enabled": False, "connected": False},
            "fix": {"quality_id": 0, "quality": "No Fix"},
            "session": {},
        }

    age = None
    updated_at = data.get("updated_at")
    if updated_at:
        try:
            update_time = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            age = max(0.0, (now - update_time).total_seconds())
        except (TypeError, ValueError):
            age = None

    data["dashboard"] = {
        "fresh": age is not None and age <= STALE_AFTER_SECONDS,
        "age_seconds": age,
        "stale_after_seconds": STALE_AFTER_SECONDS,
    }
    return data


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the dashboard and its read-only JSON endpoint."""

    status_file = DEFAULT_STATUS_FILE

    def _send(self, content: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            payload = json.dumps(load_status(self.status_file), ensure_ascii=False).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8")
        elif path == "/healthz":
            self._send(b'{"ok":true}\n', "application/json; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(b"", "image/x-icon", status=204)
        else:
            self._send(b"Not found\n", "text/plain; charset=utf-8", status=404)

    def log_message(self, message_format, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), message_format % args))


def parse_args():
    parser = argparse.ArgumentParser(description="Mobile Web dashboard for the UM980 rover")
    parser.add_argument("--host", default="0.0.0.0", help="Listening address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Listening port (default: 8080)")
    parser.add_argument(
        "--status-file",
        default=str(DEFAULT_STATUS_FILE),
        help=f"Recorder JSON status file (default: {DEFAULT_STATUS_FILE})",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    DashboardHandler.status_file = Path(args.status_file)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Rover dashboard listening on http://{args.host}:{server.server_port}")
    print(f"Reading status from {DashboardHandler.status_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
