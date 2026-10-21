  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
  const widget = document.getElementById('assistant-widget');
  if (!widget) return;

  const toggleBtn = document.getElementById('assistant-toggle');
  const closeBtn = document.getElementById('assistant-close-btn');
  const panel = document.getElementById('assistant-panel');
  const messagesContainer = document.getElementById('assistant-messages');
  const form = document.getElementById('assistant-form');
  const input = document.getElementById('assistant-input');
  const sendBtn = document.getElementById('assistant-send-btn');
  const voiceBtn = document.getElementById('assistant-voice-btn');
  const voiceModeBtn = document.getElementById('assistant-voice-mode-btn');
  const voiceModeExitBtn = document.getElementById('assistant-voice-mode-exit');
  const voiceStatus = document.getElementById('assistant-voice-status');
  const voiceModeIcon = voiceModeBtn?.querySelector('.assistant-voice-mode-icon');
  const csrfInput = document.getElementById('assistant-csrf');
  let formSendInFlight = false;
  const strings = {
    loading: widget.dataset.loadingLabel,
    rateLimit: widget.dataset.rateLimitMessage,
    unavailable: widget.dataset.unavailableMessage,
    emptyResponse: widget.dataset.emptyResponseMessage,
    connection: widget.dataset.connectionMessage,
    timeout: widget.dataset.timeoutMessage,
    permissionDenied: widget.dataset.voicePermissionDeniedMessage,
    noSpeech: widget.dataset.voiceNoSpeechMessage,
    voiceNetwork: widget.dataset.voiceNetworkMessage,
    audioUnavailable: widget.dataset.voiceAudioUnavailableMessage,
    speechUnavailable: widget.dataset.speechUnsupportedLabel,
  };
  const speechOutput = setupSpeechOutput({
    synthesis: window.speechSynthesis,
    utteranceConstructor: window.SpeechSynthesisUtterance,
    locale: document.documentElement.lang,
    onError: () => appendError(strings.speechUnavailable),
  });
  const csrfToken = csrfInput ? csrfInput.value : '';
  const recognitionConstructor = window.SpeechRecognition || window.webkitSpeechRecognition;
  const iosSafari = window.isIOSSafari?.(window.navigator) === true;
  const assistantClient = createAssistantClient({
    fetchImpl: window.fetch.bind(window),
    csrfToken,
    AbortControllerConstructor: window.AbortController,
  });

  const voiceMode = window.createVoiceMode({
    recognition: iosSafari ? null : createRecognition(recognitionConstructor),
    synthesis: window.speechSynthesis,
    utteranceConstructor: window.SpeechSynthesisUtterance,
    locale: document.documentElement.lang,
    send: sendAssistantMessage,
    onStateChange: updateVoiceModeState,
    onInterimTranscript: transcript => {
      input.value = transcript;
    },
    onTranscript: message => {
      appendMessage(message, true);
      input.value = '';
      createLoadingIndicator();
    },
    onResponse: response => {
      removeLoadingIndicator();
      appendMessage(response, false);
    },
    onError: error => {
      removeLoadingIndicator();
      appendError(voiceErrorMessage(error));
    },
  });

  if (!voiceMode.supported && voiceModeBtn) {
    voiceModeBtn.hidden = true;
    voiceModeBtn.disabled = true;
  }

  voiceModeBtn?.addEventListener('click', () => voiceMode.toggle());
  voiceModeExitBtn?.addEventListener('click', () => voiceMode.toggle());

  const stopVoiceSession = () => {
    voiceMode.stop();
    speechOutput.cancel();
  };

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopVoiceSession();
  });
  window.addEventListener('beforeunload', stopVoiceSession);
  document.querySelectorAll('.language-switcher a').forEach(link => {
    link.addEventListener('click', stopVoiceSession);
  });

  setupVoiceInput({
    button: voiceBtn,
    input,
    recognitionConstructor: window.SpeechRecognition || window.webkitSpeechRecognition,
    locale: document.documentElement.lang,
    strings: {
      label: widget.dataset.voiceLabel,
      listening: widget.dataset.voiceListeningLabel,
      unsupported: widget.dataset.voiceUnsupportedLabel,
    },
    onError: error => appendError(voiceErrorMessage(error)),
  });

  function voiceErrorMessage(error) {
    if (error?.status === 429) return strings.rateLimit;
    if (error?.code === 'not-allowed' || error?.code === 'service-not-allowed') return strings.permissionDenied;
    if (error?.code === 'no-speech') return strings.noSpeech;
    if (error?.code === 'network') return strings.voiceNetwork;
    if (error?.code === 'audio-capture') return strings.audioUnavailable;
    if (error?.code === 'speech-unavailable' || error?.code === 'speech-playback-failed') return strings.speechUnavailable;
    return friendlyError(error);
  }

  function friendlyError(error) {
    if (error?.status === 429) return strings.rateLimit;
    if (error?.code === 'request_timeout') return strings.timeout;
    if (error?.code === 'connection_error') return strings.connection;
    if (error?.code === 'empty_response' || error?.code === 'malformed_response') return strings.emptyResponse;
    return error?.message || strings.unavailable;
  }

  function togglePanel() {
    const isHidden = panel.classList.contains('hidden');
    if (isHidden) {
      panel.classList.remove('hidden');
      widget.classList.add('active');
      toggleBtn.setAttribute('aria-expanded', 'true');
      panel.setAttribute('aria-hidden', 'false');
      input.focus();
    } else {
      stopVoiceSession();
      panel.classList.add('hidden');
      widget.classList.remove('active');
      toggleBtn.setAttribute('aria-expanded', 'false');
      panel.setAttribute('aria-hidden', 'true');
    }
  }

  toggleBtn?.addEventListener('click', togglePanel);
  closeBtn?.addEventListener('click', togglePanel);

  function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  function appendMessage(text, isUser = false) {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${isUser ? 'user-bubble' : 'assistant-bubble'}`;
    const messageText = document.createElement('span');
    messageText.className = 'chat-message-text';
    messageText.textContent = text;
    bubble.appendChild(messageText);
    if (!isUser && speechOutput.supported) {
      const speakButton = document.createElement('button');
      speakButton.type = 'button';
      speakButton.className = 'assistant-speak-btn';
      speakButton.title = widget.dataset.speakLabel;
      speakButton.setAttribute('aria-label', widget.dataset.speakLabel);
      speakButton.textContent = '🔊';
      speakButton.addEventListener('click', () => speechOutput.speak(text));
      bubble.appendChild(speakButton);
    }
    messagesContainer.appendChild(bubble);
    scrollToBottom();
    return bubble;
  }

  function appendError(text) {
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble error-bubble';
    bubble.textContent = text;
    messagesContainer.appendChild(bubble);
    scrollToBottom();
  }

  function createLoadingIndicator() {
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'chat-bubble assistant-bubble assistant-loading';
    loadingDiv.id = 'assistant-loading-indicator';
    loadingDiv.setAttribute('role', 'status');
    loadingDiv.setAttribute('aria-label', strings.loading);
    loadingDiv.innerHTML = '<span></span><span></span><span></span>';
    messagesContainer.appendChild(loadingDiv);
    scrollToBottom();
    return loadingDiv;
  }

  function removeLoadingIndicator() {
    const loadingDiv = document.getElementById('assistant-loading-indicator');
    if (loadingDiv) {
      loadingDiv.remove();
    }
  }

  function createRecognition(RecognitionConstructor) {
    return RecognitionConstructor ? new RecognitionConstructor() : null;
  }

  function updateVoiceModeState(state) {
    if (!voiceModeBtn) return;
    const labels = {
      IDLE: widget.dataset.voiceModeLabel,
      LISTENING: widget.dataset.voiceModeListeningLabel,
      THINKING: widget.dataset.voiceModeThinkingLabel,
      SPEAKING: widget.dataset.voiceModeSpeakingLabel,
    };
    const active = state !== 'IDLE';
    voiceModeBtn.classList.toggle('active', active);
    voiceModeBtn.classList.toggle('listening', state === 'LISTENING');
    voiceModeBtn.classList.toggle('thinking', state === 'THINKING');
    voiceModeBtn.classList.toggle('speaking', state === 'SPEAKING');
    voiceModeBtn.setAttribute('aria-label', active ? widget.dataset.voiceModeActiveLabel : labels.IDLE);
    voiceModeBtn.title = active ? widget.dataset.voiceModeActiveLabel : labels.IDLE;
    voiceModeBtn.setAttribute('aria-pressed', String(active));
    if (voiceStatus) {
      voiceStatus.hidden = !active;
      voiceStatus.textContent = labels[state];
      voiceStatus.classList.toggle('thinking', state === 'THINKING');
    }
    if (voiceModeIcon) {
      voiceModeIcon.textContent = state === 'SPEAKING' ? '🔊' : state === 'THINKING' ? '…' : state === 'LISTENING' ? '🎙' : '◉';
    }
  }

  async function sendAssistantMessage(message) {
    return assistantClient.send(message);
  }

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message || formSendInFlight || assistantClient.inFlight) return;
    formSendInFlight = true;

    speechOutput.cancel();
    appendMessage(message, true);
    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;

    createLoadingIndicator();

    try {
      appendMessage(await sendAssistantMessage(message), false);
    } catch (err) {
      appendError(friendlyError(err));
    } finally {
      removeLoadingIndicator();
      formSendInFlight = false;
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    }
  });
});

function createAssistantClient({
  fetchImpl,
  csrfToken,
  timeoutMs = 15000,
  AbortControllerConstructor = typeof AbortController === 'undefined' ? null : AbortController,
}) {
  let inFlight = false;

  async function send(message) {
    if (inFlight) {
      const duplicateError = new Error('A message is already being sent.');
      duplicateError.code = 'request_in_progress';
      throw duplicateError;
    }
    inFlight = true;
    const controller = AbortControllerConstructor ? new AbortControllerConstructor() : null;
    let timedOut = false;
    const timeout = controller ? setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs) : null;
    try {
      const response = await fetchImpl('/api/assistant', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ message }),
        signal: controller?.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.error || 'The assistant is temporarily unavailable.');
        error.status = response.status;
        error.code = data.code;
        throw error;
      }
      if (!data || typeof data.response !== 'string' || !data.response.trim()) {
        const error = new Error('The assistant returned an empty response.');
        error.code = 'empty_response';
        throw error;
      }
      return data.response.trim();
    } catch (error) {
      if (timedOut) {
        const timeoutError = new Error('The assistant request timed out.');
        timeoutError.code = 'request_timeout';
        throw timeoutError;
      }
      if (error instanceof TypeError) error.code = error.code || 'connection_error';
      throw error;
    } finally {
      if (timeout !== null) clearTimeout(timeout);
      inFlight = false;
    }
  }

  return { send, get inFlight() { return inFlight; } };
}

function setupVoiceInput({ button, input, recognitionConstructor, locale, strings, onError = () => {} }) {
  if (!button) return { supported: false };

  if (!recognitionConstructor) {
    button.hidden = true;
    button.disabled = true;
    button.title = strings.unsupported;
    return { supported: false };
  }

  const recognition = new recognitionConstructor();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = String(locale).toLowerCase().startsWith('hi') ? 'hi-IN' : 'en-IN';

  const stopListening = () => {
    button.classList.remove('listening');
    button.setAttribute('aria-label', strings.label);
    button.title = strings.label;
  };

  let listening = false;
  button.addEventListener('click', () => {
    if (listening) return;
    listening = true;
    button.classList.add('listening');
    button.setAttribute('aria-label', strings.listening);
    button.title = strings.listening;
    try {
      recognition.start();
    } catch (error) {
      listening = false;
      stopListening();
      onError(error);
    }
  });

  recognition.addEventListener('result', (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript;
    if (transcript) input.value = transcript;
  });
  recognition.addEventListener('end', () => {
    listening = false;
    stopListening();
  });
  recognition.addEventListener('error', (event) => {
    listening = false;
    stopListening();
    const error = new Error('Voice recognition failed.');
    error.code = event?.error || 'recognition-error';
    onError(error);
  });

  return { supported: true, recognition };
}

function setupSpeechOutput({ synthesis, utteranceConstructor, locale, onError = () => {} }) {
  const language = String(locale).toLowerCase().startsWith('hi') ? 'hi-IN' : 'en-IN';
  if (!synthesis || !utteranceConstructor) {
    return { supported: false, speak: () => false, cancel: () => {} };
  }

  function cancel() {
    try {
      synthesis.cancel();
    } catch (_) {
      // Speech output is optional; cancellation must not disrupt chat.
    }
  }

  function speak(text) {
    try {
      const voices = synthesis.getVoices ? synthesis.getVoices() : [];
      const voice = voices.find((candidate) => candidate.lang?.toLowerCase() === language.toLowerCase())
        || voices.find((candidate) => candidate.lang?.toLowerCase().startsWith(language.slice(0, 2).toLowerCase()));
      if (!voice) {
        const error = new Error('Speech output is unavailable for this language.');
        error.code = 'speech-unavailable';
        onError(error);
        return false;
      }
      const utterance = new utteranceConstructor(text);
      utterance.lang = language;
      utterance.voice = voice;
      utterance.onerror = () => {
        const error = new Error('Speech playback failed.');
        error.code = 'speech-playback-failed';
        onError(error);
      };
      cancel();
      synthesis.speak(utterance);
      return true;
    } catch (error) {
      error.code = error.code || 'speech-playback-failed';
      onError(error);
      return false;
    }
  }

  return { supported: true, speak, cancel, language };
}

if (typeof module !== 'undefined') {
  module.exports = { createAssistantClient, setupSpeechOutput, setupVoiceInput };
}
