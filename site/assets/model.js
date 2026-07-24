(function attachTemeraireModel(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.TemeraireModel = api;
})(typeof globalThis === "undefined" ? window : globalThis, function createTemeraireModel() {
  "use strict";

  const OUTLIER_ID = "balanced-4-on";

  function selectEnvironment(results, requestedId) {
    const environments = results?.environments;
    if (!environments) return results;
    const selectedId = Object.hasOwn(environments, requestedId)
      ? requestedId
      : results.defaultEnvironment;
    return environments[selectedId];
  }

  function median(values) {
    if (values.length === 0) return Number.NaN;
    const ordered = [...values].sort((first, second) => first - second);
    const middle = Math.floor(ordered.length / 2);
    if (ordered.length % 2 === 1) return ordered[middle];
    return (ordered[middle - 1] + ordered[middle]) / 2;
  }

  function filterPairs(pairs, filters) {
    const outlierId = filters.outlierId || OUTLIER_ID;
    return pairs.filter((pair) => {
      if (filters.releaseMode !== "all" && pair.releaseMode !== filters.releaseMode) return false;
      if (filters.order !== "all" && pair.order !== filters.order) return false;
      if (!filters.includeOutlier && pair.id === outlierId) return false;
      return true;
    });
  }

  function summariseDeltas(pairs, metric) {
    const values = pairs.map((pair) => pair.deltaPercent[metric]);
    if (values.length === 0) {
      return { count: 0, positive: 0, median: Number.NaN, min: Number.NaN, max: Number.NaN };
    }
    return {
      count: values.length,
      positive: values.filter((value) => value > 0).length,
      median: median(values),
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }

  function groupSensitivity(records, metric) {
    const groups = new Map();
    records.forEach((record) => {
      const values = groups.get(record.rateMiB) || [];
      values.push(record.deltaPercent[metric]);
      groups.set(record.rateMiB, values);
    });
    return [...groups.entries()]
      .sort(([first], [second]) => first - second)
      .map(([rateMiB, values]) => ({ rateMiB, median: median(values), values }));
  }

  function formatSigned(value) {
    const absolute = Math.abs(value).toFixed(2);
    return `${value < 0 ? "−" : "+"}${absolute}%`;
  }

  function linearTicks(minimum, maximum, count) {
    if (count < 2) throw new RangeError("Tick count must be at least two");
    return Array.from(
      { length: count },
      (_, index) => minimum + (((maximum - minimum) * index) / (count - 1)),
    );
  }

  return {
    OUTLIER_ID,
    filterPairs,
    formatSigned,
    groupSensitivity,
    linearTicks,
    median,
    selectEnvironment,
    summariseDeltas,
  };
});
