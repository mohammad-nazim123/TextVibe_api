from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class TokenPackage(models.Model):
    """Token packages users can purchase."""

    amount = models.PositiveIntegerField(help_text="Cost in rupees")
    tokens = models.PositiveIntegerField(help_text="Number of tokens to credit")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["amount"]
        verbose_name_plural = "Token Packages"
        indexes = [
            models.Index(fields=["is_active", "amount"], name="tokenpkg_active_amt_idx"),
        ]

    def __str__(self):
        return f"₹{self.amount} = {self.tokens} tokens"


class Payment(models.Model):
    """Tracks all token purchase transactions."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
    ]
    PAYMENT_METHOD_LEGACY = "legacy"
    PAYMENT_METHOD_UPI = "upi"
    PAYMENT_METHOD_CARD = "card"
    PAYMENT_METHOD_NETBANKING = "netbanking"
    PAYMENT_METHOD_WALLET = "wallet"
    PAYMENT_METHOD_GOOGLE_PLAY = "google_play"
    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_LEGACY, "Legacy"),
        (PAYMENT_METHOD_GOOGLE_PLAY, "Google Play"),
        (PAYMENT_METHOD_UPI, "UPI"),
        (PAYMENT_METHOD_CARD, "Cards"),
        (PAYMENT_METHOD_NETBANKING, "Net Banking"),
        (PAYMENT_METHOD_WALLET, "Wallets"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    package = models.ForeignKey(TokenPackage, on_delete=models.SET_NULL, null=True)
    razorpay_order_id = models.CharField(max_length=100, unique=True, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default=PAYMENT_METHOD_LEGACY,
    )
    amount = models.PositiveIntegerField(help_text="Amount in rupees")
    tokens = models.PositiveIntegerField(help_text="Tokens to be credited")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Payments"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="pay_user_created_idx"),
        ]

    def __str__(self):
        return f"Payment {self.razorpay_order_id} - {self.status}"
