"""Regression coverage for the security-critical Flask API paths.

Run with: python -m unittest discover -s tests -v
"""
import importlib
import os
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
