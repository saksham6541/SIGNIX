/* The active Flask-Babel catalog is supplied before any application scripts. */
(function () {
  const node = document.getElementById('signix-translations');
  const catalog = node ? JSON.parse(node.textContent) : {};
  const folded = new Map(Object.entries(catalog).map(([key, value]) => [key.toLowerCase(), value]));
  function t(message, values = {}) {
    if (typeof message !== 'string') return message == null ? '' : String(message);
    const translated = Object.prototype.hasOwnProperty.call(catalog, message)
      ? catalog[message] : folded.get(message.toLowerCase()) || message;
    return translated.replace(/%\((\w+)\)s/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
    ));
  }
  window.SignixI18n = Object.freeze({ t, locale: document.documentElement.lang });
})();
