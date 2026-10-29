// filename: app/static/js/map.js
// Sets up the Leaflet map with two drawing modes:
//   "roof"        - the single rooftop boundary polygon (usable estimate input)
//   "obstruction" - one or more non-usable patches inside it (water tanks,
//                   staircase rooms, AC units, chimneys, existing coverings)
// Usable area = roof polygon area - sum(obstruction polygon areas).

/** Lightweight store for map drawing state (replaces scattered globals). */
const SignixMapStore = {
  map: null,
  roofLayerGroup: null,
  obstructionLayerGroup: null,
  roofDrawer: null,
  obstructionDrawer: null,
  currentMode: 'roof', // 'roof' | 'obstruction'
  roofPolygonLatLngs: null,
  obstructionPolygons: [], // array of {id, coords}
  currentCenter: { lat: 28.6139, lng: 77.2090 }, // default: New Delhi
  locationMarker: null, // pin for searched / Maps-link location
  recenterTimer: null,
  resizeObserver: null,
};

function initMap() {
  const t = window.SignixI18n.t;
  // Leaflet.Draw reads its locale when each drawing controller is created.
  L.drawLocal.draw.toolbar.actions = { title: t('Cancel drawing'), text: t('Cancel') };
  L.drawLocal.draw.toolbar.finish = { title: t('Finish drawing'), text: t('Finish') };
  L.drawLocal.draw.toolbar.undo = { title: t('Delete last point'), text: t('Delete last point') };
  L.drawLocal.draw.handlers.polygon.tooltip = {
    start: t('Click to start drawing the roof boundary.'),
    cont: t('Click to continue drawing the boundary.'),
    end: t('Click the first point to finish the boundary.'),
  };
  L.drawLocal.draw.handlers.polyline.error = t('Boundary edges must not cross.');
  // maxZoom 21 for Google satellite (often has detail past Esri's limit in
  // Indian cities). Esri is capped at 19 so it never requests the grey
  // "Map data not yet available" placeholder tiles.
  SignixMapStore.map = L.map('map', { maxZoom: 21, minZoom: 3, zoomControl: false }).setView(
    [SignixMapStore.currentCenter.lat, SignixMapStore.currentCenter.lng],
    18
  );
  L.control.zoom({ zoomInTitle: t('Zoom in'), zoomOutTitle: t('Zoom out') }).addTo(SignixMapStore.map);

  const streetLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 21,
    maxNativeZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  });

  // Google satellite — typically sharper rooftop detail in dense Indian urban
  // areas than Esri at zoom 19–21. No API key required for basic tile access
  // (prototype / hackathon use). lyrs=s = pure satellite, lyrs=y = hybrid.
  const googleSat = L.tileLayer(
    'https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
    {
      subdomains: ['0', '1', '2', '3'],
      maxZoom: 21,
      maxNativeZoom: 21,
      attribution: '&copy; Google'
    }
  );
  const googleHybrid = L.tileLayer(
    'https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
    {
      subdomains: ['0', '1', '2', '3'],
      maxZoom: 21,
      maxNativeZoom: 21,
      attribution: '&copy; Google'
    }
  );

  // Esri fallback — stop at zoom 19 so blank "Map data not yet available"
  // tiles are never requested when the user zooms further.
  const esriImagery = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {
      maxZoom: 19,
      maxNativeZoom: 19,
      attribution: 'Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics'
    }
  );
  const esriLabels = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    {
      maxZoom: 19,
      maxNativeZoom: 19,
      opacity: 0.9,
      attribution: 'Labels &copy; Esri'
    }
  );
  const esriSatellite = L.layerGroup([esriImagery, esriLabels]);

  // Default: Google satellite for rooftop tracing in India
  googleSat.addTo(SignixMapStore.map);

  L.control.layers(
    {
      [t('Satellite (Google)')]: googleSat,
      [t('Hybrid (Google)')]: googleHybrid,
      [t('Satellite (Esri)')]: esriSatellite,
      [t('Street Map')]: streetLayer
    },
    {},
    { position: 'topright', collapsed: false }
  ).addTo(SignixMapStore.map);

  SignixMapStore.roofLayerGroup = new L.FeatureGroup();
  SignixMapStore.obstructionLayerGroup = new L.FeatureGroup();
  SignixMapStore.map.addLayer(SignixMapStore.roofLayerGroup);
  SignixMapStore.map.addLayer(SignixMapStore.obstructionLayerGroup);

  // Two separate Draw controllers (not added as toolbars) - triggered
  // programmatically depending on currentMode, so the person always draws
  // with one clearly-labeled button rather than picking a tool from a
  // generic toolbar.
  // Outline-first styles so satellite imagery stays visible under the shapes.
  // Roof: thin indigo edge, almost no fill. Obstruction: dashed red edge only.
  SignixMapStore.roofDrawer = new L.Draw.Polygon(SignixMapStore.map, {
    allowIntersection: false,
    showArea: true,
    shapeOptions: {
      color: '#3d2570',
      fillColor: '#3d2570',
      fillOpacity: 0.06,
      weight: 2.5,
      opacity: 0.95
    }
  });
  SignixMapStore.obstructionDrawer = new L.Draw.Polygon(SignixMapStore.map, {
    allowIntersection: false,
    showArea: true,
    shapeOptions: {
      color: '#e0384c',
      fillColor: '#e0384c',
      fillOpacity: 0.04,   // edge-focused — barely any fill
      weight: 2.5,
      opacity: 1,
      dashArray: '8,5'
    }
  });

  SignixMapStore.map.on(L.Draw.Event.CREATED, function (e) {
    const layer = e.layer;
    const latlngs = layer.getLatLngs()[0].map(p => [p.lat, p.lng]);

    if (SignixMapStore.currentMode === 'roof') {
      SignixMapStore.roofLayerGroup.clearLayers(); // one rooftop boundary at a time
      // Outline-focused: keep satellite visible under the boundary
      layer.setStyle({
        color: '#3d2570',
        fillColor: '#3d2570',
        fillOpacity: 0.06,
        weight: 2.5,
        opacity: 0.95
      });
      SignixMapStore.roofLayerGroup.addLayer(layer);
      SignixMapStore.roofPolygonLatLngs = latlngs;
    } else {
      layer.obstructionId = Date.now();
      // Edge-only look: dashed red stroke, almost transparent fill
      layer.setStyle({
        color: '#e0384c',
        fillColor: '#e0384c',
        fillOpacity: 0.04,
        weight: 2.5,
        opacity: 1,
        dashArray: '8,5'
      });
      SignixMapStore.obstructionLayerGroup.addLayer(layer);
      SignixMapStore.obstructionPolygons.push({ id: layer.obstructionId, coords: latlngs });
    }
    recalculateAreas();
    setMapFeedback(
      SignixMapStore.currentMode === 'roof'
        ? t('Roof boundary saved for this estimate. You can now mark obstructions.')
        : window.SignixI18n.t('Obstruction added and excluded from the usable-area readout.'),
      'success'
    );
  });

  SignixMapStore.map.on(L.Draw.Event.DRAWSTART, function () {
    document.getElementById('cancel-draw-btn')?.classList.remove('hidden');
    setMapFeedback(window.SignixI18n.t('Click each roof corner, then click the first point to finish.'), 'neutral');
  });
  SignixMapStore.map.on(L.Draw.Event.DRAWSTOP, function () {
    document.getElementById('cancel-draw-btn')?.classList.add('hidden');
  });

  if ('ResizeObserver' in window) {
    SignixMapStore.resizeObserver = new ResizeObserver(() => {
      try { SignixMapStore.map.invalidateSize(false); } catch (e) { /* map may be closing */ }
    });
    SignixMapStore.resizeObserver.observe(document.getElementById('map'));
  }

  setMode('roof');
}

// Precise area (matches backend solar_logic.calculate_polygon_area_sqm):
// 1) clean ring  2) local ENU tangent-plane shoelace  3) spherical excess check
const EARTH_RADIUS_M = 6371008.8; // WGS84 mean / authalic radius

function normalizeRing(coords) {
  if (!coords || !coords.length) return [];
  const pts = [];
  for (const c of coords) {
    if (!c || c.length < 2) continue;
    const lat = Number(c[0]), lng = Number(c[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) continue;
    if (pts.length && Math.abs(pts[pts.length - 1][0] - lat) < 1e-12 &&
        Math.abs(pts[pts.length - 1][1] - lng) < 1e-12) continue;
    pts.push([lat, lng]);
  }
  if (pts.length >= 2 &&
      Math.abs(pts[0][0] - pts[pts.length - 1][0]) < 1e-12 &&
      Math.abs(pts[0][1] - pts[pts.length - 1][1]) < 1e-12) {
    pts.pop();
  }
  return pts;
}

function areaLocalTangentSqm(pts) {
  const n = pts.length;
  if (n < 3) return 0;
  let lat0 = 0, lon0 = 0;
  for (const [lat, lng] of pts) { lat0 += lat; lon0 += lng; }
  lat0 = lat0 / n * Math.PI / 180;
  lon0 = lon0 / n * Math.PI / 180;
  const cosLat0 = Math.cos(lat0);
  const projected = pts.map(([lat, lng]) => {
    const dlat = lat * Math.PI / 180 - lat0;
    const dlon = lng * Math.PI / 180 - lon0;
    return [dlon * EARTH_RADIUS_M * cosLat0, dlat * EARTH_RADIUS_M];
  });
  let area = 0;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = projected[i];
    const [x2, y2] = projected[(i + 1) % n];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}

function areaSphericalExcessSqm(pts) {
  const n = pts.length;
  if (n < 3) return 0;
  const toUnit = (lat, lng) => {
    const phi = lat * Math.PI / 180;
    const lam = lng * Math.PI / 180;
    const cp = Math.cos(phi);
    return [cp * Math.cos(lam), cp * Math.sin(lam), Math.sin(phi)];
  };
  const cross = (u, v) => [
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0]
  ];
  const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  const vecs = pts.map(([lat, lng]) => toUnit(lat, lng));
  let total = 0;
  for (let i = 0; i < n; i++) {
    const a = vecs[(i - 1 + n) % n];
    const b = vecs[i];
    const c = vecs[(i + 1) % n];
    let n1 = cross(a, b);
    let n2 = cross(b, c);
    const nn1 = Math.hypot(n1[0], n1[1], n1[2]);
    const nn2 = Math.hypot(n2[0], n2[1], n2[2]);
    if (nn1 < 1e-15 || nn2 < 1e-15) continue;
    n1 = [n1[0] / nn1, n1[1] / nn1, n1[2] / nn1];
    n2 = [n2[0] / nn2, n2[1] / nn2, n2[2] / nn2];
    const sinAng = dot(b, cross(n1, n2));
    const cosAng = -dot(n1, n2);
    total += Math.atan2(sinAng, cosAng);
  }
  let excess = total - (n - 2) * Math.PI;
  while (excess > Math.PI) excess -= 2 * Math.PI;
  while (excess < -Math.PI) excess += 2 * Math.PI;
  return Math.abs(excess) * EARTH_RADIUS_M * EARTH_RADIUS_M;
}

function polygonAreaSqm(latlngArray) {
  const pts = normalizeRing(latlngArray);
  if (pts.length < 3) return 0;
  const local = areaLocalTangentSqm(pts);
  let spherical = local;
  try { spherical = areaSphericalExcessSqm(pts); } catch (e) { /* keep local */ }
  if (local <= 0) return Math.max(spherical, 0);
  if (spherical <= 0) return local;
  const rel = Math.abs(local - spherical) / Math.max(local, spherical);
  // Prefer spherical if methods diverge >2% (odd geometries)
  const area = (rel > 0.02 && spherical > 0) ? spherical : local;
  return Math.round(area * 100) / 100;
}

function recalculateAreas() {
  const roofArea = SignixMapStore.roofPolygonLatLngs ? polygonAreaSqm(SignixMapStore.roofPolygonLatLngs) : 0;
  const obstructedArea = SignixMapStore.obstructionPolygons.reduce((sum, o) => sum + polygonAreaSqm(o.coords), 0);
  const usableArea = Math.max(roofArea - obstructedArea, 0);
  const systemSizeKw = usableArea / 10.0; // matches backend SQM_PER_KWP

  updateAreaReadout(roofArea, obstructedArea, usableArea, systemSizeKw);
  const btn = document.getElementById('start-estimation-btn');
  if (btn) btn.disabled = !SignixMapStore.roofPolygonLatLngs || (window.SignixAssessmentReady && !window.SignixAssessmentReady());
  const status = document.getElementById('estimate-action-status');
  if (status) {
    status.textContent = SignixMapStore.roofPolygonLatLngs
      ? window.SignixI18n.t('Roof boundary ready. Review the assumptions, then start the estimate.')
      : window.SignixI18n.t('Draw a roof boundary to enable the estimate.');
    status.className = `estimate-action-status is-${SignixMapStore.roofPolygonLatLngs ? 'ready' : 'neutral'}`;
  }
}

function updateAreaReadout(roofArea, obstructedArea, usableArea, systemSizeKw) {
  const el = document.getElementById('area-readout');
  if (!el) return;
  el.innerHTML =
    `${window.SignixI18n.t('Total roof')}: <strong>${roofArea.toFixed(2)} m²</strong> &nbsp;-&nbsp; ` +
    `${window.SignixI18n.t('Obstructed')}: <strong>${obstructedArea.toFixed(2)} m²</strong> &nbsp;=&nbsp; ` +
    `${window.SignixI18n.t('Usable')}: <strong>${usableArea.toFixed(2)} m²</strong> &nbsp;|&nbsp; ` +
    `${window.SignixI18n.t('Est. system size')}: <strong>${systemSizeKw.toFixed(2)} kW</strong>`;
}

function setMode(mode) {
  SignixMapStore.currentMode = mode;
  const roofBtn = document.getElementById('mode-roof-btn');
  const obsBtn = document.getElementById('mode-obstruction-btn');
  if (roofBtn && obsBtn) {
    roofBtn.classList.toggle('active', mode === 'roof');
    obsBtn.classList.toggle('active', mode === 'obstruction');
    roofBtn.setAttribute('aria-pressed', String(mode === 'roof'));
    obsBtn.setAttribute('aria-pressed', String(mode === 'obstruction'));
  }
}

function setMapFeedback(message, tone = 'neutral') {
  const feedback = document.getElementById('map-feedback');
  if (!feedback) return;
  feedback.textContent = message;
  feedback.className = `map-feedback is-${tone}`;
}

function startDrawing() {
  if (SignixMapStore.currentMode === 'roof') {
    SignixMapStore.roofDrawer.enable();
  } else {
    if (!SignixMapStore.roofPolygonLatLngs) {
      setMapFeedback(window.SignixI18n.t('Draw the rooftop boundary first, then mark obstructions inside it.'), 'error');
      setMode('roof');
      return;
    }
    SignixMapStore.obstructionDrawer.enable();
  }
}

function cancelDrawing() {
  SignixMapStore.roofDrawer?.disable();
  SignixMapStore.obstructionDrawer?.disable();
  document.getElementById('cancel-draw-btn')?.classList.add('hidden');
  setMapFeedback(window.SignixI18n.t('Drawing cancelled. Existing roof and obstruction markings were kept.'), 'neutral');
}

function clearObstructions() {
  SignixMapStore.obstructionLayerGroup.clearLayers();
  SignixMapStore.obstructionPolygons = [];
  recalculateAreas();
  setMapFeedback(window.SignixI18n.t('All obstruction markings were cleared.'), 'neutral');
}

function clearAll() {
  SignixMapStore.roofLayerGroup.clearLayers();
  SignixMapStore.obstructionLayerGroup.clearLayers();
  SignixMapStore.roofPolygonLatLngs = null;
  SignixMapStore.obstructionPolygons = [];
  recalculateAreas();
  setMapFeedback(window.SignixI18n.t('Roof boundary and obstruction markings were cleared.'), 'neutral');
}

// NASA GIBS imagery typically lags ~1-2 days behind real time; step back a
// few days to reliably hit an already-published date rather than 404s.
function getRecentGibsDate() {
  const d = new Date();
  d.setDate(d.getDate() - 3);
  return d.toISOString().split('T')[0];
}

function setMapCenter(lat, lng, zoom, address) {
  // Prefer native tile zoom (≤19) so imagery stays sharp for rooftop tracing.
  const z = Math.min(zoom || 19, 21);
  lat = Number(lat);
  lng = Number(lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    console.warn('setMapCenter: invalid coordinates', lat, lng);
    return;
  }
  SignixMapStore.currentCenter = { lat, lng };
  if (!SignixMapStore.map) return;
  if (SignixMapStore.recenterTimer) clearTimeout(SignixMapStore.recenterTimer);

  // Ensure container has size (helps when map was laid out early)
  try { SignixMapStore.map.invalidateSize(true); } catch (e) { /* ignore */ }

  SignixMapStore.map.flyTo([lat, lng], z, { animate: true, duration: 0.8 });

  // Drop / move a clear pin so the user sees the exact point
  if (SignixMapStore.locationMarker) {
    SignixMapStore.locationMarker.setLatLng([lat, lng]);
  } else {
    SignixMapStore.locationMarker = L.marker([lat, lng], {
      title: window.SignixI18n.t('Selected location'),
      riseOnHover: true,
    }).addTo(SignixMapStore.map);
  }
  const popup = document.createElement('div');
  const popupTitle = document.createElement('strong');
  popupTitle.textContent = address || window.SignixI18n.t('Selected location');
  const popupCoords = document.createElement('span');
  popupCoords.className = 'map-popup-coordinates';
  popupCoords.textContent = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
  popup.append(popupTitle, document.createElement('br'), popupCoords);
  SignixMapStore.locationMarker.bindPopup(popup).openPopup();

  // Second recenter after tiles settle (fixes occasional wrong viewport)
  SignixMapStore.recenterTimer = setTimeout(() => {
    try {
      SignixMapStore.map.invalidateSize(true);
      SignixMapStore.map.setView([lat, lng], z);
    } catch (e) { /* ignore */ }
  }, 300);
}

function getCurrentPolygon() {
  return SignixMapStore.roofPolygonLatLngs;
}

function getCurrentObstructions() {
  return SignixMapStore.obstructionPolygons.map(o => o.coords);
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('map')) {
    initMap();

    const roofBtn = document.getElementById('mode-roof-btn');
    const obsBtn = document.getElementById('mode-obstruction-btn');
    const drawBtn = document.getElementById('draw-btn');
    const cancelDrawBtn = document.getElementById('cancel-draw-btn');
    const clearObsBtn = document.getElementById('clear-obstructions-btn');
    const clearAllBtn = document.getElementById('clear-all-btn');

    if (roofBtn) roofBtn.addEventListener('click', () => setMode('roof'));
    if (obsBtn) obsBtn.addEventListener('click', () => setMode('obstruction'));
    if (drawBtn) drawBtn.addEventListener('click', startDrawing);
    if (cancelDrawBtn) cancelDrawBtn.addEventListener('click', cancelDrawing);
    if (clearObsBtn) clearObsBtn.addEventListener('click', clearObstructions);
    if (clearAllBtn) clearAllBtn.addEventListener('click', clearAll);
  }
});
