// Run: node --test frontend/src/lib/timeline.test.mjs  (Node 22.18+/24 strips the TypeScript types itself)
import assert from "node:assert/strict";
import test from "node:test";
import { bucketOf, sections } from "./timeline.ts";

test("bucketOf: days, ISO weeks across a year boundary, missing and invalid dates", () => {
  assert.equal(bucketOf("2026-11-12T08:00:00Z", "day"), "2026-11-12");
  assert.equal(bucketOf("2026-11-12", "week"), "2026-11-09");
  assert.equal(bucketOf("2027-01-01", "week"), "2026-12-28");
  assert.equal(bucketOf(null, "day"), null);
  assert.equal(bucketOf("not-a-date", "day"), null);
  assert.equal(bucketOf("2026-02-30", "day"), null);
});

test("sections: one section per bucket, sort order kept, every undated row in one last section", () => {
  const rows = [
    { id: 1, last_contact: "not-a-date", first_contact: null },
    { id: 2, last_contact: "2026-11-12", first_contact: null },
    { id: 3, last_contact: null, first_contact: null },
    { id: 4, last_contact: "2026-11-10", first_contact: null },
    { id: 5, last_contact: "2026-11-12T09:00:00Z", first_contact: null },
  ];
  const day = sections(rows, "last_contact", "day").map((s) => [s.key, s.rows.map((r) => r.id)]);
  assert.deepEqual(day, [["2026-11-12", [2, 5]], ["2026-11-10", [4]], [null, [1, 3]]]);
  const week = sections(rows, "last_contact", "week").map((s) => [s.key, s.rows.map((r) => r.id)]);
  assert.deepEqual(week, [["2026-11-09", [2, 4, 5]], [null, [1, 3]]]);
});
