/* SIGNIX detailed-report tabs. Keeps existing report controls mounted between views. */
(function (global, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (!global || !global.document) return;

  global.SIGNIX_REPORT_TABS = api;
  const initialize = () => api.initReportTabs(global.document, global);
  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }
})(typeof window !== 'undefined' ? window : null, function () {
  function nextTabIndex(currentIndex, key, count) {
    if (!count) return -1;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    if (key === 'ArrowRight') return (currentIndex + 1) % count;
    if (key === 'ArrowLeft') return (currentIndex - 1 + count) % count;
    return currentIndex;
  }

  function createReportTabs(root, environment) {
    if (!root) return null;
    if (root.__signixReportTabs) return root.__signixReportTabs;

    const view = environment || root.ownerDocument?.defaultView;
    const doc = root.ownerDocument || view?.document;
    const tabs = Array.from(root.querySelectorAll('[role="tab"]'));
    const entries = tabs.map(tab => ({
      tab,
      panel: doc?.getElementById(tab.getAttribute('aria-controls')),
    })).filter(entry => entry.panel);
    if (!entries.length) return null;

    function refreshVisiblePanel(panel) {
      const refresh = () => {
        view?.SIGNIX_REPORT_CHARTS?.resize?.();
        view?.dispatchEvent?.(new view.Event('resize'));
        panel.dispatchEvent?.(new view.CustomEvent('signix:report-panel-shown', {
          bubbles: true,
          detail: { panelId: panel.id },
        }));
      };
      if (view?.requestAnimationFrame) view.requestAnimationFrame(refresh);
      else refresh();
    }

    function activate(tab, moveFocus) {
      const activeEntry = entries.find(entry => entry.tab === tab) || entries[0];
      entries.forEach(entry => {
        const selected = entry === activeEntry;
        entry.tab.setAttribute('aria-selected', String(selected));
        entry.tab.tabIndex = selected ? 0 : -1;
        entry.panel.hidden = !selected;
      });
      root.classList.add('is-ready');
      activeEntry.tab.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
      if (moveFocus) activeEntry.tab.focus();
      refreshVisiblePanel(activeEntry.panel);
      return activeEntry.panel;
    }

    entries.forEach((entry, index) => {
      entry.tab.addEventListener('click', () => activate(entry.tab, false));
      entry.tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        activate(entries[nextTabIndex(index, event.key, entries.length)].tab, true);
      });
    });

    const selected = entries.find(entry => entry.tab.getAttribute('aria-selected') === 'true') || entries[0];
    const controller = { activate, tabs: entries.map(entry => entry.tab), panels: entries.map(entry => entry.panel) };
    root.__signixReportTabs = controller;
    activate(selected.tab, false);
    return controller;
  }

  function initReportTabs(doc, environment) {
    return createReportTabs(doc?.querySelector('[data-report-tabs]'), environment);
  }

  return { createReportTabs, initReportTabs, nextTabIndex };
});
