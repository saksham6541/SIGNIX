const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const listeners = {};
const button = { dataset: {}, disabled: true, addEventListener: (name, callback) => { listeners[name] = callback; } };
const field = (name, value = '') => ({ name, value, type: 'number', checkValidity: () => true });
const bill = field('monthly_bill');
const units = field('monthly_units_kwh');
const ppa = field('ppa_rate_rs_per_kwh');
const options = { dataset: { estimateUrl: '/business/estimate' }, querySelectorAll: () => [units, ppa] };
const status = {};
const loading = new Set(['hidden']);
const elements = {
  'start-estimation-btn': button, 'business-assessment-fields': options,
  'monthly-bill': bill, monthly_units_kwh: units,
  'estimate-action-status': status,
  'loading-panel': { classList: { add: x => loading.add(x), remove: x => loading.delete(x) } },
};
let polygon = null;
let sent;
const context = vm.createContext({
  window: { SignixI18n: { t: text => text } },
  document: { getElementById: id => elements[id] || null,
    addEventListener: (name, callback) => { listeners[name] = callback; }, querySelector: () => null },
  getCurrentPolygon: () => polygon, getCurrentObstructions: () => [],
  ESTIMATE_URL: '/api/estimate', console: { error() {}, warn() {} },
  fetch: async (url, request) => { sent = { url, request }; return { ok: false, status: 503, text: async () => JSON.stringify({error: 'Temporary fixture failure'}) }; },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/js/main.js'), 'utf8'), context);
vm.runInContext('initEstimationButton()', context);
assert.equal(button.disabled, true, 'no roof cannot submit');
polygon = [[28, 77], [28, 77.01], [28.01, 77.01]];
listeners.input();
assert.equal(button.disabled, true, 'roof without usage cannot submit');
units.value = '1000'; listeners.input();
assert.equal(button.disabled, false, 'roof and consumption enable assessment');
ppa.value = '0'; listeners.input();
assert.equal(button.disabled, true, 'supplied zero PPA is invalid');
ppa.value = ''; listeners.input();
assert.equal(button.disabled, false, 'absent PPA remains a quote');
button.dataset.loading = 'true'; listeners.input();
assert.equal(button.disabled, true, 'an in-flight assessment cannot be submitted again');
delete button.dataset.loading;
vm.runInContext("SignixFormStore.selectedAddress = 'Fixture'; SignixFormStore.selectedLatLng = {lat:28, lng:77}", context);
(async () => {
  await listeners.click();
  assert.equal(sent.url, '/business/estimate');
  const body = JSON.parse(sent.request.body);
  assert.deepEqual(body.polygon, polygon);
  assert.deepEqual(body.obstructions, []);
  assert.equal(body.monthly_units_kwh, '1000');
  assert.equal(body.ppa_rate_rs_per_kwh, '');
  assert.equal(sent.request.credentials, 'same-origin');
  assert.ok('X-CSRFToken' in sent.request.headers);
  assert.equal(button.disabled, false, 'failure re-enables valid submission');
  assert.equal(button.dataset.loading, undefined);
  assert.ok(loading.has('hidden'), 'failure terminates loading state');
  assert.equal(status.textContent, 'Temporary fixture failure');
  delete elements['business-assessment-fields'];
  assert.equal(context.window.SignixAssessmentReady(), true, 'home readiness behavior is unchanged');
  console.log('Commercial assessment: readiness, payload, CSRF header, failure recovery, and home compatibility passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
