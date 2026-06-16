from django.urls import path

from .views import (
    InitiatePaymentView,
    InitiateSubscriptionPaymentView,
    PaymentHistoryView,
    PurchasePaymentView,
    PurchaseSubscriptionView,
    SubscriptionPlanListView,
    TokenPackageListView,
    VerifyPaymentView,
    VerifySubscriptionPaymentView,
)

urlpatterns = [
    path("packages/", TokenPackageListView.as_view(), name="token-packages"),
    path("initiate/", InitiatePaymentView.as_view(), name="initiate-payment"),
    path("verify/", VerifyPaymentView.as_view(), name="verify-payment"),
    path("purchase/", PurchasePaymentView.as_view(), name="purchase-payment"),
    path("history/", PaymentHistoryView.as_view(), name="payment-history"),
    path("subscriptions/plans/", SubscriptionPlanListView.as_view(), name="subscription-plans"),
    path(
        "subscriptions/initiate/",
        InitiateSubscriptionPaymentView.as_view(),
        name="initiate-subscription",
    ),
    path(
        "subscriptions/verify/",
        VerifySubscriptionPaymentView.as_view(),
        name="verify-subscription",
    ),
    path(
        "subscriptions/purchase/",
        PurchaseSubscriptionView.as_view(),
        name="purchase-subscription",
    ),
]
