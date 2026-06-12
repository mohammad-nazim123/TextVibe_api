import io
import tempfile
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Post, User
from accounts.services import otp_service

PHONE = "+919876543210"
KNOWN_OTP = "123456"


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


class OtpFlowTests(APITestCase):
    def setUp(self):
        # In-memory Redis + deterministic OTP + clean throttle state.
        # (Django's test runner forces DEBUG=False, so the API never echoes the
        # OTP; we pin secrets.randbelow instead to know the code.)
        self.fake = fakeredis.FakeStrictRedis(decode_responses=True)
        redis_patch = patch.object(otp_service, "_client", self.fake)
        rand_patch = patch.object(
            otp_service.secrets, "randbelow", return_value=int(KNOWN_OTP)
        )
        redis_patch.start()
        rand_patch.start()
        self.addCleanup(redis_patch.stop)
        self.addCleanup(rand_patch.stop)
        # Production settings enforce HTTPS; make every test request secure so
        # endpoint assertions exercise the view logic instead of 301 redirects.
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        self.client.defaults["SERVER_PORT"] = "443"
        cache.clear()

    def _send(self, phone=PHONE):
        return self.client.post(
            "/api/auth/send-otp/", {"phone_number": phone}, format="json"
        )

    def _verify(self, otp=KNOWN_OTP, phone=PHONE):
        return self.client.post(
            "/api/auth/verify-otp/",
            {"phone_number": phone, "otp": otp},
            format="json",
        )

    def test_send_stores_hash_not_plaintext(self):
        res = self._send()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        stored = self.fake.get(f"otp:{PHONE}")
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored, KNOWN_OTP)  # never plaintext
        self.assertEqual(stored, otp_service._digest(PHONE, KNOWN_OTP))

    def test_verify_creates_user_and_returns_tokens(self):
        self._send()
        res = self._verify()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)
        self.assertTrue(res.data["user"]["is_new"])
        self.assertTrue(
            User.objects.filter(phone_number=PHONE, is_verified=True).exists()
        )
        self.assertIsNone(self.fake.get(f"otp:{PHONE}"))  # destroyed on success

    def test_refresh_token_lifetime_is_extended(self):
        self.assertEqual(
            settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"], timedelta(days=365)
        )

    def test_refresh_token_rotates_without_relogin(self):
        user = User.objects.create(phone_number=PHONE, is_verified=True)
        refresh = RefreshToken.for_user(user)

        res = self.client.post(
            "/api/auth/token/refresh/",
            {"refresh": str(refresh)},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)
        self.assertNotEqual(res.data["refresh"], str(refresh))

    def test_reuse_after_success_fails(self):
        self._send()
        self._verify()
        self.assertEqual(self._verify().status_code, status.HTTP_400_BAD_REQUEST)

    def test_existing_user_is_not_marked_new(self):
        self._send()
        self._verify()
        # Second login (fresh OTP) for the same number.
        self.fake.flushall()
        cache.clear()
        self._send()
        res = self._verify()
        self.assertFalse(res.data["user"]["is_new"])
        self.assertEqual(User.objects.filter(phone_number=PHONE).count(), 1)

    def test_wrong_code_rejected(self):
        self._send()
        self.assertEqual(self._verify("000000").status_code, status.HTTP_400_BAD_REQUEST)

    def test_cooldown_blocks_immediate_resend(self):
        self._send()
        self.assertEqual(self._send().status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_max_attempts_locks_out(self):
        self._send()
        for _ in range(5):
            self._verify("000000")
        self.assertEqual(
            self._verify("000000").status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )

    def test_invalid_phone_rejected(self):
        res = self.client.post(
            "/api/auth/send-otp/", {"phone_number": "abc"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_profile_requires_auth(self):
        self.assertEqual(
            self.client.get("/api/auth/profile/").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_profile_with_token(self):
        self._send()
        access = self._verify().data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        res = self.client.get("/api/auth/profile/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["phone_number"], PHONE)

    def _login(self):
        self._send()
        access = self._verify().data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_profile_patch_updates_name(self):
        self._login()
        res = self.client.patch(
            "/api/auth/profile/", {"name": "Nazim"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["name"], "Nazim")
        self.assertEqual(User.objects.get(phone_number=PHONE).name, "Nazim")

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_profile_patch_uploads_avatar(self):
        self._login()
        upload = SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png")
        res = self.client.patch(
            "/api/auth/profile/", {"avatar": upload}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data["avatar"])
        self.assertTrue(User.objects.get(phone_number=PHONE).avatar)

    def test_profile_patch_requires_auth(self):
        res = self.client.patch(
            "/api/auth/profile/", {"name": "X"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_profile_includes_tokens_default_zero(self):
        self._login()
        res = self.client.get("/api/auth/profile/")
        self.assertEqual(res.data["tokens"], 0)

    def test_create_post(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 100
        user.save()
        res = self.client.post(
            "/api/auth/posts/", {"text": "Hello world #love"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["text"], "Hello world #love")
        user = User.objects.get(phone_number=PHONE)
        self.assertEqual(user.posts.count(), 1)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_create_post_accepts_text_layer(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 100
        user.save()
        upload = SimpleUploadedFile("text.png", _png_bytes(), content_type="image/png")
        res = self.client.post(
            "/api/auth/posts/",
            {
                "text": "Hello layered text",
                "text_image": upload,
                "text_canvas_width": "1024",
                "text_canvas_height": "768",
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        post = User.objects.get(phone_number=PHONE).posts.get()
        self.assertTrue(post.text_image)
        self.assertEqual(post.text_canvas_width, 1024)
        self.assertEqual(post.text_canvas_height, 768)

    def test_create_post_requires_auth(self):
        res = self.client.post(
            "/api/auth/posts/", {"text": "hi"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_post_deducts_tokens(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 100
        user.save()
        res = self.client.post(
            "/api/auth/posts/",
            {"text": "Hello world", "duration_seconds": 5},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        user.refresh_from_db()
        self.assertEqual(user.tokens, 93)
        self.assertEqual(res.data["user_tokens"], 93)

    def test_create_post_normal_background_and_border_are_free(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 100
        user.save()
        res = self.client.post(
            "/api/auth/posts/",
            {
                "text": "Hello world",
                "background_color": "#fff5f8",
                "border": {
                    "width": 6,
                    "style": "double",
                    "color": "#8b5cf6",
                    "radius": 28,
                },
                "duration_seconds": 5,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        user.refresh_from_db()
        self.assertEqual(user.tokens, 93)
        self.assertEqual(res.data["user_tokens"], 93)

    def test_create_post_rejects_premium_background_texture(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 100
        user.save()
        res = self.client.post(
            "/api/auth/posts/",
            {
                "text": "Hello world",
                "background_texture": {
                    "style": "texture",
                    "texture": "premium_wood",
                },
                "duration_seconds": 5,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["detail"], "Coming soon.")
        user.refresh_from_db()
        self.assertEqual(user.tokens, 100)
        self.assertEqual(user.posts.count(), 0)

    def test_create_post_rejects_legendary_style_run_effect(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 100
        user.save()
        res = self.client.post(
            "/api/auth/posts/",
            {
                "text": "Hello world",
                "style_runs": [
                    {
                        "text": "Hello world",
                        "color": "#e8c56a",
                        "fontFamily": "Roboto",
                        "fontSize": 18,
                        "fontWeight": 400,
                        "fontStyle": "normal",
                        "effect": "legendary",
                        "legendaryColor": "#e8c56a",
                        "legendaryMaterial": "gold",
                    }
                ],
                "duration_seconds": 5,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["detail"], "Coming soon.")
        user.refresh_from_db()
        self.assertEqual(user.tokens, 100)
        self.assertEqual(user.posts.count(), 0)

    def test_create_post_fails_if_not_enough_tokens(self):
        self._login()
        user = User.objects.get(phone_number=PHONE)
        user.tokens = 5
        user.save()
        res = self.client.post(
            "/api/auth/posts/",
            {"text": "Hello world", "duration_seconds": 5},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        user.refresh_from_db()
        self.assertEqual(user.tokens, 5)  # tokens unchanged
        self.assertEqual(user.posts.count(), 0)  # post not created

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_billboard_feed_includes_rendering_payload(self):
        user = User.objects.create(
            phone_number=PHONE,
            name="Nazim",
            is_verified=True,
        )
        user.avatar = SimpleUploadedFile(
            "avatar.png", _png_bytes(), content_type="image/png"
        )
        user.save()

        style_runs = [
            {
                "text": "Hello ",
                "color": "#1d4ed8",
                "fontFamily": "Montserrat",
                "fontSize": 20,
                "fontWeight": 700,
                "fontStyle": "normal",
                "x": 12,
                "y": 12,
                "width": 58.4,
                "height": 24,
            },
            {
                "text": "💗",
                "color": "#ff6b9d",
                "fontFamily": "Lobster",
                "fontSize": 24,
                "fontWeight": 400,
                "fontStyle": "italic",
                "x": 72.5,
                "y": 12,
                "width": 24,
                "height": 28,
            },
        ]
        border = {"width": 6, "style": "double", "color": "#8b5cf6", "radius": 28}
        background_texture = {
            "style": "texture",
            "texture": "pearl_linen",
        }
        post = Post.objects.create(
            user=user,
            text="Hello 💗",
            image=SimpleUploadedFile(
                "post.png", _png_bytes(), content_type="image/png"
            ),
            canvas_image=SimpleUploadedFile(
                "canvas.png", _png_bytes(), content_type="image/png"
            ),
            text_image=SimpleUploadedFile(
                "text.png", _png_bytes(), content_type="image/png"
            ),
            text_canvas_width=1024,
            text_canvas_height=768,
            background_color="#fff5f8",
            background_texture=background_texture,
            style_runs=style_runs,
            border=border,
            duration_seconds=17,
        )

        feed_res = self.client.get("/api/billboard/")
        self.assertEqual(feed_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(feed_res.data), 1)

        payload = feed_res.data[0]
        self.assertEqual(payload["id"], post.id)
        self.assertEqual(payload["text"], "Hello 💗")
        self.assertEqual(payload["background_color"], "#fff5f8")
        self.assertEqual(payload["background_texture"], background_texture)
        self.assertEqual(payload["style_runs"], style_runs)
        self.assertEqual(payload["border"], border)
        self.assertEqual(payload["duration_seconds"], 17)
        self.assertEqual(payload["user"], user.id)
        self.assertEqual(payload["user_name"], "Nazim")
        self.assertRegex(payload["image"], r"^https?://testserver")
        self.assertTrue(payload["image"].endswith(post.image.url))
        self.assertRegex(payload["canvas_image"], r"^https?://testserver")
        self.assertTrue(payload["canvas_image"].endswith(post.canvas_image.url))
        self.assertRegex(payload["text_image"], r"^https?://testserver")
        self.assertTrue(payload["text_image"].endswith(post.text_image.url))
        self.assertEqual(payload["text_canvas_width"], 1024)
        self.assertEqual(payload["text_canvas_height"], 768)
        self.assertRegex(payload["user_avatar"], r"^https?://testserver")
        self.assertTrue(payload["user_avatar"].endswith(user.avatar.url))

        self.assertEqual(
            self.client.get(f"/api/billboard/?after={post.id}").data,
            [],
        )
        replay_res = self.client.get("/api/billboard/?after=0")
        self.assertEqual(replay_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(replay_res.data), 1)
