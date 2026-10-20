# Skye Crafts — Website + Shop Backend

The Skye Crafts storefront (design, copy, and products) runs on a Flask + SQLite
backend: products come from a database, customers can create accounts and
place orders, and the shop owner manages everything from `/admin`.



## Folder structure

```
skyefable-website/
├── app.py                 Flask backend (API, database, sessions)
├── requirements.txt
├── .env.example           Copy to .env and fill in
├── .gitignore
├── templates/
│   ├── index.html         The storefront
│   └── admin.html         Shop owner dashboard
└── static/
    ├── css/style.css      Skye Crafts design + styles for the shop/admin parts
    └── js/
        ├── common.js      Shared helpers (API calls, ₱ formatting, toast)
        ├── script.js      Storefront: products, cart, login, checkout, orders, reviews
        └── admin.js        Admin dashboard: orders, products, coupons, requests, reviews
```

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
```

Create your settings file (`copy` on Windows, `cp` on macOS/Linux):

```bash
copy .env.example .env
```

Open `.env` and set:

- `SECRET_KEY` — generate one with
  `python -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_USERNAME` and `ADMIN_PASSWORD` — your shop owner login
  (3–30 letters/numbers/underscores, and a password of 10+ characters)
- `RESEND_API_KEY` — optional, enables order-status emails (see above)

Then start the site:

```bash
python app.py
```

Open http://127.0.0.1:5000 for the shop and http://127.0.0.1:5000/admin for the
dashboard. Opening `index.html` directly from disk no longer works, because the
page needs the server.

Run the API regression suite with:

```bash
python -m unittest discover -s tests -v
```

The database (`skyefable.db`) is created on first start with your four products.
The stock numbers are placeholders, so set the real ones in the admin page.
If you're upgrading from an earlier version of this project, the new columns
(coupon fields, status history, reviews) are added automatically the first
time you run the updated `app.py` against your existing database.

## What's connected

**Customers**
- Products, prices (₱), and availability come from the database.
- Cart works before logging in (saved in the browser); log in or register at checkout.
- A coupon code can be applied at checkout; the discount is always recomputed
  server-side. Coupons are one use per customer unless the shop turns that
  off, follow Philippine time (valid from 12:00 AM on the start date to
  11:59 PM on the end date), and are given back if the order is cancelled or
  declined.
- Retrying a checkout after a dropped connection returns the original order
  instead of placing a second one.
- Checkout with **Delivery (J&T Express)** or **Pickup**. The server re-checks prices
  and stock, so the browser can't change them.
- **Pre-order** products (like the Desk Figurine) have no stock limit.
- "My orders" shows status updates and history, lets a customer cancel an
  order that hasn't been packed yet, and confirm receipt/pickup once shipped.
- Customers can leave a 1–5 star rating (with an optional comment) on a
  product once an order containing it has been delivered.
- "Create a Custom Order" form and the newsletter signup save to the database.

**Shop owner (`/admin`)**
- Orders: move through Approved → Packed → Shipped → Delivered, or Decline /
  Cancel with an optional reason; stock is restored automatically on
  Decline/Cancel. Full status history is shown per order.
- Products: add, edit, change price and stock, mark pre-order, hide from the shop.
- Coupons: create percentage or fixed-amount codes with a cap, minimum order,
  usage limit, and date range.
- Custom order requests: read them and set a status.
- Reviews: see every rating and comment left on a product.
- Newsletter subscribers list.

## Not built yet

- **Payment.** Checkout records the order but collects no payment, and shipping fees
  aren't calculated. (See "Deliberately not ported" above.)
- **Image uploads** for products and custom requests (customers can paste a link).
- **Distributed abuse protection** for multi-worker or multi-instance hosting.
- **Production hosting.** `python app.py` is Flask's development server. Before going
  public, serve it with a production server, use HTTPS, and set `COOKIE_SECURE=1`.

## Production security notes

- The app sets a 12-hour authenticated session lifetime, CSRF protection, a
  Content Security Policy, and process-local rate limits for login, registration,
  checkout, coupon checks, newsletter, and custom-order submissions.
- The built-in rate limiter is suitable only for a single application process. If
  deploying multiple workers or instances, configure equivalent limits at the
  reverse proxy or replace it with a shared rate-limit store.
- `COOKIE_SECURE=1` enables secure cookies and the HSTS response header. Enable it
  only when the entire public site is served over HTTPS.
- SQLite backups should be taken before deployments. Schema changes currently run
  during application startup; introduce versioned migrations before making
  production schema changes.
