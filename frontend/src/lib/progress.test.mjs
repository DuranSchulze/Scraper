// Run: node --test frontend/src/lib/progress.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { clock, stageView } from "./progress.ts";
import { day, phDateTime, stamp } from "./format.ts";

test("format: instants show in Philippine time; plain dates stay as they are", () => {
  // 23:30 UTC on Nov 23 is 07:30 on Nov 24 in Manila.
  assert.equal(stamp("2026-11-23T23:30:00Z"), "2026-11-24 07:30");
  assert.equal(day("2026-11-23T23:30:00Z"), "2026-11-24");
  assert.equal(day("2026-11-15"), "2026-11-15");
  assert.equal(stamp("2026-11-24T07:30:00+08:00"), "2026-11-24 07:30");
  assert.equal(phDateTime("2026-11-24T01:23:00Z"), "Nov 24, 2026, 9:23 AM PHT");
  assert.equal(stamp(null), "");
  assert.equal(phDateTime("nonsense"), "");
});

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
  assert.deepEqual(stageView({ stage: "extract", done: 81, total: 81, started_at: at }), { label: "Finishing up", count: "81/81 items", value: 99 });
  assert.deepEqual(stageView({ stage: "tag", done: 1, total: 1, started_at: at }), { label: "Finishing up", count: "", value: 99 });
  assert.equal(stageView({ stage: "extract", done: 5, total: 5, started_at: at, backlog_done: 1000, backlog_total: 1000 }).label, "Finishing up");
  assert.deepEqual(stageView({ stage: "extract", done: 0, total: 0, started_at: at }), { label: "Extracting records", count: "", value: null });
  assert.equal(stageView({ stage: "tag", done: 0, total: 1, started_at: at }).count, "");
});

test("stageView: backlog pass advances while this run's count stays fixed", () => {
  const at = "2026-11-13T00:00:00Z";
  const view = stageView({ stage: "extract", done: 5, total: 5, started_at: at, backlog_done: 250, backlog_total: 1000 });
  assert.deepEqual(view, { label: "Extracting older backlog", count: "250/1,000 backlog items", value: 25 });
});
