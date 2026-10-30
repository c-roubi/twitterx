# -*- coding: utf-8 -*-
"""Shared runtime primitives: one lock per account (a browser profile can only be opened once)."""
import threading

_locks = {}
_guard = threading.Lock()


def account_lock(acc_id):
    with _guard:
        return _locks.setdefault(acc_id, threading.Lock())
