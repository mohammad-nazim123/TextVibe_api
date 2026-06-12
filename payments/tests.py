from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Payment, TokenPackage

User = get_user_model()


class PurchasePaymentViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="buyer@example.com",
            is_verified=True,
        )
        self.active_package = TokenPackage.objects.create(amount=25, tokens=100, is_active=True)
        self.inactive_package = TokenPackage.objects.create(
            amount=60,
            tokens=300,
            is_active=False,
        )
        self.url = reverse("purchase-payment")

    def authenticate(self):
        self.client.force_authenticate(self.user)

    def test_purchase_requires_authentication(self):
        response = self.client.post(
            self.url,
            {
                "package_id": self.active_package.id,
                "payment_method": Payment.PAYMENT_METHOD_UPI,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_valid_purchase_creates_payment_and_credits_tokens(self):
        self.authenticate()

        response = self.client.post(
            self.url,
            {
                "package_id": self.active_package.id,
                "payment_method": Payment.PAYMENT_METHOD_GOOGLE_PLAY,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Payment.objects.count(), 1)

        payment = Payment.objects.get()
        self.user.refresh_from_db()

        self.assertEqual(payment.user, self.user)
        self.assertEqual(payment.package, self.active_package)
        self.assertEqual(payment.status, "success")
        self.assertEqual(payment.payment_method, Payment.PAYMENT_METHOD_GOOGLE_PLAY)
        self.assertEqual(self.user.tokens, self.active_package.tokens)
        self.assertEqual(response.data["credited_tokens"], self.active_package.tokens)
        self.assertEqual(response.data["user_tokens"], self.user.tokens)
        self.assertEqual(response.data["payment_id"], payment.id)
        self.assertEqual(response.data["reference"], payment.razorpay_order_id)

    def test_invalid_package_is_rejected(self):
        self.authenticate()

        response = self.client.post(
            self.url,
            {
                "package_id": 999999,
                "payment_method": Payment.PAYMENT_METHOD_CARD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("package_id", response.data)
        self.assertEqual(Payment.objects.count(), 0)

    def test_inactive_package_is_rejected(self):
        self.authenticate()

        response = self.client.post(
            self.url,
            {
                "package_id": self.inactive_package.id,
                "payment_method": Payment.PAYMENT_METHOD_CARD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("package_id", response.data)
        self.assertEqual(Payment.objects.count(), 0)

    def test_invalid_payment_method_is_rejected(self):
        self.authenticate()

        response = self.client.post(
            self.url,
            {
                "package_id": self.active_package.id,
                "payment_method": "cash",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment_method", response.data)
        self.assertEqual(Payment.objects.count(), 0)

    def test_repeated_purchases_credit_tokens_each_time(self):
        self.authenticate()

        first_response = self.client.post(
            self.url,
            {
                "package_id": self.active_package.id,
                "payment_method": Payment.PAYMENT_METHOD_NETBANKING,
            },
            format="json",
        )
        second_response = self.client.post(
            self.url,
            {
                "package_id": self.active_package.id,
                "payment_method": Payment.PAYMENT_METHOD_WALLET,
            },
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(Payment.objects.count(), 2)

        self.user.refresh_from_db()
        payments = list(Payment.objects.order_by("created_at"))

        self.assertEqual(self.user.tokens, self.active_package.tokens * 2)
        self.assertEqual(second_response.data["user_tokens"], self.user.tokens)
        self.assertEqual(payments[0].payment_method, Payment.PAYMENT_METHOD_NETBANKING)
        self.assertEqual(payments[1].payment_method, Payment.PAYMENT_METHOD_WALLET)
        self.assertNotEqual(payments[0].razorpay_order_id, payments[1].razorpay_order_id)
