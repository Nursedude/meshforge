"""The accept-queue depth every first-party listener uses (MAP-H H13).

Python's socketserver listens with ``request_queue_size = 5``. Measured on
the manager 2026-11-06: of 50 simultaneous requests to the map, ~8 waited a
full 1 s TCP SYN retry before the server saw them (connect 1.01-1.04 s,
answer <= 30 ms) — a cost no handler change can remove. Every listener class
sets ``request_queue_size = LISTEN_BACKLOG``; the kernel still caps it at
``net.core.somaxconn``. Guard: tests/test_listen_backlog_every_listener.py.

Scope: socketserver/http.server listener CLASSES under src/. Raw
``socket.listen(n)`` sockets (a one-shot drill, the lab simulator) are not
covered and need their own reason to change.
"""

LISTEN_BACKLOG = 128
