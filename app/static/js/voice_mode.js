const VOICE_MODE_STATES = Object.freeze({
  IDLE: 'IDLE',
  LISTENING: 'LISTENING',
  THINKING: 'THINKING',
  SPEAKING: 'SPEAKING',
});

const VOICE_MODE_SILENCE_GAP_MS = 1200;
const VOICE_MODE_SPEECH_START_TIMEOUT_MS = 3000;

function localeToSpeechLanguage(locale) {
  return String(locale).toLowerCase().startsWith('hi') ? 'hi-IN' : 'en-IN';
}

function isIOSSafari({ userAgent = '', platform = '', maxTouchPoints = 0 } = {}) {
  const iosDevice = /iPad|iPhone|iPod/i.test(userAgent)
    || (platform === 'MacIntel' && maxTouchPoints > 1);
  const safariBrowser = /Safari/i.test(userAgent)
    && !/CriOS|FxiOS|EdgiOS|OPiOS|GSA/i.test(userAgent);
  return iosDevice && safariBrowser;
}

function createVoiceMode({
  recognition,
  synthesis,
  utteranceConstructor,
  locale,
  send,
  onStateChange = () => {},
  onInterimTranscript = () => {},
  onTranscript = () => {},
  onResponse = () => {},
  onError = () => {},
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  silenceGapMs = VOICE_MODE_SILENCE_GAP_MS,
}) {
  let state = VOICE_MODE_STATES.IDLE;
  let silenceTimer = null;
  let speechStartTimer = null;
  let transcript = '';
  const finalSegments = new Map();
  let enabled = false;
  let consecutiveErrors = 0;
  let lastErrorKey = null;
  const language = localeToSpeechLanguage(locale);
  const supported = Boolean(recognition && synthesis && utteranceConstructor);

  const setState = nextState => {
    state = nextState;
    onStateChange(state);
  };

  const clearSilenceTimer = () => {
    if (silenceTimer !== null) {
      clearTimer(silenceTimer);
      silenceTimer = null;
    }
  };

  const clearSpeechStartTimer = () => {
    if (speechStartTimer !== null) {
      clearTimer(speechStartTimer);
      speechStartTimer = null;
    }
  };

  const stopRecognition = () => {
    try {
      recognition?.stop();
    } catch (_) {}
  };

  const startRecognition = () => {
    if (!enabled || state !== VOICE_MODE_STATES.LISTENING) return;
    try {
      recognition?.start();
    } catch (_) {}
  };

  const cancelSpeech = () => {
    try {
      synthesis?.cancel();
    } catch (_) {}
  };

  const errorKey = error => `${error?.status || 'unknown'}:${error?.message || 'unknown'}`;

  const stopAfterError = error => {
    const key = errorKey(error);
    consecutiveErrors = key === lastErrorKey ? consecutiveErrors + 1 : 1;
    lastErrorKey = key;
    const sendError = error instanceof Error ? error : new Error(String(error));
    if (error?.status) sendError.status = error.status;
    sendError.voiceModeSendFailure = true;
    enabled = false;
    clearSilenceTimer();
    clearSpeechStartTimer();
    stopRecognition();
    cancelSpeech();
    transcript = '';
    finalSegments.clear();
    setState(VOICE_MODE_STATES.IDLE);
    onError(sendError);
  };

  const stopForCapabilityError = (source, fallbackCode) => {
    const error = source instanceof Error ? source : new Error('Voice mode is unavailable.');
    error.code = source?.error || source?.code || fallbackCode;
    enabled = false;
    clearSilenceTimer();
    clearSpeechStartTimer();
    stopRecognition();
    cancelSpeech();
    transcript = '';
    finalSegments.clear();
    setState(VOICE_MODE_STATES.IDLE);
    onError(error);
  };

  const speakResponse = responseText => {
    if (!enabled || !synthesis || !utteranceConstructor) {
      setState(VOICE_MODE_STATES.LISTENING);
      startRecognition();
      return;
    }

    const voices = synthesis.getVoices ? synthesis.getVoices() : [];
    const voice = voices.find(candidate => candidate.lang?.toLowerCase() === language.toLowerCase())
      || voices.find(candidate => candidate.lang?.toLowerCase().startsWith(language.slice(0, 2).toLowerCase()));
    if (voices.length > 0 && !voice) {
      stopForCapabilityError(null, 'speech-unavailable');
      return;
    }

    let utterance;
    try {
      utterance = new utteranceConstructor(responseText);
      utterance.lang = language;
      if (voice) utterance.voice = voice;
      utterance.onstart = () => {
        clearSpeechStartTimer();
        stopRecognition();
        setState(VOICE_MODE_STATES.SPEAKING);
      };
      const resumeListening = () => {
        clearSpeechStartTimer();
        if (enabled && state === VOICE_MODE_STATES.SPEAKING) {
          setState(VOICE_MODE_STATES.LISTENING);
          startRecognition();
        }
      };
      utterance.onend = resumeListening;
      utterance.onerror = error => {
        stopForCapabilityError(error, 'speech-playback-failed');
      };
      cancelSpeech();
      stopRecognition();
      speechStartTimer = setTimer(
        () => stopForCapabilityError(null, 'speech-playback-failed'),
        VOICE_MODE_SPEECH_START_TIMEOUT_MS,
      );
      synthesis.speak(utterance);
    } catch (error) {
      stopForCapabilityError(error, 'speech-playback-failed');
    }
  };

  const sendTranscript = async () => {
    silenceTimer = null;
    const message = transcript.trim();
    transcript = '';
    finalSegments.clear();
    if (!message || !enabled) {
      if (enabled) setState(VOICE_MODE_STATES.LISTENING);
      return;
    }

    stopRecognition();
    setState(VOICE_MODE_STATES.THINKING);
    onTranscript(message);
    try {
      const response = await send(message);
      if (!enabled) return;
      consecutiveErrors = 0;
      lastErrorKey = null;
      onResponse(response);
      speakResponse(response);
    } catch (error) {
      stopAfterError(error);
    }
  };

  const scheduleSend = () => {
    clearSilenceTimer();
    silenceTimer = setTimer(sendTranscript, silenceGapMs);
  };

  if (recognition) {
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = language;
    recognition.onresult = event => {
      if (!enabled || state !== VOICE_MODE_STATES.LISTENING) return;
      let receivedFinal = false;
      let receivedSpeech = false;
      let interimTranscript = '';
      for (let index = event.resultIndex || 0; index < event.results.length; index += 1) {
        const result = event.results[index];
        const resultTranscript = result[0]?.transcript?.trim() || '';
        receivedSpeech = receivedSpeech || Boolean(resultTranscript);
        if (result.isFinal) {
          finalSegments.set(index, resultTranscript);
          transcript = [...finalSegments.values()].filter(Boolean).join(' ');
          receivedFinal = true;
        } else {
          interimTranscript += `${resultTranscript} `;
        }
      }
      if (receivedSpeech) {
        onInterimTranscript(`${transcript} ${interimTranscript}`.trim());
      }
      if (receivedFinal) {
        scheduleSend();
      } else if (receivedSpeech && transcript.trim()) {
        scheduleSend();
      }
    };
    recognition.onend = () => {
      if (enabled && state === VOICE_MODE_STATES.LISTENING) startRecognition();
    };
    recognition.onerror = error => {
      if (!enabled || error?.error === 'aborted') return;
      const fatalErrors = new Set([
        'not-allowed',
        'service-not-allowed',
        'audio-capture',
        'network',
        'language-not-supported',
      ]);
      if (fatalErrors.has(error?.error)) {
        stopForCapabilityError(error, 'recognition-error');
        return;
      }
      const recognitionError = new Error('Voice recognition failed.');
      recognitionError.code = error?.error || 'recognition-error';
      onError(recognitionError);
    };
  }

  return {
    get state() {
      return state;
    },
    language,
    supported,
    toggle() {
      if (!supported) return;
      if (enabled) {
        enabled = false;
        clearSilenceTimer();
        clearSpeechStartTimer();
        stopRecognition();
        cancelSpeech();
        transcript = '';
        finalSegments.clear();
        consecutiveErrors = 0;
        lastErrorKey = null;
        setState(VOICE_MODE_STATES.IDLE);
        return;
      }
      enabled = true;
      setState(VOICE_MODE_STATES.LISTENING);
      startRecognition();
    },
    stop() {
      if (!enabled && state === VOICE_MODE_STATES.IDLE) return;
      enabled = false;
      clearSilenceTimer();
      clearSpeechStartTimer();
      stopRecognition();
      cancelSpeech();
      transcript = '';
      finalSegments.clear();
      consecutiveErrors = 0;
      lastErrorKey = null;
      setState(VOICE_MODE_STATES.IDLE);
    },
  };
}

if (typeof module !== 'undefined') {
  module.exports = {
    VOICE_MODE_SILENCE_GAP_MS,
    VOICE_MODE_SPEECH_START_TIMEOUT_MS,
    VOICE_MODE_STATES,
    createVoiceMode,
    isIOSSafari,
    localeToSpeechLanguage,
  };
}

if (typeof window !== 'undefined') {
  window.createVoiceMode = createVoiceMode;
  window.isIOSSafari = isIOSSafari;
}
