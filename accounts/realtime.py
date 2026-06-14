"""In-process new-post signaling and payload buffer for the billboard feed.

A single Condition guards:
  - a cached "latest post id" used to short-circuit empty polls, and
  - a small buffer of recently committed posts, already serialized in
    billboard shape, so a woken long-poll (or a poll that has new data) can
    answer from memory without a single database round trip.

Post creation calls notify_new_post() via transaction.on_commit — waiters
never wake before the row is visible — handing over the serialized payload.

Coverage invariant: the buffer may serve a request with cursor `after` only
when it provably holds EVERY existing post with id > after. `_buffer_floor`
is the id up to which the buffer's coverage starts (it covers ids strictly
greater), and coverage is intact only while the buffer's newest id matches
the cached latest id. Anything else — posts created by a sibling process,
trimmed entries, a notify without payload — shrinks or resets coverage, and
requests outside it simply fall back to the normal database query.

Scope: state is per-process. Under a multi-process server a post created in a
sibling worker cannot notify this one, so the cached id is re-validated
against the database every BILLBOARD_DB_RECHECK_SECONDS — that staleness
window bounds the worst-case added latency.
"""

import threading
import time

from django.conf import settings

_RECENT_CAP = 50  # matches the feed's max page size

_cond = threading.Condition()
_latest_id = None  # None until first initialized from the database
_refreshed_at = 0.0  # time.monotonic() of the last DB confirmation
_recent = []  # [(id, payload)] ascending, committed in this process
_buffer_floor = None  # buffer covers exactly the posts with id > this


def _recheck_seconds() -> float:
    return float(getattr(settings, "BILLBOARD_DB_RECHECK_SECONDS", 2.0))


def _query_latest_id() -> int:
    from .models import Post

    return Post.objects.order_by("-id").values_list("id", flat=True).first() or 0


def _buffer_max():
    """Newest id the buffer covers up to. Caller must hold _cond."""
    return _recent[-1][0] if _recent else _buffer_floor


def _store_from_db(fetched: int) -> int:
    """Record a DB-confirmed latest id. Caller must hold _cond."""
    global _latest_id, _refreshed_at, _buffer_floor
    if _buffer_floor is None:
        _buffer_floor = fetched
    elif fetched > (_buffer_max() or 0):
        # Posts exist that never passed through this process's notify
        # (sibling worker); the buffer can no longer prove coverage.
        _recent.clear()
        _buffer_floor = fetched
    _latest_id = max(_latest_id or 0, fetched)
    _refreshed_at = time.monotonic()
    return _latest_id


def get_latest_post_id() -> int:
    """Return the newest post id, hitting the DB only when the cache is stale."""
    now = time.monotonic()
    with _cond:
        if _latest_id is not None and now - _refreshed_at <= _recheck_seconds():
            return _latest_id
    # Query outside the lock: a slow DB round trip must never block notify.
    fetched = _query_latest_id()
    with _cond:
        return _store_from_db(fetched)


def notify_new_post(post_id: int, payload=None) -> None:
    """Record a committed post and wake all held long-poll requests.

    `payload` is the post serialized in billboard shape; when provided it is
    buffered so readers can be answered from memory.
    """
    global _latest_id, _refreshed_at, _buffer_floor
    with _cond:
        if payload is None:
            # No payload to serve: everything up to this id must come from
            # the DB, so coverage restarts above it.
            _recent.clear()
            _buffer_floor = max(_buffer_floor or 0, _latest_id or 0, post_id)
        else:
            if _buffer_floor is None or (_buffer_max() or 0) < (_latest_id or 0):
                # Buffer wasn't covering up to the latest id; restart
                # coverage at this post.
                _recent.clear()
                _buffer_floor = max(_latest_id or 0, post_id - 1)
            _recent.append((post_id, payload))
            overflow = len(_recent) - _RECENT_CAP
            if overflow > 0:
                _buffer_floor = _recent[overflow - 1][0]
                del _recent[:overflow]
        _latest_id = max(_latest_id or 0, post_id)
        _refreshed_at = time.monotonic()
        _cond.notify_all()


def get_buffered_posts_after(after_id: int):
    """Return new posts (newest first) straight from memory, or None when the
    buffer cannot prove it holds every post newer than `after_id`."""
    with _cond:
        if (
            _buffer_floor is None
            or after_id < _buffer_floor
            or (_buffer_max() or 0) != (_latest_id or 0)
        ):
            return None
        return [payload for pid, payload in reversed(_recent) if pid > after_id]


def wait_for_post_after(after_id: int, timeout: float) -> bool:
    """Block until a post with id > after_id exists or timeout elapses.

    Wakes instantly on same-process notify; otherwise rechecks the DB whenever
    the cache goes stale (covers posts created by sibling processes).
    """
    deadline = time.monotonic() + timeout
    while True:
        if get_latest_post_id() > after_id:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        with _cond:
            if (_latest_id or 0) > after_id:
                return True
            _cond.wait(min(remaining, 1.0))


def _reset_for_tests() -> None:
    global _latest_id, _refreshed_at, _buffer_floor
    with _cond:
        _latest_id = None
        _refreshed_at = 0.0
        _recent.clear()
        _buffer_floor = None
