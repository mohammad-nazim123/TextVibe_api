from rest_framework import serializers
from .models import Payment, TokenPackage


class TokenPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TokenPackage
        fields = ["id", "amount", "tokens"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "razorpay_order_id", "amount", "tokens", "status", "created_at"]
        read_only_fields = ["id", "razorpay_order_id", "status", "created_at"]


class InitiatePaymentSerializer(serializers.Serializer):
    """Initiate a payment by selecting a token package."""

    package_id = serializers.IntegerField()

    def validate_package_id(self, value):
        try:
            package = TokenPackage.objects.get(id=value, is_active=True)
        except TokenPackage.DoesNotExist:
            raise serializers.ValidationError("Invalid or inactive package.")
        return value


class VerifyPaymentSerializer(serializers.Serializer):
    """Verify payment from Razorpay webhook."""

    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()
