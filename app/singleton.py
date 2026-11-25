"""Make sure only one copy of a background program runs.

The first copy claims a local port; any later copy finds it taken and exits. The port is
released automatically when the program ends or crashes, so there are no stale lock files.
"""
import socket

SCHEDULER_PORT = 47653


def acquire(port: int = SCHEDULER_PORT):
    """Returns a socket to keep alive while running, or None if another copy already holds it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        sock.close()
        return None
    return sock