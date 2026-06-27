"""Leaflet document generation for the optional desktop map."""

from __future__ import annotations

import json

from flat_searcher.mapping import MapMarker


def build_leaflet_html(markers: tuple[MapMarker, ...]) -> str:
    marker_json = json.dumps(
        [marker.to_dict() for marker in markers],
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{
      width: 100%;
      height: 100%;
      margin: 0;
      background: #f4f5f7;
    }}
    .leaflet-container {{
      font-family: "Segoe UI", Arial, sans-serif;
    }}
    .marker-popup {{
      min-width: 130px;
      line-height: 1.45;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const markerData = {marker_json};
    const scoreColors = {{
      high: "#18864b",
      medium: "#c58a00",
      low: "#d55b2d",
      very_low: "#b42318",
      unknown: "#68717d"
    }};
    const stateColors = {{
      normal: "#24303d",
      approximate: "#6d5bd0",
      district: "#8b5e34",
      favorite: "#c89200",
      rejected: "#8e98a5",
      inactive: "#a9afb8"
    }};
    const map = L.map("map", {{ zoomControl: true }}).setView([56.9496, 24.1052], 11);
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }}).addTo(map);

    let bridge = null;
    if (window.qt && window.QWebChannel) {{
      new QWebChannel(qt.webChannelTransport, channel => {{
        bridge = channel.objects.mapBridge;
      }});
    }}

    const markerByListing = new Map();
    const bounds = [];
    for (const item of markerData) {{
      const marker = L.circleMarker([item.latitude, item.longitude], {{
        radius: item.visual_state === "favorite" ? 10 : 8,
        color: stateColors[item.visual_state] || stateColors.normal,
        weight: item.visual_state === "favorite" ? 4 : 2,
        dashArray: ["approximate", "district"].includes(item.visual_state)
          ? "4 3"
          : null,
        fillColor: scoreColors[item.score_bucket] || scoreColors.unknown,
        fillOpacity: item.visual_state === "inactive" ? 0.35 : 0.9
      }}).addTo(map);
      marker.bindPopup(
        `<div class="marker-popup"><strong>Listing #${{item.listing_id}}</strong><br>` +
        `Score: ${{item.score_bucket.replace("_", " ")}}<br>` +
        `State: ${{item.visual_state.replace("_", " ")}}</div>`
      );
      marker.on("click", () => {{
        if (bridge) {{
          bridge.markerSelected(item.listing_id);
        }}
      }});
      markerByListing.set(item.listing_id, marker);
      bounds.push([item.latitude, item.longitude]);
    }}
    if (bounds.length === 1) {{
      map.setView(bounds[0], 15);
    }} else if (bounds.length > 1) {{
      map.fitBounds(bounds, {{ padding: [30, 30] }});
    }}

    function focusMarker(listingId) {{
      const marker = markerByListing.get(listingId);
      if (!marker) return;
      map.panTo(marker.getLatLng());
      marker.openPopup();
    }}
    window.focusMarker = focusMarker;
  </script>
</body>
</html>
"""
