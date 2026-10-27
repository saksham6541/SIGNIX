/* SIGNIX saved-scenario selection. Server-side validation remains authoritative. */
(function () {
  const form = document.querySelector('[data-compare-picker]');
  if (!form) return;

  const inputs = Array.from(form.querySelectorAll('input[name="location_ids"]'));
  const submit = form.querySelector('.compare-submit');
  const count = form.querySelector('[data-selected-count]');
  const guidance = document.getElementById('compare-selection-guidance');
  const minimum = Number(form.dataset.minSelections) || 2;
  const maximum = Number(form.dataset.maxSelections) || 3;

  function selectedInputs() {
    return inputs.filter(input => input.checked);
  }

  function guidanceMessage(selectedCount) {
    if (selectedCount >= maximum) return form.dataset.maximumMessage;
    if (selectedCount >= minimum) return form.dataset.readyMessage;
    if (selectedCount === 1) return form.dataset.oneMessage;
    return form.dataset.emptyMessage;
  }

  function syncSelection() {
    const selected = selectedInputs();
    const selectedCount = selected.length;
    const atMaximum = selectedCount >= maximum;

    inputs.forEach(input => {
      const card = input.closest('[data-scenario-option]');
      const selectionLabel = card?.querySelector('[data-selection-label]');
      card?.classList.toggle('is-selected', input.checked);
      input.disabled = atMaximum && !input.checked;
      card?.classList.toggle('is-disabled', input.disabled);
      card?.setAttribute('aria-disabled', String(input.disabled));
      input.setAttribute(
        'aria-label',
        `${input.checked ? form.dataset.deselectLabel : form.dataset.selectLabel} ${input.dataset.scenarioName}`,
      );
      if (selectionLabel) {
        selectionLabel.textContent = input.checked ? form.dataset.selectedLabel : form.dataset.selectLabel;
      }
    });

    if (count) count.textContent = String(selectedCount);
    if (guidance) guidance.textContent = guidanceMessage(selectedCount);
    if (submit) submit.disabled = selectedCount < minimum || selectedCount > maximum;
  }

  inputs.forEach(input => {
    input.addEventListener('change', () => {
      if (selectedInputs().length > maximum) input.checked = false;
      syncSelection();
    });

    input.closest('[data-scenario-option]')?.addEventListener('click', event => {
      if (!input.disabled || event.target === input) return;
      event.preventDefault();
      if (guidance) guidance.textContent = form.dataset.maximumMessage;
    });
  });

  form.addEventListener('submit', event => {
    const selectedCount = selectedInputs().length;
    if (selectedCount >= minimum && selectedCount <= maximum) return;
    event.preventDefault();
    if (guidance) guidance.textContent = guidanceMessage(selectedCount);
  });

  syncSelection();
})();
