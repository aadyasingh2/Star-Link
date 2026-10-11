import { init, use } from 'echarts/core';
import { BarChart, HeatmapChart } from 'echarts/charts';
import {
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

use([
  BarChart,
  HeatmapChart,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

const ROUTER_COLOURS = {
  Oracle: '#ffffff',
  LinkState: '#22d3ee',
  DistanceVector: '#fbbf24',
  SAPR: '#a78bfa',
};
const CLASS_NAMES = ['correct', 'stale_working', 'loop', 'blackhole'];
const CLASS_COLOURS = {
  correct: '#34d399',
  stale_working: '#fbbf24',
  loop: '#a78bfa',
  blackhole: '#fb7185',
};
const CLASS_LABELS = {
  correct: 'Correct',
  stale_working: 'Stale working',
  loop: 'Loop',
  blackhole: 'Blackhole',
};
const DARK_TEXT = '#a8b3bf';
const GRID_COLOR = 'rgba(255,255,255,0.07)';

function numeric(value) {
  if (value === null || value === undefined || value === '' || value === '-') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function percentile(values, fraction) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const position = (ordered.length - 1) * fraction;
  const low = Math.floor(position);
  const high = Math.ceil(position);
  return ordered[low] + (ordered[high] - ordered[low]) * (position - low);
}

function percentages(counts, denominator) {
  return Object.fromEntries(CLASS_NAMES.map((name) => [
    name,
    denominator ? counts[name] * 100 / denominator : 0,
  ]));
}

function createAccumulator(routers) {
  return Object.fromEntries(routers.map((router) => [router, {
    classes: Object.fromEntries(CLASS_NAMES.map((name) => [name, 0])),
    reachable: 0,
    nearOptimal: 0,
    delays: [],
    stretches: [],
    routeChanges: 0,
  }]));
}

function accumulatePair(pairIndex, routes, events, routers, tolerance, durationSeconds) {
  const values = createAccumulator(routers);
  for (const sample of routes.samples) {
    const route = sample[pairIndex];
    const oracleDelay = route.delay_ms[0];
    for (let index = 0; index < routers.length; index += 1) {
      const item = values[routers[index]];
      const category = route.classes[index];
      const delay = route.delay_ms[index];
      if (Number.isFinite(oracleDelay)) {
        item.reachable += 1;
        if (item.classes[category] !== undefined) item.classes[category] += 1;
        if (
          ['correct', 'stale_working'].includes(category)
          && Number.isFinite(delay)
          && delay <= oracleDelay + tolerance
        ) item.nearOptimal += 1;
      }
      if (Number.isFinite(delay)) item.delays.push(delay);
      if (Number.isFinite(delay) && Number.isFinite(oracleDelay) && oracleDelay > 0) {
        item.stretches.push(delay / oracleDelay);
      }
    }
  }
  for (const event of events) {
    if (event.type !== 'route_change' || event.pair !== pairIndex) continue;
    const item = values[event.router];
    if (item) item.routeChanges += 1;
  }
  for (const router of routers) {
    const item = values[router];
    item.classPercentages = percentages(item.classes, item.reachable);
    item.nearOptimalPercent = item.reachable ? item.nearOptimal * 100 / item.reachable : 0;
    item.meanDelay = mean(item.delays);
    item.p99Delay = percentile(item.delays, 0.99);
    item.meanStretch = mean(item.stretches);
    item.changesPerMinute = durationSeconds ? item.routeChanges * 60 / durationSeconds : 0;
  }
  return values;
}

function aggregatePairs(pairMetrics, routers, durationSeconds) {
  const result = createAccumulator(routers);
  for (const values of pairMetrics) {
    for (const router of routers) {
      const target = result[router];
      const source = values[router];
      target.reachable += source.reachable;
      target.nearOptimal += source.nearOptimal;
      target.routeChanges += source.routeChanges;
      for (const name of CLASS_NAMES) target.classes[name] += source.classes[name];
      target.delays.push(...source.delays);
      target.stretches.push(...source.stretches);
    }
  }
  for (const router of routers) {
    const item = result[router];
    item.classPercentages = percentages(item.classes, item.reachable);
    item.nearOptimalPercent = item.reachable ? item.nearOptimal * 100 / item.reachable : 0;
    item.meanDelay = mean(item.delays);
    item.p99Delay = percentile(item.delays, 0.99);
    item.meanStretch = mean(item.stretches);
    item.changesPerMinute = durationSeconds ? item.routeChanges * 60 / durationSeconds : 0;
  }
  return result;
}

function metricFormat(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : '—';
}

function chartBase(legend = {}) {
  return {
    animationDuration: 380,
    animationDurationUpdate: 260,
    textStyle: { color: DARK_TEXT, fontFamily: 'Inter, system-ui, sans-serif' },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(10,14,22,0.96)',
      borderColor: 'rgba(255,255,255,0.13)',
      textStyle: { color: '#e5ebef', fontSize: 10 },
      axisPointer: { type: 'shadow' },
      ...legend,
    },
    grid: { left: 52, right: 18, top: 30, bottom: 34, containLabel: false },
    legend: {
      top: 0,
      textStyle: { color: DARK_TEXT, fontSize: 9 },
      itemWidth: 9,
      itemHeight: 7,
    },
  };
}

function createHeadlineTable(summary) {
  const table = document.querySelector('#headline-table');
  const { headers, rows } = summary;
  const bestByColumn = headers.map((header, column) => {
    if (column === 0) return null;
    const values = rows.map((row) => numeric(row[column])).filter((value) => value !== null);
    if (!values.length) return null;
    const maximize = header.startsWith('%');
    return maximize ? Math.max(...values) : Math.min(...values);
  });
  table.innerHTML = `<thead><tr>${headers.map((header) => `<th>${header}</th>`).join('')}</tr></thead><tbody>${
    rows.map((row) => `<tr>${row.map((value, column) => {
      const number = numeric(value);
      const best = column > 0 && number !== null && number === bestByColumn[column];
      return `<td${best ? ' class="best-value"' : ''}>${value}</td>`;
    }).join('')}</tr>`).join('')
  }</tbody>`;
}

function createNotMeasuredCards(meta) {
  const reasonByKey = {
    packet_queues: 'Queue occupancy timeseries are not exported by this simulator.',
    packet_loss_beyond_route_probes: 'The export contains route outcomes, not packet-level loss counters.',
    tcp_throughput: 'No TCP traffic or throughput measurements are exported.',
  };
  const labelByKey = {
    packet_queues: 'Packet queues',
    packet_loss_beyond_route_probes: 'Loss beyond route-level probes',
    tcp_throughput: 'TCP throughput',
  };
  document.querySelector('#not-measured-cards').innerHTML = meta.not_measured.map((key) => `
    <article class="not-measured-card">
      <span class="not-measured-icon" aria-hidden="true">—</span>
      <strong>${labelByKey[key] ?? key.replaceAll('_', ' ')}</strong>
      <p>${reasonByKey[key] ?? 'No measurement is exported for this quantity.'}</p>
    </article>`).join('');
}

function createMethodPanel(meta) {
  const config = meta.config;
  const facts = [
    ...CLASS_NAMES.map((name) => `<p><b>${CLASS_LABELS[name]}:</b> ${({
      correct: 'the routed path matches the true-snapshot oracle path or delay.',
      stale_working: 'the path is usable but differs from the oracle path and delay.',
      loop: 'the routing result reports a forwarding loop.',
      blackhole: 'no usable route exists, or at least one routed link is down.',
    })[name]}</p>`),
    '<p><b>SAPR:</b> assumes perfect orbit prediction and no signalling.</p>',
    '<p><b>Probe model:</b> route-level probes are not packet simulation.</p>',
    `<p><b>Dataset:</b> one seed (${meta.seed}) and a ${meta.duration_s} s observation window.</p>`,
    `<p><b>LinkState hold-down:</b> ${config['routing.spf_holddown_s']} s.</p>`,
    `<p><b>DV triggered holdoff:</b> ${config['routing.dv_triggered_holdoff_s']} s.</p>`,
    `<p><b>Detection delay:</b> ${config['routing.detection_delay_s']} s.</p>`,
  ];
  document.querySelector('#method-content').innerHTML = facts.join('');
}

export function createAnalyticsView({ meta, routes, events, summary, getTime, setTime, devMode = false }) {
  const routers = routes.routers;
  const duration = meta.duration_s;
  const tolerance = meta.config['evaluation.near_optimal_tolerance_ms'];
  const pairLabels = Object.keys(summary.per_pair);
  const pairMetrics = pairLabels.map((_, index) => accumulatePair(
    index, routes, events.events, routers, tolerance, duration,
  ));
  const allMetrics = aggregatePairs(pairMetrics, routers, duration);
  const pairSelector = document.querySelector('#analytics-pair');
  const charts = new Map();
  let selectedPairIndex = 0;
  let active = false;
  let cursorSecond = null;

  pairSelector.innerHTML = pairLabels.map((label, index) =>
    `<option value="${index}">${label.replace(' -> ', ' → ')}</option>`
  ).join('');
  createHeadlineTable(summary);
  createNotMeasuredCards(meta);
  createMethodPanel(meta);

  function currentMetrics() {
    return pairMetrics[selectedPairIndex];
  }

  function currentPairKey() {
    return pairLabels[selectedPairIndex];
  }

  function selectChartMetrics() {
    return currentMetrics();
  }

  function populatePairBreakdown() {
    const metrics = currentMetrics();
    document.querySelector('#pair-breakdown-title').textContent = currentPairKey().replace(' -> ', ' → ');
    const table = document.querySelector('#pair-breakdown-table');
    const columns = ['Router', '%correct', '%stale_working', '%loop', '%blackhole', '%near_optimal', 'Mean delay ms'];
    table.innerHTML = `<thead><tr>${columns.map((column) => `<th>${column}</th>`).join('')}</tr></thead><tbody>${
      routers.map((router) => {
        const item = metrics[router];
        return `<tr><th><i class="router-swatch" style="--router-color:${ROUTER_COLOURS[router]}"></i>${router}</th>
          ${CLASS_NAMES.map((name) => `<td>${metricFormat(item.classPercentages[name])}</td>`).join('')}
          <td>${metricFormat(item.nearOptimalPercent)}</td><td>${metricFormat(item.meanDelay, 3)}</td></tr>`;
      }).join('')
    }</tbody>`;
  }

  function updateClassChart() {
    const metrics = selectChartMetrics();
    charts.get('quality').setOption({
      ...chartBase(),
      grid: { left: 96, right: 22, top: 32, bottom: 24 },
      xAxis: { type: 'value', max: 100, axisLabel: { color: DARK_TEXT, formatter: '{value}%' }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      yAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3' }, axisLine: { show: false }, axisTick: { show: false } },
      series: CLASS_NAMES.map((name) => ({
        name: CLASS_LABELS[name],
        type: 'bar',
        stack: 'classes',
        barMaxWidth: 23,
        itemStyle: { color: CLASS_COLOURS[name], borderRadius: name === 'blackhole' ? [0, 4, 4, 0] : 0 },
        data: routers.map((router) => metrics[router].classPercentages[name]),
      })),
    }, true);
  }

  function updateNearChart() {
    const metrics = selectChartMetrics();
    charts.get('near').setOption({
      ...chartBase(),
      legend: { show: false },
      grid: { left: 96, right: 24, top: 32, bottom: 24 },
      xAxis: { type: 'value', max: 100, axisLabel: { color: DARK_TEXT, formatter: '{value}%' }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      yAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3' }, axisLine: { show: false }, axisTick: { show: false } },
      series: [{
        name: '% near-optimal',
        type: 'bar',
        barMaxWidth: 22,
        data: routers.map((router) => ({
          value: metrics[router].nearOptimalPercent,
          itemStyle: { color: ROUTER_COLOURS[router], borderRadius: [0, 4, 4, 0] },
        })),
      }],
    }, true);
  }

  function updateDelayChart() {
    const metrics = selectChartMetrics();
    const oracleMean = metrics.Oracle.meanDelay;
    charts.get('delay').setOption({
      ...chartBase(),
      grid: { left: 52, right: 18, top: 38, bottom: 45 },
      xAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3', rotate: 0 }, axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false } },
      yAxis: { type: 'value', name: 'ms', nameTextStyle: { color: DARK_TEXT, fontSize: 9 }, axisLabel: { color: DARK_TEXT }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      series: [
        { name: 'Mean actual', type: 'bar', barMaxWidth: 19, itemStyle: { color: '#22d3ee', borderRadius: [3, 3, 0, 0] }, data: routers.map((router) => metrics[router].meanDelay) },
        { name: 'p99 actual', type: 'bar', barMaxWidth: 19, itemStyle: { color: '#8b5cf6', borderRadius: [3, 3, 0, 0] }, data: routers.map((router) => metrics[router].p99Delay), markLine: Number.isFinite(oracleMean) ? { symbol: 'none', label: { position: 'insideEndTop', formatter: `Oracle mean ${oracleMean.toFixed(2)} ms`, color: '#fff', fontSize: 8 }, lineStyle: { color: '#fff', type: 'dashed', width: 1 }, data: [{ yAxis: oracleMean }] } : undefined },
      ],
    }, true);
  }

  function updateStretchChart() {
    const metrics = selectChartMetrics();
    charts.get('stretch').setOption({
      ...chartBase(),
      legend: { show: false },
      grid: { left: 52, right: 18, top: 31, bottom: 45 },
      xAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3' }, axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false } },
      yAxis: { type: 'value', name: '×', nameTextStyle: { color: DARK_TEXT, fontSize: 9 }, axisLabel: { color: DARK_TEXT }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      series: [{ name: 'Mean stretch', type: 'bar', barMaxWidth: 26, data: routers.map((router) => ({ value: metrics[router].meanStretch, itemStyle: { color: ROUTER_COLOURS[router], borderRadius: [3, 3, 0, 0] } })) }],
    }, true);
  }

  function overheadRows(column) {
    const index = summary.headers.indexOf(column);
    return summary.rows.map((row) => ({ router: row[0], value: numeric(row[index]) ?? 0 }));
  }

  function updateMessagesChart() {
    const messages = overheadRows('measured control messages');
    const boot = overheadRows('boot messages');
    const nonzero = [...messages, ...boot].map((item) => item.value).filter((value) => value > 0);
    const logarithmic = nonzero.length > 0 && Math.max(...nonzero) / Math.min(...nonzero) > 100;
    charts.get('messages').setOption({
      ...chartBase(),
      grid: { left: 62, right: 18, top: 35, bottom: 42 },
      xAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3' }, axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false } },
      yAxis: { type: logarithmic ? 'log' : 'value', logBase: 10, name: logarithmic ? 'log count' : 'messages', nameTextStyle: { color: DARK_TEXT, fontSize: 8 }, axisLabel: { color: DARK_TEXT }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      series: [
        { name: 'Measured control messages', type: 'bar', barMaxWidth: 20, itemStyle: { color: '#22d3ee', borderRadius: [3, 3, 0, 0] }, data: messages.map((item) => item.value || null) },
        { name: 'Boot messages', type: 'bar', barMaxWidth: 20, itemStyle: { color: '#8b5cf6', decal: { symbol: 'rect', symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [2, 3], rotation: -Math.PI / 4, color: 'rgba(255,255,255,0.55)' }, borderRadius: [3, 3, 0, 0] }, data: boot.map((item) => item.value || null) },
      ],
    }, true);
  }

  function updateBytesChart() {
    const bytes = overheadRows('control bytes');
    const nonzero = bytes.map((item) => item.value).filter((value) => value > 0);
    const logarithmic = nonzero.length > 0 && Math.max(...nonzero) / Math.min(...nonzero) > 100;
    charts.get('bytes').setOption({
      ...chartBase(),
      grid: { left: 62, right: 18, top: 35, bottom: 42 },
      xAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3' }, axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false } },
      yAxis: { type: logarithmic ? 'log' : 'value', logBase: 10, name: logarithmic ? 'log bytes' : 'bytes', nameTextStyle: { color: DARK_TEXT, fontSize: 8 }, axisLabel: { color: DARK_TEXT }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      series: [{ name: 'Control bytes', type: 'bar', barMaxWidth: 28, data: bytes.map((item) => ({ value: item.value || null, itemStyle: { color: ROUTER_COLOURS[item.router], borderRadius: [3, 3, 0, 0] } })) }],
    }, true);
  }

  function updateStabilityChart() {
    const metrics = selectChartMetrics();
    charts.get('stability').setOption({
      ...chartBase(),
      legend: { show: false },
      grid: { left: 52, right: 18, top: 32, bottom: 43 },
      xAxis: { type: 'category', data: routers, axisLabel: { color: '#d4dde3' }, axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false } },
      yAxis: { type: 'value', name: 'changes / min', nameTextStyle: { color: DARK_TEXT, fontSize: 8 }, axisLabel: { color: DARK_TEXT }, splitLine: { lineStyle: { color: GRID_COLOR } } },
      series: [{ name: 'Route changes / min', type: 'bar', barMaxWidth: 26, data: routers.map((router) => ({ value: metrics[router].changesPerMinute, itemStyle: { color: ROUTER_COLOURS[router], borderRadius: [3, 3, 0, 0] } })) }],
    }, true);
  }

  function timelineOption() {
    const times = routes.times_s;
    const pair = routes.pairs[selectedPairIndex];
    const data = [];
    for (let timeIndex = 0; timeIndex < times.length; timeIndex += 1) {
      const sample = routes.samples[timeIndex][selectedPairIndex];
      for (let routerIndex = 0; routerIndex < routers.length; routerIndex += 1) {
        data.push([timeIndex, routerIndex, CLASS_NAMES.indexOf(sample.classes[routerIndex])]);
      }
    }
    const displayName = pairLabels[selectedPairIndex].replace(' -> ', ' → ');
    return {
      ...chartBase(),
      animation: false,
      grid: { left: 88, right: 16, top: 7, bottom: 22 },
      tooltip: {
        trigger: 'item',
        formatter: ({ value }) => `${displayName}<br/>${routers[value[1]]} · ${times[value[0]]} s<br/>${CLASS_LABELS[CLASS_NAMES[value[2]]] ?? 'Unknown'}`,
        backgroundColor: 'rgba(10,14,22,0.96)',
        borderColor: 'rgba(255,255,255,0.13)',
        textStyle: { color: '#e5ebef', fontSize: 10 },
      },
      xAxis: { type: 'category', data: times, boundaryGap: true, axisLabel: { color: DARK_TEXT, formatter: (value) => value % 100 === 0 ? `${value}s` : '' }, axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false }, splitLine: { show: false } },
      yAxis: { type: 'category', data: routers, boundaryGap: true, axisLabel: { color: '#d4dde3', fontSize: 9 }, axisLine: { show: false }, axisTick: { show: false } },
      visualMap: {
        show: false,
        type: 'piecewise',
        dimension: 2,
        pieces: CLASS_NAMES.map((name, index) => ({ value: index, color: CLASS_COLOURS[name] })),
      },
      series: [{ name: 'Outcome class', type: 'heatmap', data, itemStyle: { borderWidth: 0, opacity: 0.96 }, emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } } }],
      graphic: [],
    };
  }

  function updateTimeline() {
    const chart = charts.get('timeline');
    chart.setOption(timelineOption(), true);
    chart.off('click');
    chart.on('click', (params) => {
      if (params.seriesType !== 'heatmap' || !Array.isArray(params.value)) return;
      const time = routes.times_s[params.value[0]];
      if (Number.isFinite(time)) setTime(time);
    });
    document.querySelector('#timeline-chart').dataset.pair = currentPairKey();
    cursorSecond = null;
    updateTime(getTime(), true);
  }

  function updatePairCharts() {
    populatePairBreakdown();
    if (!active) return;
    updateClassChart();
    updateNearChart();
    updateDelayChart();
    updateStretchChart();
    updateStabilityChart();
    updateTimeline();
    document.querySelector('#analytics-dashboard').dataset.selectedPair = currentPairKey();
  }

  function initializeCharts() {
    const options = { renderer: 'canvas' };
    for (const [key, id] of [
      ['quality', 'quality-chart'],
      ['near', 'near-optimal-chart'],
      ['delay', 'delay-chart'],
      ['stretch', 'stretch-chart'],
      ['messages', 'messages-chart'],
      ['bytes', 'bytes-chart'],
      ['stability', 'stability-chart'],
      ['timeline', 'timeline-chart'],
    ]) charts.set(key, init(document.querySelector(`#${id}`), null, options));
    updateMessagesChart();
    updateBytesChart();
    updatePairCharts();
    for (const chart of charts.values()) chart.resize();
  }

  function updateTime(time, force = false) {
    if (!active || !charts.has('timeline')) return;
    const chart = charts.get('timeline');
    const second = Math.floor(time);
    if (!force && cursorSecond === second) return;
    cursorSecond = second;
    const x = chart.convertToPixel({ xAxisIndex: 0 }, Math.max(routes.times_s[0], Math.min(routes.times_s.at(-1), time)));
    const width = chart.getWidth();
    if (!Number.isFinite(x) || x < 0 || x > width) return;
    chart.setOption({
      graphic: [{
        id: 'simulation-time-cursor',
        type: 'line',
        silent: true,
        shape: { x1: x, y1: 5, x2: x, y2: chart.getHeight() - 20 },
        style: { stroke: '#fbbf24', lineWidth: 1, lineDash: [4, 3], opacity: 0.95 },
      }],
    });
  }

  function activate() {
    if (!active) active = true;
    if (!charts.size) initializeCharts();
    else {
      for (const chart of charts.values()) chart.resize();
      updateTime(getTime(), true);
    }
    updatePairCharts();
  }

  pairSelector.addEventListener('change', () => {
    selectedPairIndex = Number(pairSelector.value);
    updatePairCharts();
  });
  window.addEventListener('resize', () => {
    for (const chart of charts.values()) chart.resize();
    updateTime(getTime(), true);
  });
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(() => {
      if (active) for (const chart of charts.values()) chart.resize();
    });
    for (const element of document.querySelectorAll('.chart-box')) observer.observe(element);
  }
  if (devMode) {
    window.__analyticsDebug = {
      getPairMetrics: (index = selectedPairIndex) => pairMetrics[index],
      getAllMetrics: () => allMetrics,
      getHeadlineRows: () => summary.rows,
      getChartValues: (key) => charts.get(key)?.getOption().series.map((series) => series.data),
      getSelectedPair: currentPairKey,
      getTimelineClickPoint: (time, router) => {
        const chart = charts.get('timeline');
        const bounds = document.querySelector('#timeline-chart').getBoundingClientRect();
        const timeIndex = routes.times_s.indexOf(time);
        const routerIndex = routers.indexOf(router);
        const [x, y] = chart.convertToPixel({ seriesIndex: 0 }, [timeIndex, routerIndex]);
        return {
          x: bounds.left + x,
          y: bounds.top + y,
        };
      },
    };
  }

  return { activate, updateTime };
}
