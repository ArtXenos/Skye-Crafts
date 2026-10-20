# Skye Crafts repository audit

**Audit dates:** 2026-10-21 (initial pass and second, evidence-driven pass)
**Scope:** Flask application (`skyefable-website/app.py`), SQLite schema,
templates, browser JavaScript, configuration, documentation and tests.

Findings are marked **Confirmed** only when they were reproduced, either by an
automated test, a scripted request against a temporary database, or a headless
browser session against a locally running copy with a temporary database. Code
reading alone is marked **Hypothesis**. No production system or the real
`skyefable.db` was touched.

## 1. Executive summary

Skye Crafts is a small, server-rendered Flask + SQLite storefront. Its security
baseline is sound for a single-process deployment: passwords are hashed, every
mutating API call needs a session-bound CSRF token, SQL is parameterised,
prices/stock/coupons are recomputed on the server, roles are enforced on the
server, and dynamic HTML is escaped.

The second pass found defects that the first pass missed, and two of them are
user-facing failures:

- **The `/products` page never loads its catalogue (UI-001, High).** A script
  error on that page stops all storefront JavaScript, so the page shows
  "Loading creations…" forever and the cart, sign-in and account menu are dead.
  The header "Products" link and the hero call-to-action both lead there.
- **Fixed-amount coupons cannot be created from the admin page (ADM-001, High).**
  Choosing "Fixed amount" immediately snaps back to "Percentage" and clears the
  form.
- **A customer cancellation that races an admin decline restocks the order
  twice (DATA-004, High)** and overwrites the admin's `Declined` status.

The application is **not production ready** (section 9). Payment, shipping fees
and tax are deliberately absent and must not be simulated.

## 2. Baseline and how to run

From `skyefable-website`, with the repository's virtual environment
(Python 3.14.7, Flask 3.1.3, Werkzeug 3.1.9, python-dotenv 1.2.4):

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

| Check (before any change in this pass) | Result |
| --- | --- |
| Regression suite | 8 passed, 0 failed |
| `/products` in headless Chrome | **Failed**: `TypeError` at `static/js/script.js:878`, grid never loads |
| Admin coupon form in headless Chrome | **Failed**: discount type reverts to percentage |

The browser checks were driven over the Chrome DevTools Protocol with a
throw-away script outside the repository; no browser dependency was added.

## 3. Architecture and feature inventory

One Flask module serves two HTML pages plus a JSON API. Browser JavaScript
renders everything dynamic; the server is the only authority for prices, stock,
coupons, order status and roles.

```
Browser ──(HTML)── GET /, /products, /admin
   │  common.js  (fetch wrapper + CSRF header, escaping, formatting)
   │  script.js  (storefront)        admin.js (dashboard)
   └──(JSON, X-CSRF-Token)── /api/*  ──  app.py  ──  SQLite (WAL, FKs on)
                                            └── Resend HTTP API (optional email)
```

| Area | Status | Notes |
| --- | --- | --- |
| Registration, login, logout | Implemented | Werkzeug hashes; session rotated and CSRF token reissued at login |
| Account management | **Missing** | No profile edit, password change/reset or email verification |
| Catalogue, categories, search, filters | Implemented | Client-side filtering over `/api/state`; `/products` broken (UI-001) |
| Product details and reviews | Partial | Ratings work; **comment field missing from UI** (UI-002) |
| Cart | Implemented | `localStorage`; reconciled against live stock |
| Checkout and orders | Implemented (unpaid) | Atomic `BEGIN IMMEDIATE` stock decrement; item price snapshots |
| Order history, cancel, confirm receipt | Implemented | Cancel race (DATA-004) |
| Order status workflow (admin) | Implemented | Server-enforced transition table; history recorded |
| Inventory restoration | Implemented | On Declined/Cancelled; pre-orders bypass stock |
| Coupons | Implemented | Fixed type unusable from UI (ADM-001); see DATA-001, BIZ-001, BIZ-002 |
| Custom-order requests | Implemented | Public form; admin status updates |
| Newsletter | Implemented | Collects emails; no unsubscribe or consent record (BIZ-004) |
| Admin dashboard | Implemented | Shows only newest 200 orders (PERF-001) |
| Customer management (admin) | **Missing** | No customer list, disable or role management |
| Email notifications | Optional | Synchronous Resend call after commit; failures logged |
| Payments, shipping fees, tax | Deliberately absent | Documented in README |

## 4. Confirmed defects

| ID | Severity | Category | Location | Status |
| --- | --- | --- | --- | --- |
| UI-001 | High | Functionality | `static/js/script.js:878` | Fixed in this pass |
| ADM-001 | High | Functionality | `static/js/admin.js:466` | Fixed in this pass |
| DATA-004 | High | Inventory integrity | `app.py` `customer_cancel_order` | Fixed in this pass |
| CFG-001 | High | Configuration | `.env.example` | Fixed (commit `94d20b1`) |
| SEC-002 | Medium | Abuse prevention | `/api/coupons/validate` | Fixed in this pass |
| UI-002 | Medium | Incomplete feature | review form in `script.js` | Fixed in this pass |
| VAL-001 | Low | Input validation | `app.py` checkout, coupon validation, admin status | Fixed in this pass |
| UI-003 | Low | Navigation | footer of `templates/index.html` | Fixed in this pass |

### UI-001 — `/products` never loads (High, Confirmed)

- **Evidence:** headless Chrome on `/products` logs
  `TypeError: Cannot read properties of null (reading 'addEventListener') at
  script.js:878`. `#customForm` is inside the `{% if page_mode != 'products' %}`
  block of `templates/index.html`, but `script.js` binds it unconditionally. The
  throw happens before `loadState()` is called at the end of the file.
- **Expected:** the full catalogue, a working cart and sign-in.
  **Actual:** "Loading creations…" forever; no handlers are attached.
- **Impact:** the main browse-and-buy entry point is unusable.
- **Fix:** bind the custom-order form only when it exists.
- **Regression test:** the browser smoke check in section 8; the server-side
  test asserts the page omits the form, which is why the script must tolerate
  its absence.

### ADM-001 — Fixed-amount coupons cannot be created (High, Confirmed)

- **Evidence:** in headless Chrome, setting "Discount type" to "Fixed amount"
  re-renders the coupon form from the saved coupon (or the percentage default),
  so the select returns to "Percentage" and typed values are erased.
  Submitting therefore never sends `discount_type: "fixed"`.
- **Fix:** update the value field's label and limits in place instead of
  re-rendering the form.
- **Regression test:** browser check that the selection and typed code survive
  the change and a fixed coupon is saved with the right type and value.

### DATA-004 — Cancellation race double-restocks inventory (High, Confirmed)

- **Evidence:** a scripted reproduction made the admin decline the order (which
  restocks it) between the customer-cancel route's `SELECT` and its `UPDATE`.
  Stock went 10 → 8 at checkout → **12** afterwards, and the order status
  `Declined` was overwritten with `Cancelled`. The route's `UPDATE` had no
  `AND status = ?` guard; the admin route already had one.
- **Impact:** phantom stock that can be sold; the audit trail misstates who
  ended the order.
- **Fix:** make the update conditional on the status that was read, and reject
  with 409 if it changed, before restocking.
- **Regression test:** `test_customer_cancel_does_not_restock_twice_when_raced`.

### SEC-002 — Coupon codes can be guessed without limit (Medium, Confirmed)

- **Evidence:** 30 consecutive guesses at `/api/coupons/validate` from one
  signed-in client all returned 404; none was throttled.
- **Fix:** add the endpoint to the existing per-IP limiter (20 requests per
  5 minutes).
- **Regression test:** `test_coupon_validation_is_rate_limited`.

### UI-002 — Review comments cannot be written (Medium, Confirmed)

- **Evidence:** the API and the admin page support an optional review comment
  (up to 2,000 characters) and README advertises it, but the storefront review
  form contains only star buttons and posts `{rating}`.
- **Fix:** add a labelled, optional comment field and send it.

### VAL-001 — Malformed fields cause HTTP 500 (Low, Confirmed)

- **Evidence:** `coupon_code: 5` at checkout and `reason: 5` on the admin status
  route raised `AttributeError` (500). A non-object cart entry on
  `/api/coupons/validate` hits the same failure once a valid code is used.
- **Fix:** use the shared `text_field` validator and the same cart-entry check
  as checkout; return 400.
- **Regression tests:** `test_malformed_text_fields_are_rejected_not_crashed`.

### UI-003 — Footer links go nowhere on `/products` (Low, Confirmed)

- **Evidence:** on `/products` the footer's `#custom`, `#gallery` and `#faq`
  links target sections that are only rendered on the home page.
- **Fix:** point them at the home page anchors, as the header already does.

## 5. Business-rule decisions

The owner decided DATA-001, BIZ-001, BIZ-002, DATA-002 and BIZ-003 on
2026-10-21; all five are now implemented and covered by tests.

| ID | Was | Decision and implementation | Test |
| --- | --- | --- | --- |
| DATA-001 | A cancelled/declined order kept its coupon use | **Release it.** Orders store `coupon_id`; `_release_order` decrements `used_count` on Cancelled/Declined, together with the restock | `test_cancelled_and_declined_orders_give_the_coupon_back` |
| BIZ-001 | One account could reuse a coupon on every order | **One use per customer by default.** New `coupons.once_per_customer` (default 1, applied to existing coupons too); orders that were cancelled or declined don't count. Admin can untick it for store-wide vouchers | `test_coupon_is_once_per_customer_unless_disabled` |
| BIZ-002 | Coupon dates compared in UTC | **Philippine time.** `SHOP_TZ` = UTC+8 (no DST): valid 12:00 AM on the start date to 11:59 PM on the end date | `test_coupon_dates_follow_philippine_time` |
| DATA-002 | A retried checkout created a second order | **Return the original.** The browser sends a random `checkout_key`, reused on retry and reset when the cart changes; the server looks it up inside the checkout transaction (unique index per buyer as a backstop) and answers 200 `{duplicate: true}` | `test_retried_checkout_returns_the_original_order` |
| BIZ-003 | Anyone signed in could review any product | **Delivered purchases only.** The API returns 403 otherwise; the reviews listing reports `can_review` so the form is only shown to eligible customers | `test_reviews_require_a_delivered_order` |

Notes on these changes:

- Existing data is not rewritten. `used_count` values that already include
  cancelled orders stay as they are; only future cancellations release a use.
  Existing reviews from non-buyers stay visible.
- On upgrade, old orders are linked to their coupon by code, so they count
  towards the per-customer rule and release their use if cancelled later.
- Deleting a coupon clears `orders.coupon_id` (`ON DELETE SET NULL`); the order
  keeps its `coupon_code` text and discount.
- Not built: daily-resetting vouchers, and "seller service" ratings for
  cancelled orders. Both need their own requirements.

Still open:

| ID | Severity | Current behaviour | Decision needed |
| --- | --- | --- | --- |
| BIZ-004 | Low | Newsletter has no unsubscribe link or consent timestamp beyond signup time | Required before sending any campaign (data-privacy obligations) |

## 6. Security and data-integrity findings

Confirmed controls: hashed passwords; session fixation prevented
(`session.clear()` on login); CSRF token compared in constant time; `SameSite=Lax`,
`HttpOnly` cookies, `Secure` + HSTS behind `COOKIE_SECURE=1`; restrictive CSP
(`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`; `no-store` on API
responses; 64 KB request limit; parameterised SQL throughout; customer order
queries scoped by `buyer_id`; admin routes all call `require_user(admin=True)`;
self-registration cannot set `is_admin`; existing customers are never promoted
to admin from configuration.

| ID | Severity | Type | Finding |
| --- | --- | --- | --- |
| SEC-001 | Medium | Confirmed (by design) | Rate limits are process-local; multiple workers multiply the allowance |
| SEC-003 | Medium | Hypothesis (deployment) | Limits key on `request.remote_addr`. Behind a reverse proxy every visitor shares the proxy's address, so 10 failed logins by anyone would lock everyone out for 5 minutes. Needs `ProxyFix` configured for the actual proxy depth at deployment time |
| SEC-004 | Low | Hypothesis | Login for an unknown username skips the password-hash check, so response time can reveal whether a username exists |
| SEC-005 | Low | Confirmed | Changing `ADMIN_PASSWORD` in `.env` does not change an existing admin's password; there is no password-change feature at all |
| DATA-003 | Medium | Confirmed (by design) | Inventory correctness relies on SQLite write serialisation on a single host |
| OPS-001 | Medium | Confirmed | Schema changes run at startup with no migration versioning, backup check or restore drill |

The audit did not run an external dependency-vulnerability scan, penetration
test or email-provider test.

## 7. UI/UX and accessibility

Strengths: labelled form fields, `role="alert"` errors, live regions, dialog
roles with Escape-to-close, a Tab focus trap and focus return, visible focus
styles, reduced-motion CSS and responsive breakpoints.

| ID | Severity | Finding |
| --- | --- | --- |
| A11Y-001 | Low | Opening the cart drawer does not move focus into it, and it is not trapped like the modals |
| A11Y-002 | Low | Admin tabs use `role="tab"` without `aria-controls` or arrow-key navigation |
| A11Y-003 | Low | Star rating buttons did not expose the selected value; fixed in this pass with `aria-pressed` |
| UI-004 | Low | Footer TikTok link is a placeholder (`href="#"`) |
| A11Y-004 | Low | Opening a product dialog leaves focus on the page body (observed in headless Chrome; pre-existing), so screen-reader users are not taken into the dialog |
| UI-005 | Low | A coupon that becomes invalid between "Apply" and "Place order" leaves the stale discount on screen after the 409 error |

Colour contrast and screen-reader announcements still need a manual pass with
real assistive technology.

## 8. Performance and maintainability

| ID | Severity | Finding |
| --- | --- | --- |
| PERF-001 | Medium | `/api/admin/overview` returns only the newest 200 orders and requests, with no paging: older orders become unmanageable from the dashboard |
| PERF-002 | Low | Order emails are sent synchronously (5 s timeout) inside the request, after commit |
| MAINT-001 | Low | Coupon eligibility and discount maths were duplicated between `validate_coupon` and `checkout`; now shared in `coupon_discount` |
| MAINT-002 | Low | `app.py` holds configuration, schema, validation, routes and email in one 1,400-line module |
| TEST-001 | Medium | Coverage still thin for stock exhaustion, coupon boundaries, transitions and ownership; there are no automated browser tests. This is how UI-001 and ADM-001 went unnoticed |

At current catalogue size the client-side filtering and single state request
are appropriate; no measured bottleneck justifies optimisation yet.

## 9. Production readiness

**Status: not ready for public production.** Before launch:

1. Serve with a production WSGI server (for example Waitress on Windows or
   Gunicorn on Linux) behind HTTPS; set `COOKIE_SECURE=1`.
2. Configure `ProxyFix` for the real proxy (SEC-003), and shared or proxy-level
   rate limits if more than one worker runs (SEC-001).
3. Schedule SQLite backups (`sqlite3 skyefable.db ".backup ..."`), and test a
   restore before relying on them.
4. Introduce versioned migrations before the next schema change (OPS-001).
5. Collect application logs and alert on 5xx responses and email failures.
6. Settle payment, shipping-fee, tax and fulfilment requirements with real
   providers; never mark recorded orders as paid.

## 10. Recommended new features (with justification)

| Feature | Why |
| --- | --- |
| Password change and reset by email | Customers and the admin currently cannot recover or rotate a password (SEC-005) |
| Admin order paging and search | Needed as soon as there are more than 200 orders (PERF-001) |
| Customer list for the admin | Prompt-listed requirement; needed to answer customer queries and disable abusive accounts |
| Product images | The catalogue uses category illustrations only; real photos matter for handmade goods |
| Low-stock indicator in admin | Stock is placeholder data and is already tracked; a simple threshold view helps restocking |

## 11. Prioritised roadmap

| Step | Items | State |
| --- | --- | --- |
| 1 | CFG-001 | Done |
| 2 | UI-001, ADM-001, DATA-004 (High) | Done in this pass |
| 3 | SEC-002, UI-002, VAL-001, UI-003 | Done in this pass |
| 4 | DATA-001, BIZ-001, BIZ-002, DATA-002, BIZ-003 (owner decided 2026-10-21) | Done |
| 5 | TEST-001: stock-boundary, coupon-limit and transition tests; a repeatable browser smoke test | Pending |
| 6 | PERF-001 paging; password change (SEC-005) | Pending |
| 7 | Production items in section 9 (SEC-001, SEC-003, OPS-001) | Pending; needs hosting decision |
| 8 | Accessibility refinements A11Y-001, A11Y-002, A11Y-004, UI-004, UI-005 (A11Y-003 done in this pass) | Pending |

## 12. Verification log (2026-10-21, after the fixes)

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` (with `-W error::ResourceWarning`) | **14 passed**, 0 failed |
| New tests run against the code *before* the fixes | 4 failed as expected (race, coupon rate limit, malformed fields, footer links); 2 new coverage tests passed on old code too |
| `git diff --check` | passed |
| Headless Chrome, `/products` | 4 products rendered, add-to-cart updates the count, no console errors |
| Headless Chrome, home page | 4 featured products, no console errors |
| Headless Chrome, admin coupon form | "Fixed amount" stays selected, typed code kept; ₱50 fixed coupon saved as `fixed` / 5000 centavos |
| Headless Chrome, review form | Comment saved and returned by the API; selected star exposes `aria-pressed="true"` |

Behaviour changes to note: an admin status `reason` longer than 300 characters
is now rejected with 400 instead of being silently truncated (the admin form
already caps input at 300), and a `coupon_code` longer than 40 characters at
checkout returns 400 instead of 409.

### Second round: business rules (2026-10-21)

| Check | Result |
| --- | --- |
| Full suite, `-W error::ResourceWarning` | **19 passed**, 0 failed |
| Upgrade of a database created by the previous `app.py` (orders with and without a coupon), run twice | Columns and indexes added; coupon orders linked; totals unchanged; `integrity_check` ok; no foreign-key violations; second run a no-op |
| Same upgrade on a read-only copy of the local `skyefable.db` | All row counts unchanged; integrity ok; original file's checksum unchanged; copy deleted |
| Headless Chrome, checkout through the form | Order placed; a 32-character `checkout_key` stored |
| Headless Chrome, reviews | Form hidden with explanatory note before delivery; shown and saving after delivery |
| Headless Chrome, coupon form | "One use per customer" on by default; start date defaults to today in Manila |

A lost-response retry was tested at the API level only; the browser run did
not simulate a dropped connection.

Not verified: real email delivery, multi-worker deployment, mobile devices and
screen readers. The browser checks used a throw-away script outside the
repository and are not yet a repeatable test (roadmap step 5).
