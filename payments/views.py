import logging
from uuid import uuid4

from django.db import transaction
from django.db.models import F
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payment, TokenPackage
from .serializers import (
    PaymentSerializer,
    PurchasePaymentSerializer,
    TokenPackageSerializer,
)

logger = logging.getLogger("payments")


class TokenPackageListView(generics.ListAPIView):
    """List all active token packages."""

    queryset = TokenPackage.objects.filter(is_active=True).only(
        "id", "amount", "tokens"
    ).order_by("amount")
    serializer_class = TokenPackageSerializer
    permission_classes = [AllowAny]


def _generate_internal_reference() -> str:
    return f"mock_{uuid4().hex}"


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
        ).order_by("-created_at")
