"""Self-contained queue admission barrier for a dedicated ComfyUI process.

This file is owned by Shine and is not copied from ComfyUI. It makes drain
atomic with respect to calls through the installed queue's ``put`` method.
It does not prove that an arbitrary custom node cannot bypass that method.
"""

import hmac
from threading import RLock


class DrainBusy(RuntimeError):
    pass


def authorized(token, header):
    if not isinstance(token, str) or len(token) < 32 or not isinstance(header, str):
        return False
    return hmac.compare_digest(("Bearer " + token).encode("utf-8"), header.encode("utf-8"))


class DrainBarrier:
    def __init__(self, queue, *, start_drained=False):
        if type(start_drained) is not bool:
            raise ValueError("SHINE_START_STATE_INVALID")
        self._queue = queue
        self._lock = RLock()
        self._drained = start_drained
        original_put = queue.put

        def guarded_put(item):
            with self._lock:
                if self._drained:
                    raise DrainBusy("SHINE_COMFY_DRAINED")
                return original_put(item)

        self._guarded_put = guarded_put
        queue.put = guarded_put

    def drain(self):
        with self._lock:
            if self._queue.put is not self._guarded_put:
                raise DrainBusy("SHINE_QUEUE_GUARD_CHANGED")
            state = self._queue.get_current_queue()
            if (not isinstance(state, tuple) or len(state) != 2
                    or not all(isinstance(part, list) for part in state)):
                raise DrainBusy("SHINE_QUEUE_STATE_UNKNOWN")
            if any(state):
                raise DrainBusy("SHINE_QUEUE_BUSY")
            self._drained = True
            return True

    def state(self):
        with self._lock:
            if self._queue.put is not self._guarded_put:
                raise DrainBusy("SHINE_QUEUE_GUARD_CHANGED")
            return "drained" if self._drained else "accepting"

    def resume(self):
        with self._lock:
            if self._queue.put is not self._guarded_put:
                raise DrainBusy("SHINE_QUEUE_GUARD_CHANGED")
            state = self._queue.get_current_queue()
            if (not isinstance(state, tuple) or len(state) != 2
                    or not all(isinstance(part, list) for part in state)):
                raise DrainBusy("SHINE_QUEUE_STATE_UNKNOWN")
            if any(state):
                raise DrainBusy("SHINE_QUEUE_BUSY")
            if not self._drained:
                raise DrainBusy("SHINE_ALREADY_ACCEPTING")
            self._drained = False
            return True
