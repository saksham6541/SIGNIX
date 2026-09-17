const assert = require('node:assert/strict');
const { setupVoiceInput } = require('../app/static/js/assistant.js');

class FakeElement {
  constructor() {
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.title = '';
    this.attributes = {};
    this.listeners = {};
    this.classList = {
      values: new Set(),
      add: (value) => this.classList.values.add(value),
      remove: (value) => this.classList.values.delete(value),
      contains: (value) => this.classList.values.has(value),
    };
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  click() {
    this.listeners.click();
  }
}

class FakeRecognition {
  static instance;

  constructor() {
    this.listeners = {};
    this.started = false;
    FakeRecognition.instance = this;
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  start() {
    this.started = true;
  }

  emit(name, event = {}) {
    this.listeners[name](event);
  }
}

{
  const button = new FakeElement();
  const input = new FakeElement();
  const result = setupVoiceInput({
    button,
    input,
    recognitionConstructor: FakeRecognition,
    locale: 'hi',
    strings: { label: 'Use voice input', listening: 'Listening...', unsupported: 'Unsupported' },
  });

  button.click();
  FakeRecognition.instance.emit('result', {
    results: [[{ transcript: 'सौर ऊर्जा कैसे काम करती है' }]],
  });

  assert.equal(result.supported, true);
  assert.equal(FakeRecognition.instance.lang, 'hi-IN');
  assert.equal(FakeRecognition.instance.started, true);
  assert.equal(input.value, 'सौर ऊर्जा कैसे काम करती है');
}

{
  const button = new FakeElement();
  const result = setupVoiceInput({
    button,
    input: new FakeElement(),
    recognitionConstructor: undefined,
    locale: 'en',
    strings: { label: 'Use voice input', listening: 'Listening...', unsupported: 'Voice input is not supported' },
  });

  assert.equal(result.supported, false);
  assert.equal(button.hidden, true);
  assert.equal(button.disabled, true);
  assert.equal(button.title, 'Voice input is not supported');
}

console.log('assistant voice tests passed');