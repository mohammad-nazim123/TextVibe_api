from django.urls import path

from .views import (
    PaymentHistoryView,
    PurchasePaymentView,
    TokenPackageListView,
)

urlpatterns = [
    path("packages/", TokenPackageListView.as_view(), name="token-packages"),
    path("purchase/", PurchasePaymentView.as_view(), name="purchase-payment"),
    path("history/", PaymentHistoryView.as_view(), name="payment-history"),
]
