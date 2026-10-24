/* SIGNIX analytical report charts. All series arrive from the backend. */
(function () {
  const charts = {};
  const pending = {};
  let initialized = false;
  const palette = {
    ink: '#261942',
    green: '#17845b',
    greenSoft: 'rgba(23,132,91,.16)',
    amber: '#d98c0f',
    amberSoft: 'rgba(217,140,15,.18)',
    violet: '#6950a1',
    coral: '#d96145',
    grid: 'rgba(38,25,66,.10)',
  };

  function money(value) {
    return '₹' + Math.round(Number(value) || 0).toLocaleString('en-IN');
  }

  function number(value, digits = 0) {
    return Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: digits });
  }

  function tooltip(unit, formatter) {
    return {
      callbacks: {
        label(context) {
          const value = Number(context.raw) || 0;
          return `${context.dataset.label}: ${formatter ? formatter(value) : number(value, 1)}${unit ? ` ${unit}` : ''}`;
        },
      },
    };
  }

  function baseOptions(extra) {
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 420 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#756e82' } },
        y: { beginAtZero: true, grid: { color: palette.grid }, ticks: { color: '#756e82' } },
      },
    }, extra || {});
  }

  function replace(name, canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    pending[name] = { canvasId, config };
    if (charts[name]) charts[name].destroy();
    delete charts[name];
    if (typeof Chart === 'undefined' || !isCanvasVisible(canvas)) return;
    charts[name] = new Chart(canvas, config);
  }

  function isCanvasVisible(canvas) {
    return Boolean(canvas && canvas.getClientRects().length && canvas.clientWidth > 0 && canvas.clientHeight > 0);
  }

  function renderVisible() {
    if (typeof Chart === 'undefined') return;
    Object.entries(pending).forEach(([name, item]) => {
      if (charts[name]) {
        charts[name].resize();
        return;
      }
      const canvas = document.getElementById(item.canvasId);
      if (isCanvasVisible(canvas)) charts[name] = new Chart(canvas, item.config);
    });
  }

  function unavailable(canvasId, messageId, message) {
    const canvas = document.getElementById(canvasId);
    const empty = document.getElementById(messageId);
    if (canvas) {
      canvas.classList.add('hidden');
      canvas.parentElement?.classList.add('chart-frame-unavailable');
    }
    if (empty) {
      empty.textContent = message;
      empty.classList.remove('hidden');
    }
  }

  function chartLibraryUnavailable() {
    const states = [
      ['roofUtilizationChart', null],
      ['monthlyGenerationChart', 'monthly-chart-empty'],
      ['energyFlowChart', 'energy-flow-empty'],
      ['consumptionGenerationChart', 'consumption-chart-empty'],
      ['monthlyBillChart', 'monthly-bill-empty'],
      ['generation25yrChart', 'gen25-chart-empty'],
      ['cashflowChart', 'cashflow-chart-empty'],
      ['annualSavingsChart', 'annual-savings-empty'],
      ['electricityCostChart', 'electricity-cost-empty'],
      ['financingComparisonChart', 'financing-chart-empty'],
    ];
    states.forEach(([canvasId, messageId]) => {
      if (messageId) unavailable(canvasId, messageId, 'Chart rendering is temporarily unavailable.');
      else document.getElementById(canvasId)?.parentElement?.classList.add('chart-frame-unavailable');
    });
  }

  function available(canvasId, messageId) {
    const canvas = document.getElementById(canvasId);
    canvas?.classList.remove('hidden');
    canvas?.parentElement?.classList.remove('chart-frame-unavailable');
    document.getElementById(messageId)?.classList.add('hidden');
  }

  function renderEnergy(data) {
    const energy = data.energy || {};
    const monthly = energy.monthly_generation || [];
    if (monthly.length === 12) {
      available('monthlyGenerationChart', 'monthly-chart-empty');
      replace('monthly', 'monthlyGenerationChart', {
        type: 'bar',
        data: {
          labels: monthly.map(item => item.month),
          datasets: [{
            label: 'Solar generation',
            data: monthly.map(item => item.generation_kwh),
            backgroundColor: palette.amber,
            borderRadius: 5,
            maxBarThickness: 34,
          }],
        },
        options: baseOptions({ plugins: { legend: { display: false }, tooltip: tooltip('kWh') } }),
      });
    } else {
      unavailable('monthlyGenerationChart', 'monthly-chart-empty', 'Monthly generation data is unavailable for this estimate.');
    }

    const generation = energy.generation_25yr || [];
    if (generation.length) {
      available('generation25yrChart', 'gen25-chart-empty');
      replace('generation25', 'generation25yrChart', {
        data: {
          labels: generation.map((_, index) => `Year ${index + 1}`),
          datasets: [
            {
              type: 'bar', label: 'Annual generation', data: generation,
              borderColor: palette.amber, backgroundColor: palette.amberSoft,
              borderWidth: 1, borderRadius: 3, yAxisID: 'y',
            },
            {
              type: 'line', label: 'Cumulative generation',
              data: energy.cumulative_generation_25yr || [], borderColor: palette.green,
              pointRadius: 1.5, pointHoverRadius: 5, tension: .18, yAxisID: 'y1',
            },
          ],
        },
        options: baseOptions({
          plugins: { legend: { position: 'bottom' }, tooltip: tooltip('kWh') },
          scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 13 } },
            y: { beginAtZero: false, position: 'left', grid: { color: palette.grid }, title: { display: true, text: 'Annual kWh' } },
            y1: { beginAtZero: true, position: 'right', grid: { display: false }, title: { display: true, text: 'Cumulative kWh' } },
          },
        }),
      });
    } else {
      unavailable('generation25yrChart', 'gen25-chart-empty', 'The 25-year generation series is unavailable.');
    }

    const flow = data.energy_flow || {};
    if (flow.available) {
      available('energyFlowChart', 'energy-flow-empty');
      replace('flow', 'energyFlowChart', {
        type: 'bar',
        data: {
          labels: ['Solar generated', 'Self-consumed', 'Grid export', 'Grid import'],
          datasets: [{
            label: 'Annual energy',
            data: [flow.solar_generation_kwh, flow.self_consumed_kwh, flow.grid_export_kwh, flow.grid_import_kwh],
            backgroundColor: [palette.amber, palette.green, palette.violet, palette.coral],
            borderRadius: 5,
          }],
        },
        options: baseOptions({
          indexAxis: 'y',
          plugins: { legend: { display: false }, tooltip: tooltip('kWh') },
        }),
      });
    } else {
      unavailable('energyFlowChart', 'energy-flow-empty', flow.reason || 'Energy-flow data is unavailable.');
    }

    const consumption = data.consumption || {};
    if (consumption.monthly_available && consumption.monthly_consumption?.length === 12) {
      available('consumptionGenerationChart', 'consumption-chart-empty');
      replace('consumption', 'consumptionGenerationChart', {
        type: 'bar',
        data: {
          labels: monthly.map(item => item.month),
          datasets: [
            { label: 'Solar generation', data: monthly.map(item => item.generation_kwh), backgroundColor: palette.amber, borderRadius: 4 },
            { label: 'Consumption', data: consumption.monthly_consumption, backgroundColor: palette.violet, borderRadius: 4 },
          ],
        },
        options: baseOptions({ plugins: { legend: { position: 'bottom' }, tooltip: tooltip('kWh') } }),
      });
    } else {
      unavailable('consumptionGenerationChart', 'consumption-chart-empty', consumption.unavailable_reason || 'A monthly consumption profile is unavailable.');
    }

    const bill = data.monthly_bill_impact || {};
    if (bill.available) {
      available('monthlyBillChart', 'monthly-bill-empty');
      replace('bill', 'monthlyBillChart', {
        type: 'bar',
        data: {
          labels: ['Current bill', 'With solar', 'Savings'],
          datasets: [{
            label: 'Representative month',
            data: [bill.current_bill, bill.estimated_bill_with_solar, bill.savings],
            backgroundColor: [palette.coral, palette.ink, palette.green],
            borderRadius: 5,
          }],
        },
        options: baseOptions({ plugins: { legend: { display: false }, tooltip: tooltip('', money) } }),
      });
    } else {
      unavailable('monthlyBillChart', 'monthly-bill-empty', bill.reason || 'Monthly bill impact is unavailable.');
    }
  }

  function renderRoof(data) {
    const roof = data.roof_utilization || {};
    if (!(roof.total_area_sqm > 0)) return;
    replace('roof', 'roofUtilizationChart', {
      type: 'bar',
      data: {
        labels: ['Selected roof'],
        datasets: [
          { label: 'Usable area', data: [roof.usable_area_sqm], backgroundColor: palette.green, borderRadius: 5 },
          { label: 'Manual obstructions', data: [roof.obstruction_area_sqm], backgroundColor: palette.coral, borderRadius: 5 },
        ],
      },
      options: baseOptions({
        indexAxis: 'y',
        plugins: { legend: { position: 'bottom' }, tooltip: tooltip('m²') },
        scales: {
          x: { stacked: true, beginAtZero: true, grid: { color: palette.grid } },
          y: { stacked: true, grid: { display: false } },
        },
      }),
    });
  }

  function renderFinancial(data) {
    const financial = data.financial || {};
    if (financial.available === false || !financial.cumulative_cashflow) {
      unavailable('cashflowChart', 'cashflow-chart-empty', financial.reason || 'Cashflow data is unavailable.');
      unavailable('annualSavingsChart', 'annual-savings-empty', financial.reason || 'Annual savings data is unavailable.');
      return;
    }
    available('cashflowChart', 'cashflow-chart-empty');
    available('annualSavingsChart', 'annual-savings-empty');
    const labels = ['Year 0', ...(financial.years || []).map(year => `Year ${year}`)];
    const cashflow = [-financial.initial_investment, ...financial.cumulative_cashflow];
    const breakEvenIndex = cashflow.findIndex(value => value >= 0);
    replace('cashflow', 'cashflowChart', {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Cumulative cashflow',
          data: cashflow,
          borderColor: palette.green,
          backgroundColor: palette.greenSoft,
          fill: { target: 'origin', above: palette.greenSoft, below: 'rgba(217,97,69,.13)' },
          pointRadius: cashflow.map((_, index) => index === breakEvenIndex ? 6 : 1.5),
          pointBackgroundColor: cashflow.map((_, index) => index === breakEvenIndex ? palette.amber : palette.green),
          tension: .2,
        }],
      },
      options: baseOptions({
        plugins: { legend: { display: false }, tooltip: tooltip('', money) },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 13 } },
          y: { grid: { color: context => context.tick.value === 0 ? palette.ink : palette.grid }, ticks: { callback: money } },
        },
      }),
    });

    replace('annualSavings', 'annualSavingsChart', {
      data: {
        labels: financial.years.map(year => `Y${year}`),
        datasets: [
          { type: 'bar', label: 'Annual operating benefit after O&M', data: financial.annual_savings, backgroundColor: palette.amberSoft, borderColor: palette.amber, borderWidth: 1, borderRadius: 3, yAxisID: 'y' },
          { type: 'line', label: 'Cumulative operating benefit after O&M', data: financial.cumulative_savings, borderColor: palette.green, pointRadius: 1.5, tension: .2, yAxisID: 'y1' },
        ],
      },
      options: baseOptions({
        plugins: { legend: { position: 'bottom' }, tooltip: tooltip('', money) },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 13 } },
          y: { beginAtZero: true, position: 'left', grid: { color: palette.grid }, ticks: { callback: money } },
          y1: { beginAtZero: true, position: 'right', grid: { display: false }, ticks: { callback: money } },
        },
      }),
    });

    const donutValues = [Number(typeof NET_INVESTMENT !== 'undefined' ? NET_INVESTMENT : 0)];
    const donutLabels = ['Net investment'];
    if (typeof PSG_GUIDANCE_ALLOWED !== 'undefined' && PSG_GUIDANCE_ALLOWED) {
      donutValues.push(Number(typeof SUBSIDY_AMOUNT !== 'undefined' ? SUBSIDY_AMOUNT : 0));
      donutLabels.push('Subsidy');
    }
    if (typeof BATTERY_COST !== 'undefined' && BATTERY_COST > 0) {
      donutLabels.push('Battery');
      donutValues.push(BATTERY_COST);
    }
    replace('donut', 'financialDonutChart', {
      type: 'doughnut',
      data: { labels: donutLabels, datasets: [{ data: donutValues, backgroundColor: [palette.ink, palette.amber, palette.green], borderWidth: 0 }] },
      options: baseOptions({ cutout: '68%', plugins: { legend: { position: 'bottom' }, tooltip: tooltip('', money) } }),
    });

    const cost = data.electricity_cost || {};
    if (cost.available) {
      available('electricityCostChart', 'electricity-cost-empty');
      const labelsCost = ['Grid only', 'Solar + CAPEX'];
      const values = [cost.grid_only_lifetime_cost, cost.solar_capex_lifetime_cost];
      if (cost.solar_dpa_lifetime_cost != null) {
        labelsCost.push('Solar + DPA');
        values.push(cost.solar_dpa_lifetime_cost);
      }
      replace('cost', 'electricityCostChart', {
        type: 'bar',
        data: { labels: labelsCost, datasets: [{ label: '25-year cost', data: values, backgroundColor: [palette.coral, palette.green, palette.violet], borderRadius: 5 }] },
        options: baseOptions({ plugins: { legend: { display: false }, tooltip: tooltip('', money) } }),
      });
    } else {
      unavailable('electricityCostChart', 'electricity-cost-empty', cost.reason || 'Electricity-cost comparison is unavailable.');
    }

    const financing = data.financing || {};
    if (financing.available) {
      available('financingComparisonChart', 'financing-chart-empty');
      replace('financing', 'financingComparisonChart', {
        type: 'line',
        data: {
          labels: financing.years.map(year => `Y${year}`),
          datasets: [
            { label: 'CAPEX cumulative position', data: financing.capex.cumulative_cashflow, borderColor: palette.green, pointRadius: 1.5, tension: .2 },
            { label: 'DPA cumulative position', data: financing.dpa.cumulative_cashflow, borderColor: palette.violet, pointRadius: 1.5, tension: .2 },
          ],
        },
        options: baseOptions({ plugins: { legend: { position: 'bottom' }, tooltip: tooltip('', money) }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 13 } }, y: { grid: { color: palette.grid }, ticks: { callback: money } } } }),
      });
    } else {
      unavailable('financingComparisonChart', 'financing-chart-empty', financing.reason || 'Financing comparison data is unavailable.');
    }
  }

  function render(data) {
    if (!data) return;
    if (typeof Chart === 'undefined') {
      chartLibraryUnavailable();
      return;
    }
    renderEnergy(data);
    renderRoof(data);
    renderFinancial(data);
    requestAnimationFrame(renderVisible);
  }

  function resize() {
    renderVisible();
    Object.values(charts).forEach(chart => chart.resize());
  }

  function health() {
    const canvases = {};
    document.querySelectorAll('.chart-frame canvas').forEach(canvas => {
      const chartName = Object.keys(pending).find(name => pending[name].canvasId === canvas.id);
      canvases[canvas.id] = {
        found: true,
        datasetNonEmpty: Boolean(chartName && pending[chartName]?.config?.data?.datasets?.some(dataset => dataset.data?.length)),
        initialized: Boolean(chartName && charts[chartName]),
        width: canvas.clientWidth,
        height: canvas.clientHeight,
      };
    });
    return {
      chartJsLoaded: typeof Chart !== 'undefined',
      reportDataPresent: typeof REPORT_CHART_DATA !== 'undefined' && Boolean(REPORT_CHART_DATA),
      canvases,
    };
  }

  function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  }

  function formatYears(value) {
    return value == null ? '—' : `${number(value, 1)} yrs`;
  }

  function updateFinancingValues(comparison, chartData) {
    const capex = comparison.capex || {};
    const dpa = comparison.dpa || {};
    setText('fc-capex-dp', money(capex.down_payment));
    setText('fc-dpa-dp', money(dpa.down_payment));
    setText('fc-dpa-loan', money(dpa.loan_amount));
    setText('fc-dpa-tenure', `${number(dpa.tenure_years)} years`);
    setText('fc-dpa-rate', `${number(dpa.annual_rate_pct, 1)}% p.a.`);
    setText('fc-dpa-emi', money(dpa.monthly_emi));
    setText('fc-dpa-annual', money(dpa.annual_emi));
    setText('fc-dpa-repayment', money(dpa.total_repayment));
    setText('fc-dpa-interest', money(dpa.total_interest));
    setText('fc-capex-payback', formatYears(capex.payback_years));
    setText('fc-dpa-payback', formatYears(dpa.payback_years));
    setText('fc-capex-irr', capex.equity_irr_pct == null ? '—' : `${number(capex.equity_irr_pct, 1)}%`);
    setText('fc-dpa-irr', dpa.equity_irr_pct == null ? '—' : `${number(dpa.equity_irr_pct, 1)}%`);
    setText('fc-capex-out', money(capex.total_cash_outflow));
    setText('fc-dpa-out', money(dpa.total_cash_outflow));
    setText('fc-summary-down', money(dpa.down_payment));
    setText('fc-summary-loan', money(dpa.loan_amount));
    setText('fc-summary-emi', money(dpa.monthly_emi));
    setText('fc-summary-repayment', money(dpa.total_repayment));
    setText('fc-summary-irr', dpa.equity_irr_pct == null ? '—' : `${number(dpa.equity_irr_pct, 1)}%`);
    const financing = chartData.financing || {};
    if (financing.available) {
      setText('fc-capex-position', money(financing.capex.cumulative_cashflow.at(-1)));
      setText('fc-dpa-position', money(financing.dpa.cumulative_cashflow.at(-1)));
    }
  }

  function initFinancingControls() {
    const panel = document.getElementById('financing-controls');
    if (!panel || panel.dataset.bound === 'true') return;
    panel.dataset.bound = 'true';
    const status = document.getElementById('financing-controls-status');
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const pairs = [
      ['dpa-down-payment-range', 'dpa-down-payment-value'],
      ['dpa-interest-rate-range', 'dpa-interest-rate-value'],
      ['dpa-tenure-range', 'dpa-tenure-value'],
    ];
    let timer;
    let requestSequence = 0;

    function statusText(text, className) {
      if (!status) return;
      status.textContent = text;
      status.classList.toggle('is-loading', className === 'loading');
      status.classList.toggle('is-error', className === 'error');
    }

    async function recalculate() {
      const sequence = ++requestSequence;
      statusText('Recalculating…', 'loading');
      const payload = {
        down_payment_pct: Number(document.getElementById('dpa-down-payment-value').value),
        dpa_rate_pct: Number(document.getElementById('dpa-interest-rate-value').value),
        dpa_tenure_years: Number(document.getElementById('dpa-tenure-value').value),
      };
      try {
        const response = await fetch(panel.dataset.endpoint, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrf },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Financing preview failed');
        if (sequence !== requestSequence) return;
        updateFinancingValues(result.financing_comparison, result.chart_data);
        window.SIGNIX_REPORT_CHARTS.update(result.chart_data);
        statusText('Preview updated — estimate inputs unchanged', 'success');
      } catch (error) {
        if (sequence === requestSequence) statusText(error.message || 'Unable to update financing preview', 'error');
      }
    }

    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(recalculate, 250);
    }

    pairs.forEach(([rangeId, valueId]) => {
      const range = document.getElementById(rangeId);
      const value = document.getElementById(valueId);
      if (!range || !value) return;
      range.addEventListener('input', () => { value.value = range.value; schedule(); });
      value.addEventListener('input', () => { range.value = value.value; schedule(); });
      value.addEventListener('change', () => {
        const numeric = Math.min(Number(value.max), Math.max(Number(value.min), Number(value.value)));
        value.value = String(numeric);
        range.value = String(numeric);
        schedule();
      });
    });
  }

  window.SIGNIX_REPORT_CHARTS = {
    init() {
      if (initialized) {
        renderVisible();
        return;
      }
      initialized = true;
      render(typeof REPORT_CHART_DATA === 'undefined' ? null : REPORT_CHART_DATA);
      initFinancingControls();
    },
    update(data) {
      REPORT_CHART_DATA = data;
      render(data);
    },
    resize,
    renderVisible,
    health,
    count: () => Object.keys(charts).length,
  };

  window.addEventListener('signix:chart-data', event => {
    if (event.detail) window.SIGNIX_REPORT_CHARTS.update(event.detail);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', window.SIGNIX_REPORT_CHARTS.init);
  } else {
    window.SIGNIX_REPORT_CHARTS.init();
  }
})();
