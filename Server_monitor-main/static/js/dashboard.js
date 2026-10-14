/**
 * Server Health Dashboard - Client Application Logic
 * Pure vanilla JavaScript with local Chart.js integration.
 */

(function () {
  'use strict';

  // Global state
  let config = null;
  let charts = {};
  let currentMinutes = 15;
  let isConnected = true;
  let consecutiveErrors = 0;
  let pollMetricsTimer = null;
  let pollAlertsTimer = null;

  // DOM Elements cache
  const el = {
    disconnectedBanner: document.getElementById('disconnected-banner'),
    connectionPill: document.getElementById('connection-status-pill'),
    connectionText: document.getElementById('connection-status-text'),
    clock: document.getElementById('current-clock'),

    // Cards
    valCpu: document.getElementById('val-cpu'),
    valMemory: document.getElementById('val-memory'),
    valDisk: document.getElementById('val-disk'),
    valUptime: document.getElementById('val-uptime'),

    barCpu: document.getElementById('bar-cpu'),
    barMemory: document.getElementById('bar-memory'),
    barDisk: document.getElementById('bar-disk'),

    statusCpu: document.getElementById('status-pill-cpu'),
    statusMemory: document.getElementById('status-pill-memory'),
    statusDisk: document.getElementById('status-pill-disk'),

    cardCpu: document.getElementById('card-cpu'),
    cardMemory: document.getElementById('card-memory'),
    cardDisk: document.getElementById('card-disk'),

    lblCpuWarn: document.getElementById('lbl-cpu-warn'),
    lblCpuCrit: document.getElementById('lbl-cpu-crit'),
    lblMemWarn: document.getElementById('lbl-memory-warn'),
    lblMemCrit: document.getElementById('lbl-memory-crit'),
    lblDiskPath: document.getElementById('lbl-disk-path'),
    lblDiskCrit: document.getElementById('lbl-disk-crit'),
    lblSampleInterval: document.getElementById('lbl-sample-interval'),

    // Alert Banner
    alertBannerCard: document.getElementById('alert-banner-card'),
    alertBannerTitle: document.getElementById('alert-banner-title'),
    alertBannerDesc: document.getElementById('alert-banner-desc'),
    activeAlertBadges: document.getElementById('active-alert-badges'),

    // Alerts Table
    alertsTableBody: document.getElementById('alerts-table-body'),

    // Range Buttons
    rangeBtns: document.querySelectorAll('.time-range-btn'),
  };

  // -------------------------------------------------------------
  // CHART.JS SETUP & THRESHOLD PLUGIN
  // -------------------------------------------------------------
  const thresholdLinePlugin = {
    id: 'thresholdLines',
    afterDatasetsDraw(chart, args, pluginOptions) {
      if (!pluginOptions) return;
      const { ctx, chartArea, scales } = chart;
      if (!chartArea || !scales || !scales.y) return;

      const { left, right } = chartArea;
      const yScale = scales.y;

      const drawHLine = (val, color, dash) => {
        if (typeof val !== 'number') return;
        const yPos = yScale.getPixelForValue(val);
        if (yPos < chartArea.top || yPos > chartArea.bottom) return;

        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.setLineDash(dash);
        ctx.beginPath();
        ctx.moveTo(left, yPos);
        ctx.lineTo(right, yPos);
        ctx.stroke();
        ctx.restore();
      };

      if (pluginOptions.warning) {
        drawHLine(pluginOptions.warning, '#f59e0b', [5, 4]);
      }
      if (pluginOptions.critical) {
        drawHLine(pluginOptions.critical, '#ef4444', [5, 4]);
      }
    },
  };

  if (window.Chart) {
    Chart.register(thresholdLinePlugin);
  }

  function createChart(canvasId, label, color, gradientStart, gradientEnd, thresholds) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 200);
    gradient.addColorStop(0, gradientStart);
    gradient.addColorStop(1, gradientEnd);

    return new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: label,
            data: [],
            borderColor: color,
            backgroundColor: gradient,
            borderWidth: 2,
            tension: 0.35,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointBackgroundColor: color,
            pointHoverBackgroundColor: '#ffffff',
            pointHoverBorderColor: color,
            pointHoverBorderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 300,
        },
        interaction: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(17, 24, 39, 0.95)',
            titleColor: '#f9fafb',
            bodyColor: '#9ca3af',
            borderColor: 'rgba(255, 255, 255, 0.1)',
            borderWidth: 1,
            padding: 10,
            displayColors: false,
            callbacks: {
              label: (context) => `${context.dataset.label}: ${context.parsed.y.toFixed(1)}%`,
            },
          },
          thresholdLines: thresholds || {},
        },
        scales: {
          x: {
            grid: {
              color: 'rgba(255, 255, 255, 0.04)',
            },
            ticks: {
              color: '#6b7280',
              font: { size: 10 },
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 6,
            },
          },
          y: {
            min: 0,
            max: 100,
            grid: {
              color: 'rgba(255, 255, 255, 0.06)',
            },
            ticks: {
              color: '#9ca3af',
              font: { size: 10 },
              stepSize: 25,
              callback: (val) => val + '%',
            },
          },
        },
      },
    });
  }

  // -------------------------------------------------------------
  // TIME & FORMATTING HELPERS
  // -------------------------------------------------------------
  function formatTimestamp(epochSec) {
    if (!epochSec) return '—';
    const d = new Date(epochSec * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function formatDateTime(epochSec) {
    if (!epochSec) return '—';
    const d = new Date(epochSec * 1000);
    return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
  }

  function formatDuration(startSec, endSec) {
    const end = endSec ? endSec : Date.now() / 1000;
    const diff = Math.max(0, Math.floor(end - startSec));
    if (diff < 60) return `${diff}s`;
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  function updateClock() {
    const now = new Date();
    el.clock.textContent = now.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  // -------------------------------------------------------------
  // NETWORK & CONNECTIVITY WATCHDOG
  // -------------------------------------------------------------
  function setConnectionStatus(connected) {
    if (isConnected === connected) return;
    isConnected = connected;

    if (!connected) {
      el.disconnectedBanner.classList.add('show');
      el.connectionPill.classList.add('offline');
      el.connectionText.textContent = 'DISCONNECTED';
    } else {
      el.disconnectedBanner.classList.remove('show');
      el.connectionPill.classList.remove('offline');
      el.connectionText.textContent = 'LIVE MONITORING';
    }
  }

  async function fetchJson(url) {
    try {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error(`HTTP error ${response.status}`);
      }
      const data = await response.json();
      consecutiveErrors = 0;
      setConnectionStatus(true);
      return data;
    } catch (err) {
      consecutiveErrors++;
      if (consecutiveErrors >= 2) {
        setConnectionStatus(false);
      }
      console.warn(`Fetch error for ${url}:`, err);
      throw err;
    }
  }

  // -------------------------------------------------------------
  // DASHBOARD DATA LOADERS & UPDATERS
  // -------------------------------------------------------------
  async function loadConfig() {
    try {
      config = await fetchJson('/api/config');
      const th = config.thresholds || {};

      // Update card config labels
      if (th.cpu) {
        el.lblCpuWarn.textContent = `${th.cpu.warning}%`;
        el.lblCpuCrit.textContent = `${th.cpu.critical}%`;
      }
      if (th.memory) {
        el.lblMemWarn.textContent = `${th.memory.warning}%`;
        el.lblMemCrit.textContent = `${th.memory.critical}%`;
      }
      if (th.disk) {
        el.lblDiskCrit.textContent = `${th.disk.critical}%`;
      }
      if (config.disk_path) {
        el.lblDiskPath.textContent = config.disk_path;
      }
      if (config.sample_interval_seconds) {
        el.lblSampleInterval.textContent = `Sample: ${config.sample_interval_seconds}s`;
      }

      // Initialize Charts with thresholds
      initCharts();
    } catch (err) {
      console.error('Failed to load initial config:', err);
    }
  }

  function initCharts() {
    const th = (config && config.thresholds) || {};

    charts.cpu = createChart(
      'chart-cpu',
      'CPU %',
      '#06b6d4',
      'rgba(6, 182, 212, 0.25)',
      'rgba(6, 182, 212, 0.0)',
      th.cpu
    );

    charts.memory = createChart(
      'chart-memory',
      'Memory %',
      '#8b5cf6',
      'rgba(139, 92, 246, 0.25)',
      'rgba(139, 92, 246, 0.0)',
      th.memory
    );

    charts.disk = createChart(
      'chart-disk',
      'Disk %',
      '#3b82f6',
      'rgba(59, 130, 246, 0.25)',
      'rgba(59, 130, 246, 0.0)',
      th.disk
    );

    // Initial history fetch for default range
    loadHistory(currentMinutes);
  }

  async function loadHistory(minutes) {
    try {
      const data = await fetchJson(`/api/history?minutes=${minutes}`);
      const samples = data.samples || [];

      const labels = samples.map((s) => formatTimestamp(s.timestamp));
      const cpuData = samples.map((s) => s.cpu_percent);
      const memData = samples.map((s) => s.memory_percent);
      const diskData = samples.map((s) => s.disk_percent);

      if (charts.cpu) {
        charts.cpu.data.labels = labels;
        charts.cpu.data.datasets[0].data = cpuData;
        charts.cpu.update('none');
      }

      if (charts.memory) {
        charts.memory.data.labels = [...labels];
        charts.memory.data.datasets[0].data = memData;
        charts.memory.update('none');
      }

      if (charts.disk) {
        charts.disk.data.labels = [...labels];
        charts.disk.data.datasets[0].data = diskData;
        charts.disk.update('none');
      }
    } catch (err) {
      console.warn('Failed to load history:', err);
    }
  }

  function updateMetricCard(metricName, value, activeAlerts) {
    const valEl = el[`val${capitalize(metricName)}`];
    const barEl = el[`bar${capitalize(metricName)}`];
    const pillEl = el[`status${capitalize(metricName)}`];
    const cardEl = el[`card${capitalize(metricName)}`];

    if (!valEl) return;

    const numVal = typeof value === 'number' ? value : 0;
    valEl.textContent = numVal.toFixed(1);

    // Progress bar width
    if (barEl) {
      barEl.style.width = `${Math.min(100, Math.max(0, numVal))}%`;
    }

    // Determine status from active alerts or threshold levels
    const active = activeAlerts.find((a) => a.metric === metricName);
    let status = 'normal';
    if (active) {
      status = active.level === 'critical' ? 'critical' : 'warning';
    } else if (config && config.thresholds && config.thresholds[metricName]) {
      const th = config.thresholds[metricName];
      if (numVal >= th.critical) status = 'critical';
      else if (numVal >= th.warning) status = 'warning';
    }

    // Update pill badge and classes
    if (pillEl) {
      pillEl.className = `status-pill ${status}`;
      pillEl.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    }

    if (barEl) {
      barEl.className = `progress-bar-fill ${status}`;
    }

    if (cardEl) {
      cardEl.classList.remove('status-warning', 'status-critical');
      if (status !== 'normal') {
        cardEl.classList.add(`status-${status}`);
      }
    }
  }

  function updateActiveAlertBanner(activeAlerts) {
    const count = activeAlerts.length;
    el.activeAlertBadges.innerHTML = '';

    if (count === 0) {
      el.alertBannerCard.className = 'alert-banner-card healthy';
      el.alertBannerTitle.textContent = 'System Status: All Systems Operational';
      el.alertBannerDesc.textContent = 'No active threshold alerts. Telemetry sampling continuous.';
      return;
    }

    const hasCritical = activeAlerts.some((a) => a.level === 'critical');
    el.alertBannerCard.className = `alert-banner-card ${hasCritical ? 'critical-banner' : 'warning-banner'}`;
    el.alertBannerTitle.textContent = `${hasCritical ? 'CRITICAL ALERT' : 'WARNING ALERT'}: ${count} Metric ${count > 1 ? 'Thresholds' : 'Threshold'} Breached`;
    el.alertBannerDesc.textContent = 'Debounce condition satisfied. Telemetry values exceed safety parameters:';

    activeAlerts.forEach((alert) => {
      const badge = document.createElement('span');
      badge.className = `alert-badge-item ${alert.level}`;
      badge.textContent = `${alert.metric.toUpperCase()}: ${alert.value.toFixed(1)}% (${alert.level})`;
      el.activeAlertBadges.appendChild(badge);
    });
  }

  function appendLivePointToChart(chart, timeLabel, value, maxWindowSeconds) {
    if (!chart) return;
    chart.data.labels.push(timeLabel);
    chart.data.datasets[0].data.push(value);

    // Prune points older than current window (sampling every ~5s)
    const maxPoints = Math.max(30, Math.ceil((currentMinutes * 60) / 4.5));
    while (chart.data.labels.length > maxPoints) {
      chart.data.labels.shift();
      chart.data.datasets[0].data.shift();
    }
    chart.update('none');
  }

  async function pollMetrics() {
    try {
      const data = await fetchJson('/api/metrics');
      const latest = data.latest;
      const uptime = data.uptime;
      const activeAlerts = data.active_alerts || [];

      // Update Uptime
      if (el.valUptime && uptime) {
        el.valUptime.textContent = uptime;
      }

      if (latest) {
        // Update Cards
        updateMetricCard('cpu', latest.cpu_percent, activeAlerts);
        updateMetricCard('memory', latest.memory_percent, activeAlerts);
        updateMetricCard('disk', latest.disk_percent, activeAlerts);

        // Append live points to charts
        const label = formatTimestamp(latest.timestamp);
        appendLivePointToChart(charts.cpu, label, latest.cpu_percent, currentMinutes * 60);
        appendLivePointToChart(charts.memory, label, latest.memory_percent, currentMinutes * 60);
        appendLivePointToChart(charts.disk, label, latest.disk_percent, currentMinutes * 60);
      }

      // Update Active Alerts Banner
      updateActiveAlertBanner(activeAlerts);
    } catch (err) {
      console.warn('Metric poll cycle failed:', err);
    }
  }

  async function pollAlertsHistory() {
    try {
      const data = await fetchJson('/api/alerts?limit=50');
      const alerts = data.alerts || [];

      if (alerts.length === 0) {
        el.alertsTableBody.innerHTML = `
          <tr class="empty-state-row">
            <td colspan="8">No alerts recorded. System running smoothly.</td>
          </tr>
        `;
        return;
      }

      el.alertsTableBody.innerHTML = alerts
        .map((a) => {
          const isResolved = a.resolved_at !== null && a.resolved_at !== undefined;
          const statusHtml = isResolved
            ? '<span class="state-badge resolved">● Resolved</span>'
            : '<span class="state-badge active">● Active</span>';

          const durationStr = formatDuration(a.started_at, a.resolved_at);

          return `
            <tr>
              <td>#${a.id}</td>
              <td style="font-weight: 600; text-transform: uppercase;">${a.metric}</td>
              <td><span class="table-badge ${a.level}">${a.level}</span></td>
              <td style="font-family: var(--font-mono); font-weight: 600;">${a.value.toFixed(1)}%</td>
              <td>${formatDateTime(a.started_at)}</td>
              <td>${formatDateTime(a.resolved_at)}</td>
              <td>${durationStr}</td>
              <td>${statusHtml}</td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      console.warn('Alerts history poll failed:', err);
    }
  }

  function capitalize(str) {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1);
  }

  // -------------------------------------------------------------
  // EVENT LISTENERS & INITIALIZATION
  // -------------------------------------------------------------
  function setupEventListeners() {
    el.rangeBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        el.rangeBtns.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const mins = parseInt(btn.getAttribute('data-minutes'), 10) || 15;
        currentMinutes = mins;
        loadHistory(currentMinutes);
      });
    });
  }

  async function init() {
    setupEventListeners();
    updateClock();
    setInterval(updateClock, 1000);

    await loadConfig();

    // Start live polling intervals (3s for metrics, 6s for alerts table)
    pollMetrics();
    pollAlertsHistory();
    pollMetricsTimer = setInterval(pollMetrics, 3000);
    pollAlertsTimer = setInterval(pollAlertsHistory, 6000);
  }

  // Boot on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
