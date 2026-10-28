/* SIGNIX Stage 2 — AI-assisted roof interpretation. */
(function () {
  function setStatus(message, busy) {
    const el = document.getElementById('roof-ai-status');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('is-busy', !!busy);
  }

  function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
      || document.getElementById('assistant-csrf')?.value || '';
  }

  async function analyze() {
    const polygon = typeof ROOF_POLYGON_GEOJSON !== 'undefined' ? ROOF_POLYGON_GEOJSON?.coordinates?.[0] : null;
    if (!polygon || polygon.length < 3) {
      setStatus(window.SignixI18n.t('Drawn roof geometry is unavailable.'), false);
      return;
    }
    const button = document.getElementById('roof-ai-btn');
    if (button) button.disabled = true;
    setStatus(window.SignixI18n.t('AI is inspecting the satellite view…'), true);
    try {
      const locationId = typeof SIGNIX_LOCATION_ID !== 'undefined' ? SIGNIX_LOCATION_ID : null;
      const response = await fetch(locationId
        ? `/api/locations/${encodeURIComponent(locationId)}/rooftop-design/ingest`
        : '/api/roof-ai/analyze', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': csrf(),
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify(locationId
          ? { providers: ['gemini'] }
          : { polygon })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || window.SignixI18n.t('AI roof analysis failed'));
      const geminiSource = Array.isArray(data.sources)
        ? data.sources.find((item) => item.provider === 'gemini')
        : null;
      const analysis = data.analysis || geminiSource?.analysis;
      if (!analysis) {
        const status = geminiSource?.status || 'unavailable';
        throw new Error(`AI roof analysis ${status.replaceAll('_', ' ')}`);
      }
      if (window.SIGNIX_ROOF3D?.stageAIAnalysis) {
        window.SIGNIX_ROOF3D.stageAIAnalysis(analysis);
      }
      const a = analysis;
      const confidence = Math.round((Number(a.confidence) || 0) * 100);
      const objects = Array.isArray(a.obstructions) ? a.obstructions.length : 0;
      const apply = document.getElementById('roof-ai-apply-btn');
      if (apply) { apply.disabled = objects === 0; apply.textContent = objects ? `Apply ${objects} detected structure${objects === 1 ? '' : 's'}` : window.SignixI18n.t('No structures detected'); }
      const pitch = a.pitch_degrees != null ? `${Math.round(a.pitch_degrees)}°` : 'unknown';
      const cacheNote = geminiSource?.cached ? ' · cached result' : '';
      setStatus(`AI analysis: ${a.roof_type || 'unknown'} roof · ${pitch} pitch · ${objects} visible obstruction${objects === 1 ? '' : 's'} · ${confidence}% confidence${cacheNote}`, false);
      const detail = document.getElementById('roof-ai-details');
      if (detail) detail.textContent = window.SignixI18n.t('AI suggestions are advisory; your drawn rooftop boundary remains authoritative.');
    } catch (error) {
      setStatus(error.message || window.SignixI18n.t('AI roof analysis unavailable.'), false);
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    const button = document.getElementById('roof-ai-btn');
    if (button) button.addEventListener('click', analyze);
    const apply = document.getElementById('roof-ai-apply-btn');
    if (apply) apply.addEventListener('click', function () { if (window.SIGNIX_ROOF3D?.applyAIStructures) window.SIGNIX_ROOF3D.applyAIStructures(); });
    // Gemini is advisory and potentially billable, so it runs only on request.
  });
})();
