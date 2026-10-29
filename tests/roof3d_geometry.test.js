const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'static', 'js', 'roof3d.js'),
  'utf8'
);
const context = { window: {}, console };
vm.createContext(context);
vm.runInContext(source, context);

const geometry = context.window.SIGNIX_ROOF3D_GEOMETRY;

const contractRing = [[1, 2], [3, 4], [5, 6]];
assert.deepEqual(JSON.parse(JSON.stringify(geometry.contractLocalRing(contractRing, null, null))), contractRing);
const projectedFallback = geometry.contractLocalRing(null, { coordinates: [[[77, 28], [77.0001, 28], [77, 28.0001]]] }, { lng: 77, lat: 28 });
assert.ok(projectedFallback[1][0] > projectedFallback[0][0]);
assert.ok(projectedFallback[2][1] < projectedFallback[0][1]);
const projectionOrigin = { lng: 77.0005, lat: 28.0005 };
const westAdvisory = geometry.normalizedImagePolygonToLocal([[100,400],[200,400],[200,500],[100,500]], [77,28,77.001,28.001], projectionOrigin);
const eastAdvisory = geometry.normalizedImagePolygonToLocal([[800,400],[900,400],[900,500],[800,500]], [77,28,77.001,28.001], projectionOrigin);
assert.ok(westAdvisory.every((point) => point[0] < 0), 'western image polygon should stay west of the shared roof origin');
assert.ok(eastAdvisory.every((point) => point[0] > 0), 'eastern image polygon should stay east of the shared roof origin');
assert.equal(geometry.normalizedImagePolygonToLocal([[0,0],[1,0],[0,1]], [77,28,77,28.001], projectionOrigin), null);

function planeHeightOffset(point, surface) {
  const pitch = Number(surface.pitch) * Math.PI / 180;
  const azimuth = Number(surface.azimuth) * Math.PI / 180;
  return -Math.tan(pitch) * (point[0] * Math.sin(azimuth) - point[1] * Math.cos(azimuth));
}
function distance3d(a, b, surface) {
  return Math.hypot(a[0] - b[0], planeHeightOffset(a, surface) - planeHeightOffset(b, surface), a[1] - b[1]);
}
const pitchedSurface = { pitch: 30, azimuth: 180 };
const physicalModule = geometry.surfacePanelRect(0, 0, 1.134, 1.762, 0, pitchedSurface);
assert.ok(Math.abs(distance3d(physicalModule[0], physicalModule[1], pitchedSurface) - 1.134) < 1e-9);
assert.ok(Math.abs(distance3d(physicalModule[1], physicalModule[2], pitchedSurface) - 1.762) < 1e-9);
assert.ok(geometry, 'roof geometry test API should be exposed');

const smallView = geometry.cameraFrameForBounds(
  { width: 4, depth: 3, height: 2 },
  16 / 9
);
const largeView = geometry.cameraFrameForBounds(
  { width: 40, depth: 28, height: 8 },
  16 / 9
);
assert.ok(smallView >= 5.5);
assert.ok(largeView > smallView * 3);
const framingFootprints = {
  compact: [[-2,-1.5],[2,-1.5],[2,1.5],[-2,1.5]],
  elongated: [[-20,-1.5],[20,-1.5],[20,1.5],[-20,1.5]],
  concave: [[0,0],[10,0],[10,8],[6,8],[6,3],[4,3],[4,8],[0,8]],
  irregular: [[-6,-2],[-1,-5],[5,-3],[7,2],[2,5],[-5,4]]
};
const framing = Object.fromEntries(Object.entries(framingFootprints).map(([name, ring]) => [name, geometry.cameraFrameForRing(ring, 16 / 9, .72, .55, 3)]));
assert.ok(Object.values(framing).every(Number.isFinite), 'all authoritative footprint shapes should produce deterministic finite framing');
assert.ok(framing.elongated > framing.compact * 2, 'elongated roofs must not be clipped by a compact-roof frame');
assert.ok(geometry.cameraFrameForRing(framingFootprints.elongated, 16 / 9, 0, 1.555, 3) > framing.elongated, 'top view should account for the elongated projected axis');

const roof = [[0, 0], [10, 0], [10, 8], [0, 8]];
const surface = [[0, 0], [5, 0], [5, 8], [0, 8]];
const validPanel = [[1, 1], [2, 1], [2, 2.5], [1, 2.5]];
const outsideSurface = [[4.5, 1], [5.5, 1], [5.5, 2.5], [4.5, 2.5]];
const obstruction = [[0.8, 0.8], [2.2, 0.8], [2.2, 2.7], [0.8, 2.7]];

assert.equal(geometry.polygonFits(validPanel, surface, []), true);
assert.equal(geometry.polygonFits(validPanel, roof, []), true);
assert.equal(geometry.polygonFits(outsideSurface, surface, []), false);
assert.equal(geometry.polygonFits(validPanel, surface, [obstruction]), false);
assert.equal(geometry.polygonFitReason(outsideSurface, surface, [], { surfaceCheck: true }), 'surface');
assert.equal(geometry.polygonFitReason(validPanel, surface, [obstruction]), 'obstruction');
const centerInsideEdgeOutside = [[8,1],[11,1],[11,3],[8,3]];
assert.equal(geometry.pointInRing(9.5,2,roof), true);
assert.equal(geometry.polygonFits(centerInsideEdgeOutside, roof, [], { grid: null, edgeSetback: 0, layoutMode: 'planning_preview' }), false);
const concaveRoof = [[0,0],[6,0],[6,6],[4,6],[4,2],[2,2],[2,6],[0,6]];
const crossesNotch = [[1.5,1],[4.5,1],[4.5,2.5],[1.5,2.5]];
assert.equal(geometry.pointInRing(3,1.75,concaveRoof), true);
assert.equal(geometry.polygonFits(crossesNotch, concaveRoof, [], { grid: null, edgeSetback: 0, layoutMode: 'planning_preview' }), false);
assert.equal(geometry.sumSurfacePanelCounts({ east: 7, west: 8 }), 15);
assert.equal(geometry.layoutCapacityKw(15, 545), 8.175);
assert.equal(geometry.layoutDiffersFromEstimate(8.175, 8.0), true);
assert.equal(geometry.layoutDiffersFromEstimate(8.1, 8.0), false);

const classificationGrid = {
  rows: 2, cols: 2, origin_x: 0, origin_z: 0, resolution_meters: 2,
  classifications: ['usable', 'shaded', 'obstruction', 'unknown'],
  placement_policy: { unknown_cells: 'exclude' }
};
assert.equal(geometry.panelTouchesExcludedGrid([[.2,.2],[1.6,.2],[1.6,1.6],[.2,1.6]], classificationGrid, 'planning_preview'), false);
assert.equal(geometry.panelTouchesExcludedGrid([[2.1,.2],[3.7,.2],[3.7,1.6],[2.1,1.6]], classificationGrid, 'planning_preview'), true);
assert.equal(geometry.panelTouchesExcludedGrid([[2.1,2.1],[3.7,2.1],[3.7,3.7],[2.1,3.7]], classificationGrid, 'planning_preview'), false);
assert.equal(geometry.panelTouchesExcludedGrid([[2.1,2.1],[3.7,2.1],[3.7,3.7],[2.1,3.7]], classificationGrid, 'conservative'), true);
assert.equal(geometry.panelTouchesExcludedGrid([[.2,.2],[1.6,.2],[1.6,1.6],[.2,1.6]], { ...classificationGrid, classifications: ['outside_roof', 'usable', 'usable', 'usable'] }, 'planning_preview'), true);
assert.equal(geometry.panelTouchesExcludedGrid([[.2,.2],[1.6,.2],[1.6,1.6],[.2,1.6]], { ...classificationGrid, classifications: ['setback', 'usable', 'usable', 'usable'] }, 'planning_preview'), true);
assert.equal(geometry.polygonFits([[2.1,2.1],[3.7,2.1],[3.7,3.7],[2.1,3.7]], [[0,0],[4,0],[4,4],[0,4]], [], { grid: classificationGrid, edgeSetback: 0, layoutMode: 'conservative' }), false);
assert.equal(geometry.polygonFits([[2.1,2.1],[3.7,2.1],[3.7,3.7],[2.1,3.7]], [[0,0],[4,0],[4,4],[0,4]], [], { grid: classificationGrid, edgeSetback: 0, layoutMode: 'planning_preview' }), true);
assert.equal(geometry.panelShadeVerified([[.2,.2],[1.6,.2],[1.6,1.6],[.2,1.6]], classificationGrid), true);
assert.equal(geometry.panelShadeVerified([[2.1,2.1],[3.7,2.1],[3.7,3.7],[2.1,3.7]], classificationGrid), false);
assert.equal(geometry.polygonFits([[.2,.2],[1.6,.2],[1.6,1.6],[.2,1.6]], [[0,0],[4,0],[4,4],[0,4]], [], { grid: classificationGrid, edgeSetback: 0 }), true);
assert.equal(geometry.polygonFits([[-.1,.5],[1,.5],[1,1.5],[-.1,1.5]], [[0,0],[4,0],[4,4],[0,4]], [], { grid: classificationGrid, edgeSetback: 0 }), false);
const nearRoofEdge = [[.1,.5],[1.2,.5],[1.2,1.5],[.1,1.5]];
assert.equal(geometry.polygonFits(nearRoofEdge, [[0,0],[4,0],[4,4],[0,4]], [], { grid: null, layoutMode: 'planning_preview' }), false);
assert.equal(geometry.polygonFits(nearRoofEdge, [[0,0],[4,0],[4,4],[0,4]], [], { grid: null, layoutMode: 'conservative' }), false);
const nearbyObstruction = [[1.8,.5],[2.4,.5],[2.4,1.5],[1.8,1.5]];
assert.equal(geometry.polygonFits([[.4,.5],[1.55,.5],[1.55,1.5],[.4,1.5]], [[0,0],[4,0],[4,4],[0,4]], [nearbyObstruction], { grid: null, edgeSetback: 0 }), false);
assert.equal(geometry.polygonsOverlap(validPanel, obstruction), true);
assert.equal(geometry.polygonsOverlap(validPanel, [[7,6],[8,6],[8,7],[7,7]]), false);

const packed = geometry.selectNonOverlappingPanels([
  { corners: [[0,0],[1,0],[1,1],[0,1]] },
  { corners: [[.5,.5],[1.5,.5],[1.5,1.5],[.5,1.5]] },
  { corners: [[2,0],[3,0],[3,1],[2,1]] }
], 10);
assert.equal(packed.length, 2);
assert.ok(packed.length <= 3, 'preview cannot exceed physical candidate capacity');

const malformedGrid = { rows: 2, cols: 2, classifications: ['usable'] };
assert.equal(geometry.classificationGridValid(malformedGrid), false);
assert.equal(geometry.panelTouchesExcludedGrid(validPanel, malformedGrid, 'conservative'), true);
assert.equal(geometry.panelTouchesExcludedGrid(validPanel, malformedGrid, 'planning_preview'), false);
assert.equal(geometry.panelShadeVerified(validPanel, malformedGrid), false);
const incompatibleGrid = {...classificationGrid, geometry_compatible: false};
assert.equal(geometry.classificationGridValid(incompatibleGrid), false);
assert.equal(geometry.panelTouchesExcludedGrid(validPanel, incompatibleGrid, 'conservative'), true);
assert.equal(geometry.panelTouchesExcludedGrid(validPanel, incompatibleGrid, 'planning_preview'), false);
assert.equal(geometry.panelShadeVerified(validPanel, incompatibleGrid), false);

const savedFinancial = Object.freeze({ systemSize: 8, annualGeneration: 12000 });
const previewMetrics = geometry.panelLayoutMetrics([{ shadeVerified: false }, { shadeVerified: false }], 545);
const conservativeMetrics = geometry.panelLayoutMetrics([], 545);
assert.deepEqual(JSON.parse(JSON.stringify(previewMetrics)), { total: 2, preview: 2, confirmed: 0, totalKw: 1.09, previewKw: 1.09, confirmedKw: 0 });
assert.deepEqual(JSON.parse(JSON.stringify(conservativeMetrics)), { total: 0, preview: 0, confirmed: 0, totalKw: 0, previewKw: 0, confirmedKw: 0 });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.acceptedLayoutMetrics({ status: 'accepted', panel_count: 6, dc_capacity_kw: 3.27 }))), { status: 'accepted', count: 6, capacityKw: 3.27 });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.acceptedLayoutMetrics({ status: 'stale', panel_count: 6, dc_capacity_kw: 3.27 }))), { status: 'stale', count: null, capacityKw: null });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.acceptedLayoutMetrics({ status: 'accepted', panel_count: 6 }))), { status: 'invalid', count: null, capacityKw: null });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.acceptedLayoutMetrics({ status: 'accepted', panel_count: null, dc_capacity_kw: 3.27 }))), { status: 'invalid', count: null, capacityKw: null });
assert.equal(geometry.placementShortfallText(6, 11, { boundary: 3, classification: 2 }), '5 estimate modules could not be placed after roof boundary, shade/classification checks.');
assert.equal(geometry.placementShortfallText(6, 11, {}), '5 estimate modules could not be placed.');
assert.equal(geometry.placementShortfallText(11, 11), '');
assert.deepEqual(savedFinancial, { systemSize: 8, annualGeneration: 12000 });
assert.equal(geometry.angularDifference(359, 0), 1);
assert.equal(geometry.angularDifference(5, 355), 10);

assert.deepEqual(JSON.parse(JSON.stringify(geometry.viewerCapability(false, true))), { ok: false, code: 'three_unavailable' });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.viewerCapability(true, false))), { ok: false, code: 'webgl_unavailable' });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.viewerCapability(true, true))), { ok: true, code: 'ready' });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.reconstructionPayloadState(null))), { ok: false, code: 'unavailable' });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.reconstructionPayloadState({ available: true, surfaces: [] }))), { ok: false, code: 'malformed' });
const validReconstruction = {
  available: true,
  authoritative_boundary_local_xz_meters: [[0,0],[4,0],[4,4],[0,4]],
  surfaces: [{ id: 'surface-1', polygon_local_xz_meters: [[0,0],[4,0],[4,4],[0,4]], is_valid: true }],
  design_revision: { id: 12, number: 3 },
  geometry_key: 'geometry-current'
};
const compatibleValidation = { geometry_key: 'geometry-current', geometry_compatible: true, status: 'insufficient_data' };
assert.deepEqual(JSON.parse(JSON.stringify(geometry.reconstructionPayloadState(validReconstruction))), { ok: true, code: 'ready' });
assert.deepEqual(JSON.parse(JSON.stringify(geometry.layoutAcceptanceState(validReconstruction, [{ id: 'panel-1' }], compatibleValidation, 'planning_preview'))), { ok: true, code: 'ready' });
assert.equal(geometry.layoutAcceptanceState(validReconstruction, [{ id: 'panel-1' }], null, 'planning_preview').code, 'validation_unavailable');
assert.equal(geometry.layoutAcceptanceState(validReconstruction, [{ id: 'panel-1' }], compatibleValidation, 'conservative').code, 'classification_unavailable');
assert.equal(geometry.layoutAcceptanceState(validReconstruction, [], compatibleValidation, 'planning_preview').code, 'no_panels');

function fakeElement() {
  const classes = new Set(['hidden']);
  return {
    textContent: '', innerHTML: '', disabled: false, className: '', style: {},
    classList: { add: (...names) => names.forEach((name) => classes.add(name)), remove: (...names) => names.forEach((name) => classes.delete(name)), contains: (name) => classes.has(name) }
  };
}
const viewerElements = Object.fromEntries([
  'roof-3d-fallback', 'roof3d-surface-list', 'roof3d-structure-list',
  'roof3d-satellite-badge', 'roof3d-shade-badge', 'roof-3d-panel-status',
  'roof3d-provider-status', 'roof3d-layout-accept', 'roof3d-layout-acceptance-status',
  'roof-ai-status'
].map((id) => [id, fakeElement()]));
context.document = { getElementById: (id) => viewerElements[id] || null };
geometry.resolveViewerUnavailable('renderer_initialization_failed', 'context creation failed');
assert.match(viewerElements['roof-3d-fallback'].textContent, /could not initialize/);
assert.equal(viewerElements['roof-3d-fallback'].classList.contains('hidden'), false);
assert.match(viewerElements['roof3d-surface-list'].textContent, /unavailable/);
assert.match(viewerElements['roof3d-shade-badge'].textContent, /unavailable/);
assert.match(viewerElements['roof-3d-panel-status'].textContent, /unavailable/);
assert.equal(viewerElements['roof3d-layout-accept'].disabled, true);
assert.match(viewerElements['roof3d-layout-acceptance-status'].textContent, /cannot be accepted/);

viewerElements['roof3d-layout-accept'].disabled = false;
geometry.resolveReconstructionFailure('request failed');
assert.match(viewerElements['roof3d-surface-list'].textContent, /user-drawn boundary/);
assert.match(viewerElements['roof3d-shade-badge'].textContent, /unavailable/);
assert.match(viewerElements['roof-ai-status'].textContent, /request failed/);
assert.equal(viewerElements['roof3d-layout-accept'].disabled, true);

const applyStateButton = { disabled: true };
assert.equal(geometry.syncOrientationApplyButton(applyStateButton, true), true);
assert.equal(applyStateButton.disabled, false);
assert.equal(geometry.syncOrientationApplyButton(applyStateButton, false), false);
assert.equal(applyStateButton.disabled, true);

console.log('roof3d geometry tests passed');

// Missing provider-specific orientation ratios must never become a zero yield.
for (const unavailable of [null, undefined, '', false, NaN, Infinity]) {
  assert.equal(geometry.orientationYieldText(unavailable), 'Unavailable');
}
assert.equal(geometry.orientationYieldText(0), '0% relative yield');
assert.equal(geometry.orientationYieldText(.653), '65% relative yield');
