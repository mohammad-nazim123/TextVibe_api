import hashlib
import hmac
import logging
import os
from uuid import uuid4

try:
    import razorpay
except ImportError:  # pragma: no cover - covered by dependency checks in deploys.
    razorpay = None
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payment, TokenPackage
from .serializers import (
    InitiatePaymentSerializer,
    PaymentSerializer,
    PurchasePaymentSerializer,
    TokenPackageSerializer,
    VerifyPaymentSerializer,
)

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

logger = logging.getLogger("payments")


class TokenPackageListView(generics.ListAPIView):
    """List all active token packages. Packages rarely change, so the
    serialized list is served from cache for 60s instead of querying Neon."""

    queryset = TokenPackage.objects.filter(is_active=True).only(
        "id", "amount", "tokens"
    ).order_by("amount")
    serializer_class = TokenPackageSerializer
    permission_classes = [AllowAny]

    def list(self, request, *args, **kwargs):
        data = cache.get_or_set(
            "token_packages_v1",
            lambda: self.get_serializer(self.get_queryset(), many=True).data,
            60,
        )
        return Response(data)


def _generate_internal_reference() -> str:
    return f"mock_{uuid4().hex}"


def _gateway_is_configured() -> bool:
    return bool(razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def _gateway_unavailable_response() -> Response:
    return Response(
        {"detail": "Payment gateway is not configured. Please try again later."},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class PurchasePaymentView(APIView):
    """Temporarily credit tokens immediately after a payment option is tapped."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PurchasePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        package = TokenPackage.objects.get(
            id=serializer.validated_data["package_id"], is_active=True
        )
        payment_method = serializer.validated_data["payment_method"]

        with transaction.atomic():
            payment = Payment.objects.create(
                user=request.user,
                package=package,
                razorpay_order_id=_generate_internal_reference(),
                amount=package.amount,
                tokens=package.tokens,
                payment_method=payment_method,
                status="success",
            )

            request.user.tokens = F("tokens") + payment.tokens
            request.user.save(update_fields=["tokens"])
            request.user.refresh_from_db(fields=["tokens"])

        logger.info(
            "Mock payment %s completed via %s. Credited %s tokens to user %s",
            payment.razorpay_order_id,
            payment_method,
            payment.tokens,
            request.user.id,
        )

        return Response(
            {
                "detail": "Payment completed and tokens added",
                "payment_id": payment.id,
                "reference": payment.razorpay_order_id,
                "payment_method": payment.payment_method,
                "credited_tokens": payment.tokens,
                "user_tokens": request.user.tokens,
            },
            status=status.HTTP_200_OK,
        )


class InitiatePaymentView(APIView):
    """Create a Razorpay order and return order details to the client."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        package = TokenPackage.objects.get(
            id=serializer.validated_data["package_id"], is_active=True
        )

        if not _gateway_is_configured():
            logger.error("Razorpay payment initiation requested before gateway configuration.")
            return _gateway_unavailable_response()

        try:
            client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
            order = client.order.create({
                "amount": package.amount * 100,
                "currency": "INR",
                "receipt": f"pkg{package.id}_{uuid4().hex[:8]}",
            })
        except Exception:
            logger.exception(
                "Razorpay order creation failed for package %s and user %s",
                package.id,
                request.user.id,
            )
            return Response(
                {"detail": "Could not create payment order. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        order_id = order.get("id") if isinstance(order, dict) else None
        if not order_id:
            logger.error("Razorpay order response did not include an id: %s", order)
            return Response(
                {"detail": "Could not create payment order. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        Payment.objects.create(
            user=request.user,
            package=package,
            razorpay_order_id=order_id,
            amount=package.amount,
            tokens=package.tokens,
            status="pending",
        )

        return Response({
            "order_id": order_id,
            "amount": package.amount,
            "tokens": package.tokens,
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
        })


class VerifyPaymentView(APIView):
    """Verify Razorpay signature, mark payment success, and credit tokens."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifyPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_id = serializer.validated_data["razorpay_order_id"]
        payment_id = serializer.validated_data["razorpay_payment_id"]
        signature = serializer.validated_data["razorpay_signature"]

        if not RAZORPAY_KEY_SECRET:
            logger.error("Razorpay payment verification requested before gateway configuration.")
            return _gateway_unavailable_response()

        message = f"{order_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            logger.warning(
                "Signature mismatch for order %s from user %s", order_id, request.user.id
            )
            return Response(
                {"detail": "Invalid payment signature."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                payment = Payment.objects.select_for_update().get(
                    razorpay_order_id=order_id,
                    user=request.user,
                    status="pending",
                )
                payment.razorpay_payment_id = payment_id
                payment.status = "success"
                payment.save(update_fields=["razorpay_payment_id", "status", "updated_at"])

                request.user.tokens = F("tokens") + payment.tokens
                request.user.save(update_fields=["tokens"])
                request.user.refresh_from_db(fields=["tokens"])
        except Payment.DoesNotExist:
            return Response(
                {"detail": "Order not found or already processed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            "Payment %s verified for order %s. Credited %s tokens to user %s",
            payment_id,
            order_id,
            payment.tokens,
            request.user.id,
        )

        return Response({
            "detail": "Payment verified and tokens added",
            "payment_id": payment.id,
            "credited_tokens": payment.tokens,
            "user_tokens": request.user.tokens,
        })


class PaymentHistoryView(generics.ListAPIView):
    """User's payment history."""

    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(user=self.request.user).only(
            "id",
            "user_id",
            "razorpay_order_id",
            "payment_method",
            "amount",
            "tokens",
            "status",
            "created_at",
        ).order_by("-created_at")[:100]
