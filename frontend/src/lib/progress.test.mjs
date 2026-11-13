// Run: node --test frontend/src/lib/progress.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { clock, stageView } from "./progress.ts";

test("clock: minutes, hours, never negative", () => {
  assert.equal(clock(754_000), "12:34");
  assert.equal(clock(3_723_000), "1:02:03");
  assert.equal(clock(-5000), "0:00");
  assert.equal(clock(999), "0:00");
});

test("stageView: counts, unknown totals, never a full bar while running", () => {
  const at = "2026-11-13T00:00:00Z";
  assert.deepEqual(stageView({ stage: "fetch", done: 3, total: 8, started_at: at }), { label: "Fetching mail", count: "3/8 mailboxes", value: 37 });
  assert.deepEqual(stageView({ stage: "extract", done: 1240, total: 5600, started_at: at }).count, "1,240/5,600 items");
  assert.equal(stageView({ stage: "extract", done: 5600, total: 5600, started_at: at }).value, 99);
  assert.deepEqual(stageView({ stage: "extract", done: 0, total: 0, started_at: at }), { label: "Extracting records", count: "", value: null });
  assert.equal(stageView({ stage: "tag", done: 0, total: 1, started_at: at }).count, "");
});
