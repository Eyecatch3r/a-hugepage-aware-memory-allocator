(function runResultsExplorer() {
  "use strict";

  const results = window.TEMERAIRE_RESULTS;
  const model = window.TemeraireModel;
  const requestedEnvironment = new URLSearchParams(window.location.search).get("environment");
  let data = model?.selectEnvironment(results, requestedEnvironment);
  const SVG_NS = "http://www.w3.org/2000/svg";
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const metricNames = {
    combined: "Combined throughput",
    lpush: "LPUSH",
    lrange: "LRANGE",
  };
  const panelNames = ["overview", "explorer", "sensitivity", "method"];
  const state = {
    environment: data?.id || results?.defaultEnvironment || "",
    activePanel: "overview",
    releaseMode: "all",
    overviewMetric: "combined",
    order: "all",
    includeOutlier: true,
    selectedPair: data?.historical?.[0]?.id || "",
    explorerMetric: "combined",
    explorerView: "throughput",
    memoryMetric: "rssMiB",
    sensitivityMetric: "combined",
  };
  let previousDeltaPositions = new Map();
  let resizeTimer;

  function byId(id) {
    return document.getElementById(id);
  }

  function svgElement(name, attributes = {}, text = "") {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    if (text) element.textContent = text;
    return element;
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) element.textContent = value;
  }

  function formatRps(value) {
    return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(value);
  }

  function formatInteger(value) {
    return new Intl.NumberFormat("en").format(value);
  }

  function formatMiB(value) {
    return `${new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(value)} MiB`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    })[character]);
  }

  function niceMaximum(value) {
    if (value <= 0) return 1;
    const magnitude = 10 ** Math.floor(Math.log10(value));
    return Math.ceil(value / magnitude) * magnitude;
  }

  function linearScale(domainMin, domainMax, rangeMin, rangeMax) {
    if (domainMin === domainMax) return () => (rangeMin + rangeMax) / 2;
    return (value) => rangeMin + (((value - domainMin) / (domainMax - domainMin)) * (rangeMax - rangeMin));
  }

  function chartWidth(svg) {
    return Math.max(280, Math.floor(svg.parentElement.getBoundingClientRect().width - 14));
  }

  function chartHeight(stage) {
    const available = Math.floor(stage.getBoundingClientRect().height);
    if (available > 0) return Math.max(250, Math.min(560, available));
    return Math.max(250, Math.min(560, window.innerHeight - 340));
  }

  function configureSvg(svg, width, height) {
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.style.height = `${height}px`;
  }

  function appendGrid(svg, x1, x2, y1, y2, ticks, scale, formatter, orientation = "x") {
    ticks.forEach((tick) => {
      const position = scale(tick);
      if (orientation === "x") {
        svg.append(svgElement("line", { x1: position, x2: position, y1, y2, class: tick === 0 ? "zero-line" : "grid-line" }));
        svg.append(svgElement("text", { x: position, y: y2 + 22, "text-anchor": "middle" }, formatter(tick)));
        return;
      }
      svg.append(svgElement("line", { x1, x2, y1: position, y2: position, class: tick === 0 ? "zero-line" : "grid-line" }));
      svg.append(svgElement("text", { x: x1 - 10, y: position + 3, "text-anchor": "end" }, formatter(tick)));
    });
  }

  function showTooltip(tooltip, stage, clientX, clientY, contents) {
    const bounds = stage.getBoundingClientRect();
    const x = Math.min(Math.max(clientX - bounds.left, 90), bounds.width - 90);
    const y = Math.max(clientY - bounds.top, 65);
    tooltip.innerHTML = contents;
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
    tooltip.classList.add("is-visible");
  }

  function hideTooltip(tooltip) {
    tooltip.classList.remove("is-visible");
  }

  function attachTooltip(element, tooltip, stage, getContents) {
    element.addEventListener("pointerenter", (event) => showTooltip(tooltip, stage, event.clientX, event.clientY, getContents()));
    element.addEventListener("pointermove", (event) => showTooltip(tooltip, stage, event.clientX, event.clientY, getContents()));
    element.addEventListener("pointerleave", () => hideTooltip(tooltip));
    element.addEventListener("focus", () => {
      const bounds = element.getBoundingClientRect();
      showTooltip(tooltip, stage, bounds.left + (bounds.width / 2), bounds.top, getContents());
    });
    element.addEventListener("blur", () => hideTooltip(tooltip));
  }

  function animateFromPrevious(element, previous, current) {
    if (!previous || reducedMotion || typeof element.animate !== "function") return;
    const deltaX = previous.x - current.x;
    const deltaY = previous.y - current.y;
    if (Math.abs(deltaX) < 0.5 && Math.abs(deltaY) < 0.5) return;
    element.animate(
      [{ transform: `translate(${deltaX}px, ${deltaY}px)` }, { transform: "translate(0, 0)" }],
      { duration: 440, easing: "cubic-bezier(.2,.7,.2,1)" },
    );
  }

  function renderEnvironmentContext() {
    setText("environment-kicker", `Redis 6.0.9 · ${data.shortLabel}`);
    setText("scope-description", data.methodology.scope);
    const outlierControl = byId("overview-outlier").closest("label");
    outlierControl.hidden = !data.outlier;
    if (data.outlier) setText("outlier-label", data.outlier.label);
  }

  function initializeHero() {
    const offPairs = data.historical.filter((pair) => pair.releaseMode === "off");
    const offSummary = model.summariseDeltas(offPairs, "combined");
    const allSummary = model.summariseDeltas(data.historical, "combined");
    const samples = data.historical.reduce(
      (total, pair) => total + ((pair.legacy.trials + pair.temeraire.trials) * 2),
      0,
    );
    setText("hero-median", model.formatSigned(offSummary.median));
    setText("hero-positive", `${allSummary.positive}/${allSummary.count}`);
    setText("hero-samples", formatRps(samples));
    setText("generated-at", new Intl.DateTimeFormat("en", { dateStyle: "medium", timeZone: "UTC" }).format(new Date(results.generatedAt)));
  }

  function overviewPairs() {
    return model.filterPairs(data.historical, {
      releaseMode: state.releaseMode,
      order: state.order,
      includeOutlier: state.includeOutlier,
      outlierId: data.outlier?.id,
    });
  }

  function renderOverview() {
    const pairs = overviewPairs();
    const summary = model.summariseDeltas(pairs, state.overviewMetric);
    setText("delta-chart-title", `${metricNames[state.overviewMetric]} delta`);
    setText("overview-median", summary.count ? model.formatSigned(summary.median) : "—");
    setText("overview-positive", `${summary.positive} of ${summary.count}`);
    setText("overview-range", summary.count ? `${model.formatSigned(summary.min)} to ${model.formatSigned(summary.max)}` : "—");
    setText("overview-count", String(summary.count));
    if (!data.outlier) {
      setText("overview-note", "Select filters to compare a smaller set of matched runs.");
    } else {
      const status = state.includeOutlier
        ? "The chart includes the identified outlier. Clear Show outlier to remove it."
        : "The chart does not include the identified outlier.";
      setText("overview-note", data.outlier.note ? `${status} ${data.outlier.note}` : status);
    }
    renderDeltaChart(pairs);
  }

  function renderDeltaChart(pairs) {
    const svg = byId("delta-chart");
    const stage = byId("delta-stage");
    const tooltip = byId("delta-tooltip");
    const width = chartWidth(svg);
    const compact = width < 620;
    const margin = { top: 30, right: compact ? 44 : 70, bottom: 48, left: compact ? 84 : 124 };
    const height = chartHeight(stage);
    const rowHeight = Math.min(compact ? 34 : 44, Math.max(22, (height - margin.top - margin.bottom) / Math.max(1, pairs.length)));
    configureSvg(svg, width, height);
    if (!pairs.length) {
      const message = svgElement("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "label-text" }, "No matched pairs meet these filters");
      svg.append(message);
      return;
    }

    const values = pairs.map((pair) => pair.deltaPercent[state.overviewMetric]);
    const maxAbsolute = Math.max(1, ...values.map(Math.abs));
    const extent = Math.ceil(maxAbsolute * 1.12);
    const scaleX = linearScale(-extent, extent, margin.left, width - margin.right);
    const plotBottom = height - margin.bottom;
    const tickCount = compact ? 5 : 7;
    const ticks = Array.from({ length: tickCount }, (_, index) => -extent + ((extent * 2 * index) / (tickCount - 1)));
    appendGrid(svg, margin.left, width - margin.right, margin.top - 10, plotBottom, ticks, scaleX, (value) => `${Math.round(value)}%`);
    const nextPositions = new Map();

    pairs.forEach((pair, index) => {
      const value = pair.deltaPercent[state.overviewMetric];
      const y = margin.top + (index * rowHeight) + (rowHeight / 2);
      const x = scaleX(value);
      const zeroX = scaleX(0);
      const group = svgElement("g", {
        class: "mark",
        tabindex: "0",
        role: "img",
        "aria-label": `${pair.family}, release ${pair.releaseMode}: ${model.formatSigned(value)}`,
      });
      const label = compact ? pair.family.replace("balanced ", "B") : pair.family;
      group.append(svgElement("text", { x: margin.left - 13, y: y + 4, "text-anchor": "end", class: "label-text" }, `${label} · ${pair.releaseMode}`));
      group.append(svgElement("line", {
        x1: Math.min(zeroX, x), x2: Math.max(zeroX, x), y1: y, y2: y,
        class: `delta-stem ${value >= 0 ? "positive" : "negative"}`,
      }));
      if (pair.releaseMode === "off") {
        group.append(svgElement("circle", { cx: x, cy: y, r: 5.5, class: `release-off-mark ${value < 0 ? "negative-mark" : ""}` }));
      } else {
        const size = 6;
        group.append(svgElement("path", {
          d: `M ${x} ${y - size} L ${x + size} ${y} L ${x} ${y + size} L ${x - size} ${y} Z`,
          class: `release-on-mark ${value < 0 ? "negative-mark" : ""}`,
        }));
      }
      const textAnchor = value >= 0 ? "start" : "end";
      const textX = value >= 0 ? x + 10 : x - 10;
      group.append(svgElement("text", {
        x: textX, y: y + 4, "text-anchor": textAnchor,
        class: `value-text ${value >= 0 ? "positive-text" : "negative-text"}`,
      }, model.formatSigned(value)));
      svg.append(group);
      attachTooltip(group, tooltip, stage, () => (
        `<strong>${pair.family} · release ${pair.releaseMode}</strong>`
        + `${metricNames[state.overviewMetric]}: <b>${model.formatSigned(value)}</b><br>`
        + `${pair.order === "L first" ? "Legacy" : "Temeraire"} ran first`
      ));
      const current = { x, y };
      animateFromPrevious(group, previousDeltaPositions.get(pair.id), current);
      nextPositions.set(pair.id, current);
    });
    previousDeltaPositions = nextPositions;
  }

  function initializePairSelector() {
    const select = byId("pair-select");
    select.replaceChildren();
    data.historical.forEach((pair) => {
      const option = document.createElement("option");
      option.value = pair.id;
      option.textContent = `${pair.family} · release ${pair.releaseMode} · ${pair.order}`;
      select.append(option);
    });
    select.value = state.selectedPair;
  }

  function selectEnvironment(environmentId) {
    data = model.selectEnvironment(results, environmentId);
    state.environment = data.id;
    state.selectedPair = data.historical[0]?.id || "";
    state.includeOutlier = true;
    byId("overview-outlier").checked = true;
    byId("environment-select").value = data.id;
    const url = new URL(window.location.href);
    url.searchParams.set("environment", data.id);
    window.history.replaceState(null, "", url);
    previousDeltaPositions = new Map();
    renderEnvironmentContext();
    initializeHero();
    initializePairSelector();
    renderActivePanel();
  }

  function selectedPair() {
    return data.historical.find((pair) => pair.id === state.selectedPair) || data.historical[0];
  }

  function updateRunCard(pair) {
    const delta = pair.deltaPercent[state.explorerMetric];
    setText("run-family", `${pair.family} / release ${pair.releaseMode}`);
    setText("run-delta", model.formatSigned(delta));
    setText("run-delta-label", `Temeraire ${metricNames[state.explorerMetric].toLowerCase()} delta`);
    setText("run-release", pair.releaseMode === "on" ? "On" : "Off");
    setText("run-order", pair.order === "L first" ? "Legacy first" : "Temeraire first");
    setText("run-trials", formatInteger(Math.min(pair.legacy.trials, pair.temeraire.trials)));
    byId("legacy-source").href = `../${pair.legacy.path}/summary.csv`;
    byId("temeraire-source").href = `../${pair.temeraire.path}/summary.csv`;
  }

  function renderExplorer() {
    const pair = selectedPair();
    if (!pair) return;
    updateRunCard(pair);
    const isMemory = state.explorerView === "memory";
    byId("memory-control").hidden = !isMemory;
    document.querySelector('[data-control="explorerMetric"]').closest("fieldset").hidden = isMemory;
    if (state.explorerView === "throughput") renderThroughputChart(pair);
    if (state.explorerView === "distribution") renderDistributionChart(pair);
    if (state.explorerView === "memory") renderMemoryChart(pair);
  }

  function renderThroughputChart(pair) {
    setText("explorer-chart-label", "Absolute requests per second");
    setText("explorer-chart-title", "Throughput by operation");
    const svg = byId("explorer-chart");
    const stage = byId("explorer-stage");
    const tooltip = byId("explorer-tooltip");
    const width = chartWidth(svg);
    const height = chartHeight(stage);
    const compact = width < 620;
    const margin = { top: 54, right: compact ? 32 : 74, bottom: 55, left: compact ? 82 : 125 };
    configureSvg(svg, width, height);
    const metrics = ["combined", "lpush", "lrange"];
    const maximum = niceMaximum(Math.max(...metrics.flatMap((metric) => [pair.legacy.throughput[metric], pair.temeraire.throughput[metric]])) * 1.05);
    const scaleX = linearScale(0, maximum, margin.left, width - margin.right);
    const ticks = model.linearTicks(0, maximum, compact ? 4 : 6);
    appendGrid(svg, margin.left, width - margin.right, margin.top - 24, height - margin.bottom, ticks, scaleX, formatRps);

    const plotHeight = height - margin.top - margin.bottom;
    metrics.forEach((metric, index) => {
      const y = margin.top + (plotHeight * (index + 0.5) / metrics.length);
      const legacy = pair.legacy.throughput[metric];
      const temeraire = pair.temeraire.throughput[metric];
      const highlighted = metric === state.explorerMetric;
      const group = svgElement("g", { opacity: highlighted ? "1" : "0.48" });
      group.append(svgElement("text", { x: margin.left - 14, y: y + 4, "text-anchor": "end", class: "label-text" }, metricNames[metric].replace(" throughput", "")));
      group.append(svgElement("line", { x1: scaleX(legacy), x2: scaleX(temeraire), y1: y, y2: y, class: "dumbbell-line" }));
      const legacyMark = svgElement("circle", { cx: scaleX(legacy), cy: y, r: highlighted ? 7 : 5, class: "mark legacy-mark", tabindex: "0" });
      const temeraireMark = svgElement("circle", { cx: scaleX(temeraire), cy: y, r: highlighted ? 7 : 5, class: "mark temeraire-mark", tabindex: "0" });
      group.append(legacyMark, temeraireMark);
      group.append(svgElement("text", { x: scaleX(legacy), y: y - 15, "text-anchor": "middle", class: "value-text" }, formatRps(legacy)));
      group.append(svgElement("text", { x: scaleX(temeraire), y: y + 25, "text-anchor": "middle", class: "value-text positive-text" }, formatRps(temeraire)));
      svg.append(group);
      attachTooltip(legacyMark, tooltip, stage, () => `<strong>Legacy · ${metricNames[metric]}</strong>${formatInteger(Math.round(legacy))} requests/s`);
      attachTooltip(temeraireMark, tooltip, stage, () => `<strong>Temeraire · ${metricNames[metric]}</strong>${formatInteger(Math.round(temeraire))} requests/s<br><b>${model.formatSigned(pair.deltaPercent[metric])}</b> vs legacy`);
    });
  }

  function renderDistributionChart(pair) {
    setText("explorer-chart-label", "Trial-level spread · P05 to P95");
    setText("explorer-chart-title", `${metricNames[state.explorerMetric]} distribution`);
    const svg = byId("explorer-chart");
    const stage = byId("explorer-stage");
    const tooltip = byId("explorer-tooltip");
    const width = chartWidth(svg);
    const height = chartHeight(stage);
    const compact = width < 620;
    const margin = { top: 70, right: compact ? 28 : 65, bottom: 60, left: compact ? 85 : 125 };
    configureSvg(svg, width, height);
    const legacy = pair.legacy.distributions[state.explorerMetric];
    const temeraire = pair.temeraire.distributions[state.explorerMetric];
    if (!legacy || !temeraire) {
      svg.append(svgElement("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "label-text" }, "Trial distributions are unavailable for this pair"));
      return;
    }
    const low = Math.min(legacy.p05, temeraire.p05);
    const high = Math.max(legacy.p95, temeraire.p95);
    const padding = (high - low) * 0.12 || high * 0.05;
    const scaleX = linearScale(low - padding, high + padding, margin.left, width - margin.right);
    const tickCount = compact ? 4 : 6;
    const ticks = Array.from({ length: tickCount }, (_, index) => (low - padding) + (((high - low) + (2 * padding)) * index / (tickCount - 1)));
    appendGrid(svg, margin.left, width - margin.right, margin.top - 20, height - margin.bottom, ticks, scaleX, formatRps);

    const distributionHeight = height - margin.top - margin.bottom;
    [
      { name: "Legacy", summary: legacy, className: "box-legacy" },
      { name: "Temeraire", summary: temeraire, className: "box-temeraire" },
    ].forEach((series, index) => {
      const y = margin.top + (distributionHeight * (index + 0.5) / 2);
      const group = svgElement("g", { class: "mark", tabindex: "0", role: "img", "aria-label": `${series.name} distribution` });
      group.append(svgElement("text", { x: margin.left - 14, y: y + 4, "text-anchor": "end", class: "label-text" }, series.name));
      group.append(svgElement("line", { x1: scaleX(series.summary.p05), x2: scaleX(series.summary.p95), y1: y, y2: y, class: "box-whisker" }));
      group.append(svgElement("line", { x1: scaleX(series.summary.p05), x2: scaleX(series.summary.p05), y1: y - 12, y2: y + 12, class: "box-whisker" }));
      group.append(svgElement("line", { x1: scaleX(series.summary.p95), x2: scaleX(series.summary.p95), y1: y - 12, y2: y + 12, class: "box-whisker" }));
      group.append(svgElement("rect", { x: scaleX(series.summary.p25), y: y - 24, width: Math.max(1, scaleX(series.summary.p75) - scaleX(series.summary.p25)), height: 48, class: series.className }));
      group.append(svgElement("line", { x1: scaleX(series.summary.median), x2: scaleX(series.summary.median), y1: y - 24, y2: y + 24, class: "median-stroke" }));
      const meanX = scaleX(series.summary.mean);
      group.append(svgElement("path", { d: `M ${meanX} ${y - 5} L ${meanX + 5} ${y} L ${meanX} ${y + 5} L ${meanX - 5} ${y} Z`, class: "mean-mark" }));
      group.append(svgElement("text", { x: scaleX(series.summary.median), y: y - 34, "text-anchor": "middle", class: "value-text" }, formatRps(series.summary.median)));
      svg.append(group);
      attachTooltip(group, tooltip, stage, () => (
        `<strong>${series.name} · ${metricNames[state.explorerMetric]}</strong>`
        + `Median ${formatInteger(Math.round(series.summary.median))} requests/s<br>`
        + `Mean ${formatInteger(Math.round(series.summary.mean))}<br>`
        + `P05–P95 ${formatRps(series.summary.p05)}–${formatRps(series.summary.p95)}`
      ));
    });
  }

  function linePath(points, scaleX, scaleY) {
    return points.map((point, index) => `${index ? "L" : "M"} ${scaleX(point.trial)} ${scaleY(point.value)}`).join(" ");
  }

  function renderMemoryChart(pair) {
    const measureName = state.memoryMetric === "rssMiB" ? "Resident memory" : "Process huge pages";
    setText("explorer-chart-label", "Process snapshots over benchmark trials");
    setText("explorer-chart-title", `${measureName} trajectory`);
    const svg = byId("explorer-chart");
    const stage = byId("explorer-stage");
    const tooltip = byId("explorer-tooltip");
    const width = chartWidth(svg);
    const height = chartHeight(stage);
    const compact = width < 620;
    const margin = { top: 45, right: compact ? 25 : 55, bottom: 62, left: compact ? 64 : 80 };
    configureSvg(svg, width, height);
    const series = [
      { name: "Legacy", values: pair.legacy.memory, className: "legacy-line", pointClass: "legacy-point" },
      { name: "Temeraire", values: pair.temeraire.memory, className: "temeraire-line", pointClass: "temeraire-point" },
    ];
    if (series.some((item) => !item.values.length)) {
      svg.append(svgElement("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "label-text" }, "Memory snapshots are unavailable for this pair"));
      return;
    }
    const maximumTrial = Math.max(...series.flatMap((item) => item.values.map((point) => point.trial)));
    const maximumValue = Math.max(1, ...series.flatMap((item) => item.values.map((point) => point[state.memoryMetric])));
    const yMax = niceMaximum(maximumValue * 1.08);
    const scaleX = linearScale(0, maximumTrial, margin.left, width - margin.right);
    const scaleY = linearScale(0, yMax, height - margin.bottom, margin.top);
    const xTicks = model.linearTicks(0, maximumTrial, compact ? 4 : 5);
    const yTicks = Array.from({ length: 5 }, (_, index) => yMax * index / 4);
    appendGrid(svg, margin.left, width - margin.right, margin.top, height - margin.bottom, yTicks, scaleY, formatMiB, "y");
    xTicks.forEach((tick) => {
      const x = scaleX(tick);
      svg.append(svgElement("text", { x, y: height - margin.bottom + 25, "text-anchor": "middle" }, formatInteger(Math.round(tick))));
    });
    svg.append(svgElement("text", { x: width - margin.right, y: height - 14, "text-anchor": "end" }, "Benchmark trial"));

    series.forEach((item) => {
      const points = item.values.map((point) => ({ ...point, value: point[state.memoryMetric] }));
      svg.append(svgElement("path", { d: linePath(points, scaleX, scaleY), class: `memory-line ${item.className}` }));
      points.forEach((point) => {
        const mark = svgElement("circle", { cx: scaleX(point.trial), cy: scaleY(point.value), r: 4.5, class: `memory-point ${item.pointClass}`, tabindex: "0" });
        svg.append(mark);
        attachTooltip(mark, tooltip, stage, () => `<strong>${item.name} · trial ${formatInteger(point.trial)}</strong>${measureName}: <b>${formatMiB(point.value)}</b>`);
      });
    });
  }

  function renderSensitivity() {
    const records = data.releaseSensitivity;
    const section = byId("sensitivity");
    if (!records.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    setText("sensitivity-chart-title", `${metricNames[state.sensitivityMetric]} delta by release rate`);
    const groups = model.groupSensitivity(records, state.sensitivityMetric);
    const table = byId("sensitivity-table");
    table.replaceChildren();
    groups.forEach((group) => {
      const row = document.createElement("tr");
      [
        `${group.rateMiB} MiB/s`,
        String(group.values.length),
        model.formatSigned(group.median),
      ].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      table.append(row);
    });
    renderSensitivityChart(records, groups);
  }

  function renderSensitivityChart(records, groups) {
    const svg = byId("sensitivity-chart");
    const stage = byId("sensitivity-stage");
    const tooltip = byId("sensitivity-tooltip");
    const width = chartWidth(svg);
    const height = chartHeight(stage);
    const compact = width < 620;
    const margin = { top: 36, right: compact ? 30 : 58, bottom: 62, left: compact ? 56 : 72 };
    configureSvg(svg, width, height);
    const values = records.map((record) => record.deltaPercent[state.sensitivityMetric]);
    const extent = Math.max(2, Math.ceil(Math.max(...values.map(Math.abs)) * 1.1));
    const rates = groups.map((group) => group.rateMiB);
    const logMin = Math.log2(Math.min(...rates));
    const logMax = Math.log2(Math.max(...rates));
    const scaleX = (rate) => linearScale(logMin, logMax, margin.left, width - margin.right)(Math.log2(rate));
    const scaleY = linearScale(-extent, extent, height - margin.bottom, margin.top);
    const yTicks = [-extent, -extent / 2, 0, extent / 2, extent];
    appendGrid(svg, margin.left, width - margin.right, margin.top, height - margin.bottom, yTicks, scaleY, (value) => `${Math.round(value)}%`, "y");
    rates.forEach((rate) => {
      svg.append(svgElement("text", { x: scaleX(rate), y: height - margin.bottom + 26, "text-anchor": "middle" }, `${rate} MiB/s`));
    });
    const medianPoints = groups.map((group) => ({ trial: Math.log2(group.rateMiB), value: group.median }));
    const medianScaleX = linearScale(logMin, logMax, margin.left, width - margin.right);
    svg.append(svgElement("path", { d: linePath(medianPoints, medianScaleX, scaleY), class: "median-path" }));

    records.forEach((record) => {
      const value = record.deltaPercent[state.sensitivityMetric];
      const jitter = (((record.pair - 1) % 5) - 2) * (compact ? 3 : 5);
      const mark = svgElement("circle", {
        cx: scaleX(record.rateMiB) + jitter,
        cy: scaleY(value),
        r: 5,
        class: "sensitivity-point",
        tabindex: "0",
        role: "img",
        "aria-label": `${record.rateMiB} MiB per second pair ${record.pair}: ${model.formatSigned(value)}`,
      });
      svg.append(mark);
      attachTooltip(mark, tooltip, stage, () => (
        `<strong>${record.rateMiB} MiB/s · pair ${record.pair}</strong>`
        + `${metricNames[state.sensitivityMetric]}: <b>${model.formatSigned(value)}</b><br>${escapeHtml(record.order)}`
      ));
    });
    groups.forEach((group) => {
      svg.append(svgElement("circle", { cx: scaleX(group.rateMiB), cy: scaleY(group.median), r: 5.5, class: "median-point" }));
      svg.append(svgElement("text", { x: scaleX(group.rateMiB), y: scaleY(group.median) - 14, "text-anchor": "middle", class: "value-text positive-text" }, model.formatSigned(group.median)));
    });
  }

  function bindSegmentedControl(name, callback) {
    const control = document.querySelector(`[data-control="${name}"]`);
    if (!control) return;
    control.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-value]");
      if (!button) return;
      control.querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      callback(button.dataset.value);
    });
  }

  function bindControls() {
    byId("environment-select").addEventListener("change", (event) => {
      selectEnvironment(event.target.value);
    });
    bindSegmentedControl("releaseMode", (value) => {
      state.releaseMode = value;
      renderOverview();
    });
    bindSegmentedControl("explorerMetric", (value) => {
      state.explorerMetric = value;
      renderExplorer();
    });
    byId("overview-metric").addEventListener("change", (event) => {
      state.overviewMetric = event.target.value;
      renderOverview();
    });
    byId("overview-order").addEventListener("change", (event) => {
      state.order = event.target.value;
      renderOverview();
    });
    byId("overview-outlier").addEventListener("change", (event) => {
      state.includeOutlier = event.target.checked;
      renderOverview();
    });
    byId("pair-select").addEventListener("change", (event) => {
      state.selectedPair = event.target.value;
      renderExplorer();
    });
    byId("memory-metric").addEventListener("change", (event) => {
      state.memoryMetric = event.target.value;
      renderExplorer();
    });
    byId("sensitivity-metric").addEventListener("change", (event) => {
      state.sensitivityMetric = event.target.value;
      renderSensitivity();
    });
    document.querySelector(".view-tabs").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-view]");
      if (!button) return;
      document.querySelectorAll(".view-tabs button").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
      state.explorerView = button.dataset.view;
      renderExplorer();
    });
  }

  function renderActivePanel() {
    if (state.activePanel === "overview") renderOverview();
    if (state.activePanel === "explorer") renderExplorer();
    if (state.activePanel === "sensitivity") renderSensitivity();
  }

  function activatePanel(panelName, moveFocus = false) {
    if (!panelNames.includes(panelName)) return;
    state.activePanel = panelName;
    document.querySelector(".app-shell").dataset.currentPanel = panelName;
    document.querySelectorAll(".app-panel").forEach((panel) => {
      panel.hidden = panel.id !== panelName;
      panel.classList.remove("panel-enter");
    });
    const activePanel = byId(panelName);
    const tabs = [...document.querySelectorAll(".primary-nav button[data-panel]")];
    tabs.forEach((tab) => {
      const selected = tab.dataset.panel === panelName;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && moveFocus) tab.focus();
    });
    setText("panel-position", `${String(panelNames.indexOf(panelName) + 1).padStart(2, "0")} / 04`);
    if (!reducedMotion) activePanel.classList.add("panel-enter");
    window.requestAnimationFrame(renderActivePanel);
  }

  function bindNavigation() {
    const navigation = document.querySelector(".primary-nav");
    navigation.addEventListener("click", (event) => {
      const tab = event.target.closest("button[data-panel]");
      if (tab) activatePanel(tab.dataset.panel);
    });
    navigation.addEventListener("keydown", (event) => {
      const currentIndex = panelNames.indexOf(state.activePanel);
      const keyOffsets = { ArrowLeft: -1, ArrowRight: 1 };
      if (Object.hasOwn(keyOffsets, event.key)) {
        event.preventDefault();
        const nextIndex = (currentIndex + keyOffsets[event.key] + panelNames.length) % panelNames.length;
        activatePanel(panelNames[nextIndex], true);
      }
      if (event.key === "Home") {
        event.preventDefault();
        activatePanel(panelNames[0], true);
      }
      if (event.key === "End") {
        event.preventDefault();
        activatePanel(panelNames.at(-1), true);
      }
    });
  }

  function initialize() {
    if (!results || !data || !model || !Array.isArray(data.historical)) {
      document.body.innerHTML = '<main class="empty-state">The generated result bundle is missing. Run <code>python scripts/build_results_site.py</code>.</main>';
      return;
    }
    byId("environment-select").value = state.environment;
    renderEnvironmentContext();
    initializeHero();
    initializePairSelector();
    bindControls();
    bindNavigation();
    const requestedPanel = window.location.hash.slice(1);
    activatePanel(panelNames.includes(requestedPanel) ? requestedPanel : "overview");
    window.addEventListener("resize", () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(renderActivePanel, 120);
    }, { passive: true });
  }

  initialize();
})();
