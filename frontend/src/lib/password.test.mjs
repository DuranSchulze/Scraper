// Run: node --test frontend/src/lib/password.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { canSubmit, passwordRules } from "./password.ts";

const hint = (pw, label, email = "someone@example.test") => passwordRules(pw, email).find((r) => r.label.startsWith(label)).ok;

test("length counts code points like the API", () => {
  assert.equal(canSubmit("😀".repeat(64) + "Aa1!", "someone@example.test"), true);
  assert.equal(canSubmit("😀".repeat(125) + "Aa1!", "someone@example.test"), false);
});

test("exact rules gate submit", () => {
  assert.equal(canSubmit("short", "someone@example.test"), false);
  assert.equal(canSubmit("alllowercase1!", "someone@example.test"), false);
});

test("hint-only rules never block a password the API accepts", () => {
  // API accepts U+FEFF as the symbol; JS treats it as whitespace.
  assert.equal(canSubmit("Abcdefgh1234﻿", "someone@example.test"), true);
  // API accepts dotless ı as different from i.
  assert.equal(canSubmit("Admın!Pass12", "admin@example.test"), true);
});

test("email-name hint folds common cases", () => {
  assert.equal(hint("Groß!Ax1long", "Doesn't", "gross@example.test"), true); // API rejects; the API message covers it
  assert.equal(hint("Oﬃce!Ax1long", "Doesn't", "office@example.test"), false);
  assert.equal(hint("MyAdminPass1!", "Doesn't", "admin@example.test"), false);
});
