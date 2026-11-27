// Mirrors validate_password in backend/scraper/auth.py; the API has the final say.
// `exact` rules match Python exactly and gate Submit. The symbol and email-name rules depend on
// Python's isspace and casefold, which JS cannot reproduce for every Unicode character, so they
// are live hints only: the API decides edge cases and its message is shown.
const fold = (s: string) => s.normalize("NFKC").toLowerCase();

export function passwordRules(pw: string, email: string) {
  const length = [...pw].length;
  const local = email.split("@")[0];
  return [
    { label: "12 to 128 characters", ok: length >= 12 && length <= 128, exact: true },
    { label: "An uppercase letter", ok: /[A-Z]/.test(pw), exact: true },
    { label: "A lowercase letter", ok: /[a-z]/.test(pw), exact: true },
    { label: "A number", ok: /[0-9]/.test(pw), exact: true },
    { label: "A symbol, like ! # or %", ok: /[^\p{L}\p{N}\s]/u.test(pw), exact: false },
    { label: "Doesn't contain your email name", ok: [...local].length < 3 || !fold(pw).includes(fold(local)), exact: false },
  ];
}

/** Submit is blocked only by rules the browser checks exactly; never refuses a password the API would accept. */
export const canSubmit = (pw: string, email: string) => passwordRules(pw, email).every((r) => r.ok || !r.exact);
