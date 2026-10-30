/* Progressive enhancement: native GET filters and POST bookmarks still work. */
(() => {
  'use strict';
  const root = document.querySelector('.vendor-leads');
  if (!root) return;
  root.querySelector('[aria-invalid="true"]')?.focus();
  const feedback = root.querySelector('.lead-feedback');
  root.querySelectorAll('.lead-save-form').forEach(form => {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('button');
      if (button.disabled) return;
      const body = new FormData(form);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      feedback.textContent = root.dataset.saving;
      try {
        const response = await fetch(form.action, {
          method: 'POST', body, credentials: 'same-origin',
          headers: { Accept: 'application/json' }, signal: controller.signal
        });
        // Session expiry and CSRF errors can return HTML; never display it.
        if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
        const data = await response.json();
        if (!response.ok || typeof data.saved !== 'boolean') throw new Error();
        form.elements.saved.value = data.saved ? 'false' : 'true';
        button.setAttribute('aria-pressed', String(data.saved));
        button.textContent = data.saved ? button.dataset.unsave : button.dataset.save;
        feedback.textContent = data.message;
        if (form.elements.view.value === 'saved' && !data.saved) window.location.reload();
      } catch (_) {
        feedback.textContent = root.dataset.failure;
      } finally {
        clearTimeout(timer);
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
    });
  });
})();
