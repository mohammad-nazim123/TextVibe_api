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

    def __str__(self):
        return f"₹{self.amount} = {self.tokens} tokens"


class Payment(models.Model):
    """Tracks all payment transactions from Razorpay."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    package = models.ForeignKey(TokenPackage, on_delete=models.SET_NULL, null=True)
    razorpay_order_id = models.CharField(max_length=100, unique=True, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    amount = models.PositiveIntegerField(help_text="Amount in rupees")
    tokens = models.PositiveIntegerField(help_text="Tokens to be credited")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Payments"

    def __str__(self):
        return f"Payment {self.razorpay_order_id} - {self.status}"
