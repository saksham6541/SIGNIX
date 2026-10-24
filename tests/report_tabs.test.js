const assert = require('node:assert/strict');
const { createReportTabs, nextTabIndex } = require('../app/static/js/report-tabs.js');

class FakeElement {
  constructor(id, attributes = {}) {
    this.id = id;
    this.attributes = { ...attributes };
    this.listeners = {};
    this.hidden = false;
    this.tabIndex = Number(attributes.tabindex || -1);
    this.focusCalls = 0;
    this.scrollCalls = 0;
    this.events = [];
    const classes = new Set();
    this.classList = {
      add: name => classes.add(name),
      contains: name => classes.has(name),
    };
  }

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(name, handler) {
    this.listeners[name] ||= [];
    this.listeners[name].push(handler);
  }

  emit(name, event = {}) {
    this.listeners[name]?.forEach(handler => handler(event));
  }

  focus() {
    this.focusCalls += 1;
  }

  scrollIntoView() {
    this.scrollCalls += 1;
  }

  dispatchEvent(event) {
    this.events.push(event);
  }
}

function harness() {
  const panels = Array.from({ length: 6 }, (_, index) => new FakeElement(`panel-${index}`));
  const tabs = panels.map((panel, index) => new FakeElement(`tab-${index}`, {
    'aria-controls': panel.id,
    'aria-selected': index === 0 ? 'true' : 'false',
    tabindex: index === 0 ? '0' : '-1',
  }));
  const byId = new Map(panels.map(panel => [panel.id, panel]));
  const doc = { getElementById: id => byId.get(id) || null };
  const root = new FakeElement('tabs');
  root.ownerDocument = doc;
  root.querySelectorAll = selector => selector === '[role="tab"]' ? tabs : [];
  let resizeCalls = 0;
  let windowResizeEvents = 0;
  class FakeEvent { constructor(type) { this.type = type; } }
  class FakeCustomEvent extends FakeEvent {
    constructor(type, options) { super(type); Object.assign(this, options); }
  }
  const view = {
    Event: FakeEvent,
    CustomEvent: FakeCustomEvent,
    requestAnimationFrame: callback => callback(),
    dispatchEvent: event => { if (event.type === 'resize') windowResizeEvents += 1; },
    SIGNIX_REPORT_CHARTS: { resize: () => { resizeCalls += 1; } },
  };
  doc.defaultView = view;
  return { root, tabs, panels, view, resizeCalls: () => resizeCalls, windowResizeEvents: () => windowResizeEvents };
}

assert.equal(nextTabIndex(0, 'ArrowLeft', 6), 5);
assert.equal(nextTabIndex(5, 'ArrowRight', 6), 0);
assert.equal(nextTabIndex(3, 'Home', 6), 0);
assert.equal(nextTabIndex(3, 'End', 6), 5);

{
  const test = harness();
  const controller = createReportTabs(test.root, test.view);
  assert.ok(controller);
  assert.equal(test.root.classList.contains('is-ready'), true);
  assert.equal(test.tabs[0].getAttribute('aria-selected'), 'true');
  assert.equal(test.tabs[0].tabIndex, 0);
  assert.equal(test.panels[0].hidden, false);
  assert.equal(test.panels.slice(1).every(panel => panel.hidden), true);
  assert.equal(test.resizeCalls(), 1);
  assert.equal(test.windowResizeEvents(), 1);

  test.panels[2].unsavedValue = 'preserved';
  test.tabs[2].emit('click');
  assert.equal(test.tabs[2].getAttribute('aria-selected'), 'true');
  assert.equal(test.tabs[0].getAttribute('aria-selected'), 'false');
  assert.equal(test.panels[2].hidden, false);
  assert.equal(test.panels[0].hidden, true);

  let prevented = false;
  test.tabs[2].emit('keydown', { key: 'ArrowRight', preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(test.tabs[3].getAttribute('aria-selected'), 'true');
  assert.equal(test.tabs[3].focusCalls, 1);
  assert.equal(test.panels[2].unsavedValue, 'preserved');

  test.tabs[3].emit('keydown', { key: 'End', preventDefault() {} });
  assert.equal(test.tabs[5].getAttribute('aria-selected'), 'true');
  test.tabs[5].emit('keydown', { key: 'ArrowRight', preventDefault() {} });
  assert.equal(test.tabs[0].getAttribute('aria-selected'), 'true');

  assert.equal(createReportTabs(test.root, test.view), controller);
  assert.equal(test.tabs[0].listeners.click.length, 1);
}

console.log('report tab tests passed');
