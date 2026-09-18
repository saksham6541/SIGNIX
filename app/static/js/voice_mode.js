const VOICE_MODE_STATES = Object.freeze({
  IDLE: 'IDLE',
  LISTENING: 'LISTENING',
  THINKING: 'THINKING',
  SPEAKING: 'SPEAKING',
});

const VOICE_MODE_SILENCE_GAP_MS = 1200;

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
  let transcript = '';
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
    stopRecognition();
    cancelSpeech();
    transcript = '';
    setState(VOICE_MODE_STATES.IDLE);
    onError(sendError);
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

    let utterance;
    try {
      utterance = new utteranceConstructor(responseText);
      utterance.lang = language;
      if (voice) utterance.voice = voice;
      utterance.onstart = () => {
        stopRecognition();
        setState(VOICE_MODE_STATES.SPEAKING);
      };
      const resumeListening = () => {
        if (enabled && state === VOICE_MODE_STATES.SPEAKING) {
          setState(VOICE_MODE_STATES.LISTENING);
          startRecognition();
        }
      };
      utterance.onend = resumeListening;
      utterance.onerror = error => {
        onError(error);
        resumeListening();
      };
      cancelSpeech();
      stopRecognition();
      setState(VOICE_MODE_STATES.SPEAKING);
      synthesis.speak(utterance);
    } catch (error) {
      onError(error);
      setState(VOICE_MODE_STATES.LISTENING);
      startRecognition();
    }
  };

  const sendTranscript = async () => {
    silenceTimer = null;
    const message = transcript.trim();
    transcript = '';
    sendTranscript.callCount = (sendTranscript.callCount || 0) + 1;
    console.log('[voice-sendTranscript]', sendTranscript.callCount, 'state=', state, 'transcript=', JSON.stringify(message));
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
          transcript += `${resultTranscript} `;
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
      onError(error);
      if (enabled && state === VOICE_MODE_STATES.LISTENING) startRecognition();
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
        stopRecognition();
        cancelSpeech();
        transcript = '';
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
      stopRecognition();
      cancelSpeech();
      transcript = '';
      consecutiveErrors = 0;
      lastErrorKey = null;
      setState(VOICE_MODE_STATES.IDLE);
    },
  };
}

if (typeof module !== 'undefined') {
  module.exports = {
    VOICE_MODE_SILENCE_GAP_MS,
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
