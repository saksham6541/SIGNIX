function getSafeAssistantHref(value, baseUrl = null) {
  const href = String(value || '').trim();
  if (!href || /[\u0000-\u001F\u007F]/.test(href)) return null;
  if (href.startsWith('#') || (href.startsWith('/') && !href.startsWith('//'))) return href;

  try {
    const parsed = new URL(href, baseUrl || 'https://signix.invalid/');
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol) ? href : null;
  } catch (_) {
    return null;
  }
}

function appendAssistantInlineMarkdown(parent, source, doc, depth = 0) {
  const text = String(source || '');
  if (depth > 8) {
    parent.appendChild(doc.createTextNode(text));
    return;
  }

  let cursor = 0;
  const appendText = value => parent.appendChild(doc.createTextNode(value));
  while (cursor < text.length) {
    const remaining = text.slice(cursor);

    if (remaining[0] === '\\' && remaining.length > 1 && /[\\`*_[\]()]/.test(remaining[1])) {
      appendText(remaining[1]);
      cursor += 2;
      continue;
    }

    if (remaining[0] === '`') {
      const end = remaining.indexOf('`', 1);
      if (end > 0) {
        const code = doc.createElement('code');
        code.textContent = remaining.slice(1, end);
        parent.appendChild(code);
        cursor += end + 1;
        continue;
      }
    }

    const link = remaining.match(/^\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/);
    if (link) {
      const safeHref = getSafeAssistantHref(link[2], doc.defaultView?.location?.href);
      if (safeHref) {
        const anchor = doc.createElement('a');
        anchor.href = safeHref;
        appendAssistantInlineMarkdown(anchor, link[1], doc, depth + 1);
        if (/^https?:/i.test(safeHref)) {
          anchor.target = '_blank';
          anchor.rel = 'noopener noreferrer nofollow';
        }
        parent.appendChild(anchor);
      } else {
        appendText(link[0]);
      }
      cursor += link[0].length;
      continue;
    }

    const boldMarker = remaining.startsWith('**') ? '**' : remaining.startsWith('__') ? '__' : null;
    if (boldMarker) {
      const end = remaining.indexOf(boldMarker, boldMarker.length);
      if (end > boldMarker.length) {
        const strong = doc.createElement('strong');
        appendAssistantInlineMarkdown(strong, remaining.slice(boldMarker.length, end), doc, depth + 1);
        parent.appendChild(strong);
        cursor += end + boldMarker.length;
        continue;
      }
    }

    const italicMarker = remaining[0] === '*' || remaining[0] === '_' ? remaining[0] : null;
    if (italicMarker) {
      const end = remaining.indexOf(italicMarker, 1);
      if (end > 1) {
        const emphasis = doc.createElement('em');
        appendAssistantInlineMarkdown(emphasis, remaining.slice(1, end), doc, depth + 1);
        parent.appendChild(emphasis);
        cursor += end + 1;
        continue;
      }
    }

    const bareUrl = remaining.match(/^https?:\/\/[^\s<>]+/i);
    if (bareUrl) {
      let url = bareUrl[0];
      let trailing = '';
      while (/[.,!?;:]$/.test(url)) {
        trailing = url.slice(-1) + trailing;
        url = url.slice(0, -1);
      }
      const anchor = doc.createElement('a');
      anchor.href = url;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer nofollow';
      anchor.textContent = url;
      parent.appendChild(anchor);
      appendText(trailing);
      cursor += bareUrl[0].length;
      continue;
    }

    const nextSpecial = remaining.search(/[\\`*_[\]hH]/);
    const length = nextSpecial === 0 ? 1 : nextSpecial < 0 ? remaining.length : nextSpecial;
    appendText(remaining.slice(0, length));
    cursor += length;
  }
}

function renderAssistantMarkdown(markdown, doc = document) {
  const fragment = doc.createDocumentFragment();
  const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n');
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    const fence = lines[index].match(/^\s*```.*$/);
    if (fence) {
      index += 1;
      const codeLines = [];
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const pre = doc.createElement('pre');
      const code = doc.createElement('code');
      code.textContent = codeLines.join('\n');
      pre.appendChild(code);
      fragment.appendChild(pre);
      continue;
    }

    const heading = lines[index].match(/^\s*(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      const title = doc.createElement(`h${level}`);
      appendAssistantInlineMarkdown(title, heading[2], doc);
      fragment.appendChild(title);
      index += 1;
      continue;
    }

    const unordered = lines[index].match(/^\s*[-*+]\s+(.+)$/);
    const ordered = lines[index].match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const list = doc.createElement(unordered ? 'ul' : 'ol');
      const itemPattern = unordered ? /^\s*[-*+]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/;
      while (index < lines.length) {
        const item = lines[index].match(itemPattern);
        if (!item) break;
        const listItem = doc.createElement('li');
        appendAssistantInlineMarkdown(listItem, item[1], doc);
        list.appendChild(listItem);
        index += 1;
      }
      fragment.appendChild(list);
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length
      && lines[index].trim()
      && !/^\s*```.*$/.test(lines[index])
      && !/^\s*(?:#{1,6}\s+.+|[-*+]\s+.+|\d+[.)]\s+.+)/.test(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    const paragraph = doc.createElement('p');
    paragraphLines.forEach((line, lineIndex) => {
      if (lineIndex) paragraph.appendChild(doc.createElement('br'));
      appendAssistantInlineMarkdown(paragraph, line, doc);
    });
    fragment.appendChild(paragraph);
  }

  return fragment;
}

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
    retrying: widget.dataset.retryingLabel,
    retry: widget.dataset.retryLabel,
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
    return getAssistantErrorMessage(error, strings);
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
    const messageText = document.createElement(isUser ? 'span' : 'div');
    messageText.className = 'chat-message-text';
    if (isUser) {
      messageText.textContent = text;
    } else {
      messageText.appendChild(renderAssistantMarkdown(text));
    }
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

  function appendError(text, retryMessage = null) {
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble error-bubble';
    const messageText = document.createElement('span');
    messageText.textContent = text;
    bubble.appendChild(messageText);
    if (retryMessage) {
      const retryButton = document.createElement('button');
      retryButton.type = 'button';
      retryButton.className = 'assistant-retry-btn';
      retryButton.textContent = strings.retry || 'Retry';
      retryButton.addEventListener('click', () => {
        bubble.remove();
        submitMessage(retryMessage, { appendUser: false, retrying: true });
      });
      bubble.appendChild(retryButton);
    }
    messagesContainer.appendChild(bubble);
    scrollToBottom();
  }

  function createLoadingIndicator(labelText = strings.loading) {
    removeLoadingIndicator();
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'chat-bubble assistant-bubble assistant-loading';
    loadingDiv.id = 'assistant-loading-indicator';
    loadingDiv.setAttribute('role', 'status');
    loadingDiv.setAttribute('aria-label', labelText);
    const label = document.createElement('span');
    label.className = 'assistant-loading-label';
    label.textContent = labelText;
    const dots = document.createElement('span');
    dots.className = 'assistant-loading-dots';
    for (let dotIndex = 0; dotIndex < 3; dotIndex += 1) {
      dots.appendChild(document.createElement('span'));
    }
    loadingDiv.append(label, dots);
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

  function isRetryableAssistantError(error) {
    return [
      'request_timeout',
      'connection_error',
      'provider_rate_limited',
      'provider_timeout',
      'provider_connection',
      'provider_unavailable',
    ].includes(error?.code);
  }

  async function submitMessage(message, { appendUser = true, retrying = false } = {}) {
    if (!message || formSendInFlight || assistantClient.inFlight) return;
    formSendInFlight = true;

    speechOutput.cancel();
    if (appendUser) appendMessage(message, true);
    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;
    createLoadingIndicator(retrying ? strings.retrying : strings.loading);

    try {
      appendMessage(await sendAssistantMessage(message), false);
    } catch (error) {
      appendError(
        friendlyError(error),
        isRetryableAssistantError(error) ? message : null,
      );
    } finally {
      removeLoadingIndicator();
      formSendInFlight = false;
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    await submitMessage(message);
  });
});

function createAssistantClient({
  fetchImpl,
  csrfToken,
  timeoutMs = 30000,
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
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const hasProviderMessage = data && typeof data === 'object' && !Array.isArray(data)
          && typeof data.error === 'string' && data.error.trim();
        const errorMessage = hasProviderMessage
          ? data.error
          : 'The assistant is temporarily unavailable.';
        const error = new Error(errorMessage);
        error.status = response.status;
        error.code = data && typeof data === 'object' && !Array.isArray(data)
          && typeof data.code === 'string'
          ? data.code
          : 'unavailable';
        throw error;
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        const error = new Error('The assistant returned an invalid response.');
        error.code = 'malformed_response';
        throw error;
      }
      if (typeof data.response !== 'string' || !data.response.trim()) {
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

function getAssistantErrorMessage(error, strings = {}) {
  if (error?.status === 429) return strings.rateLimit || error.message || strings.unavailable;
  if (error?.code === 'request_timeout') return strings.timeout || error.message || strings.unavailable;
  if (error?.code === 'connection_error') return strings.connection || error.message || strings.unavailable;
  if (error?.code === 'unavailable') return strings.unavailable || error.message;
  if (error?.code === 'empty_response' || error?.code === 'malformed_response') {
    return strings.emptyResponse || error.message || strings.unavailable;
  }
  return error?.message || strings.unavailable;
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
  module.exports = {
    createAssistantClient,
    getAssistantErrorMessage,
    getSafeAssistantHref,
    renderAssistantMarkdown,
    setupSpeechOutput,
    setupVoiceInput,
  };
}
