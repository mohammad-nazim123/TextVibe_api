from django.urls import path

from .views import (
    InitiatePaymentView,
    PaymentHistoryView,
    TokenPackageListView,
    VerifyPaymentWebhookView,
)

urlpatterns = [
    path("packages/", TokenPackageListView.as_view(), name="token-packages"),
    path("initiate/", InitiatePaymentView.as_view(), name="initiate-payment"),
    path("verify/", VerifyPaymentWebhookView.as_view(), name="verify-payment"),
    path("history/", PaymentHistoryView.as_view(), name="payment-history"),
]
