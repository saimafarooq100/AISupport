"""Bounded AI execution and public-only, version-aware cache."""
import asyncio
import copy
import hashlib
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path

from fastapi import HTTPException
from prometheus_client import Counter

_SLOTS = max(1, min(4, int(os.getenv('AI_SUPPORT_AI_CONCURRENCY', '1'))))
_slots = threading.BoundedSemaphore(_SLOTS)
_executor = ThreadPoolExecutor(max_workers=_SLOTS, thread_name_prefix='campus-ai')
AI_ADMISSIONS = Counter('campus_ai_admission_total', 'AI execution admission decisions', ['outcome'])
CACHE_EVENTS = Counter('campus_public_answer_cache_total', 'Public direct answer cache lookups', ['outcome'])


def _acquire():
    if not _slots.acquire(blocking=False):
        AI_ADMISSIONS.labels('busy').inc()
        raise HTTPException(503, 'AI is busy. Please retry shortly.', headers={'Retry-After': '5'})
    AI_ADMISSIONS.labels('accepted').inc()


def _execute(fn, args, kwargs):
    try:
        return fn(*args, **kwargs)
    finally:
        _slots.release()


async def run_ai(fn, *args, **kwargs):
    """Keep the ASGI loop free; keep the slot until actual work ends."""
    _acquire()
    try:
        future = asyncio.get_running_loop().run_in_executor(_executor, _execute, fn, args, kwargs)
    except BaseException:
        _slots.release()
        raise
    # A disconnected caller must not free an actively running model's slot.
    return await asyncio.shield(future)


def bounded_sync(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _acquire()
        return _execute(fn, args, kwargs)
    return wrapper


class PublicAnswerCache:
    """LRU + TTL, no arbitrary/private answers, never cache errors or empty results."""
    def __init__(self, capacity=256, ttl=900, clock=time.monotonic):
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get_or_compute(self, question, snapshot, compute):
        path = Path(snapshot).resolve()
        st = path.stat()
        version = (str(path), st.st_mtime_ns, st.st_size)
        key = (version, hashlib.sha256(question.encode('utf-8')).hexdigest())
        with self.lock:
            now = self.clock()
            # Evict old dataset versions and expired entries immediately.
            for k, (expires, _) in list(self.entries.items()):
                if k[0] != version or expires <= now:
                    del self.entries[k]
            if key in self.entries:
                self.entries.move_to_end(key)
                CACHE_EVENTS.labels('hit').inc()
                return copy.deepcopy(self.entries[key][1])
        CACHE_EVENTS.labels('miss').inc()
        result = compute(question)
        if result:  # This function is wired only to verified direct_answer.
            # Never cache a result if the snapshot changed during computation.
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) == version[1:]:
                with self.lock:
                    self.entries[key] = (self.clock() + self.ttl, copy.deepcopy(result))
                    self.entries.move_to_end(key)
                    while len(self.entries) > self.capacity:
                        self.entries.popitem(last=False)
        return result


public_cache = PublicAnswerCache()
