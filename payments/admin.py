from django.contrib import admin

from .models import Payment, TokenPackage


@admin.register(TokenPackage)
class TokenPackageAdmin(admin.ModelAdmin):
    list_display = ["amount", "tokens", "is_active", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["amount"]
    ordering = ["amount"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["razorpay_order_id", "user", "amount", "tokens", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["razorpay_order_id", "razorpay_payment_id", "user__email", "user__phone_number"]
    readonly_fields = ["razorpay_order_id", "razorpay_payment_id", "created_at", "updated_at"]
    ordering = ["-created_at"]
