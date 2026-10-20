"""Regression coverage for the security-critical Flask API paths.

Run with: python -m unittest discover -s tests -v
"""
import importlib
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock


class StoreApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.database.close()
        os.environ.update({
            "DATABASE_PATH": cls.database.name,
            "SECRET_KEY": "test-only-secret-not-for-production",
            "ADMIN_USERNAME": "testadmin",
            "ADMIN_PASSWORD": "test-admin-password-123",
            "EMAIL_ENABLED": "false",
        })
        module = importlib.import_module("app")
        cls.module = module
        cls.app = module.app
        cls.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(cls.database.name + suffix)
            except FileNotFoundError:
                pass

    def setUp(self):
        with self.module._rate_lock:
            self.module._rate_buckets.clear()
        self.client = self.app.test_client()

    def csrf(self):
        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200)
        return {"X-CSRF-Token": state.get_json()["csrf"]}

    def register(self, username=None):
        username = username or f"customer_{id(self)}"
        response = self.client.post(
            "/api/register",
            json={"username": username, "password": "customer-password-123"},
            headers=self.csrf(),
        )
        self.assertEqual(response.status_code, 201)

    def login_admin(self):
        response = self.client.post(
            "/api/login",
            json={"username": "testadmin", "password": "test-admin-password-123"},
            headers=self.csrf(),
        )
        self.assertEqual(response.status_code, 200)

    def admin_post(self, path, data):
        """POST as the admin from a separate client, leaving the customer's
        session in self.client untouched."""
        admin = self.app.test_client()
        token = admin.get("/api/state").get_json()["csrf"]
        login = admin.post(
            "/api/login",
            json={"username": "testadmin", "password": "test-admin-password-123"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(login.status_code, 200)
        token = admin.get("/api/state").get_json()["csrf"]
        return admin.post(path, json=data, headers={"X-CSRF-Token": token})

    def create_coupon(self, code, **overrides):
        response = self.admin_post("/api/admin/coupons", {
            "code": code, "discount_type": "percentage", "discount_value": 10,
            "max_uses": 0, "start_date": "2020-01-01", "end_date": "2099-12-31",
            "is_active": True, **overrides,
        })
        self.assertEqual(response.status_code, 201, response.get_json())

    def deliver(self, order_id):
        for status in ("Approved", "Packed", "Shipped", "Delivered"):
            response = self.admin_post(
                f"/api/admin/orders/{order_id}/status", {"status": status}
            )
            self.assertEqual(response.status_code, 200, response.get_json())

    def coupon_used_count(self, code):
        connection = sqlite3.connect(self.database.name)
        try:
            return connection.execute(
                "SELECT used_count FROM coupons WHERE code = ?", (code,)
            ).fetchone()[0]
        finally:
            connection.close()

    def stocked_product(self):
        products = self.client.get("/api/state").get_json()["products"]
        return next(p for p in products if not p["is_preorder"] and p["stock"] > 0)

    def place_order(self, product_id, **extra):
        response = self.client.post(
            "/api/checkout",
            json={
                "recipient": "Test Customer",
                "contact": "09171234567",
                "fulfillment": "pickup",
                "items": [{"product_id": product_id, "quantity": 1}],
                **extra,
            },
            headers=self.csrf(),
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["order_id"]

    def stock_of(self, product_id):
        connection = sqlite3.connect(self.database.name)
        try:
            return connection.execute(
                "SELECT stock FROM products WHERE id = ?", (product_id,)
            ).fetchone()[0]
        finally:
            connection.close()

    def test_mutations_require_csrf(self):
        response = self.client.post(
            "/api/register",
            json={"username": "customer", "password": "customer-password-123"},
        )
        self.assertEqual(response.status_code, 403)

    def test_login_is_rate_limited(self):
        header = self.csrf()
        for _ in range(10):
            response = self.client.post(
                "/api/login",
                json={"username": "nobody", "password": "wrong-password"},
                headers=header,
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/login",
            json={"username": "nobody", "password": "wrong-password"},
            headers=header,
        )
        self.assertEqual(response.status_code, 429)

    def test_customer_cannot_read_admin_data(self):
        self.register()
        response = self.client.get("/api/admin/overview")
        self.assertEqual(response.status_code, 403)

    def test_checkout_uses_database_price_and_creates_own_order(self):
        self.register()
        state = self.client.get("/api/state").get_json()
        product = state["products"][0]
        response = self.client.post(
            "/api/checkout",
            json={
                "recipient": "Test Customer",
                "contact": "09171234567",
                "fulfillment": "delivery",
                "address": "123 Test Street, Test City",
                "items": [{"product_id": product["id"], "quantity": 1}],
                "total_cents": 1,
            },
            headers={"X-CSRF-Token": state["csrf"]},
        )
        self.assertEqual(response.status_code, 201)
        order = self.client.get("/api/orders").get_json()["orders"][0]
        self.assertEqual(order["total_cents"], product["price_cents"])

    def test_coupon_requires_real_calendar_dates(self):
        state = self.client.get("/api/state").get_json()
        login = self.client.post(
            "/api/login",
            json={"username": "testadmin", "password": "test-admin-password-123"},
            headers={"X-CSRF-Token": state["csrf"]},
        )
        self.assertEqual(login.status_code, 200)
        header = self.csrf()
        response = self.client.post(
            "/api/admin/coupons",
            json={
                "code": "DATES", "discount_type": "percentage", "discount_value": 10,
                "max_discount": None, "min_order": None, "max_uses": 1,
                "start_date": "2026-02-30", "end_date": "2026-12-31", "is_active": True,
            },
            headers=header,
        )
        self.assertEqual(response.status_code, 400)

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_products_route_renders_the_product_browser(self):
        response = self.client.get("/products")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-page="products"', response.data)
        self.assertIn(b'id="productSearch"', response.data)
        self.assertNotIn(b'id="home"', response.data)

    def test_optional_custom_request_fields_are_accepted(self):
        response = self.client.post(
            "/api/custom-requests",
            json={
                "name": "Customer Name",
                "contact": "09171234567",
                "occasion": None,
                "details": "Need a handmade bouquet in pastel tones for a birthday.",
            },
            headers=self.csrf(),
        )
        self.assertEqual(response.status_code, 201)

    def test_customer_cancel_does_not_restock_twice_when_raced(self):
        self.register()
        product = self.stocked_product()
        order_id = self.place_order(product["id"])
        stock_before_cancel = self.stock_of(product["id"])
        database = self.database.name

        class DeclinedMidRequest:
            """Wraps the request's connection so an admin decline (with its
            restock) lands between the cancel route's read and its write."""

            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def __enter__(self):
                return self._connection.__enter__()

            def __exit__(self, *exc):
                return self._connection.__exit__(*exc)

            def execute(self, sql, *args):
                result = self._connection.execute(sql, *args)
                if sql.startswith("SELECT * FROM orders WHERE id = ? AND buyer_id"):
                    row = result.fetchone()
                    with sqlite3.connect(database) as other:
                        other.execute(
                            "UPDATE orders SET status = 'Declined' WHERE id = ?",
                            (order_id,),
                        )
                        other.execute(
                            "UPDATE products SET stock = stock + 1 WHERE id = ?",
                            (product["id"],),
                        )
                    other.close()
                    return mock.Mock(fetchone=lambda: row)
                return result

        real_db = self.module.db
        with mock.patch.object(
            self.module, "db", lambda: DeclinedMidRequest(real_db())
        ):
            response = self.client.post(
                f"/api/orders/{order_id}/cancel", json={}, headers=self.csrf()
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock_of(product["id"]), stock_before_cancel + 1)
        order = self.client.get("/api/orders").get_json()["orders"][0]
        self.assertEqual(order["status"], "Declined")

    def test_customer_cancel_restocks_once(self):
        self.register()
        product = self.stocked_product()
        stock_before_order = self.stock_of(product["id"])
        order_id = self.place_order(product["id"])
        self.assertEqual(self.stock_of(product["id"]), stock_before_order - 1)

        header = self.csrf()
        first = self.client.post(f"/api/orders/{order_id}/cancel", json={}, headers=header)
        second = self.client.post(f"/api/orders/{order_id}/cancel", json={}, headers=header)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.stock_of(product["id"]), stock_before_order)

    def test_coupon_validation_is_rate_limited(self):
        self.register()
        product = self.stocked_product()
        header = self.csrf()
        guess = {"items": [{"product_id": product["id"], "quantity": 1}]}
        for attempt in range(20):
            response = self.client.post(
                "/api/coupons/validate",
                json={**guess, "code": f"GUESS{attempt}"},
                headers=header,
            )
            self.assertEqual(response.status_code, 404)
        response = self.client.post(
            "/api/coupons/validate", json={**guess, "code": "GUESS20"}, headers=header
        )
        self.assertEqual(response.status_code, 429)

    def test_malformed_text_fields_are_rejected_not_crashed(self):
        self.login_admin()
        coupon = self.client.post(
            "/api/admin/coupons",
            json={
                "code": "SHAPES", "discount_type": "percentage", "discount_value": 10,
                "max_uses": 0, "start_date": "2020-01-01", "end_date": "2099-12-31",
                "is_active": True,
            },
            headers=self.csrf(),
        )
        self.assertEqual(coupon.status_code, 201)
        product = self.stocked_product()
        order_id = self.place_order(product["id"])
        header = self.csrf()

        checkout = self.client.post(
            "/api/checkout",
            json={
                "recipient": "Test Customer", "contact": "09171234567",
                "fulfillment": "pickup", "coupon_code": 5,
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
            headers=header,
        )
        validate = self.client.post(
            "/api/coupons/validate",
            json={"code": "SHAPES", "items": [[product["id"], 1]]},
            headers=header,
        )
        status = self.client.post(
            f"/api/admin/orders/{order_id}/status",
            json={"status": "Approved", "reason": 5},
            headers=header,
        )
        self.assertEqual(
            [checkout.status_code, validate.status_code, status.status_code],
            [400, 400, 400],
        )

    def test_review_comment_is_saved(self):
        self.register()
        product = self.stocked_product()
        self.deliver(self.place_order(product["id"]))
        response = self.client.post(
            f"/api/products/{product['id']}/reviews",
            json={"rating": 4, "text": "  Lovely colours.  "},
            headers=self.csrf(),
        )
        self.assertEqual(response.status_code, 201)
        reviews = self.client.get(f"/api/products/{product['id']}/reviews").get_json()
        self.assertIn("Lovely colours.", [review["text"] for review in reviews["reviews"]])

    def test_reviews_require_a_delivered_order(self):
        self.register()
        product = self.stocked_product()
        path = f"/api/products/{product['id']}/reviews"
        self.assertFalse(self.client.get(path).get_json()["can_review"])
        self.assertEqual(
            self.client.post(path, json={"rating": 5}, headers=self.csrf()).status_code,
            403,
        )

        order_id = self.place_order(product["id"])
        self.admin_post(f"/api/admin/orders/{order_id}/status", {"status": "Approved"})
        self.assertEqual(
            self.client.post(path, json={"rating": 5}, headers=self.csrf()).status_code,
            403,
            "an order that has not been delivered does not unlock reviews",
        )

        for status in ("Packed", "Shipped", "Delivered"):
            self.admin_post(f"/api/admin/orders/{order_id}/status", {"status": status})
        self.assertTrue(self.client.get(path).get_json()["can_review"])
        self.assertEqual(
            self.client.post(path, json={"rating": 5}, headers=self.csrf()).status_code,
            201,
        )

    def test_cancelled_and_declined_orders_give_the_coupon_back(self):
        self.create_coupon("COMEBACK", max_uses=1)
        self.register()
        product = self.stocked_product()

        order_id = self.place_order(product["id"], coupon_code="COMEBACK")
        self.assertEqual(self.coupon_used_count("COMEBACK"), 1)
        cancel = self.client.post(f"/api/orders/{order_id}/cancel", json={}, headers=self.csrf())
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(self.coupon_used_count("COMEBACK"), 0)

        order_id = self.place_order(product["id"], coupon_code="COMEBACK")
        decline = self.admin_post(
            f"/api/admin/orders/{order_id}/status", {"status": "Declined"}
        )
        self.assertEqual(decline.status_code, 200)
        self.assertEqual(self.coupon_used_count("COMEBACK"), 0)

        # Released uses are usable again, by the same customer too.
        self.place_order(product["id"], coupon_code="COMEBACK")
        self.assertEqual(self.coupon_used_count("COMEBACK"), 1)

    def test_coupon_is_once_per_customer_unless_disabled(self):
        self.create_coupon("FIRSTBUY")
        self.create_coupon("EVERYDAY", once_per_customer=False)
        self.register()
        product = self.stocked_product()
        items = [{"product_id": product["id"], "quantity": 1}]

        self.place_order(product["id"], coupon_code="FIRSTBUY")
        again = self.client.post(
            "/api/coupons/validate", json={"code": "FIRSTBUY", "items": items},
            headers=self.csrf(),
        )
        self.assertEqual(again.status_code, 400)
        self.assertIn("already used", again.get_json()["error"])
        checkout = self.client.post(
            "/api/checkout",
            json={"recipient": "Test Customer", "contact": "09171234567",
                  "fulfillment": "pickup", "items": items, "coupon_code": "FIRSTBUY"},
            headers=self.csrf(),
        )
        self.assertEqual(checkout.status_code, 409)

        self.place_order(product["id"], coupon_code="EVERYDAY")
        self.place_order(product["id"], coupon_code="EVERYDAY")

        self.client = self.app.test_client()
        self.register(f"other_{id(self)}")
        self.place_order(product["id"], coupon_code="FIRSTBUY")

    def test_coupon_dates_follow_philippine_time(self):
        self.create_coupon("MIDNIGHT", start_date="2026-11-01", end_date="2026-11-11")
        self.create_coupon("ENDED", start_date="2026-10-01", end_date="2026-10-31")
        self.register()
        product = self.stocked_product()
        items = [{"product_id": product["id"], "quantity": 1}]

        class AtManilaMidnight(datetime):
            """00:30 on 1 November in Manila is still 31 October in UTC."""

            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc).astimezone(tz)

        with mock.patch.object(self.module, "datetime", AtManilaMidnight):
            header = self.csrf()
            started = self.client.post(
                "/api/coupons/validate", json={"code": "MIDNIGHT", "items": items},
                headers=header,
            )
            ended = self.client.post(
                "/api/coupons/validate", json={"code": "ENDED", "items": items},
                headers=header,
            )
        self.assertEqual(started.status_code, 200, started.get_json())
        self.assertEqual(ended.status_code, 400)
        self.assertIn("expired", ended.get_json()["error"])

    def test_retried_checkout_returns_the_original_order(self):
        self.register()
        product = self.stocked_product()
        stock_before = self.stock_of(product["id"])
        key = "retry-key-0123456789abcdef"

        first_id = self.place_order(product["id"], checkout_key=key)
        retry = self.client.post(
            "/api/checkout",
            json={"recipient": "Test Customer", "contact": "09171234567",
                  "fulfillment": "pickup", "checkout_key": key,
                  "items": [{"product_id": product["id"], "quantity": 1}]},
            headers=self.csrf(),
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json(), {"ok": True, "order_id": first_id, "duplicate": True})
        self.assertEqual(self.stock_of(product["id"]), stock_before - 1)
        self.assertEqual(len(self.client.get("/api/orders").get_json()["orders"]), 1)

        second_id = self.place_order(product["id"], checkout_key="another-key-0123456789")
        self.assertNotEqual(second_id, first_id)

    def test_products_page_footer_links_reach_home_sections(self):
        response = self.client.get("/products")
        self.assertIn(b'href="/#faq"', response.data)
        self.assertNotIn(b'href="#faq"', response.data)


if __name__ == "__main__":
    unittest.main()
