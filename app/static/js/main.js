
async function applyLocationContext(opts) {
  const params = new URLSearchParams();
  if (opts.state) params.set('state', opts.state);
  if (opts.city) params.set('city', opts.city);
  if (opts.address) params.set('address', opts.address);
  if (opts.pincode) params.set('pincode', opts.pincode);
  if (opts.lat != null) params.set('lat', opts.lat);
  if (opts.lng != null) params.set('lng', opts.lng);
  try {
    const resp = await fetch(`/api/location-context?${params.toString()}`, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data || !data.found) return;

    const stateEl = document.getElementById('state');
    const tariffEl = document.getElementById('tariff-per-kwh');
    const netModeEl = document.getElementById('net-metering-mode');
    const discomEl = document.getElementById('discom-readout');

    if (stateEl && data.state) {
      // Select matching option if present
      const opt = Array.from(stateEl.options).find(
        (o) => o.value.toLowerCase() === String(data.state).toLowerCase()
      );
      if (opt) {
        stateEl.value = opt.value;
        stateEl.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
    const hasConfirmedCommercialTariff =
      data.planning_track !== 'business' || data.tariff?.found === true;
    if (tariffEl && data.tariff && data.tariff.rate_per_kwh != null && hasConfirmedCommercialTariff) {
      tariffEl.value = Number(data.tariff.rate_per_kwh).toFixed(2);
    }
    if (netModeEl && data.tariff && data.tariff.net_metering_mode) {
      netModeEl.value = data.tariff.net_metering_mode;
    }
    if (discomEl) {
      const schemes = (data.central_schemes || [])
        .map((s) => s.name)
        .concat((data.state_schemes || []).map((s) => s.name));
      discomEl.innerHTML =
        `<strong>${data.discom_name || data.discom_code || '—'}</strong>` +
        (data.state ? ` · ${data.state}` : '') +
        (schemes.length ? `<br><span class="discom-schemes">${schemes.join(' · ')}</span>` : '') +
        `<br><span class="discom-note">${data.note || ''}</span>`;
      discomEl.hidden = false;
    }
    SignixFormStore.locationContext = data;
  } catch (err) {
    console.warn('Location context lookup failed', err);
  }
}

/** Lightweight store for estimate form selection state. */
const SignixFormStore = {
  selectedAddress: null,
  selectedLatLng: null,
  selectedLocation: null,
  locationContext: null,
};

// filename: app/static/js/main.js

// -------------------------------------------------- Landing page flow -----

function initSearch() {
  const searchBtn = document.getElementById('search-btn');
  const locateBtn = document.getElementById('locate-btn');
  const input = document.getElementById('address-search');
  const resultsBox = document.getElementById('search-results');
  const mapsInput = document.getElementById('maps-link-input');
  const mapsBtn = document.getElementById('maps-link-btn');
  const clearBtn = document.getElementById('search-clear-btn');
  const statusBox = document.getElementById('location-status');

  if (!searchBtn) return; // not on the landing page

  let searchTimer = null;
  let searchController = null;
  let requestSequence = 0;
  let lastSubmittedQuery = '';
  let composing = false;

  function setStatus(message, tone = 'neutral') {
    if (!statusBox) return;
    statusBox.className = `location-status is-${tone}`;
    const text = statusBox.querySelector('span:last-child');
    if (text) text.textContent = window.SignixI18n.t(message);
  }

  function setButtonBusy(button, busy, busyLabel) {
    if (!button) return;
    button.disabled = busy;
    button.setAttribute('aria-busy', String(busy));
    const label = button.querySelector('span') || button;
    if (!button.dataset.defaultLabel) button.dataset.defaultLabel = label.textContent.trim();
    label.textContent = busy ? window.SignixI18n.t(busyLabel) : button.dataset.defaultLabel;
  }

  function clearResults() {
    if (resultsBox) resultsBox.replaceChildren();
  }

  function selectLocation(location, sourceLabel) {
    const lat = Number(location.lat);
    const lng = Number(location.lon ?? location.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      setStatus(window.SignixI18n.t('That result did not contain valid coordinates. Choose another result.'), 'error');
      return false;
    }
    const address = location.address || location.display_name || location.label || `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
    requestSequence += 1;
    if (searchController) searchController.abort();
    setButtonBusy(searchBtn, false, window.SignixI18n.t('Searching…'));
    SignixFormStore.selectedAddress = address;
    SignixFormStore.selectedLatLng = { lat, lng };
    SignixFormStore.selectedLocation = {
      city: location.city || '',
      locality: location.locality || '',
      pincode: location.postcode || location.pincode || '',
    };
    input.value = address;
    clearBtn?.classList.remove('hidden');
    clearResults();
    applyLocationContext({
      state: location.state,
      city: location.city,
      address,
      pincode: location.postcode,
      lat,
      lng,
    });
    if (typeof setMapCenter === 'function') setMapCenter(lat, lng, 19, address);
    setStatus(`${sourceLabel}: ${address} · ${lat.toFixed(5)}, ${lng.toFixed(5)}`, 'success');
    return true;
  }

  function renderSearchResults(results) {
    clearResults();
    if (!results.length) return;
    const fragment = document.createDocumentFragment();
    results.forEach((result) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'search-result-item';
      button.setAttribute('role', 'option');
      const title = document.createElement('span');
      title.className = 'result-title';
      title.textContent = result.label || result.display_name || window.SignixI18n.t('Unnamed result');
      button.appendChild(title);
      const detailText = result.display_name && result.display_name !== title.textContent
        ? result.display_name
        : (result.type || '');
      if (detailText) {
        const detail = document.createElement('span');
        detail.className = 'result-sub';
        detail.textContent = detailText;
        button.appendChild(detail);
      }
      button.addEventListener('click', () => selectLocation(result, window.SignixI18n.t('Searched location selected')));
      fragment.appendChild(button);
    });
    resultsBox.appendChild(fragment);
  }

  async function applyMapsLink() {
    if (!mapsInput) return;
    const url = mapsInput.value.trim();
    if (!url) {
      setStatus(window.SignixI18n.t('Paste a Google Maps link or latitude, longitude first.'), 'error');
      mapsInput.focus();
      return;
    }
    setButtonBusy(mapsBtn, true, 'Reading…');
    setStatus(window.SignixI18n.t('Reading the shared map location…'), 'loading');
    clearResults();
    try {
      const res = await fetch(`${PARSE_MAPS_URL}?url=${encodeURIComponent(url)}`);
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || window.SignixI18n.t('Could not read that Maps link. Search by address instead.'), 'error');
        return;
      }
      selectLocation(data, window.SignixI18n.t('Shared pin selected'));
    } catch (err) {
      setStatus(window.SignixI18n.t('The Maps link could not be read. Check your connection or search by address.'), 'error');
    } finally {
      setButtonBusy(mapsBtn, false, 'Reading…');
    }
  }

  if (mapsBtn) mapsBtn.addEventListener('click', applyMapsLink);
  if (mapsInput) {
    mapsInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        applyMapsLink();
      }
    });
  }

  async function runSearch() {
    const query = input.value.trim();
    if (!query) {
      setStatus(window.SignixI18n.t('Enter an address, landmark, society, or place to search.'), 'error');
      input.focus();
      return;
    }
    if (query === lastSubmittedQuery && resultsBox.children.length) return;
    lastSubmittedQuery = query;
    const sequence = ++requestSequence;
    if (searchController) searchController.abort();
    const controller = new AbortController();
    let searchTimedOut = false;
    searchController = controller;
    const timeoutId = setTimeout(() => {
      searchTimedOut = true;
      controller.abort();
    }, 12000);
    setButtonBusy(searchBtn, true, window.SignixI18n.t('Searching…'));
    setStatus(window.SignixI18n.t('Searching for “%(query)s”…', { query }), 'loading');
    clearResults();

    try {
      const res = await fetch(`${GEOCODE_URL}?q=${encodeURIComponent(query)}`, {
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      });
      const data = await res.json();
      if (sequence !== requestSequence) return;
      if (!res.ok || !data || !Array.isArray(data.results)) {
        throw new Error((data && data.error) || window.SignixI18n.t('Malformed search response'));
      }
      const list = data.results || [];
      if (!list.length) {
        const message = data.error || window.SignixI18n.t('No matching places found. Add a city, state, postcode, or nearby landmark.');
        setStatus(message, data.source === 'unavailable' ? 'error' : 'empty');
        return;
      }
      renderSearchResults(list);
      setStatus(window.SignixI18n.t('%(count)s location suggestions found. Choose one to move the map.', { count: list.length }), 'neutral');
    } catch (err) {
      if (sequence !== requestSequence) return;
      if (err.name === 'AbortError' && !searchTimedOut) return;
      setStatus(
        searchTimedOut
          ? window.SignixI18n.t('Place search timed out. Try again or use a Maps link.')
          : window.SignixI18n.t('Place search is unavailable. Check your connection, try again, or use another location method.'),
        'error'
      );
    } finally {
      clearTimeout(timeoutId);
      if (sequence === requestSequence) setButtonBusy(searchBtn, false, window.SignixI18n.t('Searching…'));
    }
  }

  function queueSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 250);
  }

  searchBtn.addEventListener('click', queueSearch);
  input.addEventListener('compositionstart', () => { composing = true; });
  input.addEventListener('compositionend', () => { composing = false; });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !composing) {
      e.preventDefault();
      queueSearch();
    }
  });
  input.addEventListener('input', () => {
    clearBtn?.classList.toggle('hidden', !input.value);
    lastSubmittedQuery = '';
  });
  clearBtn?.addEventListener('click', () => {
    requestSequence += 1;
    searchController?.abort();
    clearTimeout(searchTimer);
    setButtonBusy(searchBtn, false, window.SignixI18n.t('Searching…'));
    input.value = '';
    clearBtn.classList.add('hidden');
    clearResults();
    setStatus(window.SignixI18n.t('Search cleared. The current map marker is unchanged until you choose another property.'), 'neutral');
    input.focus();
  });

  locateBtn.addEventListener('click', () => {
    if (!navigator.geolocation) {
      setStatus(window.SignixI18n.t('This browser does not support device location. Search by address or paste a Maps link.'), 'error');
      return;
    }
    if (window.isSecureContext === false && !['localhost', '127.0.0.1'].includes(window.location.hostname)) {
      setStatus(window.SignixI18n.t('Device location requires a secure HTTPS connection. Search by address instead.'), 'error');
      return;
    }
    locateBtn.disabled = true;
    locateBtn.setAttribute('aria-busy', 'true');
    setStatus(window.SignixI18n.t('Requesting your device location…'), 'loading');
    clearResults();
    navigator.geolocation.getCurrentPosition(async (pos) => {
      const { latitude, longitude } = pos.coords;
      let address = `Device location · ${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
      try {
        const res = await fetch(`${REVERSE_GEOCODE_URL}?lat=${latitude}&lon=${longitude}`);
        const data = await res.json();
        if (res.ok && data.address) address = data.address;
      } catch (err) {
        // Exact device coordinates remain valid when address lookup is unavailable.
      } finally {
        selectLocation({ lat: latitude, lon: longitude, address }, window.SignixI18n.t('Device location selected'));
        locateBtn.disabled = false;
        locateBtn.setAttribute('aria-busy', 'false');
      }
    }, (error) => {
      const messages = {
        1: window.SignixI18n.t('Location permission was denied. Enable it in browser settings or search by address.'),
        2: window.SignixI18n.t('Your device location is unavailable. Try again outdoors or search by address.'),
        3: window.SignixI18n.t('Device location timed out. Try again or search by address.'),
      };
      setStatus(messages[error.code] || window.SignixI18n.t('Unable to retrieve your location. Search by address instead.'), 'error');
      locateBtn.disabled = false;
      locateBtn.setAttribute('aria-busy', 'false');
    }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
  });
}

function initSystemOptions() {
  const batteryToggle = document.getElementById('battery-toggle');
  const batteryWrap = document.getElementById('battery-size-wrap');
  const needsBackup = document.getElementById('needs-backup');
  const backupParams = document.getElementById('backup-params-wrap');
  if (batteryToggle && batteryWrap) {
    batteryToggle.addEventListener('change', () => {
      batteryWrap.classList.toggle('hidden', !batteryToggle.checked);
    });
  }
  if (needsBackup && backupParams) {
    needsBackup.addEventListener('change', () => {
      backupParams.classList.toggle('hidden', !needsBackup.checked);
    });
  }
}

function isBusinessAssessmentReady() {
  const fields = document.getElementById('business-assessment-fields');
  if (!fields) return true;
  if (document.getElementById('start-estimation-btn')?.dataset.loading === 'true') return false;
  const bill = document.getElementById('monthly-bill')?.value?.trim();
  const units = document.getElementById('monthly_units_kwh')?.value?.trim();
  if (!bill && !units) return false;
  const billInput = document.getElementById('monthly-bill');
  if (billInput && !billInput.checkValidity()) return false;
  return Array.from(fields.querySelectorAll('input, select')).every(field =>
    field.checkValidity() && !(field.name === 'ppa_rate_rs_per_kwh' && field.value !== '' && Number(field.value) <= 0));
}
window.SignixAssessmentReady = isBusinessAssessmentReady;

function initEstimationButton() {
  const btn = document.getElementById('start-estimation-btn');
  if (!btn) return;
  if (document.getElementById('business-assessment-fields')) {
    const sync = () => { btn.disabled = !getCurrentPolygon() || !isBusinessAssessmentReady(); };
    document.addEventListener('input', sync);
    document.addEventListener('change', sync);
    sync();
  }

  const status = document.getElementById('estimate-action-status');

  function setEstimateStatus(message, tone = 'neutral') {
    if (!status) return;
    status.textContent = window.SignixI18n.t(message);
    status.className = `estimate-action-status is-${tone}`;
  }

  function showEstimateResult(data) {
    const resultPanel = document.getElementById('estimate-result');
    if (!resultPanel) return;

    const ratingHost = document.getElementById('result-suitability-rating');
    if (ratingHost) {
      ratingHost.innerHTML = '';
      const rating = data.suitability_rating;
      if (rating) {
        const viability = rating.overall_viability || {};
        const confidence = rating.data_confidence || {};
        const factors = rating.factors || {};
        const score = Math.max(0, Math.min(100, Number(viability.score || 0)));
        const tier = (viability.tier || 'fair').toLowerCase();
        const confidenceTier = (confidence.tier || 'unknown').replace(/_/g, ' ');
        const priorityLabels = {
          fastest_payback: window.SignixI18n.t('Fastest payback'),
          maximum_savings: window.SignixI18n.t('Maximum long-term savings'),
          environmental_impact: window.SignixI18n.t('Environmental impact'),
          backup_power: window.SignixI18n.t('Backup power'),
          no_preference: window.SignixI18n.t('No strong preference')
        };
        const labels = {
          payback: window.SignixI18n.t('Payback'),
          financial_viability: window.SignixI18n.t('Financial viability'),
          roof_fit: window.SignixI18n.t('Roof fit'),
          orientation: window.SignixI18n.t('Orientation'),
          co2_reduction: window.SignixI18n.t('CO2 reduction'),
          backup_capability: window.SignixI18n.t('Backup capability')
        };
        const detail = (key, factor) => {
          if (key === 'payback') return `${window.SignixI18n.t(factor.tier || 'Unavailable')} ${window.SignixI18n.t('payback period')}`;
          if (key === 'financial_viability') return `${window.SignixI18n.t(factor.tier || 'Unavailable')} ${window.SignixI18n.t('lifetime savings margin')}`;
          if (key === 'roof_fit') return `${factor.usable_area_per_kw ?? '—'} ${window.SignixI18n.t('m² usable area per kW')}`;
          if (key === 'orientation') return `${factor.factor ?? '—'} ${window.SignixI18n.t('relative yield factor')}`;
          if (key === 'co2_reduction') return `${factor.tons ?? '—'} ${window.SignixI18n.t('tons CO2 reduction per year')}`;
          return `${factor.score ?? 0}${window.SignixI18n.t('/100 backup capability')}`;
        };
        const factorMarkup = Object.entries(labels).filter(([key]) => factors[key]).map(([key, label]) => {
          const factor = factors[key];
          const factorScore = Math.max(0, Math.min(100, Number(factor.score || 0)));
          const factorTier = (factor.tier || 'fair').toLowerCase();
          return `<div class="suitability-factor"><div class="factor-heading"><strong>${label}</strong>${factor.tier ? `<span class="factor-tier suitability-${factorTier}">${window.SignixI18n.t(factor.tier.replace(/_/g, ' '))}</span>` : ''}<span class="factor-score">${factorScore}/100</span></div><div class="factor-bar" role="progressbar" aria-valuenow="${factorScore}" aria-valuemin="0" aria-valuemax="100"><span class="factor-bar-fill suitability-${factorTier}" style="width: ${factorScore}%"></span></div><span class="factor-detail">${detail(key, factor)}</span></div>`;
        }).join('');
        ratingHost.innerHTML = `
          <section class="suitability-card">
            <div class="suitability-primary">
              <div class="score-ring-wrap" aria-label="${window.SignixI18n.t('Overall suitability score %(score)s out of 100', { score })}"><svg class="score-ring" viewBox="0 0 100 100" role="img" aria-hidden="true"><circle class="score-ring-track" cx="50" cy="50" r="40"></circle><circle class="score-ring-progress suitability-${tier}" cx="50" cy="50" r="40" pathLength="100" style="stroke-dasharray: ${score} 100"></circle></svg><span class="score-ring-value"><strong>${score}</strong><small>/100</small></span></div>
              <div class="suitability-primary-copy"><span class="suitability-label">${window.SignixI18n.t('Overall suitability')}</span><div class="suitability-badges"><span class="suitability-tier suitability-${tier}">${window.SignixI18n.t((viability.tier || 'Unknown').replace(/_/g, ' '))}</span><span class="confidence-badge">${window.SignixI18n.t('Estimate confidence:')} ${window.SignixI18n.t(confidenceTier)}</span></div>${rating.user_priority ? `<span class="suitability-priority">${window.SignixI18n.t('Priority:')} ${priorityLabels[rating.user_priority] || rating.user_priority.replace(/_/g, ' ')}</span>` : ''}</div>
            </div>
            <details class="suitability-details"><summary>${window.SignixI18n.t('View factor breakdown')}</summary><div class="suitability-factors">${factorMarkup}</div></details>
          </section>`;
      }
    }
    const formatCurrency = value => `₹${Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
    document.getElementById('result-system-size').textContent = Number(data.system_size || 0).toFixed(2);
    const panelEl = document.getElementById('result-panel-count');
    if (panelEl) {
      const n = data.panel_count != null ? data.panel_count
        : (data.panel_estimate && data.panel_estimate.panel_count);
      panelEl.textContent = n != null ? window.SignixI18n.t('approximately %(count)s panels', { count: n }) : '';
    }
    const sizing = data.sizing || null;
    const sizingPanel = document.getElementById('result-sizing-panel');
    if (sizingPanel && sizing) {
      sizingPanel.classList.remove('hidden');
      const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };
      setTxt('result-size-min', Number(sizing.min_kw || 0).toFixed(2));
      setTxt('result-size-recommended', Number(sizing.recommended_kw || data.system_size || 0).toFixed(2));
      setTxt('result-size-max', Number(sizing.max_kw || 0).toFixed(2));
      setTxt('result-sizing-mode', sizing.mode ? window.SignixI18n.t('Mode: %(mode)s', { mode: window.SignixI18n.t(String(sizing.mode).replace(/_/g, ' ')) }) : '');
      const rat = document.getElementById('result-sizing-rationale');
      if (rat) {
        rat.innerHTML = '';
        (sizing.rationale || []).forEach(line => {
          const li = document.createElement('li');
          li.textContent = window.SignixI18n.t(line);
          rat.appendChild(li);
        });
      }
      const cons = document.getElementById('result-sizing-constraints');
      if (cons) {
        const list = sizing.constraints || [];
        cons.textContent = list.length ? window.SignixI18n.t('Constraints: %(items)s', { items: list.map(item => window.SignixI18n.t(item)).join(' · ') }) : '';
      }
    } else if (sizingPanel) {
      sizingPanel.classList.add('hidden');
    }
    const battLine = document.getElementById('result-battery-line');
    if (battLine) {
      const bk = Number(data.battery_kwh || 0);
      const bs = data.battery_sizing || {};
      if (bk > 0) {
        let msg = window.SignixI18n.t('Battery: %(capacity)s kWh', { capacity: bk.toFixed(2) });
        if (bs.critical_load_kw && bs.backup_hours) {
          msg += window.SignixI18n.t(' (auto-sized for %(load)s kW × %(hours)s h)', { load: bs.critical_load_kw, hours: bs.backup_hours });
        } else if (bs.note) {
          msg += ` — ${window.SignixI18n.t(bs.note)}`;
        }
        if (data.battery_cost) {
          msg += ` · ~₹${Number(data.battery_cost).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
        }
        battLine.textContent = msg;
        battLine.classList.remove('hidden');
      } else {
        battLine.textContent = '';
        battLine.classList.add('hidden');
      }
    }
    document.getElementById('result-annual-generation').textContent = Number(data.annual_generation || 0).toLocaleString();
    const isBusinessTrack = data.planning_track === 'business';
    document.getElementById('result-monthly-savings').textContent = formatCurrency(
      isBusinessTrack ? data.annual_net_benefit : data.monthly_savings
    );
    const annualNetEl = document.getElementById('result-annual-net-benefit');
    if (annualNetEl) annualNetEl.textContent = formatCurrency(data.annual_net_benefit);
    const annualRoiEl = document.getElementById('result-annual-roi');
    if (annualRoiEl) {
      annualRoiEl.textContent = data.annual_roi_pct != null
        ? `${Number(data.annual_roi_pct).toFixed(2)}%`
        : window.SignixI18n.t('Unavailable');
    }
    const lifetimeNetEl = document.getElementById('result-lifetime-net-benefit');
    if (lifetimeNetEl) {
      lifetimeNetEl.textContent = data.lifetime_net_benefit != null
        ? formatCurrency(data.lifetime_net_benefit)
        : window.SignixI18n.t('Unavailable');
    }
    document.getElementById('result-co2-reduction').textContent = Number(data.co2_reduction_tons || 0).toFixed(2);
    const nominalPayback = data.nominal_payback_years != null
      ? data.nominal_payback_years
      : data.payback_years;
    document.getElementById('result-payback').textContent = nominalPayback != null
      ? Number(nominalPayback).toFixed(1)
      : window.SignixI18n.t('Indicative estimate');
    const simpleEl = document.getElementById('result-payback-simple');
    if (simpleEl) {
      const simplePayback = data.simple_payback_years;
      simpleEl.textContent = simplePayback != null
        ? window.SignixI18n.t('Simple (flat tariff): %(years)s yrs', { years: Number(simplePayback).toFixed(1) })
        : window.SignixI18n.t('Simple (flat tariff): —');
    }
    document.getElementById('result-system-cost').textContent = formatCurrency(data.system_cost);
    document.getElementById('result-subsidy').textContent = `-${formatCurrency(data.subsidy_amount)}`;
    document.getElementById('result-net-investment').textContent = formatCurrency(data.net_investment);
    const subsidyNote = document.getElementById('result-subsidy-note');
    if (subsidyNote) {
      const caveat = data.subsidy_caveat
        || window.SignixI18n.t('Central subsidy estimate — final amount subject to MNRE / DISCOM approval');
      const assumption = data.subsidy_eligibility_assumption
        || window.SignixI18n.t('Assumes residential, grid-connected system');
      subsidyNote.textContent = `${window.SignixI18n.t(caveat)} ${window.SignixI18n.t(assumption)}`;
    }
    const caveatEl = document.getElementById('result-subsidy-caveat');
    if (caveatEl && data.subsidy_caveat) caveatEl.textContent = window.SignixI18n.t(data.subsidy_caveat);
    const eligEl = document.getElementById('result-subsidy-eligibility');
    if (eligEl && data.subsidy_eligibility_assumption) {
      eligEl.textContent = window.SignixI18n.t(data.subsidy_eligibility_assumption);
    }
    document.getElementById('result-area-summary').textContent =
      window.SignixI18n.t('Usable roof area: %(usable)s m² of %(total)s m² total', {
        usable: Number(data.usable_area_sqm || 0).toFixed(2), total: Number(data.roof_area_sqm || 0).toFixed(2),
      });
    const sourceEl = document.getElementById('estimate-source');
    const sourceNames = {
      nasa_power: 'NASA POWER',
      pvgis: 'PVGIS',
      mock: 'Mock',
      mock_fallback: 'Mock',
      estimated: 'Mock'
    };
    const confTier = (
      data.irradiance_confidence
      || (data.suitability_rating && data.suitability_rating.data_confidence
          && data.suitability_rating.data_confidence.tier)
      || ''
    ).toLowerCase();
    const sourceKey = data.irradiance_source || '';
    const sourceName = sourceNames[sourceKey] || sourceKey || '';
    if (sourceEl) {
      sourceEl.classList.remove(
        'data-conf-high', 'data-conf-medium', 'data-conf-low', 'data-conf-unknown', 'hidden'
      );
      if (sourceName) {
        let confText = '';
        let confClass = 'data-conf-unknown';
        if (confTier === 'high') {
          confText = window.SignixI18n.t(' · High confidence');
          confClass = 'data-conf-high';
        } else if (confTier === 'medium') {
          confText = window.SignixI18n.t(' · Medium confidence');
          confClass = 'data-conf-medium';
        } else if (confTier === 'low') {
          confText = window.SignixI18n.t(' · Low confidence — verify locally');
          confClass = 'data-conf-low';
        }
        sourceEl.textContent = window.SignixI18n.t('Data: %(source)s%(confidence)s', { source: sourceName, confidence: confText });
        sourceEl.classList.add(confClass);
      } else {
        sourceEl.textContent = '';
        sourceEl.classList.add('hidden', 'data-conf-unknown');
      }
    }
    const sourceNote = document.getElementById('estimate-source-note');
    if (sourceNote) {
      const isFallback = sourceKey === 'mock_fallback'
        || sourceKey === 'mock'
        || sourceKey === 'estimated'
        || confTier === 'low';
      sourceNote.textContent = isFallback
        ? window.SignixI18n.t('Live irradiance services were unavailable. This result uses a rough location-adjusted estimate.')
        : '';
      sourceNote.classList.toggle('hidden', !isFallback);
    }

    const reportLink = document.getElementById('view-full-report');
    if (reportLink && data.redirect_url) reportLink.href = data.redirect_url;
    resultPanel.classList.remove('hidden');
  }

  btn.addEventListener('click', async () => {
    const polygon = getCurrentPolygon();
    const obstructions = typeof getCurrentObstructions === 'function' ? getCurrentObstructions() : [];
    if (!polygon) {
      setEstimateStatus(window.SignixI18n.t('Draw a rooftop boundary on the map before starting the estimate.'), 'error');
      document.getElementById('map-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (!SignixFormStore.selectedLatLng) {
      // Fall back to the polygon's own centroid if no address was searched.
      const avgLat = polygon.reduce((s, p) => s + p[0], 0) / polygon.length;
      const avgLng = polygon.reduce((s, p) => s + p[1], 0) / polygon.length;
      SignixFormStore.selectedLatLng = { lat: avgLat, lng: avgLng };
    }
    if (!SignixFormStore.selectedAddress) {
      SignixFormStore.selectedAddress = document.getElementById('address-search').value || 'Selected rooftop';
    }

    const orientationSelect = document.getElementById('orientation-select');
    const orientationValue = orientationSelect ? orientationSelect.value : 'auto';
    const batteryEnabled = document.getElementById('battery-toggle')?.checked || false;
    const needsBackup = document.getElementById('needs-backup')?.checked || false;
    // Manual capacity only when toggle is on; otherwise 0 so backend can auto-size when needs_backup.
    const batteryKwh = batteryEnabled
      ? parseFloat(document.getElementById('battery-size')?.value || '10')
      : 0;
    const criticalLoadKw = needsBackup
      ? parseFloat(document.getElementById('critical-load-kw')?.value || '1')
      : null;
    const backupHours = needsBackup
      ? parseFloat(document.getElementById('backup-hours')?.value || '4')
      : null;
    const sizingMode = document.getElementById('sizing-mode')?.value || null;
    const loadProfile = document.getElementById('load-profile')?.value || 'flat';
    const netMeteringMode = document.getElementById('net-metering-mode')?.value || 'full_offset';
    const monthlyBillRaw = document.getElementById('monthly-bill')?.value;
    const monthlyBill = monthlyBillRaw !== undefined && monthlyBillRaw !== ''
      ? parseFloat(monthlyBillRaw)
      : null;
    const tariffRaw = document.getElementById('tariff-per-kwh')?.value;
    const tariffPerKwh = tariffRaw !== undefined && tariffRaw !== ''
      ? parseFloat(tariffRaw)
      : null;
    const state = document.getElementById('state')?.value || null;
    const propertyType = document.getElementById('property-type')?.value || 'residential';
    const userPriority = document.getElementById('user-priority')?.value || 'no_preference';
    const inverterPreference = document.getElementById('inverter-preference')?.value || 'auto';

    if (!isBusinessAssessmentReady()) {
      setEstimateStatus(window.SignixI18n.t('Enter a monthly bill or monthly units, and check the finance inputs.'), 'error');
      return;
    }
    document.getElementById('loading-panel').classList.remove('hidden');
    btn.dataset.loading = 'true';
    btn.disabled = true;
    setEstimateStatus(window.SignixI18n.t('Generating the planning estimate…'), 'loading');

    try {
      const csrfToken =
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
        document.getElementById('assistant-csrf')?.value ||
        '';
      const businessFields = document.getElementById('business-assessment-fields');
      const preferences = {};
      businessFields?.querySelectorAll('[data-business-field]').forEach(field => {
        preferences[field.name] = field.type === 'checkbox' ? field.checked : field.value;
      });
      const res = await fetch(businessFields?.dataset.estimateUrl || ESTIMATE_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({
          ...preferences,
          address: SignixFormStore.selectedAddress,
          latitude: SignixFormStore.selectedLatLng.lat,
          longitude: SignixFormStore.selectedLatLng.lng,
          polygon: polygon,
          obstructions: obstructions,
          state: state,
          tariff_per_kwh: tariffPerKwh,
          orientation: orientationValue,
          battery_kwh: batteryKwh,
          monthly_bill: monthlyBill,
          sizing_mode: sizingMode || null,
          load_profile: loadProfile,
          net_metering_mode: netMeteringMode,
          needs_backup: needsBackup,
          critical_load_kw: criticalLoadKw,
          backup_hours: backupHours,
          property_type: propertyType,
          user_priority: userPriority,
          inverter_preference: inverterPreference,
          location_context: {
            city: SignixFormStore.selectedLocation?.city || SignixFormStore.locationContext?.city || '',
            locality: SignixFormStore.selectedLocation?.locality || SignixFormStore.locationContext?.locality || '',
            pincode: SignixFormStore.selectedLocation?.pincode || SignixFormStore.locationContext?.pincode || ''
          }
        })
      });
      let data;
      const rawText = await res.text();
      try {
        data = rawText ? JSON.parse(rawText) : {};
      } catch (parseErr) {
        setEstimateStatus(`The estimate service returned an unreadable response (HTTP ${res.status}). Please retry.`, 'error');
        return;
      }
      if (res.status === 401) {
        setEstimateStatus(data.error || window.SignixI18n.t('Please log in again, then retry the estimate.'), 'error');
        if (data.login_url) window.location.href = data.login_url;
        return;
      }
      if (!res.ok) {
        setEstimateStatus(data.error || (`Estimation failed (HTTP ${res.status}).`), 'error');
        return;
      }
      if (data.redirect_url) {
        showEstimateResult(data);
        const businessLink = document.getElementById('business-result-link');
        if (businessLink && data.business_result_url) {
          businessLink.href = data.business_result_url;
          businessLink.classList.remove('hidden');
        }
        setEstimateStatus(window.SignixI18n.t('Estimate complete. Review the snapshot below or open the full report.'), 'success');
        document.getElementById('estimate-result')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        setEstimateStatus(data.error || window.SignixI18n.t('The estimate could not be generated. Please retry.'), 'error');
      }
    } catch (err) {
      console.error(err);
      setEstimateStatus(`Estimation failed: ${err && err.message ? err.message : 'network or server error.'}`, 'error');
    } finally {
      document.getElementById('loading-panel').classList.add('hidden');
      delete btn.dataset.loading;
      btn.disabled = !getCurrentPolygon() || !isBusinessAssessmentReady();
    }
  });
}

// -------------------------------------------------------- Report page -----
function initTabs() {
  const tabButtons = Array.from(document.querySelectorAll('.tab-btn[role="tab"]'));
  if (!tabButtons.length) return;

  function activateTab(nextTab, moveFocus = false) {
    tabButtons.forEach((button) => {
      const selected = button === nextTab;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(button.getAttribute('aria-controls'));
      if (panel) {
        panel.classList.toggle('active', selected);
        panel.hidden = !selected;
      }
    });
    if (moveFocus) nextTab.focus();
    // Charts in previously-hidden panels need a resize after becoming visible.
    requestAnimationFrame(() => window.SIGNIX_REPORT_CHARTS?.resize?.());
  }

  tabButtons.forEach((btn, index) => {
    btn.addEventListener('click', () => activateTab(btn));
    btn.addEventListener('keydown', (event) => {
      let nextIndex = null;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabButtons.length;
      if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabButtons.length) % tabButtons.length;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = tabButtons.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      activateTab(tabButtons[nextIndex], true);
    });
  });
}


function initDialControls() {
  const panel = document.getElementById('dial-panel');
  if (!panel) return;

  const baseSystemKw = Number(typeof SYSTEM_SIZE !== 'undefined' ? SYSTEM_SIZE : 0) || 1;
  const baseGen = Number(typeof ANNUAL_GENERATION !== 'undefined' ? ANNUAL_GENERATION : 0) || 0;
  const baseSavings = Number(typeof MONTHLY_SAVINGS !== 'undefined' ? MONTHLY_SAVINGS : 0) || 0;
  const basePanels = Number(typeof PANEL_COUNT !== 'undefined' ? PANEL_COUNT : 0) || Math.round(baseSystemKw * 1000 / 540);

  const defaults = {
    systemSize: baseSystemKw,
    roofArea: Number(document.getElementById('range-roof-area')?.value) || 50,
    daytimePct: Number(document.getElementById('range-daytime-pct')?.value) || 75,
    shading: 0,
    units: Number(document.getElementById('range-units')?.value) || 0,
    tariff: Number(document.getElementById('range-tariff')?.value) || 0,
    pitch: 0,
  };

  const state = { ...defaults };

  function fmtNum(n, digits) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-IN', { maximumFractionDigits: digits != null ? digits : 0 });
  }

  function syncReadouts() {
    const el = (id, text) => { const n = document.getElementById(id); if (n) n.textContent = text; };
    el('dial-system-size', fmtNum(state.systemSize, 1));
    el('dial-roof-area', fmtNum(state.roofArea, 0));
    el('dial-daytime-pct', fmtNum(state.daytimePct, 0));
    el('dial-shading', fmtNum(state.shading, 0));
    if (document.getElementById('dial-units')) el('dial-units', fmtNum(state.units, 0));
    if (document.getElementById('dial-tariff')) el('dial-tariff', fmtNum(state.tariff, 2));

    // Indicative impact: scale generation by system size & shading; savings by gen + daytime + tariff
    const sizeFactor = state.systemSize / Math.max(baseSystemKw, 0.1);
    const shadeFactor = 1 - (state.shading / 100);
    const dayFactor = state.daytimePct / Math.max(defaults.daytimePct, 1);
    let tariffFactor = 1;
    if (defaults.tariff > 0 && state.tariff > 0) tariffFactor = state.tariff / defaults.tariff;

    const indGen = baseGen * sizeFactor * shadeFactor;
    const indSavings = baseSavings * sizeFactor * shadeFactor * dayFactor * tariffFactor;
    const indPanels = Math.max(1, Math.round(basePanels * sizeFactor));

    el('dial-impact-gen', fmtNum(indGen, 0) + ' kWh');
    el('dial-impact-savings', '₹' + fmtNum(indSavings, 0));
    el('dial-impact-panels', String(indPanels));
    const pcl = document.getElementById('panel-count-label');
    if (pcl) pcl.textContent = window.SignixI18n.t('Showing approximately ') + indPanels + ' panels.';

    if (window.SIGNIX_ROOF3D) {
      window.SIGNIX_ROOF3D.setPitchAndPanels(state.pitch, indPanels);
    }
  }

  function bindRange(rangeId, key, parseFn) {
    const range = document.getElementById(rangeId);
    if (!range) return;
    const card = range.closest('.dial-card');
    const minus = card && card.querySelector('.dial-minus');
    const plus = card && card.querySelector('.dial-plus');
    const step = Number(range.step) || 1;
    const apply = () => {
      state[key] = parseFn ? parseFn(range.value) : Number(range.value);
      syncReadouts();
    };
    range.addEventListener('input', apply);
    if (minus) minus.addEventListener('click', () => {
      range.value = String(Math.max(Number(range.min), Number(range.value) - step));
      apply();
    });
    if (plus) plus.addEventListener('click', () => {
      range.value = String(Math.min(Number(range.max), Number(range.value) + step));
      apply();
    });
  }

  bindRange('range-system-size', 'systemSize', (v) => Number(v));
  bindRange('range-roof-area', 'roofArea', (v) => Number(v));
  bindRange('range-daytime-pct', 'daytimePct', (v) => Number(v));
  bindRange('range-shading', 'shading', (v) => Number(v));
  bindRange('range-units', 'units', (v) => Number(v));
  bindRange('range-tariff', 'tariff', (v) => Number(v));

  const pitchSelect = document.getElementById('roof-pitch-select');
  if (pitchSelect) {
    pitchSelect.addEventListener('change', () => {
      state.pitch = Number(pitchSelect.value) || 0;
      syncReadouts();
    });
  }

  const undoBtn = document.getElementById('roof-undo-btn');
  if (undoBtn) {
    undoBtn.addEventListener('click', () => {
      Object.assign(state, defaults);
      state.pitch = 0;
      const setRange = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = String(val);
      };
      setRange('range-system-size', defaults.systemSize);
      setRange('range-roof-area', defaults.roofArea);
      setRange('range-daytime-pct', defaults.daytimePct);
      setRange('range-shading', 0);
      setRange('range-units', defaults.units);
      setRange('range-tariff', defaults.tariff);
      if (pitchSelect) pitchSelect.value = '0';
      syncReadouts();
    });
  }

  syncReadouts();
}

document.addEventListener('DOMContentLoaded', () => {
  initSearch();
  initSystemOptions();
  initEstimationButton();
  initTabs();
  window.SIGNIX_REPORT_CHARTS?.init?.();
  initDialControls();
});

(function wireStateTariffDefaults() {
  const stateEl = document.getElementById('state');
  const tariffEl = document.getElementById('tariff-per-kwh');
  const netModeEl = document.getElementById('net-metering-mode');
  if (!stateEl) return;

  async function applyStateDefaults() {
    const state = stateEl.value;
    if (!state) return;
    try {
      const resp = await fetch(`/api/tariff?state=${encodeURIComponent(state)}`, {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
      });
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data || !data.found) return;
      if (tariffEl && data.rate_per_kwh != null) {
        tariffEl.value = Number(data.rate_per_kwh).toFixed(2);
        tariffEl.dispatchEvent(new Event('change', { bubbles: true }));
      }
      if (netModeEl && data.net_metering_mode) {
        netModeEl.value = data.net_metering_mode;
        netModeEl.dispatchEvent(new Event('change', { bubbles: true }));
      }
      // Surface export credit as optional feed-in hint via title/tooltip on net mode
      if (netModeEl && data.export_credit_rate != null) {
        netModeEl.title = `State default export credit ≈ ₹${Number(data.export_credit_rate).toFixed(2)}/kWh`;
      }
    } catch (err) {
      console.warn('State tariff lookup failed', err);
    }
  }

  stateEl.addEventListener('change', applyStateDefaults);
})();
