import io
import tempfile
import threading
import time
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import realtime
from accounts.models import Post, User
from accounts.services import otp_service

PHONE = "+919876543210"
EMAIL = "mohammadnazim8273976364@gmail.com"
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
        # The realtime latest-post cache is module state; tests roll back the
        # DB between runs, so a stale id would wrongly short-circuit the feed.
        realtime._reset_for_tests()

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

    def _send_email(self, email=EMAIL):
        return self.client.post(
            "/api/auth/google-auth/", {"email": email}, format="json"
        )

    def _verify_email(self, otp=KNOWN_OTP, email=EMAIL):
        return self.client.post(
            "/api/auth/verify-email-otp/",
            {"email": email, "otp": otp},
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

    def test_reuse_after_success_fails(self):
        self._send()
        self._verify()
        self.assertEqual(self._verify().status_code, status.HTTP_400_BAD_REQUEST)

    def test_previous_unexpired_code_still_works_after_resend(self):
        self._send()
        first_code = KNOWN_OTP
        self.fake.delete(f"otp_cooldown:{PHONE}")

        with patch.object(otp_service.secrets, "randbelow", return_value=654321):
            self._send()

        res = self._verify(first_code)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

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

    def test_email_otp_waits_for_confirmed_delivery_and_stores_hash(self):
        with patch("accounts.views.send_otp_email") as send_email:
            res = self._send_email()

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        send_email.assert_called_once_with(EMAIL, KNOWN_OTP)
        stored = self.fake.get(f"otp:{EMAIL}")
        self.assertIsNotNone(stored)
        self.assertEqual(stored, otp_service._digest(EMAIL, KNOWN_OTP))

    def test_email_otp_delivery_failure_clears_code_and_cooldown(self):
        with patch(
            "accounts.views.send_otp_email", side_effect=RuntimeError("smtp down")
        ):
            res = self._send_email()

        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIsNone(self.fake.get(f"otp:{EMAIL}"))
        self.assertIsNone(self.fake.get(f"otp_cooldown:{EMAIL}"))

        with patch("accounts.views.send_otp_email") as send_email:
            retry = self._send_email()

        self.assertEqual(retry.status_code, status.HTTP_200_OK)
        send_email.assert_called_once_with(EMAIL, KNOWN_OTP)

    def test_email_otp_cooldown_blocks_after_confirmed_delivery(self):
        with patch("accounts.views.send_otp_email") as send_email:
            first = self._send_email()
            second = self._send_email()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        send_email.assert_called_once_with(EMAIL, KNOWN_OTP)

    def test_failed_email_resend_keeps_previous_unexpired_code_valid(self):
        with patch("accounts.views.send_otp_email"):
            self._send_email()
        self.fake.delete(f"otp_cooldown:{EMAIL}")

        with patch.object(otp_service.secrets, "randbelow", return_value=654321):
            with patch(
                "accounts.views.send_otp_email",
                side_effect=RuntimeError("smtp down"),
            ):
                res = self._send_email()

        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        verify = self._verify_email(KNOWN_OTP)
        self.assertEqual(verify.status_code, status.HTTP_200_OK)

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


@override_settings(BILLBOARD_DB_RECHECK_SECONDS=60.0)
class BillboardRealtimeTests(APITestCase):
    """Short-circuit and long-poll behavior of the billboard feed."""

    def setUp(self):
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        self.client.defaults["SERVER_PORT"] = "443"
        cache.clear()
        realtime._reset_for_tests()
        self.user = User.objects.create(
            phone_number=PHONE, name="Nazim", is_verified=True, tokens=1000
        )

    def _post(self, text="hello"):
        return Post.objects.create(user=self.user, text=text, duration_seconds=3)

    def test_short_circuit_skips_feed_query_when_nothing_new(self):
        post = self._post()
        # First request initializes the latest-id cache (one MAX(id) query).
        res = self.client.get(f"/api/billboard/?after={post.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])
        # While the cache is fresh, an empty poll touches the DB zero times.
        with CaptureQueriesContext(connection) as ctx:
            res = self.client.get(f"/api/billboard/?after={post.id}")
        self.assertEqual(res.data, [])
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_long_poll_returns_immediately_when_posts_exist(self):
        post = self._post()
        started = time.monotonic()
        res = self.client.get("/api/billboard/?after=0&wait=5")
        elapsed = time.monotonic() - started
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in res.data], [post.id])
        self.assertLess(elapsed, 2.0)

    def test_long_poll_times_out_with_empty_list(self):
        post = self._post()
        started = time.monotonic()
        res = self.client.get(f"/api/billboard/?after={post.id}&wait=1")
        elapsed = time.monotonic() - started
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 3.0)

    def test_invalid_wait_is_treated_as_plain_poll(self):
        post = self._post()
        started = time.monotonic()
        res = self.client.get(f"/api/billboard/?after={post.id}&wait=oops")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])
        self.assertLess(time.monotonic() - started, 1.0)

    @override_settings(BILLBOARD_LONGPOLL_MAX_WAIT=1)
    def test_wait_is_clamped_to_configured_max(self):
        post = self._post()
        started = time.monotonic()
        res = self.client.get(f"/api/billboard/?after={post.id}&wait=100")
        elapsed = time.monotonic() - started
        self.assertEqual(res.data, [])
        self.assertLess(elapsed, 3.0)  # held ~1s, not 100s

    def test_create_post_wakes_held_long_poll(self):
        baseline = realtime.get_latest_post_id()
        result = {}

        def waiter():
            started = time.monotonic()
            result["woke"] = realtime.wait_for_post_after(baseline, timeout=10)
            result["elapsed"] = time.monotonic() - started

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        time.sleep(0.2)

        self.client.force_authenticate(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                "/api/auth/posts/",
                {"text": "wake up", "duration_seconds": 3},
                format="json",
            )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        thread.join(timeout=10)
        self.assertTrue(result.get("woke"))
        self.assertLess(result["elapsed"], 5.0)
        self.assertEqual(realtime.get_latest_post_id(), res.data["id"])

    def test_new_post_is_served_from_memory_without_db_queries(self):
        baseline = self._post()
        # Initialize the realtime cache/buffer coverage.
        self.client.get(f"/api/billboard/?after={baseline.id}")
        self.client.force_authenticate(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                "/api/auth/posts/",
                {"text": "buffered", "duration_seconds": 3},
                format="json",
            )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        with CaptureQueriesContext(connection) as ctx:
            feed = self.client.get(f"/api/billboard/?after={baseline.id}")
        self.assertEqual([p["id"] for p in feed.data], [res.data["id"]])
        self.assertEqual(feed.data[0]["text"], "buffered")
        self.assertEqual(feed.data[0]["user_name"], "Nazim")
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_buffer_is_skipped_for_user_filtered_requests(self):
        baseline = self._post()
        self.client.get(f"/api/billboard/?after={baseline.id}")
        self.client.force_authenticate(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                "/api/auth/posts/",
                {"text": "filtered", "duration_seconds": 3},
                format="json",
            )
        feed = self.client.get(
            f"/api/billboard/?after={baseline.id}&user={self.user.id}"
        )
        self.assertEqual([p["id"] for p in feed.data], [res.data["id"]])
        other = self.client.get(
            f"/api/billboard/?after={baseline.id}&user={self.user.id + 1}"
        )
        self.assertEqual(other.data, [])

    def test_my_posts_list_is_capped_at_50(self):
        Post.objects.bulk_create(
            Post(user=self.user, text=f"post {i}", duration_seconds=3)
            for i in range(55)
        )
        self.client.force_authenticate(self.user)
        res = self.client.get("/api/auth/posts/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 50)

    def test_refresh_works_without_rotation(self):
        refresh = RefreshToken.for_user(self.user)
        res = self.client.post(
            "/api/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertNotIn("refresh", res.data)  # rotation is off
        # The same refresh token keeps working (no blacklist-after-rotation).
        again = self.client.post(
            "/api/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(again.status_code, status.HTTP_200_OK)
