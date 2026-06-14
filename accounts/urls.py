from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    GoogleAuthView,
    LogoutView,
    PostListCreateView,
    ProfileView,
    SendOtpView,
    SupportMessageListCreateView,
    VerifyEmailOtpView,
    VerifyOtpView,
)

urlpatterns = [
    # Google Sign-In + email OTP (new flow)
    path("google-auth/", GoogleAuthView.as_view(), name="google-auth"),
    path("verify-email-otp/", VerifyEmailOtpView.as_view(), name="verify-email-otp"),
    # Phone OTP (legacy — kept for backward compatibility)
    path("send-otp/", SendOtpView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOtpView.as_view(), name="verify-otp"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("posts/", PostListCreateView.as_view(), name="posts"),
    path("support/", SupportMessageListCreateView.as_view(), name="support"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
]
