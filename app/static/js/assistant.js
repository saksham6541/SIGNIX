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
  const csrfInput = document.getElementById('assistant-csrf');
  const strings = {
    loading: widget.dataset.loadingLabel,
    rateLimit: widget.dataset.rateLimitMessage,
    unavailable: widget.dataset.unavailableMessage,
    emptyResponse: widget.dataset.emptyResponseMessage,
    connection: widget.dataset.connectionMessage,
  };

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
  });

  function togglePanel() {
    const isHidden = panel.classList.contains('hidden');
    if (isHidden) {
      panel.classList.remove('hidden');
      widget.classList.add('active');
      toggleBtn.setAttribute('aria-expanded', 'true');
      panel.setAttribute('aria-hidden', 'false');
      input.focus();
    } else {
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
    bubble.textContent = text;
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

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return;

    appendMessage(message, true);
    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;

    createLoadingIndicator();

    const csrfToken = csrfInput ? csrfInput.value : '';

    try {
      const response = await fetch('/api/assistant', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ message }),
      });

      removeLoadingIndicator();

      if (response.status === 429) {
        appendError(strings.rateLimit);
      } else if (!response.ok) {
        let errMessage = strings.unavailable;
        try {
          const errData = await response.json();
          if (errData && errData.error) {
            errMessage = errData.error;
          }
        } catch (_) {}
        appendError(errMessage);
      } else {
        const data = await response.json();
        if (data.response) {
          appendMessage(data.response, false);
        } else {
          appendError(strings.emptyResponse);
        }
      }
    } catch (err) {
      removeLoadingIndicator();
      appendError(strings.connection);
    } finally {
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    }
  });
});

function setupVoiceInput({ button, input, recognitionConstructor, locale, strings }) {
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

  button.addEventListener('click', () => {
    button.classList.add('listening');
    button.setAttribute('aria-label', strings.listening);
    button.title = strings.listening;
    recognition.start();
  });

  recognition.addEventListener('result', (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript;
    if (transcript) input.value = transcript;
  });
  recognition.addEventListener('end', stopListening);
  recognition.addEventListener('error', stopListening);

  return { supported: true, recognition };
}

if (typeof module !== 'undefined') {
  module.exports = { setupVoiceInput };
}
