import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from . import realtime
from .models import Post, User
from .serializers import (
    BillboardPostSerializer,
    GoogleAuthSerializer,
    PostSerializer,
    ProfileUpdateSerializer,
    SendOtpSerializer,
    SupportMessageSerializer,
    UserSerializer,
    VerifyEmailOtpSerializer,
    VerifyOtpSerializer,
)
from .services import otp_service
from .services.email_service import send_otp_email
from .services.sms_service import send_otp_sms

logger = logging.getLogger("accounts")
DEFAULT_DIRECT_DASHBOARD_EMAILS = {"textvibe!7865990@example.com"}


def _ensure_dev_bonus_tokens(user: User) -> None:
    """Keep local/dev sign-in aligned with the Flutter app's starter balance."""
    if settings.DEBUG and user.tokens == 0:
        user.tokens = 5000
        user.save(update_fields=["tokens"])


def _allowed_direct_dashboard_emails() -> set[str]:
    configured = {
        email.strip().lower()
        for email in getattr(settings, "DIRECT_DASHBOARD_EMAILS", [])
        if email
    }
    if settings.DEBUG:
        configured |= DEFAULT_DIRECT_DASHBOARD_EMAILS
    return configured


def _get_or_create_verified_email_user(email: str) -> tuple[User, bool]:
    user, created = User.objects.get_or_create(email=email)
    updated_fields = []
    if created:
        user.tokens = 100
        updated_fields.append("tokens")
    if not user.is_verified:
        user.is_verified = True
        updated_fields.append("is_verified")
    if updated_fields:
        user.save(update_fields=updated_fields)
    _ensure_dev_bonus_tokens(user)
    return user, created


def _auth_response(user: User, created: bool) -> Response:
    refresh = RefreshToken.for_user(user)
    return Response(
        {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {**UserSerializer(user).data, "is_new": created},
        },
        status=status.HTTP_200_OK,
    )


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

        return Response({"detail": "OTP sent."}, status=status.HTTP_200_OK)


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
        if created:
            user.tokens = 100
            user.save(update_fields=["tokens"])
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=["is_verified"])
        _ensure_dev_bonus_tokens(user)

        logger.info("Authenticated %s (new account=%s)", phone, created)
        return _auth_response(user, created)


class GoogleAuthView(APIView):
    """Accept a Gmail address from the Flutter Google Sign-In flow and send an
    email OTP to that address. The client already verified ownership via Google
    OAuth, so we trust the email and skip any additional Google token check.
    The response is returned only after SMTP accepts the message."""

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

        try:
            send_otp_email(email, otp)
        except Exception:
            logger.exception("OTP email delivery to %s failed", email)
            try:
                otp_service.discard_otp(email, otp)
            except Exception:
                logger.exception("Could not discard failed OTP for %s", email)
            return Response(
                {"detail": "Could not deliver OTP email. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"detail": "OTP sent."}, status=status.HTTP_200_OK)


class DirectEmailLoginView(APIView):
    """Allow a configured email to open the dashboard without Google or OTP."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_otp"

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        if email not in _allowed_direct_dashboard_emails():
            return Response(
                {"detail": "Use Google or email OTP to sign in."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user, created = _get_or_create_verified_email_user(email)
        logger.info(
            "Authenticated %s via direct dashboard email (new account=%s)",
            email,
            created,
        )
        return _auth_response(user, created)


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

        user, created = _get_or_create_verified_email_user(email)
        logger.info("Authenticated %s via email OTP (new account=%s)", email, created)
        return _auth_response(user, created)


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
        return (
            self.request.user.posts.only(
                "id",
                "user_id",
                "text",
                "image",
                "canvas_image",
                "text_image",
                "text_canvas_width",
                "text_canvas_height",
                "background_color",
                "background_texture",
                "style_runs",
                "border",
                "frame_id",
                "background_id",
                "duration_seconds",
                "created_at",
            )
            .order_by("-created_at")[:50]
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        data = dict(serializer.data)
        data["user_tokens"] = getattr(self, "_updated_user_tokens", request.user.tokens)
        headers = self.get_success_headers(serializer.data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        _ensure_dev_bonus_tokens(self.request.user)

        # Billboard session gate: block sends when the billboard queue is nearly full.
        _SESSION_CAP = 300
        _SESSION_BUFFER = 5
        cutoff = timezone.now() - timedelta(seconds=_SESSION_CAP)
        recent_durations = Post.objects.filter(
            created_at__gte=cutoff
        ).values_list('duration_seconds', flat=True)
        session_used = min(
            _SESSION_CAP,
            sum(max(3, min(int(d), 300)) for d in recent_durations),
        )
        session_remaining = _SESSION_CAP - session_used
        if session_remaining <= _SESSION_BUFFER:
            raise ValidationError(
                f"Billboard session full. Wait {session_remaining} seconds for the session to end."
            )

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
        with transaction.atomic():
            user = User.objects.select_for_update().only("id", "tokens").get(
                pk=self.request.user.pk
            )
            if cost > user.tokens:
                raise ValidationError(
                    f"Not enough tokens. This message costs {cost} tokens but you have {user.tokens}."
                )
            post = serializer.save(user=user)
            user.tokens -= cost
            user.save(update_fields=["tokens"])
            self._updated_user_tokens = user.tokens
            # Serialize in billboard shape now (request.user is fully loaded;
            # the locked `user` only has id/tokens) and hand the payload to
            # the realtime buffer on commit: held billboard long-polls wake
            # the instant the row is visible and answer without touching the
            # database again.
            post.user = self.request.user
            payload = BillboardPostSerializer(
                post, context=self.get_serializer_context()
            ).data
            transaction.on_commit(
                lambda: realtime.notify_new_post(post.pk, payload)
            )


class BillboardView(generics.ListAPIView):
    """Public, read-only feed the billboard website polls. Optionally filtered
    by ?user=<id>; otherwise returns the latest posts across all users.

    With ?after=<id> the response is short-circuited to [] (no DB query) when
    nothing newer exists, and ?wait=<seconds> turns the request into a long
    poll that returns the moment a new post is committed."""

    serializer_class = BillboardPostSerializer
    permission_classes = [AllowAny]

    def _parse_after(self):
        after = self.request.query_params.get("after")
        if after in (None, ""):
            return None
        try:
            return max(0, int(after))
        except ValueError as exc:
            raise ValidationError("after must be an integer post id.") from exc

    def _parse_wait(self):
        max_wait = float(getattr(settings, "BILLBOARD_LONGPOLL_MAX_WAIT", 25))
        try:
            wait = float(self.request.query_params.get("wait", 0))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(wait, max_wait))

    def list(self, request, *args, **kwargs):
        after = self._parse_after()
        if after is not None:
            if realtime.get_latest_post_id() <= after:
                wait = self._parse_wait()
                # Nothing new: either answer [] immediately (plain poll) or
                # hold the request until a post lands or the wait expires.
                if wait <= 0 or not realtime.wait_for_post_after(after, wait):
                    return Response([])
            # Something new — serve it straight from the in-process buffer
            # when it provably holds everything past the cursor (saves the
            # remote DB round trip on the hottest path).
            if not request.query_params.get("user"):
                buffered = realtime.get_buffered_posts_after(after)
                if buffered is not None:
                    return Response(buffered)
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        qs = Post.objects.select_related("user").only(
            "id",
            "user_id",
            "text",
            "image",
            "canvas_image",
            "text_image",
            "text_canvas_width",
            "text_canvas_height",
            "background_color",
            "background_texture",
            "style_runs",
            "border",
            "frame_id",
            "background_id",
            "duration_seconds",
            "created_at",
            "user__id",
            "user__name",
            "user__avatar",
        )
        user = self.request.query_params.get("user")
        if user:
            qs = qs.filter(user_id=user)
        after = self._parse_after()
        if after is not None:
            qs = qs.filter(id__gt=after)
        return qs.order_by("-created_at", "-id")[:50]


class SupportMessageListCreateView(generics.ListCreateAPIView):
    """List the signed-in user's support messages and create a new one.

    Tuned for the compact Support panel: scoped to ``request.user`` (backed by
    the ``(user, -created_at)`` index), fetches only the three columns the panel
    renders, and caps the list at the 50 newest rows for a small, bounded
    payload (GZip-compressed by the project's middleware)."""

    serializer_class = SupportMessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            self.request.user.support_messages.only(
                "id", "message", "created_at"
            ).order_by("-created_at")[:50]
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


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
