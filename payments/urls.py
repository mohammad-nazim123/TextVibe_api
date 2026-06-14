from django.urls import path

from .views import (
    InitiatePaymentView,
    PaymentHistoryView,
    PurchasePaymentView,
    TokenPackageListView,
    VerifyPaymentView,
)

urlpatterns = [
    path("packages/", TokenPackageListView.as_view(), name="token-packages"),
    path("initiate/", InitiatePaymentView.as_view(), name="initiate-payment"),
    path("verify/", VerifyPaymentView.as_view(), name="verify-payment"),
    path("purchase/", PurchasePaymentView.as_view(), name="purchase-payment"),
    path("history/", PaymentHistoryView.as_view(), name="payment-history"),
]
