const assert = require('node:assert/strict');
const {
  VOICE_MODE_SILENCE_GAP_MS,
  VOICE_MODE_STATES,
  createVoiceMode,
  isIOSSafari,
} = require('../app/static/js/voice_mode.js');

(async () => {

class FakeRecognition {
  constructor() {
    this.handlers = {};
    this.startCalls = 0;
    this.stopCalls = 0;
  }

  addEventListener(name, handler) {
    this.handlers[name] = handler;
  }

  start() {
    this.startCalls += 1;
  }

  stop() {
    this.stopCalls += 1;
  }

  emit(name, event = {}) {
    this.handlers[name]?.(event);
    this[`on${name}`]?.(event);
  }
}

class FakeUtterance {
  constructor(text) {
    this.text = text;
  }
}

assert.equal(isIOSSafari({
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1',
  platform: 'iPhone',
}), true);
assert.equal(isIOSSafari({
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120.0.0.0 Mobile/15E148 Safari/604.1',
  platform: 'iPhone',
}), false);
assert.equal(isIOSSafari({
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15',
  platform: 'MacIntel',
  maxTouchPoints: 0,
}), false);
assert.equal(isIOSSafari({
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15',
  platform: 'MacIntel',
  maxTouchPoints: 5,
}), true);

function fakeTimers() {
  let nextId = 1;
  const timers = new Map();
  return {
    setTimer(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer(id) {
      timers.delete(id);
    },
    runAll() {
      for (const [id, timer] of [...timers]) {
        timers.delete(id);
        timer.callback();
      }
    },
    pendingDelay() {
      return [...timers.values()][0]?.delay;
    },
  };
}

function createHarness(overrides = {}) {
  const recognition = overrides.recognition || new FakeRecognition();
  const utterances = [];
  const synthesis = overrides.synthesis || {
    getVoices: () => [{ lang: 'hi-IN' }],
    cancel: () => {},
    speak: utterance => utterances.push(utterance),
  };
  const timers = fakeTimers();
  const states = [];
  const sent = [];
  let resolveSend;
  const machine = createVoiceMode({
    recognition,
    synthesis,
    utteranceConstructor: FakeUtterance,
    locale: 'hi',
    send: message => {
      sent.push(message);
      return new Promise(resolve => { resolveSend = resolve; });
    },
    onStateChange: state => states.push(state),
    onResponse: response => { machineResponse = response; },
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    ...overrides,
  });
  let machineResponse;
  return { machine, recognition, synthesis, utterances, timers, states, sent, resolveSend: value => resolveSend(value) };
}

{
  const harness = createHarness();
  harness.machine.toggle();
  assert.equal(harness.machine.state, VOICE_MODE_STATES.LISTENING);
  assert.equal(harness.recognition.continuous, true);
  assert.equal(harness.recognition.interimResults, true);
  assert.equal(harness.recognition.lang, 'hi-IN');
  assert.equal(harness.recognition.startCalls, 1);

  harness.recognition.emit('result', {
    resultIndex: 0,
    results: [{ 0: { transcript: 'सौर ऊर्जा' }, isFinal: true, length: 1 }],
  });
  assert.equal(harness.machine.state, VOICE_MODE_STATES.LISTENING);
  assert.equal(harness.timers.pendingDelay(), VOICE_MODE_SILENCE_GAP_MS);
  harness.timers.runAll();
  assert.equal(harness.machine.state, VOICE_MODE_STATES.THINKING);
  assert.deepEqual(harness.sent, ['सौर ऊर्जा']);
}

{
  const harness = createHarness();
  harness.machine.toggle();
  harness.recognition.emit('result', { results: [{ 0: { transcript: 'hello' }, isFinal: true, length: 1 }] });
  harness.timers.runAll();
  harness.resolveSend('उत्तर');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(harness.machine.state, VOICE_MODE_STATES.SPEAKING);
  const utterance = harness.utterances[0];
  utterance.onstart();
  assert.equal(harness.recognition.stopCalls > 0, true);
  utterance.onend();
  assert.equal(harness.machine.state, VOICE_MODE_STATES.LISTENING);
}

{
  const harness = createHarness();
  harness.machine.toggle();
  harness.recognition.emit('result', { results: [{ 0: { transcript: '   ' }, isFinal: true, length: 1 }] });
  harness.timers.runAll();
  assert.deepEqual(harness.sent, []);
  assert.equal(harness.machine.state, VOICE_MODE_STATES.LISTENING);
}

{
  const harness = createHarness();
  harness.machine.toggle();
  const starts = harness.recognition.startCalls;
  harness.recognition.emit('end');
  assert.equal(harness.recognition.startCalls, starts + 1);
  harness.machine.toggle();
  const stoppedStarts = harness.recognition.startCalls;
  harness.recognition.emit('end');
  assert.equal(harness.recognition.startCalls, stoppedStarts);
}

for (const target of [
  VOICE_MODE_STATES.LISTENING,
  VOICE_MODE_STATES.THINKING,
  VOICE_MODE_STATES.SPEAKING,
]) {
  const harness = createHarness();
  harness.machine.toggle();
  if (target === VOICE_MODE_STATES.THINKING) {
    harness.recognition.emit('result', { results: [{ 0: { transcript: 'hello' }, isFinal: true, length: 1 }] });
    harness.timers.runAll();
  } else if (target === VOICE_MODE_STATES.SPEAKING) {
    harness.recognition.emit('result', { results: [{ 0: { transcript: 'hello' }, isFinal: true, length: 1 }] });
    harness.timers.runAll();
    harness.resolveSend('answer');
    await new Promise(resolve => setImmediate(resolve));
  }
  harness.machine.toggle();
  assert.equal(harness.machine.state, VOICE_MODE_STATES.IDLE);
  assert.equal(harness.recognition.stopCalls > 0, true);
}

{
  const errors = [];
  const harness = createHarness({
    onError: error => errors.push(error),
  });
  harness.machine.toggle();
  harness.recognition.emit('result', { results: [{ 0: { transcript: 'hello' }, isFinal: true, length: 1 }] });
  harness.timers.runAll();
  harness.resolveSend('answer');
  await new Promise(resolve => setImmediate(resolve));
  harness.utterances[0].onerror(new Error('speech failed'));
  assert.equal(harness.machine.state, VOICE_MODE_STATES.LISTENING);
  assert.equal(harness.recognition.startCalls > 1, true);
  assert.equal(errors[0].message, 'speech failed');
}

{
  const failures = [];
  let sendCount = 0;
  const harness = createHarness({
    send: () => {
      sendCount += 1;
      return Promise.reject(Object.assign(new Error('backend failed'), { status: 502 }));
    },
    onError: error => failures.push(error),
  });

  const sendMessage = () => {
    harness.recognition.emit('result', {
      results: [{ 0: { transcript: 'same request' }, isFinal: true, length: 1 }],
    });
    harness.timers.runAll();
  };

  harness.machine.toggle();
  sendMessage();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(harness.machine.state, VOICE_MODE_STATES.IDLE);

  harness.machine.toggle();
  sendMessage();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(harness.machine.state, VOICE_MODE_STATES.IDLE);
  assert.equal(sendCount, 2);
  assert.equal(failures.length, 2);
  assert.equal(failures[0].voiceModeSendFailure, true);

  harness.machine.toggle();
  harness.timers.runAll();
  assert.equal(sendCount, 2);
  harness.machine.stop();
}

console.log('voice mode tests passed');
})();