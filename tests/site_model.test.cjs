const assert = require("node:assert/strict");
const test = require("node:test");

const model = require("../site/assets/model.js");

const pairs = [
  { id: "a-off", releaseMode: "off", order: "L first", deltaPercent: { combined: 1 } },
  { id: "balanced-4-on", releaseMode: "on", order: "T first", deltaPercent: { combined: -12.02 } },
  { id: "c-on", releaseMode: "on", order: "L first", deltaPercent: { combined: 0.4 } },
];

test("filterPairs combines release, allocator order, and explicit outlier controls", () => {
  assert.deepEqual(
    model.filterPairs(pairs, { releaseMode: "on", order: "all", includeOutlier: false }).map((pair) => pair.id),
    ["c-on"],
  );
  assert.deepEqual(
    model.filterPairs(pairs, { releaseMode: "all", order: "L first", includeOutlier: true }).map((pair) => pair.id),
    ["a-off", "c-on"],
  );
});

test("summariseDeltas reports the median, range, and positive count", () => {
  assert.deepEqual(model.summariseDeltas(pairs, "combined"), {
    count: 3,
    positive: 2,
    median: 0.4,
    min: -12.02,
    max: 1,
  });
});

test("groupSensitivity calculates one median per release rate", () => {
  const records = [
    { rateMiB: 16, deltaPercent: { lpush: -1 } },
    { rateMiB: 16, deltaPercent: { lpush: 3 } },
    { rateMiB: 64, deltaPercent: { lpush: 2 } },
  ];

  assert.deepEqual(model.groupSensitivity(records, "lpush"), [
    { rateMiB: 16, median: 1, values: [-1, 3] },
    { rateMiB: 64, median: 2, values: [2] },
  ]);
});

test("formatSigned keeps direction visible around zero", () => {
  assert.equal(model.formatSigned(1.234), "+1.23%");
  assert.equal(model.formatSigned(-0.004), "−0.00%");
  assert.equal(model.formatSigned(0), "+0.00%");
});

test("linearTicks includes both endpoints with evenly spaced values", () => {
  assert.deepEqual(model.linearTicks(0, 100, 6), [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(model.linearTicks(-12, 12, 5), [-12, -6, 0, 6, 12]);
});

test("selectEnvironment uses the default environment and permits an explicit selection", () => {
  const results = {
    defaultEnvironment: "baremetal",
    environments: {
      baremetal: { id: "baremetal", historical: [{ id: "native" }] },
      wslDocker: { id: "wslDocker", historical: [{ id: "container" }] },
    },
  };

  assert.equal(model.selectEnvironment(results).id, "baremetal");
  assert.equal(model.selectEnvironment(results, "wslDocker").id, "wslDocker");
  assert.equal(model.selectEnvironment(results, "unknown").id, "baremetal");
});
