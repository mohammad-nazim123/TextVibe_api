import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Payment, TokenPackage

User = get_user_model()

RAZORPAY_KEY_ID = "rzp_test_key"
RAZORPAY_KEY_SECRET = "test_secret"


def razorpay_signature(order_id, payment_id, secret=RAZORPAY_KEY_SECRET):
    message = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


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


class RazorpayPaymentFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="razorpay-buyer@example.com",
            is_verified=True,
        )
        self.package = TokenPackage.objects.create(amount=50, tokens=220, is_active=True)
        self.initiate_url = reverse("initiate-payment")
        self.verify_url = reverse("verify-payment")
        self.client.force_authenticate(self.user)

    def test_initiate_requires_gateway_configuration(self):
        with (
            patch("payments.views.RAZORPAY_KEY_ID", ""),
            patch("payments.views.RAZORPAY_KEY_SECRET", ""),
        ):
            response = self.client.post(
                self.initiate_url,
                {"package_id": self.package.id},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("detail", response.data)
        self.assertEqual(Payment.objects.count(), 0)

    def test_initiate_creates_razorpay_order_and_pending_payment(self):
        order_resource = Mock()
        order_resource.create.return_value = {"id": "order_test_123"}
        client_instance = SimpleNamespace(order=order_resource)
        razorpay_module = SimpleNamespace(Client=Mock(return_value=client_instance))

        with (
            patch("payments.views.razorpay", razorpay_module),
            patch("payments.views.RAZORPAY_KEY_ID", RAZORPAY_KEY_ID),
            patch("payments.views.RAZORPAY_KEY_SECRET", RAZORPAY_KEY_SECRET),
        ):
            response = self.client.post(
                self.initiate_url,
                {"package_id": self.package.id},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["order_id"], "order_test_123")
        self.assertEqual(response.data["amount"], self.package.amount)
        self.assertEqual(response.data["tokens"], self.package.tokens)
        self.assertEqual(response.data["currency"], "INR")
        self.assertEqual(response.data["key_id"], RAZORPAY_KEY_ID)
        razorpay_module.Client.assert_called_once_with(
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
        )
        order_resource.create.assert_called_once()
        order_payload = order_resource.create.call_args.args[0]
        self.assertEqual(order_payload["amount"], self.package.amount * 100)
        self.assertEqual(order_payload["currency"], "INR")

        payment = Payment.objects.get()
        self.assertEqual(payment.user, self.user)
        self.assertEqual(payment.package, self.package)
        self.assertEqual(payment.razorpay_order_id, "order_test_123")
        self.assertEqual(payment.status, "pending")

    def test_initiate_handles_razorpay_order_creation_failure(self):
        order_resource = Mock()
        order_resource.create.side_effect = Exception("gateway unavailable")
        client_instance = SimpleNamespace(order=order_resource)
        razorpay_module = SimpleNamespace(Client=Mock(return_value=client_instance))

        with (
            patch("payments.views.razorpay", razorpay_module),
            patch("payments.views.RAZORPAY_KEY_ID", RAZORPAY_KEY_ID),
            patch("payments.views.RAZORPAY_KEY_SECRET", RAZORPAY_KEY_SECRET),
        ):
            response = self.client.post(
                self.initiate_url,
                {"package_id": self.package.id},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("detail", response.data)
        self.assertEqual(Payment.objects.count(), 0)

    def test_verify_valid_signature_credits_tokens(self):
        payment = Payment.objects.create(
            user=self.user,
            package=self.package,
            razorpay_order_id="order_verify_123",
            amount=self.package.amount,
            tokens=self.package.tokens,
            status="pending",
        )
        payment_id = "pay_verify_123"

        with patch("payments.views.RAZORPAY_KEY_SECRET", RAZORPAY_KEY_SECRET):
            response = self.client.post(
                self.verify_url,
                {
                    "razorpay_order_id": payment.razorpay_order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": razorpay_signature(
                        payment.razorpay_order_id,
                        payment_id,
                    ),
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(payment.status, "success")
        self.assertEqual(payment.razorpay_payment_id, payment_id)
        self.assertEqual(self.user.tokens, self.package.tokens)
        self.assertEqual(response.data["credited_tokens"], self.package.tokens)
        self.assertEqual(response.data["user_tokens"], self.user.tokens)

    def test_verify_invalid_signature_does_not_credit_tokens(self):
        payment = Payment.objects.create(
            user=self.user,
            package=self.package,
            razorpay_order_id="order_bad_signature",
            amount=self.package.amount,
            tokens=self.package.tokens,
            status="pending",
        )

        with patch("payments.views.RAZORPAY_KEY_SECRET", RAZORPAY_KEY_SECRET):
            response = self.client.post(
                self.verify_url,
                {
                    "razorpay_order_id": payment.razorpay_order_id,
                    "razorpay_payment_id": "pay_bad_signature",
                    "razorpay_signature": "not-a-valid-signature",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payment.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.razorpay_payment_id, None)
        self.assertEqual(self.user.tokens, 0)

    def test_duplicate_verification_does_not_double_credit_tokens(self):
        payment = Payment.objects.create(
            user=self.user,
            package=self.package,
            razorpay_order_id="order_duplicate_verify",
            amount=self.package.amount,
            tokens=self.package.tokens,
            status="pending",
        )
        payment_id = "pay_duplicate_verify"
        payload = {
            "razorpay_order_id": payment.razorpay_order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": razorpay_signature(payment.razorpay_order_id, payment_id),
        }

        with patch("payments.views.RAZORPAY_KEY_SECRET", RAZORPAY_KEY_SECRET):
            first_response = self.client.post(self.verify_url, payload, format="json")
            second_response = self.client.post(self.verify_url, payload, format="json")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tokens, self.package.tokens)


class PaymentHistoryViewTests(APITestCase):
    def test_history_is_capped_at_100(self):
        user = User.objects.create_user(email="hist@example.com", is_verified=True)
        Payment.objects.bulk_create(
            Payment(
                user=user,
                razorpay_order_id=f"order_{i}",
                amount=10,
                tokens=10,
                status="success",
            )
            for i in range(105)
        )
        self.client.force_authenticate(user)
        response = self.client.get(reverse("payment-history"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 100)
