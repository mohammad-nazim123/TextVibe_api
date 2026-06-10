import logging

from django.conf import settings
from django.db import transaction
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Post, User
from .serializers import (
    BillboardPostSerializer,
    GoogleAuthSerializer,
    PostSerializer,
    ProfileUpdateSerializer,
    SendOtpSerializer,
    UserSerializer,
    VerifyEmailOtpSerializer,
    VerifyOtpSerializer,
)
from .services import otp_service
from .services.email_service import send_otp_email
from .services.sms_service import send_otp_sms

logger = logging.getLogger("accounts")


def _ensure_dev_bonus_tokens(user: User) -> None:
    """Keep local/dev sign-in aligned with the Flutter app's starter balance."""
    if settings.DEBUG and user.tokens == 0:
        user.tokens = 5000
        user.save(update_fields=["tokens"])


class SendOtpView(APIView):
    """Issue an OTP for a mobile number and deliver it via the SMS backend."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "send_otp"

    def post(self, request):
        serializer = SendOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone_number"]

        try:
            otp = otp_service.generate_and_store_otp(phone)
        except otp_service.OtpCooldownError as exc:
            return Response(
                {"detail": str(exc), "retry_after": exc.retry_after},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        send_otp_sms(phone, otp)

        payload = {"detail": "OTP sent."}
        if settings.DEBUG:
            # Dev convenience only — never exposed in production.
            payload["otp"] = otp
        return Response(payload, status=status.HTTP_200_OK)


class VerifyOtpView(APIView):
    """Verify an OTP. On success the OTP is destroyed, the user account is
    created/fetched, and JWT tokens are returned."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_otp"

    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone_number"]
        otp = serializer.validated_data["otp"]

        try:
            verified = otp_service.verify_otp(phone, otp)
        except otp_service.OtpMaxAttemptsError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        if not verified:
            return Response(
                {"detail": "Invalid or expired code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, created = User.objects.get_or_create(phone_number=phone)
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=["is_verified"])
        _ensure_dev_bonus_tokens(user)

        refresh = RefreshToken.for_user(user)
        logger.info("Authenticated %s (new account=%s)", phone, created)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {**UserSerializer(user).data, "is_new": created},
            },
            status=status.HTTP_200_OK,
        )


class GoogleAuthView(APIView):
    """Accept a Gmail address from the Flutter Google Sign-In flow and send an
    email OTP to that address. The client already verified ownership via Google
    OAuth, so we trust the email and skip any additional Google token check."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "send_otp"

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            otp = otp_service.generate_and_store_otp(email)
        except otp_service.OtpCooldownError as exc:
            return Response(
                {"detail": str(exc), "retry_after": exc.retry_after},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        send_otp_email(email, otp)

        payload = {"detail": "OTP sent."}
        if settings.DEBUG:
            payload["otp"] = otp
        return Response(payload, status=status.HTTP_200_OK)


class VerifyEmailOtpView(APIView):
    """Verify the email OTP. On success the OTP is destroyed, the user account
    is created or fetched, and JWT tokens are returned."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_otp"

    def post(self, request):
        serializer = VerifyEmailOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        otp = serializer.validated_data["otp"]

        try:
            verified = otp_service.verify_otp(email, otp)
        except otp_service.OtpMaxAttemptsError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        if not verified:
            return Response(
                {"detail": "Invalid or expired code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, created = User.objects.get_or_create(email=email)
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=["is_verified"])
        _ensure_dev_bonus_tokens(user)

        refresh = RefreshToken.for_user(user)
        logger.info("Authenticated %s via email OTP (new account=%s)", email, created)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {**UserSerializer(user).data, "is_new": created},
            },
            status=status.HTTP_200_OK,
        )


class ProfileView(APIView):
    """Authenticated user's profile — the dashboard's data source. Supports
    updating the display name and avatar."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        _ensure_dev_bonus_tokens(request.user)
        grant_tokens = request.query_params.get("grant_dev_tokens")
        if settings.DEBUG and grant_tokens:
            try:
                tokens_to_add = max(0, int(grant_tokens))
            except ValueError as exc:
                raise ValidationError("grant_dev_tokens must be an integer.") from exc
            if tokens_to_add:
                request.user.tokens += tokens_to_add
                request.user.save(update_fields=["tokens"])
        return Response(UserSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        serializer = ProfileUpdateSerializer(
            instance=request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            UserSerializer(request.user, context={"request": request}).data
        )


class PostListCreateView(generics.ListCreateAPIView):
    """List the user's posts and create a new one (the Send action)."""

    serializer_class = PostSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        return self.request.user.posts.all()

    def perform_create(self, serializer):
        _ensure_dev_bonus_tokens(self.request.user)
        # Calculate cost from validated data without saving
        temp_post = Post(
            text=serializer.validated_data.get('text', ''),
            duration_seconds=serializer.validated_data.get('duration_seconds', 5),
            background_color=serializer.validated_data.get('background_color', ''),
            background_texture=serializer.validated_data.get('background_texture'),
            border=serializer.validated_data.get('border'),
            image=serializer.validated_data.get('image'),
        )

        cost = temp_post.calculate_token_cost()
        user = self.request.user

        if cost > user.tokens:
            raise ValidationError(
                f"Not enough tokens. This message costs {cost} tokens but you have {user.tokens}."
            )

        # Use transaction to ensure post and token deduction happen together
        with transaction.atomic():
            post = serializer.save(user=self.request.user)
            user.tokens -= cost
            user.save(update_fields=["tokens"])


class BillboardView(generics.ListAPIView):
    """Public, read-only feed the billboard website polls. Optionally filtered
    by ?user=<id>; otherwise returns the latest posts across all users."""

    serializer_class = BillboardPostSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = Post.objects.select_related("user").all()
        user = self.request.query_params.get("user")
        if user:
            qs = qs.filter(user_id=user)
        return qs.order_by("-created_at")[:50]


class LogoutView(APIView):
    """Blacklist a refresh token so it can no longer be rotated."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response(
                {"detail": "A refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid or expired token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"detail": "Logged out."}, status=status.HTTP_205_RESET_CONTENT)
