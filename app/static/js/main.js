// filename: app/static/js/main.js

// -------------------------------------------------- Landing page flow -----
let selectedAddress = null;
let selectedLatLng = null;

function initSearch() {
  const searchBtn = document.getElementById('search-btn');
  const locateBtn = document.getElementById('locate-btn');
  const input = document.getElementById('address-search');
  const resultsBox = document.getElementById('search-results');
  const mapsInput = document.getElementById('maps-link-input');
  const mapsBtn = document.getElementById('maps-link-btn');

  if (!searchBtn) return; // not on the landing page

  async function applyMapsLink() {
    if (!mapsInput) return;
    const url = mapsInput.value.trim();
    if (!url) {
      alert('Paste a Google Maps link first.');
      return;
    }
    if (resultsBox) resultsBox.innerHTML = '<p class="search-status">Reading Google Maps link…</p>';
    try {
      const res = await fetch(`${PARSE_MAPS_URL}?url=${encodeURIComponent(url)}`);
      const data = await res.json();
      if (!res.ok) {
        if (resultsBox) resultsBox.innerHTML = '';
        alert(data.error || 'Could not read that Maps link.');
        return;
      }
      const lat = Number(data.lat);
      const lon = Number(data.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        alert('Invalid coordinates from Maps link.');
        return;
      }
      selectedLatLng = { lat, lng: lon };
      selectedAddress = data.address || (lat + ', ' + lon);
      if (input) input.value = selectedAddress;
      if (typeof setMapCenter === 'function') {
        setMapCenter(lat, lon, 19);
        // Re-center after a tick in case the map was still settling
        setTimeout(() => setMapCenter(lat, lon, 19), 400);
      }
      if (resultsBox) {
        resultsBox.innerHTML =
          '<p class="search-status">Pinned at ' + lat.toFixed(5) + ', ' + lon.toFixed(5) +
          ' — draw your rooftop around the blue marker.</p>';
      }
    } catch (err) {
      if (resultsBox) resultsBox.innerHTML = '';
      alert('Failed to open Maps link. Check your connection and try again.');
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
    if (!query) return;
    resultsBox.innerHTML = '<p class="search-status">Searching for places…</p>';

    try {
      const res = await fetch(`${GEOCODE_URL}?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      const list = data.results || [];
      if (data.source === 'mock_fallback') {
        resultsBox.innerHTML =
          '<p class="search-status">Live geocoding unavailable. Showing a fallback — try a fuller address (society / road / city).</p>';
      }
      renderSearchResults(list, data.source);
    } catch (err) {
      resultsBox.innerHTML = '<p class="search-status">Could not search right now. Check your connection and try again.</p>';
    }
  }

  searchBtn.addEventListener('click', runSearch);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      runSearch();
    }
  });

  locateBtn.addEventListener('click', () => {
    if (!navigator.geolocation) {
      alert('Geolocation is not supported by this browser.');
      return;
    }
    resultsBox.innerHTML = '<p class="search-status">Getting your location…</p>';
    navigator.geolocation.getCurrentPosition(async (pos) => {
      const { latitude, longitude } = pos.coords;
      selectedLatLng = { lat: latitude, lng: longitude };
      if (typeof setMapCenter === 'function') {
        setMapCenter(latitude, longitude, 19);
      }

      try {
        const res = await fetch(`${REVERSE_GEOCODE_URL}?lat=${latitude}&lon=${longitude}`);
        const data = await res.json();
        selectedAddress = data.address;
        input.value = selectedAddress;
        resultsBox.innerHTML = '';
      } catch (err) {
        selectedAddress = 'Current location';
        input.value = selectedAddress;
        resultsBox.innerHTML = '';
      }
    }, () => {
      resultsBox.innerHTML = '';
      alert('Unable to retrieve your location. Allow location access or search by address.');
    }, { enableHighAccuracy: true, timeout: 12000 });
  });

  function renderSearchResults(results, source) {
    if (!results.length) {
      resultsBox.innerHTML =
        '<p class="search-status">No results found. Try adding city/state (e.g. “GL Bajaj Greater Noida”) or a nearby landmark.</p>';
      return;
    }
    resultsBox.innerHTML = '';
    if (source) {
      const meta = document.createElement('p');
      meta.className = 'search-status';
      meta.textContent = results.length + ' place(s) found' + (source && source !== 'mock_fallback' ? '' : '');
      resultsBox.appendChild(meta);
    }
    results.forEach(r => {
      const div = document.createElement('div');
      div.className = 'search-result-item';
      const title = r.label || r.display_name;
      const sub = (r.label && r.display_name && r.label !== r.display_name)
        ? r.display_name
        : (r.type ? String(r.type) : '');
      div.innerHTML =
        '<div class="result-title"></div>' +
        (sub ? '<div class="result-sub"></div>' : '');
      div.querySelector('.result-title').textContent = title;
      if (sub) div.querySelector('.result-sub').textContent = sub;
      div.title = r.display_name || title;
      div.addEventListener('click', () => {
        selectedAddress = r.display_name || title;
        selectedLatLng = { lat: r.lat, lng: r.lon };
        input.value = selectedAddress;
        if (typeof setMapCenter === 'function') {
          setMapCenter(r.lat, r.lon, 19);
        }
        resultsBox.innerHTML = '';
      });
      resultsBox.appendChild(div);
    });
  }
}

function initSystemOptions() {
  const batteryToggle = document.getElementById('battery-toggle');
  const batteryWrap = document.getElementById('battery-size-wrap');
  if (batteryToggle && batteryWrap) {
    batteryToggle.addEventListener('change', () => {
      batteryWrap.classList.toggle('hidden', !batteryToggle.checked);
    });
  }
}

function initEstimationButton() {
  const btn = document.getElementById('start-estimation-btn');
  if (!btn) return;

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
          fastest_payback: 'Fastest payback',
          maximum_savings: 'Maximum long-term savings',
          environmental_impact: 'Environmental impact',
          backup_power: 'Backup power',
          no_preference: 'No strong preference'
        };
        const labels = {
          payback: 'Payback',
          financial_viability: 'Financial viability',
          roof_fit: 'Roof fit',
          orientation: 'Orientation',
          co2_reduction: 'CO2 reduction',
          backup_capability: 'Backup capability'
        };
        const detail = (key, factor) => {
          if (key === 'payback') return `${factor.tier || 'Unavailable'} payback period`;
          if (key === 'financial_viability') return `${factor.tier || 'Unavailable'} lifetime savings margin`;
          if (key === 'roof_fit') return `${factor.usable_area_per_kw ?? '—'} m² usable area per kW`;
          if (key === 'orientation') return `${factor.factor ?? '—'} relative yield factor`;
          if (key === 'co2_reduction') return `${factor.tons ?? '—'} tons CO2 reduction per year`;
          return `${factor.score ?? 0}/100 backup capability`;
        };
        const factorMarkup = Object.entries(labels).filter(([key]) => factors[key]).map(([key, label]) => {
          const factor = factors[key];
          const factorScore = Math.max(0, Math.min(100, Number(factor.score || 0)));
          const factorTier = (factor.tier || 'fair').toLowerCase();
          return `<div class="suitability-factor"><div class="factor-heading"><strong>${label}</strong>${factor.tier ? `<span class="factor-tier suitability-${factorTier}">${factor.tier.replace(/_/g, ' ')}</span>` : ''}<span class="factor-score">${factorScore}/100</span></div><div class="factor-bar" role="progressbar" aria-valuenow="${factorScore}" aria-valuemin="0" aria-valuemax="100"><span class="factor-bar-fill suitability-${factorTier}" style="width: ${factorScore}%"></span></div><span class="factor-detail">${detail(key, factor)}</span></div>`;
        }).join('');
        ratingHost.innerHTML = `
          <section class="suitability-card">
            <div class="suitability-primary">
              <div class="score-ring-wrap" aria-label="Overall suitability score ${score} out of 100"><svg class="score-ring" viewBox="0 0 100 100" role="img" aria-hidden="true"><circle class="score-ring-track" cx="50" cy="50" r="40"></circle><circle class="score-ring-progress suitability-${tier}" cx="50" cy="50" r="40" pathLength="100" style="stroke-dasharray: ${score} 100"></circle></svg><span class="score-ring-value"><strong>${score}</strong><small>/100</small></span></div>
              <div class="suitability-primary-copy"><span class="suitability-label">Overall suitability</span><div class="suitability-badges"><span class="suitability-tier suitability-${tier}">${(viability.tier || 'Unknown').replace(/_/g, ' ')}</span><span class="confidence-badge">Estimate confidence: ${confidenceTier}</span></div>${rating.user_priority ? `<span class="suitability-priority">Priority: ${priorityLabels[rating.user_priority] || rating.user_priority.replace(/_/g, ' ')}</span>` : ''}</div>
            </div>
            <details class="suitability-details"><summary>View factor breakdown</summary><div class="suitability-factors">${factorMarkup}</div></details>
          </section>`;
      }
    }
    const formatCurrency = value => `₹${Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
    document.getElementById('result-system-size').textContent = Number(data.system_size || 0).toFixed(2);
    document.getElementById('result-annual-generation').textContent = Number(data.annual_generation || 0).toLocaleString();
    document.getElementById('result-monthly-savings').textContent = formatCurrency(data.monthly_savings);
    document.getElementById('result-co2-reduction').textContent = Number(data.co2_reduction_tons || 0).toFixed(2);
    document.getElementById('result-payback').textContent = data.payback_years
      ? Number(data.payback_years).toFixed(1)
      : 'Indicative estimate';
    document.getElementById('result-system-cost').textContent = formatCurrency(data.system_cost);
    document.getElementById('result-subsidy').textContent = `-${formatCurrency(data.subsidy_amount)}`;
    document.getElementById('result-net-investment').textContent = formatCurrency(data.net_investment);
    document.getElementById('result-subsidy-note').textContent =
      `Subsidy shown: ${formatCurrency(data.subsidy_amount)}. Final PM Surya Ghar eligibility depends on system size and government approval.`;
    document.getElementById('result-area-summary').textContent =
      `Usable roof area: ${Number(data.usable_area_sqm || 0).toFixed(2)} m² ` +
      `of ${Number(data.roof_area_sqm || 0).toFixed(2)} m² total`;
    const sourceLabel = {
      nasa_power: 'NASA POWER',
      pvgis: 'PVGIS',
      mock_fallback: 'Estimated fallback'
    }[data.irradiance_source] || data.irradiance_source || '';
    document.getElementById('estimate-source').textContent = sourceLabel
      ? `Irradiance: ${sourceLabel}`
      : '';
    const sourceNote = document.getElementById('estimate-source-note');
    if (sourceNote) {
      const isFallback = data.irradiance_source === 'mock_fallback' || data.irradiance_source === 'estimated';
      sourceNote.textContent = isFallback
        ? 'Live irradiance services were unavailable. This result uses a rough location-adjusted estimate.'
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
      alert('Please draw your rooftop boundary on the map first.');
      return;
    }
    if (!selectedLatLng) {
      // Fall back to the polygon's own centroid if no address was searched.
      const avgLat = polygon.reduce((s, p) => s + p[0], 0) / polygon.length;
      const avgLng = polygon.reduce((s, p) => s + p[1], 0) / polygon.length;
      selectedLatLng = { lat: avgLat, lng: avgLng };
    }
    if (!selectedAddress) {
      selectedAddress = document.getElementById('address-search').value || 'Selected rooftop';
    }

    const orientationSelect = document.getElementById('orientation-select');
    const orientationValue = orientationSelect ? orientationSelect.value : 'auto';
    const batteryEnabled = document.getElementById('battery-toggle')?.checked || false;
    const batteryKwh = batteryEnabled
      ? parseFloat(document.getElementById('battery-size')?.value || '10')
      : 0;
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
    const needsBackup = document.getElementById('needs-backup')?.checked || false;
    const inverterPreference = document.getElementById('inverter-preference')?.value || 'auto';

    document.getElementById('loading-panel').classList.remove('hidden');
    btn.disabled = true;

    try {
      const res = await fetch(ESTIMATE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          address: selectedAddress,
          latitude: selectedLatLng.lat,
          longitude: selectedLatLng.lng,
          polygon: polygon,
          obstructions: obstructions,
          state: state,
          tariff_per_kwh: tariffPerKwh,
          orientation: orientationValue,
          battery_kwh: batteryKwh,
          monthly_bill: monthlyBill,
          property_type: propertyType,
          user_priority: userPriority,
          needs_backup: needsBackup,
          inverter_preference: inverterPreference
        })
      });
      let data;
      try {
        data = await res.json();
      } catch (parseErr) {
        alert('Server returned an invalid response (HTTP ' + res.status + '). Check the Flask terminal for errors.');
        return;
      }
      if (!res.ok) {
        alert(data.error || ('Estimation failed (HTTP ' + res.status + ').'));
        return;
      }
      if (data.redirect_url) {
        showEstimateResult(data);
      } else {
        alert(data.error || 'Something went wrong generating your estimate.');
      }
    } catch (err) {
      console.error(err);
      alert('Estimation failed: ' + (err && err.message ? err.message : 'network or server error. Check the Flask terminal.'));
    } finally {
      document.getElementById('loading-panel').classList.add('hidden');
      btn.disabled = false;
    }
  });
}

// -------------------------------------------------------- Report page -----
function initTabs() {
  const tabButtons = document.querySelectorAll('.tab-btn');
  if (!tabButtons.length) return;

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
    });
  });
}

function initCharts() {
  if (typeof MONTHLY_DATA === 'undefined' || typeof Chart === 'undefined') return;

  const months = Object.keys(MONTHLY_DATA);
  const values = Object.values(MONTHLY_DATA);

  const genCtx = document.getElementById('monthlyGenerationChart');
  if (genCtx) {
    new Chart(genCtx, {
      type: 'bar',
      data: {
        labels: months,
        datasets: [{
          label: 'Generation (kWh)',
          data: values,
          backgroundColor: '#f5a623'
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  const donutCtx = document.getElementById('financialDonutChart');
  if (donutCtx && typeof NET_INVESTMENT !== 'undefined') {
    const labels = ['Net Investment', 'Subsidy', 'You Save (25yrs)'];
    const data = [NET_INVESTMENT, SUBSIDY_AMOUNT, TOTAL_25YR_SAVINGS];
    const colors = ['#3d2570', '#ff7a45', '#ffb347'];
    if (typeof BATTERY_COST !== 'undefined' && BATTERY_COST > 0) {
      labels.splice(1, 0, 'Battery');
      data.splice(1, 0, BATTERY_COST);
      colors.splice(1, 0, '#2e9e6a');
    }
    new Chart(donutCtx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{ data, backgroundColor: colors, borderWidth: 0 }]
      },
      options: {
        responsive: true,
        plugins: { legend: { position: 'bottom' } },
        cutout: '65%'
      }
    });
  }

  const cashCtx = document.getElementById('cashflowChart');
  if (cashCtx) {
    // 25-year cumulative savings curve; visually anchored to the 15L/20L/25L
    // milestones shown beneath the chart.
    const years = Array.from({ length: 25 }, (_, i) => `Yr ${i + 1}`);
    const netInvestment = (typeof NET_INVESTMENT !== 'undefined') ? NET_INVESTMENT : 0;
    const annualSavings = (typeof ANNUAL_SAVINGS !== 'undefined') ? ANNUAL_SAVINGS : 0;
    let cumulative = -netInvestment;
    const cashflow = years.map(() => {
      cumulative += annualSavings;
      return Math.round(cumulative);
    });

    new Chart(cashCtx, {
      type: 'line',
      data: {
        labels: years,
        datasets: [{
          label: 'Cumulative Savings (₹)',
          data: cashflow,
          borderColor: '#d98c0f',
          backgroundColor: 'rgba(245,166,35,0.15)',
          fill: true,
          tension: 0.3
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } }
      }
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initSearch();
  initSystemOptions();
  initEstimationButton();
  initTabs();
  initCharts();
});
