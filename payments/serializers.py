from rest_framework import serializers
from .models import Payment, TokenPackage


def _validate_active_package_id(value):
    if not TokenPackage.objects.filter(id=value, is_active=True).exists():
        raise serializers.ValidationError("Invalid or inactive package.")
    return value


class TokenPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TokenPackage
        fields = ["id", "amount", "tokens"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "razorpay_order_id",
            "payment_method",
            "amount",
            "tokens",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "razorpay_order_id", "payment_method", "status", "created_at"]

class PurchasePaymentSerializer(serializers.Serializer):
    """Complete a temporary direct purchase without an external gateway."""

    package_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(
        choices=[
            Payment.PAYMENT_METHOD_GOOGLE_PLAY,
            Payment.PAYMENT_METHOD_UPI,
            Payment.PAYMENT_METHOD_CARD,
            Payment.PAYMENT_METHOD_NETBANKING,
            Payment.PAYMENT_METHOD_WALLET,
        ]
    )

    def validate_package_id(self, value):
        return _validate_active_package_id(value)


class InitiatePaymentSerializer(serializers.Serializer):
    package_id = serializers.IntegerField()

    def validate_package_id(self, value):
        return _validate_active_package_id(value)


class VerifyPaymentSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField(max_length=100)
    razorpay_payment_id = serializers.CharField(max_length=100)
    razorpay_signature = serializers.CharField(max_length=256)
