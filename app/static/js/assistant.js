  document.addEventListener('DOMContentLoaded', () => {
  const widget = document.getElementById('assistant-widget');
  if (!widget) return;

  const toggleBtn = document.getElementById('assistant-toggle');
  const closeBtn = document.getElementById('assistant-close-btn');
  const panel = document.getElementById('assistant-panel');
  const messagesContainer = document.getElementById('assistant-messages');
  const form = document.getElementById('assistant-form');
  const input = document.getElementById('assistant-input');
  const sendBtn = document.getElementById('assistant-send-btn');
  const csrfInput = document.getElementById('assistant-csrf');

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
    loadingDiv.setAttribute('aria-label', 'Assistant is thinking');
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
        appendError("You've reached today's question limit.");
      } else if (!response.ok) {
        let errMessage = "The assistant is temporarily unavailable. Please try again later.";
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
          appendError("Received an empty response from assistant.");
        }
      }
    } catch (err) {
      removeLoadingIndicator();
      appendError("Unable to connect. Please check your connection and try again.");
    } finally {
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    }
  });
});
