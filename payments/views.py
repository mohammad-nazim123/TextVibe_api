import hashlib
import hmac
import logging
import os

import razorpay
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
    TokenPackageSerializer,
    VerifyPaymentSerializer,
)

logger = logging.getLogger("payments")

# Initialize Razorpay client
razorpay_client = razorpay.Client(
    auth=(os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET"))
)


class TokenPackageListView(generics.ListAPIView):
    """List all active token packages."""

    queryset = TokenPackage.objects.filter(is_active=True)
    serializer_class = TokenPackageSerializer
    permission_classes = [AllowAny]


class InitiatePaymentView(APIView):
    """Create a Razorpay order and return order details to the app."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        package = TokenPackage.objects.get(
            id=serializer.validated_data["package_id"], is_active=True
        )

        # Create Razorpay order
        order_data = {
            "amount": package.amount * 100,  # Razorpay expects amount in paise
            "currency": "INR",
            "receipt": f"user_{request.user.id}_pkg_{package.id}",
        }

        try:
            razorpay_order = razorpay_client.order.create(data=order_data)
        except Exception as e:
            logger.error(f"Razorpay order creation failed: {e}")
            return Response(
                {"detail": "Failed to create payment order"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Create Payment record
        payment = Payment.objects.create(
            user=request.user,
            package=package,
            razorpay_order_id=razorpay_order["id"],
            amount=package.amount,
            tokens=package.tokens,
            status="pending",
        )

        return Response(
            {
                "order_id": razorpay_order["id"],
                "amount": package.amount,
                "tokens": package.tokens,
                "currency": "INR",
                "key_id": os.environ.get("RAZORPAY_KEY_ID"),
            },
            status=status.HTTP_200_OK,
        )


class VerifyPaymentWebhookView(APIView):
    """Verify Razorpay payment and credit tokens to user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifyPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_id = serializer.validated_data["razorpay_order_id"]
        payment_id = serializer.validated_data["razorpay_payment_id"]
        signature = serializer.validated_data["razorpay_signature"]

        # Verify signature
        message = f"{order_id}|{payment_id}"
        generated_signature = hmac.new(
            os.environ.get("RAZORPAY_KEY_SECRET").encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        if generated_signature != signature:
            logger.warning(f"Invalid signature for order {order_id}")
            return Response(
                {"detail": "Invalid signature"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            try:
                payment = Payment.objects.select_for_update().get(
                    razorpay_order_id=order_id
                )
            except Payment.DoesNotExist:
                logger.error(f"Payment not found for order {order_id}")
                return Response(
                    {"detail": "Payment not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if payment.status != "pending":
                return Response(
                    {"detail": "Payment already processed"},
                    status=status.HTTP_200_OK,
                )

            payment.razorpay_payment_id = payment_id
            payment.status = "success"
            payment.save(update_fields=["razorpay_payment_id", "status"])

            payment.user.tokens = F("tokens") + payment.tokens
            payment.user.save(update_fields=["tokens"])

        logger.info(
            f"Payment {order_id} verified. Credited {payment.tokens} tokens to user {payment.user.id}"
        )

        return Response(
            {"detail": "Payment verified and tokens credited"},
            status=status.HTTP_200_OK,
        )


class PaymentHistoryView(generics.ListAPIView):
    """User's payment history."""

    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(user=self.request.user).order_by("-created_at")
