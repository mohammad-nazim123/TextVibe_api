import json
import re

from rest_framework import serializers

from .models import Post, SupportMessage, User

_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def _normalize_phone(value: str) -> str:
    value = value.strip().replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(value):
        raise serializers.ValidationError(
            "Enter a valid phone number in international format, e.g. +919876543210."
        )
    return value


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class SendOtpSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)

    def validate_phone_number(self, value):
        return _normalize_phone(value)


class VerifyOtpSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    otp = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter the 6-digit code."})

    def validate_phone_number(self, value):
        return _normalize_phone(value)


class GoogleAuthSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return _normalize_email(value)


class VerifyEmailOtpSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter the 6-digit code."})

    def validate_email(self, value):
        return _normalize_email(value)


class UserSerializer(serializers.ModelSerializer):
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "phone_number",
            "name",
            "avatar",
            "tokens",
            "is_verified",
            "date_joined",
        )
        read_only_fields = fields

    def get_avatar(self, obj):
        if not obj.avatar:
            return None
        url = obj.avatar.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("name", "avatar")
        extra_kwargs = {
            "name": {"required": False},
            "avatar": {"required": False},
        }


class _JSONStringField(serializers.JSONField):
    """JSONField that also accepts a JSON-encoded *string* — needed because the
    app POSTs multipart form-data (to carry the image), so nested JSON arrives
    as a plain string rather than parsed JSON."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                raise serializers.ValidationError("Must be valid JSON.")
        if data is None:
            return None  # allow_null handled at Field.run_validation level
        return super().to_internal_value(data)


class PostSerializer(serializers.ModelSerializer):
    style_runs = _JSONStringField(required=False, default=list)
    border = _JSONStringField(required=False, allow_null=True)
    background_texture = _JSONStringField(required=False, allow_null=True)

    class Meta:
        model = Post
        fields = (
            "id",
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
        read_only_fields = ("id", "created_at")

    def validate_duration_seconds(self, value):
        # Clamp to the billboard's bounds: at least 3s, at most 5 minutes.
        return max(3, min(int(value), 300))


class BillboardPostSerializer(serializers.ModelSerializer):
    """Public read shape consumed by the billboard website."""

    image = serializers.SerializerMethodField()
    canvas_image = serializers.SerializerMethodField()
    text_image = serializers.SerializerMethodField()
    user_name = serializers.CharField(source="user.name", default="")
    user_avatar = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            "id",
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
            "user",
            "user_name",
            "user_avatar",
        )
        read_only_fields = fields

    def get_image(self, obj):
        if not obj.image:
            return None
        url = obj.image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_canvas_image(self, obj):
        if not obj.canvas_image:
            return None
        url = obj.canvas_image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_text_image(self, obj):
        if not obj.text_image:
            return None
        url = obj.text_image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_user_avatar(self, obj):
        if not obj.user.avatar:
            return None
        url = obj.user.avatar.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class SupportMessageSerializer(serializers.ModelSerializer):
    """Slim serializer for the per-user support thread (3 fields, no joins)."""

    class Meta:
        model = SupportMessage
        fields = ("id", "message", "created_at")
        read_only_fields = ("id", "created_at")

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Message cannot be empty.")
        return value[:2000]
