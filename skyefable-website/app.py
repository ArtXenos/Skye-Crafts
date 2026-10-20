import os
import re
import secrets
import sqlite3
import threading
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from time import monotonic

from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:  # Optional convenience: load a local .env file during development.
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

SECRET_KEY = os.environ.get("SECRET_KEY")

if not SECRET_KEY or SECRET_KEY.startswith("replace-with"):
    raise RuntimeError(
        "SECRET_KEY must be set to a persistent, randomly generated value. "
        "Copy .env.example to .env and fill it in. Generate a value with: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    DATABASE=os.environ.get("DATABASE_PATH")
    or os.path.join(BASE_DIR, "skyefable.db"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=64 * 1024,
)

# This is deliberately process-local: it provides a safe default for the
# single-process SQLite deployment without introducing external infrastructure.
# Multi-process deployments need equivalent limits at the reverse proxy or in a
# shared rate-limit store because in-memory counters are not shared by workers.
RATE_LIMITS = {
    "/api/login": (10, 300),
    "/api/register": (5, 3600),
    "/api/checkout": (10, 300),
    "/api/coupons/validate": (20, 300),
    "/api/custom-requests": (5, 3600),
    "/api/newsletter": (5, 3600),
}
_rate_buckets = defaultdict(deque)
_rate_lock = threading.Lock()

USERNAME_RE = re.compile(r"[A-Za-z0-9_]{3,30}")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
CHECKOUT_KEY_RE = re.compile(r"[A-Za-z0-9-]{16,64}")

# The shop runs on Philippine Standard Time (UTC+8, no daylight saving), so
# coupon start and end dates are calendar days in Manila: a coupon is valid
# from 12:00 AM on its start date until 11:59 PM on its end date.
SHOP_TZ = timezone(timedelta(hours=8), "PST")

CATEGORIES = ["Bouquets", "Keychains", "Pots", "Figurines"]
FULFILLMENT = ("delivery", "pickup")
CUSTOM_STATUSES = ("New", "In progress", "Completed", "Declined")

# Order lifecycle. Modeled on KamiKoto's ORDER_STATUSES / validTransitions
# (src/utils/orderService.js), scaled down for a single-store shop.
ORDER_STATUSES = (
    "Placed", "Approved", "Packed", "Shipped", "Delivered",
    "Declined", "Cancelled",
)
# Who may move an order from one status to another.
ADMIN_TRANSITIONS = {
    "Placed": {"Approved", "Declined"},
    "Approved": {"Packed", "Cancelled"},
    "Packed": {"Shipped", "Cancelled"},
    "Shipped": {"Delivered"},
}
# A customer may only ever cancel their own order, and only before it ships.
CUSTOMER_TRANSITIONS = {
    "Placed": {"Cancelled"},
    "Approved": {"Cancelled"},
}
# Statuses that release the stock/pre-order hold that was placed at checkout.
RESTOCK_STATUSES = {"Declined", "Cancelled"}

# Starter catalogue taken from the Skye Crafts storefront. Prices are stored in
# centavos. The stock numbers are placeholders: edit them in the admin page.
SEED_PRODUCTS = [
    ("Fuzzy Wire Bouquet",
     "A hand‑shaped rose bouquet, wrapped and ribboned to order.",
     "Bouquets", 45000, 10, 0),
    ("Mirror Keychain",
     "A compact pompom flower keychain with a built‑in mirror.",
     "Keychains", 15000, 25, 0),
    ("Fuzzy Wire Pot",
     "A miniature potted flower arrangement for any desk or shelf.",
     "Pots", 28000, 12, 0),
    ("Desk Figurine",
     "A custom character or shape figurine, made to your reference.",
     "Figurines", 32000, 0, 1),
]


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(APIError)
def handle_api_error(error):
    return jsonify(error=error.message), error.status


@app.errorhandler(HTTPException)
def handle_http_error(error):
    if request.path.startswith("/api/"):
        return jsonify(error=error.description), error.code
    return error


def db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    connection = db()
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0
                CHECK (is_admin IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            price_cents INTEGER NOT NULL CHECK (price_cents > 0),
            stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
            is_preorder INTEGER NOT NULL DEFAULT 0
                CHECK (is_preorder IN (0, 1)),
            is_active INTEGER NOT NULL DEFAULT 1
                CHECK (is_active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL REFERENCES users(id),
            recipient TEXT NOT NULL,
            contact TEXT NOT NULL,
            fulfillment TEXT NOT NULL
                CHECK (fulfillment IN ('delivery', 'pickup')),
            address TEXT NOT NULL DEFAULT '',
            subtotal_cents INTEGER NOT NULL DEFAULT 0,
            discount_cents INTEGER NOT NULL DEFAULT 0,
            total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
            coupon_code TEXT NOT NULL DEFAULT '',
            coupon_id INTEGER REFERENCES coupons(id) ON DELETE SET NULL,
            -- Client-generated key so a retried checkout returns the
            -- original order instead of placing a second one.
            checkout_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Placed'
                CHECK (status IN (
                    'Placed', 'Approved', 'Packed', 'Shipped', 'Delivered',
                    'Declined', 'Cancelled'
                )),
            status_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            title TEXT NOT NULL,
            price_cents INTEGER NOT NULL CHECK (price_cents > 0),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            is_preorder INTEGER NOT NULL DEFAULT 0
                CHECK (is_preorder IN (0, 1))
        );

        -- Status history. Mirrors KamiKoto's statusHistory array
        -- (orderService.js) so every transition is auditable.
        CREATE TABLE IF NOT EXISTS order_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        -- Coupons. Mirrors KamiKoto's coupon document shape
        -- (src/utils/couponService.js), scaled to a single store.
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL COLLATE NOCASE UNIQUE,
            discount_type TEXT NOT NULL CHECK (discount_type IN ('percentage', 'fixed')),
            discount_value INTEGER NOT NULL CHECK (discount_value > 0),
            max_discount_cents INTEGER NOT NULL DEFAULT 0,
            min_order_cents INTEGER NOT NULL DEFAULT 0,
            max_uses INTEGER NOT NULL DEFAULT 0,
            used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
            once_per_customer INTEGER NOT NULL DEFAULT 1
                CHECK (once_per_customer IN (0, 1)),
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS custom_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            name TEXT NOT NULL,
            contact TEXT NOT NULL,
            occasion TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'New'
                CHECK (status IN ('New', 'In progress', 'Completed', 'Declined')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        -- Product reviews. Mirrors KamiKoto's reviews collection rules
        -- (firestore.rules): rating 1-5, text capped at 2000 chars.
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (product_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS orders_buyer ON orders(buyer_id);
        CREATE INDEX IF NOT EXISTS order_items_order ON order_items(order_id);
        CREATE INDEX IF NOT EXISTS order_status_history_order ON order_status_history(order_id);
        CREATE INDEX IF NOT EXISTS reviews_product ON reviews(product_id);
    """)

    _migrate_schema(connection)

    if connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        with connection:
            connection.executemany(
                """
                INSERT INTO products
                    (title, description, category, price_cents, stock,
                     is_preorder)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                SEED_PRODUCTS,
            )

    ensure_admin(connection)


def _migrate_schema(connection):
    """Add columns introduced after the first release, for existing DBs."""
    existing = {row["name"] for row in connection.execute("PRAGMA table_info(orders)")}
    with connection:
        if "subtotal_cents" not in existing:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN subtotal_cents INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute("UPDATE orders SET subtotal_cents = total_cents")
        if "discount_cents" not in existing:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0"
            )
        if "coupon_code" not in existing:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN coupon_code TEXT NOT NULL DEFAULT ''"
            )
        if "status_reason" not in existing:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN status_reason TEXT NOT NULL DEFAULT ''"
            )
        if "coupon_id" not in existing:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN coupon_id INTEGER "
                "REFERENCES coupons(id) ON DELETE SET NULL"
            )
            # Link earlier orders to their coupon so cancelling them releases
            # the use and they count towards the once-per-customer limit.
            connection.execute(
                """
                UPDATE orders SET coupon_id =
                    (SELECT id FROM coupons WHERE coupons.code = orders.coupon_code)
                WHERE coupon_code != ''
                """
            )
        if "checkout_key" not in existing:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN checkout_key TEXT NOT NULL DEFAULT ''"
            )
        user_cols = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "email" not in user_cols:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        coupon_cols = {row["name"] for row in connection.execute("PRAGMA table_info(coupons)")}
        if "once_per_customer" not in coupon_cols:
            connection.execute(
                "ALTER TABLE coupons ADD COLUMN once_per_customer INTEGER NOT NULL "
                "DEFAULT 1 CHECK (once_per_customer IN (0, 1))"
            )
        # Created here rather than in init_db because older databases only
        # gain these columns in the steps above.
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS orders_checkout_key
            ON orders(buyer_id, checkout_key) WHERE checkout_key != ''
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS orders_coupon ON orders(coupon_id)")


def ensure_admin(connection):
    """Create the shop-owner account from ADMIN_USERNAME / ADMIN_PASSWORD."""
    username = (os.environ.get("ADMIN_USERNAME") or "").strip()
    password = os.environ.get("ADMIN_PASSWORD") or ""

    if not username and not password:
        if not connection.execute(
            "SELECT 1 FROM users WHERE is_admin = 1"
        ).fetchone():
            app.logger.warning(
                "No admin account exists. Set ADMIN_USERNAME and "
                "ADMIN_PASSWORD in .env to create one."
            )
        return

    if not USERNAME_RE.fullmatch(username) or not 10 <= len(password) <= 128:
        app.logger.warning(
            "ADMIN_USERNAME / ADMIN_PASSWORD ignored: the username needs "
            "3-30 letters, numbers or underscores and the password needs "
            "10-128 characters."
        )
        return

    existing = connection.execute(
        "SELECT is_admin FROM users WHERE username = ?", (username,)
    ).fetchone()

    if existing is None:
        with connection:
            connection.execute(
                """
                INSERT INTO users (username, password_hash, is_admin)
                VALUES (?, ?, 1)
                """,
                (username, generate_password_hash(password)),
            )
    elif not existing["is_admin"]:
        # Never promote an existing customer account automatically.
        app.logger.warning(
            "ADMIN_USERNAME %r already exists as a customer account and was "
            "not promoted. Choose a different admin username.", username
        )


# ---------------------------------------------------------------- helpers

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db().execute(
        "SELECT id, username, email, is_admin FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def user_json(user):
    if user is None:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
    }


def require_user(admin=False):
    user = current_user()
    if user is None:
        raise APIError("Please log in first.", 401)
    if admin and not user["is_admin"]:
        raise APIError("Shop admin access is required.", 403)
    return user


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise APIError("A JSON object is required.")
    return data


def text_field(data, key, minimum=1, maximum=200):
    value = data.get(key)
    if value is None:
        if minimum == 0:
            return ""
        raise APIError(f"{key.replace('_', ' ').title()} is required.")
    if not isinstance(value, str):
        raise APIError(f"{key.replace('_', ' ').title()} is required.")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise APIError(
            f"{key.replace('_', ' ').title()} must contain "
            f"{minimum}–{maximum} characters."
        )
    return value


def integer_field(data, key, minimum, maximum):
    value = data.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise APIError(f"{key} must be between {minimum} and {maximum}.")
    return value


def flag_field(data, key, default):
    value = data.get(key, default)
    if type(value) is not bool:
        raise APIError(f"{key} must be true or false.")
    return value


def parse_price(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not Decimal("0.01") <= amount <= 100000:
            raise ValueError
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise ValueError
        return int(cents)
    except (InvalidOperation, ValueError, TypeError):
        raise APIError(
            "Enter a price from 0.01 to 100000 with at most 2 decimals."
        )


def start_session(user_id):
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["csrf"] = secrets.token_hex(32)


def product_json(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "price_cents": row["price_cents"],
        "stock": row["stock"],
        "is_preorder": bool(row["is_preorder"]),
        "is_active": bool(row["is_active"]),
    }


def attach_items(connection, orders):
    if not orders:
        return orders
    ids = [order["id"] for order in orders]
    marks = ",".join("?" * len(ids))
    rows = connection.execute(
        f"""
        SELECT order_id, title, price_cents, quantity, is_preorder
        FROM order_items WHERE order_id IN ({marks}) ORDER BY id
        """,
        ids,
    )
    by_order = defaultdict(list)
    for row in rows:
        by_order[row["order_id"]].append({
            "title": row["title"],
            "price_cents": row["price_cents"],
            "quantity": row["quantity"],
            "is_preorder": bool(row["is_preorder"]),
        })
    for order in orders:
        order["items"] = by_order[order["id"]]
    return orders


def attach_status_history(connection, orders):
    """Attach each order's status_history, mirroring KamiKoto's statusHistory array."""
    if not orders:
        return orders
    ids = [order["id"] for order in orders]
    marks = ",".join("?" * len(ids))
    rows = connection.execute(
        f"""
        SELECT order_id, status, note, updated_by, created_at
        FROM order_status_history WHERE order_id IN ({marks}) ORDER BY id
        """,
        ids,
    )
    by_order = defaultdict(list)
    for row in rows:
        by_order[row["order_id"]].append({
            "status": row["status"],
            "note": row["note"],
            "updated_by": row["updated_by"],
            "created_at": row["created_at"],
        })
    for order in orders:
        order["status_history"] = by_order[order["id"]]
    return orders


def record_status(connection, order_id, status, note, updated_by):
    connection.execute(
        """
        INSERT INTO order_status_history (order_id, status, note, updated_by)
        VALUES (?, ?, ?, ?)
        """,
        (order_id, status, note, updated_by),
    )


def product_fields(data):
    return {
        "title": text_field(data, "title", 3, 100),
        "description": text_field(data, "description", 1, 1000),
        "category": category_field(data),
        "price_cents": parse_price(data.get("price")),
        "stock": integer_field(data, "stock", 0, 100000),
        "is_preorder": int(flag_field(data, "is_preorder", False)),
        "is_active": int(flag_field(data, "is_active", True)),
    }


def category_field(data):
    category = text_field(data, "category")
    if category not in CATEGORIES:
        raise APIError("Select a valid category.")
    return category


def iso_date_field(data, key):
    """Require a real ISO calendar date, not merely a ten-character string."""
    value = text_field(data, key, 10, 10)
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise APIError(f"{key.replace('_', ' ').title()} must be a valid ISO date.")


def shop_today():
    return datetime.now(SHOP_TZ).date().isoformat()


def coupon_discount(connection, code, user_id, subtotal, conflict=False):
    """Check a coupon against this customer and cart subtotal and return
    (coupon, discount_cents). Shared by the "Apply" check and checkout so
    the two can never disagree; at checkout failures are 409 conflicts."""
    status = 409 if conflict else 400
    coupon = connection.execute(
        "SELECT * FROM coupons WHERE code = ?", (code,)
    ).fetchone()
    if coupon is None:
        raise APIError("Invalid coupon code.", 409 if conflict else 404)
    if not coupon["is_active"]:
        raise APIError("This coupon is no longer active.", status)

    today = shop_today()
    if today < coupon["start_date"]:
        raise APIError(f"This coupon is valid starting {coupon['start_date']}.", status)
    if today > coupon["end_date"]:
        raise APIError("This coupon has expired.", status)
    if coupon["max_uses"] > 0 and coupon["used_count"] >= coupon["max_uses"]:
        raise APIError("This coupon has reached its usage limit.", status)
    # Cancelled and declined orders give their coupon back, so they don't count.
    if coupon["once_per_customer"] and connection.execute(
        """
        SELECT 1 FROM orders
        WHERE buyer_id = ? AND coupon_id = ?
          AND status NOT IN ('Declined', 'Cancelled')
        """,
        (user_id, coupon["id"]),
    ).fetchone():
        raise APIError("You've already used this coupon.", status)
    if coupon["min_order_cents"] > 0 and subtotal < coupon["min_order_cents"]:
        raise APIError(
            f"This coupon needs a minimum order of "
            f"₱{coupon['min_order_cents'] / 100:,.2f}.", status
        )

    if coupon["discount_type"] == "percentage":
        discount = subtotal * coupon["discount_value"] // 100
        if coupon["max_discount_cents"] > 0:
            discount = min(discount, coupon["max_discount_cents"])
    else:
        discount = min(coupon["discount_value"], subtotal)
    return coupon, discount


def has_received(connection, user_id, product_id):
    """Reviews are limited to customers with a delivered order of the item."""
    return connection.execute(
        """
        SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id
        WHERE o.buyer_id = ? AND i.product_id = ? AND o.status = 'Delivered'
        LIMIT 1
        """,
        (user_id, product_id),
    ).fetchone() is not None


def enforce_rate_limit():
    """Apply bounded per-IP limits to public state-changing endpoints."""
    limit = RATE_LIMITS.get(request.path)
    if limit is None:
        return

    maximum, window = limit
    key = (request.path, request.remote_addr or "unknown")
    now = monotonic()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] >= window:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise APIError("Too many requests. Please try again later.", 429)
        bucket.append(now)


# --------------------------------------------------------- request hooks

@app.before_request
def protect_api():
    if not request.path.startswith("/api/"):
        return

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        expected = session.get("csrf")
        provided = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            raise APIError(
                "Session expired. Refresh the page and try again.", 403
            )
        enforce_rate_limit()


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'; connect-src 'self'; "
        "img-src 'self' data:; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com"
    )
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path == "/admin":
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------------ pages

@app.get("/")
def index():
    return render_template("index.html", page_mode="home")


@app.get("/products")
def products_page():
    """Dedicated, shareable storefront route for browsing the catalogue."""
    return render_template("index.html", page_mode="products")


@app.get("/admin")
def admin_page():
    # The page itself holds no data; every /api/admin/* call checks the role.
    return render_template("admin.html")


# --------------------------------------------------------- public / shop

@app.get("/api/state")
def get_state():
    session.setdefault("csrf", secrets.token_hex(32))
    rows = db().execute(
        "SELECT * FROM products WHERE is_active = 1 ORDER BY id"
    )
    return jsonify(
        csrf=session["csrf"],
        user=user_json(current_user()),
        categories=CATEGORIES,
        products=[product_json(row) for row in rows],
    )


@app.post("/api/register")
def register():
    data = payload()
    username = text_field(data, "username", 3, 30)
    password = data.get("password", "")
    email = data.get("email", "")
    if email:
        email = text_field(data, "email", 5, 254)
        if not EMAIL_RE.fullmatch(email):
            raise APIError("Enter a valid email address.")

    if not USERNAME_RE.fullmatch(username):
        raise APIError(
            "Username may contain only letters, numbers, and underscores."
        )
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise APIError("Password must contain 10–128 characters.")

    # Security: mirrors firestore.rules `users/{userId}` create rule — a
    # self-registration can never set is_admin, whatever the client sends.
    connection = db()
    try:
        with connection:
            user_id = connection.execute(
                """
                INSERT INTO users (username, password_hash, email, is_admin)
                VALUES (?, ?, ?, 0)
                """,
                (username, generate_password_hash(password), email),
            ).lastrowid
    except sqlite3.IntegrityError:
        raise APIError("That username is already registered.", 409)

    start_session(user_id)
    return jsonify(ok=True), 201


@app.post("/api/login")
def login():
    data = payload()
    username = text_field(data, "username", 3, 30)
    password = data.get("password", "")

    if not isinstance(password, str) or len(password) > 128:
        raise APIError("Invalid username or password.", 401)

    user = db().execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        raise APIError("Invalid username or password.", 401)

    start_session(user["id"])
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/coupons/validate")
def validate_coupon():
    """Validate a coupon against the current cart. Ported from
    KamiKoto's CouponService.validateCoupon (src/utils/couponService.js)."""
    user = require_user()
    data = payload()
    code = text_field(data, "code", 1, 40).upper()
    entries = data.get("items")
    if not isinstance(entries, list) or not entries:
        raise APIError("Your cart is empty.")

    connection = db()
    subtotal = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise APIError("Your cart could not be read.")
        product_id = integer_field(entry, "product_id", 1, 2**63 - 1)
        quantity = integer_field(entry, "quantity", 1, 99)
        product = connection.execute(
            "SELECT price_cents FROM products WHERE id = ? AND is_active = 1",
            (product_id,),
        ).fetchone()
        if product is None:
            raise APIError("An item in your cart is no longer available.", 409)
        subtotal += product["price_cents"] * quantity

    coupon, discount = coupon_discount(connection, code, user["id"], subtotal)

    return jsonify(
        ok=True,
        code=coupon["code"],
        discount_cents=discount,
        subtotal_cents=subtotal,
        total_cents=max(0, subtotal - discount),
    )


@app.post("/api/checkout")
def checkout():
    user = require_user()
    data = payload()
    recipient = text_field(data, "recipient", 2, 80)
    contact = text_field(data, "contact", 7, 40)

    fulfillment = data.get("fulfillment")
    if fulfillment not in FULFILLMENT:
        raise APIError("Choose delivery or pickup.")
    address = text_field(data, "address", 10, 500) \
        if fulfillment == "delivery" else ""

    entries = data.get("items")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
        raise APIError("Your cart is empty.")

    coupon_code = text_field(data, "coupon_code", 0, 40).upper()
    checkout_key = text_field(data, "checkout_key", 0, 64)
    if checkout_key and not CHECKOUT_KEY_RE.fullmatch(checkout_key):
        raise APIError("Your checkout could not be read. Refresh and try again.")

    wanted = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise APIError("Your cart could not be read.")
        product_id = integer_field(entry, "product_id", 1, 2**63 - 1)
        quantity = integer_field(entry, "quantity", 1, 99)
        wanted[product_id] = wanted.get(product_id, 0) + quantity
        if wanted[product_id] > 99:
            raise APIError("You can order at most 99 of one item.")

    connection = db()

    # Serialize inventory changes and commit the whole order atomically.
    with connection:
        connection.execute("BEGIN IMMEDIATE")

        # A retry of a checkout that already succeeded (e.g. the response was
        # lost on a flaky connection) gets the original order back.
        if checkout_key:
            existing = connection.execute(
                "SELECT id FROM orders WHERE buyer_id = ? AND checkout_key = ?",
                (user["id"], checkout_key),
            ).fetchone()
            if existing is not None:
                return jsonify(ok=True, order_id=existing["id"], duplicate=True)

        subtotal = 0
        lines = []
        for product_id, quantity in wanted.items():
            product = connection.execute(
                "SELECT * FROM products WHERE id = ? AND is_active = 1",
                (product_id,),
            ).fetchone()

            if product is None:
                raise APIError(
                    "An item in your cart is no longer available. "
                    "Please review your cart.", 409
                )

            if not product["is_preorder"]:
                changed = connection.execute(
                    """
                    UPDATE products SET stock = stock - ?
                    WHERE id = ? AND stock >= ?
                    """,
                    (quantity, product_id, quantity),
                ).rowcount
                if changed != 1:
                    left = product["stock"]
                    raise APIError(
                        f"{product['title']} is sold out."
                        if left == 0 else
                        f"Only {left} left of {product['title']}. "
                        "Please review your cart.", 409
                    )

            subtotal += product["price_cents"] * quantity
            lines.append((
                product_id, product["title"], product["price_cents"],
                quantity, product["is_preorder"],
            ))

        # Re-validate and apply the coupon server-side — never trust a
        # discount amount sent by the client (same principle as
        # firestore.rules restricting `coupons/{id}` updates to usedCount
        # only, and checkout always recomputing totals from the DB).
        discount = 0
        coupon_id = None
        if coupon_code:
            coupon, discount = coupon_discount(
                connection, coupon_code, user["id"], subtotal, conflict=True
            )
            coupon_id = coupon["id"]
            connection.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id = ?",
                (coupon_id,),
            )

        total = max(0, subtotal - discount)

        order_id = connection.execute(
            """
            INSERT INTO orders
                (buyer_id, recipient, contact, fulfillment, address,
                 subtotal_cents, discount_cents, total_cents, coupon_code,
                 coupon_id, checkout_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user["id"], recipient, contact, fulfillment, address,
             subtotal, discount, total, coupon_code, coupon_id, checkout_key),
        ).lastrowid

        connection.executemany(
            """
            INSERT INTO order_items
                (order_id, product_id, title, price_cents, quantity,
                 is_preorder)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(order_id, *line) for line in lines],
        )

        record_status(connection, order_id, "Placed", "Order placed by customer", user["username"])

    send_order_email(connection, order_id, "Placed")
    return jsonify(ok=True, order_id=order_id), 201


@app.get("/api/orders")
def my_orders():
    user = require_user()
    connection = db()
    orders = [
        dict(row) for row in connection.execute(
            "SELECT * FROM orders WHERE buyer_id = ? ORDER BY id DESC",
            (user["id"],),
        )
    ]
    return jsonify(orders=attach_status_history(connection, attach_items(connection, orders)))


@app.post("/api/orders/<int:order_id>/cancel")
def customer_cancel_order(order_id):
    """Customer-initiated cancellation. Mirrors firestore.rules: a customer
    may only move their own order from Placed/Approved to Cancelled."""
    user = require_user()
    connection = db()

    with connection:
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ? AND buyer_id = ?",
            (order_id, user["id"]),
        ).fetchone()
        if order is None:
            raise APIError("Order not found.", 404)
        if order["status"] not in CUSTOMER_TRANSITIONS or \
                "Cancelled" not in CUSTOMER_TRANSITIONS[order["status"]]:
            raise APIError(
                "This order can no longer be cancelled — it's already being prepared.",
                409,
            )

        # Guard on the status just read: if an admin changed the order in the
        # meantime (e.g. declined it, which already restocked), don't
        # overwrite that status or return the items to stock a second time.
        changed = connection.execute(
            "UPDATE orders SET status = 'Cancelled' WHERE id = ? AND status = ?",
            (order_id, order["status"]),
        ).rowcount
        if changed != 1:
            raise APIError("Order status changed elsewhere — refresh and try again.", 409)
        record_status(connection, order_id, "Cancelled", "Cancelled by customer", user["username"])
        _release_order(connection, order_id)

    send_order_email(connection, order_id, "Cancelled")
    return jsonify(ok=True)


@app.post("/api/orders/<int:order_id>/status")
def confirm_receipt(order_id):
    user = require_user()
    if payload().get("status") != "Delivered":
        raise APIError("Invalid status.")

    connection = db()
    with connection:
        changed = connection.execute(
            """
            UPDATE orders SET status = 'Delivered'
            WHERE id = ? AND buyer_id = ? AND status = 'Shipped'
            """,
            (order_id, user["id"]),
        ).rowcount
        if changed == 1:
            record_status(connection, order_id, "Delivered", "Receipt confirmed by customer", user["username"])

    if changed != 1:
        raise APIError(
            "This order cannot be confirmed yet.", 409
        )
    send_order_email(connection, order_id, "Delivered")
    return jsonify(ok=True)


@app.post("/api/custom-requests")
def create_custom_request():
    data = payload()
    name = text_field(data, "name", 2, 80)
    contact = text_field(data, "contact", 5, 120)
    occasion = text_field(data, "occasion", 0, 100)
    details = text_field(data, "details", 10, 2000)
    user = current_user()

    connection = db()
    with connection:
        connection.execute(
            """
            INSERT INTO custom_requests
                (user_id, name, contact, occasion, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user["id"] if user else None, name, contact, occasion, details),
        )
    return jsonify(ok=True), 201


@app.post("/api/newsletter")
def subscribe():
    email = text_field(payload(), "email", 5, 254)
    if not EMAIL_RE.fullmatch(email):
        raise APIError("Enter a valid email address.")

    connection = db()
    with connection:
        # Same response whether or not the address was already subscribed.
        connection.execute(
            "INSERT OR IGNORE INTO subscribers (email) VALUES (?)", (email,)
        )
    return jsonify(ok=True), 201


# ----------------------------------------------------------------- reviews

@app.get("/api/products/<int:product_id>/reviews")
def product_reviews(product_id):
    connection = db()
    rows = connection.execute(
        """
        SELECT r.rating, r.text, r.created_at, u.username
        FROM reviews r JOIN users u ON u.id = r.user_id
        WHERE r.product_id = ? ORDER BY r.id DESC
        """,
        (product_id,),
    ).fetchall()
    summary = connection.execute(
        "SELECT COUNT(*) AS count, AVG(rating) AS average FROM reviews WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    user = current_user()
    return jsonify(
        reviews=[dict(row) for row in rows],
        count=summary["count"],
        average=round(summary["average"], 1) if summary["average"] else None,
        can_review=user is not None and has_received(connection, user["id"], product_id),
    )


@app.post("/api/products/<int:product_id>/reviews")
def create_review(product_id):
    # Security: rating 1-5 and text <=2000 chars, mirroring the
    # firestore.rules `reviews/{reviewId}` create rule verbatim.
    user = require_user()
    data = payload()
    rating = integer_field(data, "rating", 1, 5)
    text = text_field(data, "text", 0, 2000) if data.get("text") else ""

    connection = db()
    product = connection.execute(
        "SELECT id FROM products WHERE id = ? AND is_active = 1", (product_id,)
    ).fetchone()
    if product is None:
        raise APIError("Product not found.", 404)
    if not has_received(connection, user["id"], product_id):
        raise APIError(
            "Only customers who have received this item can review it.", 403
        )

    with connection:
        try:
            connection.execute(
                """
                INSERT INTO reviews (product_id, user_id, rating, text)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (product_id, user_id)
                DO UPDATE SET rating = excluded.rating, text = excluded.text
                """,
                (product_id, user["id"], rating, text),
            )
        except sqlite3.IntegrityError:
            raise APIError("Could not save your review.", 400)
    return jsonify(ok=True), 201


# ------------------------------------------------------------ shop admin

@app.get("/api/admin/overview")
def admin_overview():
    require_user(admin=True)
    connection = db()

    products = [
        product_json(row) for row in connection.execute(
            "SELECT * FROM products ORDER BY id"
        )
    ]
    orders = [
        dict(row) for row in connection.execute(
            """
            SELECT o.*, u.username AS buyer
            FROM orders o JOIN users u ON u.id = o.buyer_id
            ORDER BY o.id DESC LIMIT 200
            """
        )
    ]
    custom_requests = [
        dict(row) for row in connection.execute(
            """
            SELECT r.*, u.username AS account
            FROM custom_requests r LEFT JOIN users u ON u.id = r.user_id
            ORDER BY r.id DESC LIMIT 200
            """
        )
    ]
    subscribers = [
        dict(row) for row in connection.execute(
            "SELECT email, created_at FROM subscribers "
            "ORDER BY id DESC LIMIT 1000"
        )
    ]
    coupons = [
        dict(row) for row in connection.execute(
            "SELECT * FROM coupons ORDER BY id DESC"
        )
    ]
    reviews = [
        dict(row) for row in connection.execute(
            """
            SELECT r.id, r.rating, r.text, r.created_at,
                   p.title AS product, u.username
            FROM reviews r
            JOIN products p ON p.id = r.product_id
            JOIN users u ON u.id = r.user_id
            ORDER BY r.id DESC LIMIT 200
            """
        )
    ]

    return jsonify(
        categories=CATEGORIES,
        custom_statuses=CUSTOM_STATUSES,
        order_statuses=ORDER_STATUSES,
        products=products,
        orders=attach_status_history(connection, attach_items(connection, orders)),
        custom_requests=custom_requests,
        subscribers=subscribers,
        coupons=coupons,
        reviews=reviews,
    )


@app.post("/api/admin/products")
def admin_create_product():
    require_user(admin=True)
    fields = product_fields(payload())

    connection = db()
    with connection:
        connection.execute(
            """
            INSERT INTO products
                (title, description, category, price_cents, stock,
                 is_preorder, is_active)
            VALUES (:title, :description, :category, :price_cents, :stock,
                    :is_preorder, :is_active)
            """,
            fields,
        )
    return jsonify(ok=True), 201


@app.post("/api/admin/products/<int:product_id>")
def admin_update_product(product_id):
    require_user(admin=True)
    fields = product_fields(payload())

    connection = db()
    with connection:
        changed = connection.execute(
            """
            UPDATE products SET
                title = :title, description = :description,
                category = :category, price_cents = :price_cents,
                stock = :stock, is_preorder = :is_preorder,
                is_active = :is_active
            WHERE id = :id
            """,
            {**fields, "id": product_id},
        ).rowcount

    if changed != 1:
        raise APIError("Product not found.", 404)
    return jsonify(ok=True)


def _release_order(connection, order_id):
    """Return a cancelled/declined order's items to stock and give its coupon
    use back. Mirrors orderService.updateOrderStatus's inventory-restoration
    branch. Callers must only release an order once (status-guarded update)."""
    connection.execute(
        """
        UPDATE coupons SET used_count = used_count - 1
        WHERE used_count > 0
          AND id = (SELECT coupon_id FROM orders WHERE id = ?)
        """,
        (order_id,),
    )
    items = connection.execute(
        "SELECT product_id, quantity, is_preorder FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    for item in items:
        if not item["is_preorder"]:
            connection.execute(
                "UPDATE products SET stock = stock + ? WHERE id = ?",
                (item["quantity"], item["product_id"]),
            )


@app.post("/api/admin/orders/<int:order_id>/status")
def admin_order_status(order_id):
    """Advance an order's status. Validated against ADMIN_TRANSITIONS —
    mirrors KamiKoto's validTransitions table in orderService.js, adapted
    to a stricter server-side check (KamiKoto only warns and still allows
    an admin override; here an invalid jump is rejected outright)."""
    admin = require_user(admin=True)
    data = payload()
    target = data.get("status")
    reason = text_field(data, "reason", 0, 300)

    if target not in ORDER_STATUSES:
        raise APIError("Invalid status.")

    connection = db()
    with connection:
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            raise APIError("Order not found.", 404)

        current = order["status"]
        allowed = ADMIN_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise APIError(
                f"Order cannot move from {current} to {target}.", 409
            )

        changed = connection.execute(
            "UPDATE orders SET status = ?, status_reason = ? WHERE id = ? AND status = ?",
            (target, reason, order_id, current),
        ).rowcount
        if changed != 1:
            raise APIError("Order status changed elsewhere — refresh and try again.", 409)

        note = reason or f"Order marked {target} by admin"
        record_status(connection, order_id, target, note, admin["username"])

        if target in RESTOCK_STATUSES:
            _release_order(connection, order_id)

    send_order_email(connection, order_id, target)
    return jsonify(ok=True)


@app.post("/api/admin/custom-requests/<int:request_id>/status")
def admin_request_status(request_id):
    require_user(admin=True)
    target = payload().get("status")
    if target not in CUSTOM_STATUSES:
        raise APIError("Invalid status.")

    connection = db()
    with connection:
        changed = connection.execute(
            "UPDATE custom_requests SET status = ? WHERE id = ?",
            (target, request_id),
        ).rowcount

    if changed != 1:
        raise APIError("Request not found.", 404)
    return jsonify(ok=True)


# ------------------------------------------------------------- coupons (admin)

def coupon_fields(data):
    discount_type = data.get("discount_type")
    if discount_type not in ("percentage", "fixed"):
        raise APIError("Choose a percentage or fixed discount.")

    if discount_type == "percentage":
        discount_value = integer_field(data, "discount_value", 1, 100)
    else:
        discount_value = parse_price(data.get("discount_value"))

    max_discount = data.get("max_discount")
    max_discount_cents = parse_price(max_discount) if max_discount else 0
    min_order = data.get("min_order")
    min_order_cents = parse_price(min_order) if min_order else 0

    start_date = iso_date_field(data, "start_date")
    end_date = iso_date_field(data, "end_date")
    if end_date < start_date:
        raise APIError("The end date must be on or after the start date.")

    return {
        "code": text_field(data, "code", 3, 40).upper(),
        "discount_type": discount_type,
        "discount_value": discount_value,
        "max_discount_cents": max_discount_cents,
        "min_order_cents": min_order_cents,
        "max_uses": integer_field(data, "max_uses", 0, 1000000),
        "once_per_customer": int(flag_field(data, "once_per_customer", True)),
        "start_date": start_date,
        "end_date": end_date,
        "is_active": int(flag_field(data, "is_active", True)),
    }


@app.post("/api/admin/coupons")
def admin_create_coupon():
    require_user(admin=True)
    fields = coupon_fields(payload())

    connection = db()
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO coupons
                    (code, discount_type, discount_value, max_discount_cents,
                     min_order_cents, max_uses, once_per_customer,
                     start_date, end_date, is_active)
                VALUES (:code, :discount_type, :discount_value, :max_discount_cents,
                        :min_order_cents, :max_uses, :once_per_customer,
                        :start_date, :end_date, :is_active)
                """,
                fields,
            )
    except sqlite3.IntegrityError:
        raise APIError("That coupon code is already in use.", 409)
    return jsonify(ok=True), 201


@app.post("/api/admin/coupons/<int:coupon_id>")
def admin_update_coupon(coupon_id):
    require_user(admin=True)
    fields = coupon_fields(payload())

    connection = db()
    try:
        with connection:
            changed = connection.execute(
                """
                UPDATE coupons SET
                    code = :code, discount_type = :discount_type,
                    discount_value = :discount_value,
                    max_discount_cents = :max_discount_cents,
                    min_order_cents = :min_order_cents, max_uses = :max_uses,
                    once_per_customer = :once_per_customer,
                    start_date = :start_date, end_date = :end_date,
                    is_active = :is_active
                WHERE id = :id
                """,
                {**fields, "id": coupon_id},
            ).rowcount
    except sqlite3.IntegrityError:
        raise APIError("That coupon code is already in use.", 409)

    if changed != 1:
        raise APIError("Coupon not found.", 404)
    return jsonify(ok=True)


@app.post("/api/admin/coupons/<int:coupon_id>/delete")
def admin_delete_coupon(coupon_id):
    require_user(admin=True)
    connection = db()
    with connection:
        changed = connection.execute(
            "DELETE FROM coupons WHERE id = ?", (coupon_id,)
        ).rowcount
    if changed != 1:
        raise APIError("Coupon not found.", 404)
    return jsonify(ok=True)


# --------------------------------------------------------------- email

# Transactional emails on order-status change. Ported from KamiKoto's
# functions/api/send-email.js (a Resend REST proxy) + the status-driven
# dispatch in src/utils/orderService.js. Like KamiKoto's
# `REACT_APP_EMAIL_ENABLED=false` escape hatch, this stays a silent no-op
# whenever RESEND_API_KEY isn't configured, so checkout/status updates
# never fail because of email.
EMAIL_SUBJECTS = {
    "Placed": "We received your order",
    "Approved": "Your order is being prepared",
    "Packed": "Your order is packed",
    "Shipped": "Your order is on its way",
    "Delivered": "Your order was delivered — thank you!",
    "Declined": "There was a problem with your order",
    "Cancelled": "Your order was cancelled",
}


def send_order_email(connection, order_id, status):
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key or os.environ.get("EMAIL_ENABLED") == "false":
        return  # Email not configured — fail silently, like KamiKoto's flag.

    order = connection.execute(
        """
        SELECT o.*, u.username, u.email
        FROM orders o JOIN users u ON u.id = o.buyer_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if order is None or not order["email"]:
        return

    subject = EMAIL_SUBJECTS.get(status, f"Order update: {status}")
    html = (
        f"<p>Hi {order['username']},</p>"
        f"<p>Your order #{order['id']} is now <strong>{status}</strong>.</p>"
        f"<p>Total: ₱{order['total_cents'] / 100:,.2f}</p>"
        f"<p>— Skye Crafts</p>"
    )

    try:
        import urllib.request
        import json as _json

        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=_json.dumps({
                "from": os.environ.get("EMAIL_FROM", "Skye Crafts <hello@mailer.skyecrafts.example>"),
                "to": [order["email"]],
                "subject": subject,
                "html": html,
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as error:  # noqa: BLE001 — email must never break checkout
        app.logger.warning("Order email failed for order %s: %s", order_id, error)


with app.app_context():
    init_db()

if __name__ == "__main__":
    # Local development only. Do not expose Flask's development server publicly.
    app.run(host="127.0.0.1", port=5000, debug=False)
