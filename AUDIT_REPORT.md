# Skye Crafts initial repository audit

**Audit date:** 2026-10-21  
**Scope:** read-only review of the Flask application, database schema, templates,
browser JavaScript, styles, configuration, documentation, and current tests. No
application behavior was changed during this audit.

## Executive summary

Skye Crafts is a small, server-rendered Flask and SQLite storefront with a
reasonably solid security baseline for a single-process deployment. It performs
server-side price, stock, coupon, authorization, and CSRF checks; uses password
hashing; records order status history; and has a small regression suite covering
several security-critical paths.

The immediate confirmed release blocker is configuration documentation: the
README tells a new developer to copy `skyefable-website/.env.example`, but that
file is currently deleted from the working tree. This prevents a documented
clean setup. The application is **not production ready**: it uses startup-time
SQLite schema changes, has process-local rate limiting, no deployment/backup
runbook, and has deliberately deferred payment, shipping, and tax integration.

## Baseline and how to run

From `skyefable-website` with Python 3.10+:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe -m py_compile app.py
```

Observed on 2026-10-21:

| Check | Result |
| --- | --- |
| Unit/integration regression suite | 8 passed, 0 failed (1.138 s) |
| Python compilation of `app.py` | passed |
| `pip check` | no broken requirements found |
| `git diff --check` | passed |

The current worktree is not clean. It has user changes to `app.py` and
`tests/test_app.py`, and a deletion of `.env.example`; these were not modified
by this audit.

## Architecture and feature inventory

| Area | Implementation status | Evidence |
| --- | --- | --- |
| Web application | Implemented | `skyefable-website/app.py`; Flask pages at `/`, `/products`, and `/admin` |
| Data store | Implemented, SQLite | schema and startup initialization in `app.py:139-300`; foreign keys and WAL enabled |
| Authentication | Implemented | registration, login, logout; Werkzeug password hashes; signed Flask sessions |
| Customer account | Partial | sign-in and order lookup exist; no profile editing, password reset, or email verification |
| Catalogue | Implemented | active products, categories, search/filter UI, product details and reviews |
| Cart | Implemented, browser-local | localStorage cart in `static/js/script.js`; server recalculates checkout values |
| Checkout/orders | Implemented, unpaid order recording | atomic stock decrement, order-item price snapshots, order history and cancellation |
| Inventory | Implemented for one SQLite process | conditional stock decrement and restoration on cancellation/decline; pre-orders bypass stock |
| Coupons | Implemented | server-side date, cap, minimum, discount, and use-count checks |
| Customer reviews | Implemented | one upserted 1–5-star review per customer/product |
| Custom requests/newsletter | Implemented | database-backed public submissions and admin views |
| Administration | Implemented | role-protected products, orders, coupons, requests, reviews, subscribers |
| Email | Optional/best-effort | Resend request on status changes only when configured; failures are logged |
| Payments, tax, shipping rate | Intentionally absent | documented in `README.md` |
| Automated tests | Partial | 8 Flask test-client cases in `tests/test_app.py` |

## Confirmed findings

### CFG-001 — Missing documented environment template

**Severity:** High  
**Category:** configuration / deployability  
**Affected files:** `README.md`, `skyefable-website/.env.example`

`README.md` instructs developers to run `copy .env.example .env`, but
`.env.example` is deleted in the current worktree (`git status` shows it as a
tracked deletion). A clean developer setup following the README therefore
fails. The local `.env` file is ignored, which is correct; its credentials
should not be copied into version control.

**Recommended fix:** restore a tracked, credential-free `.env.example` with
placeholder values and keep `.env` ignored. Add a setup smoke test or CI check
that verifies the referenced file exists.

**Regression test:** assert the template exists and includes the non-secret
configuration keys used by the app.  
**Effort/dependencies:** small; no business decision required.

## Risks requiring a business decision or further verification

### DATA-001 — Coupon capacity is not released for cancelled/declined orders

**Severity:** Medium  
**Category:** business-rule/data integrity  
**Affected module:** `app.py` checkout and cancellation/status workflows

Checkout increments `coupons.used_count`; neither the customer cancellation
route nor the admin decline/cancellation route decrements it. This is a
confirmed implementation fact. Whether it is a defect depends on the intended
policy: a coupon may be consumed on placement, or only on a completed/paid
order. With the latter policy, cancelled orders can exhaust a limited coupon.

**Decision needed:** state precisely when a coupon is consumed and whether an
admin may alter or delete a coupon that has historical redemptions.

### DATA-002 — Duplicate checkout submissions have no idempotency key

**Severity:** Medium  
**Category:** order integrity  
**Affected module:** `app.py:759-890`, checkout UI in `static/js/script.js`

Each accepted checkout POST creates a new order and reduces stock. The UI
disables its submit button while awaiting one response, but a retry after an
ambiguous network failure or a manually replayed request can create another
order. No request identifier is persisted or deduplicated.

**Recommended fix:** agree the retry semantics, then persist a customer-scoped
idempotency key and return the original order for a repeated key. Test repeated
POSTs and interrupted responses.

### DATA-003 — Inventory concurrency is SQLite/single-host scoped

**Severity:** Medium  
**Category:** scalability / data integrity  
**Affected module:** `app.py:124-129`, `app.py:790-887`

The checkout transaction uses `BEGIN IMMEDIATE` plus a conditional stock update,
which is appropriate for one SQLite database file. It serializes writers and
will become a throughput/operational constraint for multiple workers or hosts;
the documentation does not define a multi-instance database strategy.

**Recommended fix:** retain the current model for a small single-instance shop,
document it explicitly, and plan a managed database plus versioned migrations
before horizontal scaling. Add a two-client stock boundary test now.

### SEC-001 — Rate limiting is not shared across workers

**Severity:** Medium  
**Category:** abuse prevention  
**Affected module:** `app.py:45-57`, `app.py:540-555`

Login, registration, checkout, newsletter, and custom-request limits are
process-local in-memory queues keyed by client IP. They work in one process but
can be bypassed by distributing requests across workers or instances. README
already notes this limitation.

**Recommended fix:** enforce equivalent limits at the reverse proxy/CDN or use
a shared rate-limit store before multi-worker deployment.

### OPS-001 — Schema changes run on application startup

**Severity:** Medium  
**Category:** migration safety / operations  
**Affected module:** `app.py:139-300`

Schema creation and `ALTER TABLE` compatibility changes run whenever the app
starts. There is no migration version table, migration command, backup check,
or restoration verification. This is manageable for the current small schema
but unsuitable as the change history grows.

**Recommended fix:** introduce versioned migrations and an operational backup,
restore, deploy, and rollback runbook before production database changes.

### TEST-001 — Critical workflows lack automated coverage

**Severity:** Medium  
**Category:** reliability  
**Affected module:** `tests/test_app.py`

The existing eight tests cover CSRF, login limiting, admin isolation, database
price calculation, date validation, security headers, products routing, and an
optional custom field. They do not cover stock exhaustion/restoration, coupon
use boundaries, order status transitions, customer ownership checks, duplicate
checkout handling, product editing with historical orders, review authorization,
or error/transaction behavior.

**Recommended fix:** add isolated Flask integration tests as each high-priority
change is made; do not modify the real `skyefable.db`.

### UX-001 — Accessibility is a good baseline but needs browser-assisted QA

**Severity:** Low  
**Category:** accessibility / UI quality  
**Affected files:** templates, `static/js/script.js`, `static/css/style.css`

The reviewed markup includes labelled controls, live/error regions, dialogs,
keyboard close handling, focus-visible styles, responsive breakpoints, and
reduced-motion CSS. Static review cannot verify modal focus trapping, focus
return, color contrast, touch layouts, or screen-reader announcements.

**Recommended fix:** conduct keyboard and screen-reader/browser testing on
mobile and desktop and resolve observed issues. Add a lightweight automated
accessibility scan if a test browser is introduced.

## Security review summary

Confirmed controls include password hashing, server-side role checks, a
server-issued CSRF token required for mutating API calls, parameterized SQL,
output escaping for dynamic HTML rendering, request-size limits, secure-cookie
configuration, and restrictive browser security headers. The reviewed JavaScript
uses a shared escaping helper where server/user values are inserted with
`innerHTML`.

The audit did not perform live penetration testing, package vulnerability
scanning against an external advisory feed, email-provider testing, or a
production-host review. No raw credentials are recorded in this report.

## Production readiness

**Status: not ready for public production.**

Before deployment, define the hosting topology and HTTPS termination; set a
production secret and `COOKIE_SECURE=1`; use a production WSGI server; put
shared abuse limits in place if more than one worker is used; establish backups
and a tested restore procedure; move to versioned migrations; configure log
collection and alerting; and decide payment, tax, shipping, fulfilment, and
coupon-cancellation policies. Never represent recorded unpaid orders as paid.

## Prioritized improvement roadmap

1. **CFG-001 (High):** restore the non-secret environment template and correct
   setup documentation; add a smoke test.
2. **TEST-001 / DATA-003:** add checkout stock-boundary, cancellation/restock,
   and transition tests against temporary databases.
3. **DATA-001:** make the coupon cancellation policy explicit, then implement
   and test it if redemptions should be released.
4. **DATA-002:** decide retry behavior and add idempotent checkout handling.
5. **OPS-001 / SEC-001:** before public or multi-worker deployment, adopt
   migrations, backups/restore drills, a WSGI deployment configuration, and
   shared/proxy rate limits.
6. **UX-001:** run manual accessibility and responsive browser QA, then address
   actual observed defects.

## Deferred requirements

Payment collection, taxes, shipping-rate calculation, carrier integration,
image uploads, verified customer email, and password recovery have not been
implemented. Each needs product/business requirements and appropriate external
providers; none should be simulated as a real payment or fulfilled order.
