const assert = require('node:assert/strict');
const { createAssistantClient, setupSpeechOutput, setupVoiceInput } = require('../app/static/js/assistant.js');

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

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
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
  const errors = [];
  setupVoiceInput({
    button,
    input: new FakeElement(),
    recognitionConstructor: FakeRecognition,
    locale: 'en',
    strings: { label: 'Use voice input', listening: 'Listening...', unsupported: 'Unsupported' },
    onError: error => errors.push(error),
  });
  button.click();
  FakeRecognition.instance.emit('error', { error: 'not-allowed' });
  assert.equal(errors[0].code, 'not-allowed');
  assert.equal(button.classList.contains('listening'), false);
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

class FakeUtterance {
  constructor(text) {
    this.text = text;
  }
}

{
  const spoken = [];
  let cancellations = 0;
  const synthesis = {
    getVoices: () => [{ lang: 'hi-IN' }, { lang: 'en-IN' }],
    speak: (utterance) => spoken.push(utterance),
    cancel: () => { cancellations += 1; },
  };
  const output = setupSpeechOutput({
    synthesis,
    utteranceConstructor: FakeUtterance,
    locale: 'hi',
  });

  output.speak('नमस्ते');

  assert.equal(output.language, 'hi-IN');
  assert.equal(spoken.length, 1);
  assert.equal(spoken[0].lang, 'hi-IN');
  assert.equal(spoken[0].voice.lang, 'hi-IN');
  assert.equal(cancellations, 1);
  output.cancel();
  assert.equal(cancellations, 2);
}

{
  const spoken = [];
  const synthesis = {
    getVoices: () => [{ lang: 'en-IN' }],
    speak: (utterance) => spoken.push(utterance),
  };
  const output = setupSpeechOutput({
    synthesis,
    utteranceConstructor: FakeUtterance,
    locale: 'en',
  });

  output.speak('How does solar work?');

  assert.equal(output.language, 'en-IN');
  assert.equal(spoken[0].lang, 'en-IN');
  assert.equal(spoken[0].voice.lang, 'en-IN');
}

{
  const synthesis = { getVoices: () => [], speak: () => { throw new Error('must not speak'); }, cancel: () => {} };
  const errors = [];
  const output = setupSpeechOutput({
    synthesis,
    utteranceConstructor: FakeUtterance,
    locale: 'hi',
    onError: error => errors.push(error),
  });

  assert.equal(output.speak('नमस्ते'), false);
  assert.equal(errors[0].code, 'speech-unavailable');
}

(async () => {
  let resolveFetch;
  let fetchCalls = 0;
  const client = createAssistantClient({
    csrfToken: 'csrf',
    fetchImpl: () => {
      fetchCalls += 1;
      return new Promise(resolve => { resolveFetch = resolve; });
    },
  });
  const first = client.send('first');
  await assert.rejects(client.send('duplicate'), error => error.code === 'request_in_progress');
  resolveFetch(jsonResponse(200, { response: '<strong>safe text</strong>' }));
  assert.equal(await first, '<strong>safe text</strong>');
  assert.equal(fetchCalls, 1);

  const malformedClient = createAssistantClient({
    csrfToken: 'csrf',
    fetchImpl: async () => jsonResponse(200, {}),
  });
  await assert.rejects(malformedClient.send('question'), error => error.code === 'empty_response');

  const timeoutClient = createAssistantClient({
    csrfToken: 'csrf',
    timeoutMs: 1,
    fetchImpl: (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('aborted')));
    }),
  });
  await assert.rejects(timeoutClient.send('slow'), error => error.code === 'request_timeout');

  console.log('assistant request reliability tests passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
