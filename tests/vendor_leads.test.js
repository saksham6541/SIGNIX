const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/vendor-leads.js'), 'utf8');

function fixture(fetcher, view = 'board') {
  let submit, timeout, reloads = 0;
  const feedback = { textContent: '' };
  const attributes = {};
  const button = { disabled: false, dataset: {save: 'Save lead', unsave: 'Unsave lead'},
    setAttribute: (key, value) => { attributes[key] = value; },
    removeAttribute: key => { delete attributes[key]; } };
  const form = { action: '/vendor/leads/1/saved', elements: {saved: {value: 'true'}, view: {value: view}},
    querySelector: () => button, addEventListener: (_, callback) => { submit = callback; } };
  const root = { dataset: {saving: 'Updating', failure: 'Retry'},
    querySelector: selector => selector === '.lead-feedback' ? feedback : null,
    querySelectorAll: () => [form] };
  vm.runInNewContext(source, {
    document: {querySelector: () => root}, window: {location: {reload: () => { reloads++; }}},
    FormData: class { constructor(value) { this.form = value; } }, AbortController,
    setTimeout: callback => { timeout = callback; return 1; }, clearTimeout: () => {}, fetch: fetcher
  });
  return {button, attributes, feedback, form, submit: () => submit({preventDefault() {}}),
    expire: () => timeout(), reloads: () => reloads};
}
const response = saved => ({ok: true, redirected: false,
  headers: {get: () => 'application/json'}, json: async () => ({saved, message: 'Confirmed'})});

test('save waits for server confirmation, prevents duplicates, preserves CSRF form data', async () => {
  let resolve, requests = 0;
  const fixtureValue = fixture((url, options) => {
    requests++;
    assert.equal(url, '/vendor/leads/1/saved');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.body.form, fixtureValue.form);
    return new Promise(done => { resolve = done; });
  });
  const pending = fixtureValue.submit();
  assert.equal(fixtureValue.button.disabled, true);
  assert.equal(fixtureValue.form.elements.saved.value, 'true');
  await fixtureValue.submit();
  assert.equal(requests, 1);
  resolve(response(true)); await pending;
  assert.equal(fixtureValue.button.disabled, false);
  assert.equal(fixtureValue.attributes['aria-pressed'], 'true');
  assert.equal(fixtureValue.form.elements.saved.value, 'false');
});

for (const failure of ['network', 'session', 'malformed', 'server']) {
  test(`${failure} failure terminates loading and preserves retry intent`, async () => {
    const f = fixture(async () => {
      if (failure === 'network') throw new Error('internal secret');
      if (failure === 'session') return {...response(true), redirected: true};
      if (failure === 'server') return {...response(true), ok: false};
      return {...response(true), json: async () => ({message: '<script>bad</script>'})};
    });
    await f.submit();
    assert.equal(f.feedback.textContent, 'Retry');
    assert.equal(f.button.disabled, false);
    assert.equal(f.attributes['aria-busy'], undefined);
    assert.equal(f.form.elements.saved.value, 'true');
  });
}

test('timeout aborts the request and permits an idempotent retry', async () => {
  const f = fixture((_, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  }));
  const pending = f.submit(); f.expire(); await pending;
  assert.equal(f.button.disabled, false);
  assert.equal(f.feedback.textContent, 'Retry');
});

test('unsaving from saved list refreshes counts and pagination', async () => {
  const f = fixture(async () => response(false), 'saved');
  f.form.elements.saved.value = 'false';
  await f.submit();
  assert.equal(f.reloads(), 1);
  assert.equal(f.attributes['aria-pressed'], 'false');
});
