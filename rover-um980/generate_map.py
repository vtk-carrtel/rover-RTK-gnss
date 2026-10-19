#!/usr/bin/env python3
"""Generate an interactive OpenStreetMap HTML map from Rover UM980 CSV recordings."""

import argparse
import csv
import json
import math
import sys
from pathlib import Path


def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two GPS points in meters."""
    R = 6371000  # meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_csv(csv_path: Path):
    points = []
    with open(csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if not row.get("latitude") or not row.get("longitude"):
                continue
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (ValueError, TypeError):
                continue

            alt = None
            if row.get("altitude_m"):
                try:
                    alt = float(row["altitude_m"])
                except ValueError:
                    pass

            speed = None
            if row.get("speed_kmh"):
                try:
                    speed = float(row["speed_kmh"])
                except ValueError:
                    pass

            hdop = None
            if row.get("hdop"):
                try:
                    hdop = float(row["hdop"])
                except ValueError:
                    pass

            sats = None
            if row.get("num_satellites"):
                try:
                    sats = int(row["num_satellites"])
                except ValueError:
                    pass

            fix_id = row.get("fix_quality_id", "").strip()
            fix_name = row.get("fix_quality", "").strip() or "Inconnu"

            points.append({
                "index": idx + 1,
                "time": row.get("utc_timestamp", "").strip(),
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "geoid_sep": row.get("geoid_sep_m", "").strip(),
                "fix_id": fix_id,
                "fix": fix_name,
                "sats": sats,
                "hdop": hdop,
                "speed": speed,
                "course": row.get("course_deg", "").strip(),
                "pos_mode": row.get("pos_mode", "").strip(),
                "diff_age": row.get("diff_age_s", "").strip(),
            })
    return points


def compute_stats(points):
    if not points:
        return {}

    total_dist = 0.0
    for i in range(len(points) - 1):
        total_dist += haversine(
            points[i]["lat"], points[i]["lon"],
            points[i + 1]["lat"], points[i + 1]["lon"]
        )

    speeds = [p["speed"] for p in points if p["speed"] is not None]
    max_speed = max(speeds) if speeds else 0.0
    avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

    hdops = [p["hdop"] for p in points if p["hdop"] is not None]
    min_hdop = min(hdops) if hdops else 0.0
    avg_hdop = sum(hdops) / len(hdops) if hdops else 0.0

    sats_list = [p["sats"] for p in points if p["sats"] is not None]
    min_sats = min(sats_list) if sats_list else 0
    max_sats = max(sats_list) if sats_list else 0
    avg_sats = sum(sats_list) / len(sats_list) if sats_list else 0.0

    alts = [p["alt"] for p in points if p["alt"] is not None]
    min_alt = min(alts) if alts else 0.0
    max_alt = max(alts) if alts else 0.0

    fix_counts = {}
    for p in points:
        name = p["fix"]
        fix_counts[name] = fix_counts.get(name, 0) + 1

    return {
        "count": len(points),
        "total_dist_m": total_dist,
        "max_speed_kmh": max_speed,
        "avg_speed_kmh": avg_speed,
        "min_hdop": min_hdop,
        "avg_hdop": avg_hdop,
        "min_sats": min_sats,
        "max_sats": max_sats,
        "avg_sats": avg_sats,
        "min_alt": min_alt,
        "max_alt": max_alt,
        "start_time": points[0]["time"],
        "end_time": points[-1]["time"],
        "fix_counts": fix_counts,
    }


def generate_html(points, stats, filename):
    points_json = json.dumps(points)
    stats_json = json.dumps(stats)

    count = stats.get("count", 0)
    fix_counts = stats.get("fix_counts", {})
    rtk_count = fix_counts.get("RTK Fixed", 0)
    rtk_pct = (rtk_count / count * 100) if count else 0
    dgps_count = fix_counts.get("Differential GNSS (DGPS)", 0)
    dgps_pct = (dgps_count / count * 100) if count else 0
    float_count = fix_counts.get("RTK Float", 0)
    sps_count = fix_counts.get("Autonomous GNSS (SPS)", 0)

    dist_m = f"{stats.get('total_dist_m', 0.0):.1f}"
    max_speed = f"{stats.get('max_speed_kmh', 0.0):.1f}"
    avg_alt = f"{((stats.get('min_alt', 0) + stats.get('max_alt', 0)) / 2.0):.1f}"
    min_hdop = f"{stats.get('min_hdop', 0.0):.2f}"
    avg_hdop = f"{stats.get('avg_hdop', 0.0):.2f}"
    max_sats = str(stats.get('max_sats', 0))
    start_time = stats.get('start_time', '')
    end_time = stats.get('end_time', '')
    min_alt = f"{stats.get('min_alt', 0.0):.1f}"
    max_alt = f"{stats.get('max_alt', 0.0):.1f}"

    html_template = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Carte GPS Rover UM980 &mdash; __FILENAME__</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <!-- Leaflet CSS -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>

  <style>
    :root {
      --bg-panel: rgba(15, 23, 42, 0.88);
      --bg-glass: rgba(30, 41, 59, 0.75);
      --border: rgba(255, 255, 255, 0.12);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --rtk-fixed: #22c55e;
      --rtk-float: #eab308;
      --dgps: #3b82f6;
      --sps: #f97316;
      --other: #a855f7;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #090d16;
      color: var(--text);
      overflow: hidden;
      height: 100vh;
      width: 100vw;
    }

    #map {
      height: 100vh;
      width: 100vw;
      z-index: 1;
    }

    /* Floating Header & Card */
    .dashboard-panel {
      position: absolute;
      top: 16px;
      left: 16px;
      z-index: 1000;
      width: 360px;
      max-width: calc(100vw - 32px);
      background: var(--bg-panel);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
      max-height: calc(100vh - 32px);
      overflow-y: auto;
      transition: transform 0.25s ease;
    }

    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }

    .panel-title {
      font-size: 1.1rem;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .file-tag {
      font-size: 0.75rem;
      background: rgba(56, 189, 248, 0.18);
      color: var(--accent);
      padding: 3px 8px;
      border-radius: 9999px;
      font-family: monospace;
      max-width: 180px;
      text-overflow: ellipsis;
      overflow: hidden;
      white-space: nowrap;
    }

    .stats-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      margin-bottom: 12px;
    }

    .stat-box {
      background: var(--bg-glass);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
    }

    .stat-label {
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 2px;
    }

    .stat-val {
      font-size: 1.05rem;
      font-weight: 700;
      color: #fff;
    }

    .stat-unit {
      font-size: 0.75rem;
      font-weight: 400;
      color: var(--text-muted);
      margin-left: 2px;
    }

    /* Fix Quality Section */
    .fix-section {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid var(--border);
    }

    .fix-title {
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 6px;
    }

    .fix-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 0.82rem;
      padding: 3px 0;
    }

    .fix-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }

    .btn-group {
      display: flex;
      gap: 8px;
      margin-top: 12px;
    }

    .btn {
      flex: 1;
      background: var(--bg-glass);
      border: 1px solid var(--border);
      color: var(--text);
      font-size: 0.8rem;
      font-weight: 600;
      padding: 8px;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: background 0.15s;
    }

    .btn:hover {
      background: rgba(255, 255, 255, 0.15);
    }

    /* Custom Leaflet Popups */
    .leaflet-popup-content-wrapper {
      background: #0f172a !important;
      color: #f8fafc !important;
      border: 1px solid var(--border) !important;
      border-radius: 8px !important;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5) !important;
    }
    .leaflet-popup-tip {
      background: #0f172a !important;
    }
    .popup-card h4 {
      font-size: 0.95rem;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .popup-row {
      display: flex;
      justify-content: space-between;
      font-size: 0.78rem;
      padding: 2px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.07);
    }
    .popup-row:last-child {
      border-bottom: none;
    }
    .popup-label {
      color: var(--text-muted);
    }
    .popup-value {
      font-weight: 600;
      color: #f1f5f9;
      font-family: monospace;
    }

    /* Altitude Profile Footer */
    .elevation-bar {
      position: absolute;
      bottom: 16px;
      left: 16px;
      right: 16px;
      z-index: 1000;
      background: var(--bg-panel);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 8px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      max-width: 650px;
      margin: 0 auto;
      font-size: 0.82rem;
    }

    @media (max-width: 640px) {
      .dashboard-panel {
        width: calc(100vw - 32px);
      }
      .elevation-bar {
        display: none;
      }
    }
  </style>
</head>
<body>

  <div id="map"></div>

  <!-- Dashboard Panel -->
  <div class="dashboard-panel" id="panel">
    <div class="panel-header">
      <div class="panel-title">
        <span>📍</span> Rover UM980
      </div>
      <div class="file-tag" title="__FILENAME__">__FILENAME__</div>
    </div>

    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-label">Points enregistrés</div>
        <div class="stat-val">__COUNT__</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Distance totale</div>
        <div class="stat-val">__DIST__<span class="stat-unit">m</span></div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Vitesse max</div>
        <div class="stat-val">__MAX_SPEED__<span class="stat-unit">km/h</span></div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Altitude moy.</div>
        <div class="stat-val">__AVG_ALT__<span class="stat-unit">m</span></div>
      </div>
      <div class="stat-box">
        <div class="stat-label">HDOP min / moy</div>
        <div class="stat-val">__MIN_HDOP__ / __AVG_HDOP__</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Satellites max</div>
        <div class="stat-val">__MAX_SATS__<span class="stat-unit">sats</span></div>
      </div>
    </div>

    <div class="fix-section">
      <div class="fix-title">Qualité du Fix GNSS</div>
      <div class="fix-item">
        <span class="fix-badge"><span class="dot" style="background: var(--rtk-fixed);"></span> RTK Fixed</span>
        <b>__RTK_COUNT__ (__RTK_PCT__%)</b>
      </div>
      <div class="fix-item">
        <span class="fix-badge"><span class="dot" style="background: var(--dgps);"></span> DGPS</span>
        <b>__DGPS_COUNT__ (__DGPS_PCT__%)</b>
      </div>
      <div class="fix-item">
        <span class="fix-badge"><span class="dot" style="background: var(--rtk-float);"></span> RTK Float</span>
        <b>__FLOAT_COUNT__</b>
      </div>
      <div class="fix-item">
        <span class="fix-badge"><span class="dot" style="background: var(--sps);"></span> SPS (Standard)</span>
        <b>__SPS_COUNT__</b>
      </div>
    </div>

    <div class="btn-group">
      <button class="btn" onclick="fitRoute()">🔍 Centrer</button>
      <button class="btn" id="btn-toggle-markers" onclick="toggleMarkers()">⚪ Masquer points</button>
    </div>
  </div>

  <div class="elevation-bar">
    <div>⏱️ Début: <b>__START_TIME__</b> &rarr; Fin: <b>__END_TIME__</b></div>
    <div>⛰️ Alt: <b>__MIN_ALT__m &ndash; __MAX_ALT__m</b></div>
  </div>

  <!-- Leaflet JS -->
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>

  <script>
    const pointsData = __POINTS_JSON__;
    const statsData = __STATS_JSON__;

    function getColor(fix) {
      if (!fix) return '#a855f7';
      if (fix.includes('RTK Fixed')) return '#22c55e';
      if (fix.includes('RTK Float')) return '#eab308';
      if (fix.includes('DGPS') || fix.includes('Differential')) return '#3b82f6';
      if (fix.includes('Autonomous') || fix.includes('SPS')) return '#f97316';
      return '#a855f7';
    }

    // Base layers
    // 1. OpenStreetMap (CartoDB Voyager - OSM data, fully accessible without Referer / file://)
    const osmCarto = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
      maxZoom: 22,
      maxNativeZoom: 19,
      subdomains: 'abcd',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributeurs &copy; <a href="https://carto.com/attributions" target="_blank">CARTO</a>'
    });

    // 2. Satellite Esri HD
    const esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 22,
      maxNativeZoom: 19,
      attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics'
    });

    // 3. OpenStreetMap France
    const osmFr = L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', {
      maxZoom: 22,
      maxNativeZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> France'
    });

    // 4. OpenStreetMap Standard (OSM Foundation - blocks file:// protocol requests)
    const osmOrg = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 22,
      maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> (bloqué sans Referer)'
    });

    // 5. OpenTopoMap
    const topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: 'Map data: &copy; OSM contributors, SRTM | Map style: &copy; OpenTopoMap'
    });

    const map = L.map('map', {
      center: pointsData.length ? [pointsData[0].lat, pointsData[0].lon] : [46.36, 6.44],
      zoom: 19,
      maxZoom: 22,
      layers: [osmCarto]
    });

    const baseMaps = {
      "Plan OpenStreetMap (Recommandé)": osmCarto,
      "Satellite Esri HD": esriSat,
      "OpenStreetMap France": osmFr,
      "OpenStreetMap Org (file:// bloqué)": osmOrg,
      "OpenTopoMap": topo
    };

    const markersLayer = L.layerGroup().addTo(map);
    const polylineLayer = L.layerGroup().addTo(map);

    const overlayMaps = {
      "Trajectoire": polylineLayer,
      "Points GNSS": markersLayer
    };

    L.control.layers(baseMaps, overlayMaps, { position: 'topright' }).addTo(map);

    // Draw trajectory segments
    const latLngs = pointsData.map(p => [p.lat, p.lon]);

    for (let i = 0; i < pointsData.length - 1; i++) {
      const p1 = pointsData[i];
      const p2 = pointsData[i + 1];
      const color = getColor(p2.fix);

      L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
        color: color,
        weight: 4.5,
        opacity: 0.9,
        lineCap: 'round',
        lineJoin: 'round'
      }).addTo(polylineLayer);
    }

    // Draw point markers
    pointsData.forEach((p, idx) => {
      const color = getColor(p.fix);
      const isStart = (idx === 0);
      const isEnd = (idx === pointsData.length - 1);

      let radius = 5;
      let weight = 1.5;

      if (isStart || isEnd) {
        radius = 8;
        weight = 3;
      }

      const marker = L.circleMarker([p.lat, p.lon], {
        radius: radius,
        fillColor: color,
        color: (isStart || isEnd) ? '#ffffff' : '#0f172a',
        weight: weight,
        opacity: 1,
        fillOpacity: 0.9
      });

      const popupContent = `
        <div class="popup-card">
          <h4>
            <span class="dot" style="background: ${color};"></span>
            Point #${p.index} ${isStart ? '(DÉPART)' : (isEnd ? '(FIN)' : '')}
          </h4>
          <div class="popup-row">
            <span class="popup-label">Qualité fix</span>
            <span class="popup-value" style="color: ${color};">${p.fix}</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Heure UTC</span>
            <span class="popup-value">${p.time}</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Latitude</span>
            <span class="popup-value">${p.lat.toFixed(7)}°</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Longitude</span>
            <span class="popup-value">${p.lon.toFixed(7)}°</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Altitude</span>
            <span class="popup-value">${p.alt !== null ? p.alt.toFixed(2) + ' m' : 'N/A'}</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Vitesse</span>
            <span class="popup-value">${p.speed !== null ? p.speed.toFixed(2) + ' km/h' : 'N/A'}</span>
          </div>
          <div class="popup-row">
            <span class="popup-label">Satellites / HDOP</span>
            <span class="popup-value">${p.sats || 'N/A'} / ${p.hdop !== null ? p.hdop.toFixed(2) : 'N/A'}</span>
          </div>
          ${p.diff_age ? `
          <div class="popup-row">
            <span class="popup-label">Âge correction</span>
            <span class="popup-value">${p.diff_age}s</span>
          </div>` : ''}
        </div>
      `;

      marker.bindPopup(popupContent);
      marker.addTo(markersLayer);

      if (isStart) {
        marker.bindTooltip("🟢 DÉPART", { permanent: false, direction: 'top' });
      } else if (isEnd) {
        marker.bindTooltip("🏁 FIN", { permanent: false, direction: 'top' });
      }
    });

    function fitRoute() {
      if (latLngs.length > 0) {
        map.fitBounds(L.latLngBounds(latLngs), { padding: [60, 60], maxZoom: 21 });
      }
    }

    let showMarkers = true;
    function toggleMarkers() {
      const btn = document.getElementById('btn-toggle-markers');
      if (showMarkers) {
        map.removeLayer(markersLayer);
        btn.innerText = '🟢 Afficher points';
      } else {
        map.addLayer(markersLayer);
        btn.innerText = '⚪ Masquer points';
      }
      showMarkers = !showMarkers;
    }

    fitRoute();
  </script>
</body>
</html>
"""

    replacements = {
        "__FILENAME__": filename,
        "__POINTS_JSON__": points_json,
        "__STATS_JSON__": stats_json,
        "__COUNT__": str(count),
        "__DIST__": dist_m,
        "__MAX_SPEED__": max_speed,
        "__AVG_ALT__": avg_alt,
        "__MIN_HDOP__": min_hdop,
        "__AVG_HDOP__": avg_hdop,
        "__MAX_SATS__": max_sats,
        "__RTK_COUNT__": str(rtk_count),
        "__RTK_PCT__": f"{rtk_pct:.1f}",
        "__DGPS_COUNT__": str(dgps_count),
        "__DGPS_PCT__": f"{dgps_pct:.1f}",
        "__FLOAT_COUNT__": str(float_count),
        "__SPS_COUNT__": str(sps_count),
        "__START_TIME__": start_time,
        "__END_TIME__": end_time,
        "__MIN_ALT__": min_alt,
        "__MAX_ALT__": max_alt,
    }

    html = html_template
    for k, v in replacements.items():
        html = html.replace(k, v)

    return html


def main():
    parser = argparse.ArgumentParser(description="Generate Leaflet OSM map from Rover UM980 CSV.")
    parser.add_argument("--input", "-i", type=str, help="Path to input CSV file. Defaults to latest file in enregistrements/.")
    parser.add_argument("--output", "-o", type=str, help="Path to output HTML file.")
    args = parser.parse_args()

    # Find input CSV
    if args.input:
        csv_file = Path(args.input)
    else:
        rec_dir = Path(__file__).parent / "enregistrements"
        csv_files = sorted(rec_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime)
        if not csv_files:
            print("Erreur: aucun fichier CSV trouvé dans", rec_dir, file=sys.stderr)
            sys.exit(1)
        csv_file = csv_files[-1]

    if not csv_file.exists():
        print(f"Erreur: fichier introuvable: {csv_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Chargement des données de : {csv_file}")
    points = parse_csv(csv_file)
    if not points:
        print("Erreur: aucun point valide trouvé.", file=sys.stderr)
        sys.exit(1)

    stats = compute_stats(points)
    print(f"Points valides: {len(points)}, Distance: {stats['total_dist_m']:.2f} m, RTK Fixed: {stats['fix_counts'].get('RTK Fixed', 0)}")

    html = generate_html(points, stats, csv_file.name)

    # Determine output path
    downloads_dir = Path.home() / "Downloads"
    if not downloads_dir.exists():
        downloads_dir.mkdir(parents=True, exist_ok=True)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = downloads_dir / f"carte_{csv_file.stem}.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"Carte enregistrée avec succès : {out_path}")

    # Also save a standard reference copy 'carte_gps_latest.html' in Downloads
    latest_path = downloads_dir / "carte_gps_latest.html"
    latest_path.write_text(html, encoding="utf-8")
    print(f"Copie 'carte_gps_latest.html' enregistrée : {latest_path}")


if __name__ == "__main__":
    main()
