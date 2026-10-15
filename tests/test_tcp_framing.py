"""Regression tests for newline-delimited TCP framing helpers."""
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from network import _recv_tcp_line


class TestTcpLineFraming(unittest.TestCase):
    def test_reads_fragmented_line(self):
        receiver, sender = socket.socketpair()
        try:
            def write_fragments():
                for part in (b'{"id":', b'42', b'}\n'):
                    sender.sendall(part)

            thread = threading.Thread(target=write_fragments)
            thread.start()
            self.assertEqual(_recv_tcp_line(receiver), b'{"id":42}')
            thread.join(timeout=1)
        finally:
            receiver.close()
            sender.close()

    def test_rejects_oversized_line(self):
        receiver, sender = socket.socketpair()
        try:
            sender.sendall(b"x" * 20)
            with self.assertRaises(ValueError):
                _recv_tcp_line(receiver, max_bytes=10)
        finally:
            receiver.close()
            sender.close()

    def test_rejects_connection_closed_before_newline(self):
        receiver, sender = socket.socketpair()
        try:
            sender.sendall(b'{"id":42}')
            sender.close()
            with self.assertRaises(ConnectionError):
                _recv_tcp_line(receiver)
        finally:
            receiver.close()


if __name__ == "__main__":
    unittest.main()
