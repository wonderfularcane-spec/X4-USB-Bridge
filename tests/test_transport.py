"""Offline HTTP/TCP + byte integrity regressions. No printer, COM3 or G-code actions."""
import hashlib
import os
import socket
import threading
import unittest

import x4_usb_bridge as bridge


class SerialWriteCollector:
    def __init__(self):
        self.writes = []
        self.on_write = None

    def write(self, payload):
        self.writes.append(bytes(payload))
        if self.on_write:
            self.on_write()
        return len(payload)


class SerialTransferTests(unittest.TestCase):
    def test_batching_is_byte_for_byte_identical_to_original_wire_format(self):
        payload = os.urandom(4 * 1024 * 1024)
        old_wire = b"".join(
            bridge.pack_frame(bridge.DATA, 72, payload[i:i + bridge.MAX_PAYLOAD])
            for i in range(0, len(payload), bridge.MAX_PAYLOAD)
        )
        optimized_wire = b"".join(bridge.serial_batches(bridge.DATA, 72, payload))
        self.assertEqual(hashlib.sha256(optimized_wire).digest(),
                         hashlib.sha256(old_wire).digest())
        self.assertEqual(optimized_wire, old_wire)
        self.assertEqual(
            b"".join(p for _, _, p in bridge.FrameParser().feed(optimized_wire)),
            payload,
        )

    def test_uplink_acks_reassemble_identical_application_bytes(self):
        sender = bridge.Session.__new__(bridge.Session)
        sender.ser = SerialWriteCollector()
        sender.stop = threading.Event()
        sender.lock = threading.Lock()
        sender.tx_lock = threading.Lock()
        sender.tx_seq = 0
        sender.tx_pending_seq = 0
        sender.tx_ack = threading.Event()
        sender.tx_retries = 0
        sender.ack_timeout = 0.015
        sender.ser.on_write = sender.tx_ack.set
        body = bytes(range(256)) * 1000
        self.assertTrue(sender.send(bridge.DATA, 31, body))
        self.assertTrue(all(len(part) <= 2065 for part in sender.ser.writes))
        received = bridge.FrameParser().feed(b"".join(sender.ser.writes))
        self.assertEqual(b"".join(data[4:] for _, _, data in received), body)
        self.assertTrue(all(typ == bridge.RDATA and sid == 31
                            for typ, sid, _ in received))
        self.assertEqual(
            [bridge.CRC.unpack(part[:4])[0] for _, _, part in received],
            list(range(1, len(received) + 1)),
        )

    def test_lost_uplink_ack_retransmits_identical_frame(self):
        sender = bridge.Session.__new__(bridge.Session)
        sender.ser = SerialWriteCollector()
        sender.stop = threading.Event()
        sender.lock = threading.Lock()
        sender.tx_lock = threading.Lock()
        sender.tx_seq = 0
        sender.tx_pending_seq = 0
        sender.tx_ack = threading.Event()
        sender.tx_retries = 0
        sender.ack_timeout = 0.015

        def lose_first_ack():
            if len(sender.ser.writes) >= 2:
                sender.tx_ack.set()

        sender.ser.on_write = lose_first_ack
        self.assertTrue(sender.send(bridge.DATA, 77, b"unaltered-data"))
        self.assertEqual(sender.tx_retries, 1)
        self.assertEqual(sender.ser.writes[0], sender.ser.writes[1])
        self.assertEqual(
            bridge.FrameParser().feed(sender.ser.writes[0]),
            [(bridge.RDATA, 77, bridge.CRC.pack(1) + b"unaltered-data")],
        )

    def test_data_and_close_frames_can_share_one_stream(self):
        frames = [
            (bridge.DATA, 13, b"abc"),
            (bridge.DATA, 13, b"HTTP/1.1 200 OK"),
            (bridge.CLOSE, 13, b""),
        ]
        parser = bridge.FrameParser()
        self.assertEqual(
            parser.feed(b"".join(bridge.pack_frame(*item) for item in frames)),
            frames,
        )


class MockNginx:
    """Mock HTTP endpoint that drops connections if client closes too early.

    Like actual nginx, it uses Content-Length to detect complete requests,
    and does not need the client socket to be half-closed to send a reply.
    """

    def __init__(self):
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.current = {}
        self.last_uploaded = b""
        self.premature_close = False
        self.unexpected_kind = False

    def add(self, handler):
        sid = id(handler)
        with self.lock:
            self.current[sid] = [handler, bytearray(), False]
        self.send(bridge.OPEN, sid)
        return sid

    def send(self, kind, sid=0, data=b""):
        with self.lock:
            record = self.current.get(sid)
            if record is None:
                return False
            handler, received, sent = record
            if kind == bridge.DATA:
                received.extend(data)
                if b"\r\n\r\n" in received and not sent:
                    headers, body = received.split(b"\r\n\r\n", 1)
                    size = None
                    for field in headers.split(b"\r\n"):
                        if field.lower().startswith(b"content-length:"):
                            size = int(field.split(b":", 1)[1].strip())
                    if size is not None and len(body) == size:
                        record[2] = True
                        # Give the local bridge time to see the client FIN.
                        # If it closes the tunnel, the pending response is lost.
                        threading.Timer(0.08, self.respond, (sid,)).start()
            elif kind == bridge.CLOSE:
                # A remote response that has not been sent is lost here.
                if record[2] and not self.last_uploaded:
                    self.premature_close = True
                self.current.pop(sid, None)
            elif kind != bridge.OPEN:
                self.unexpected_kind = True
        return True

    def respond(self, sid):
        with self.lock:
            record = self.current.get(sid)
            if record is None:
                return
            handler, received, sent = record
            if not sent:
                return
            self.last_uploaded = bytes(received)
            response = (b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n"
                        b"Connection: close\r\n\r\nUPLOAD OK")
            handler.incoming.put((bridge.DATA, response))
            handler.incoming.put((bridge.CLOSE, b""))

    def remove(self, sid):
        self.send(bridge.CLOSE, sid)


class HttpTunnelTests(unittest.TestCase):
    def test_upload_waits_for_reply_even_if_client_half_closes_tcp(self):
        remote = MockNginx()
        with bridge.LocalServer(("127.0.0.1", 0), bridge.ClientHandler) as server:
            server.bridge = remote
            listener = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01}, daemon=True,
            )
            listener.start()
            payload = os.urandom(256 * 1024)
            request = (
                b"POST /printer/test HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + b"Content-Length: " + str(len(payload)).encode("ascii")
                + b"\r\n\r\n" + payload
            )
            with socket.create_connection(server.server_address, timeout=5) as sock:
                sock.settimeout(8)
                sock.sendall(request)
                sock.shutdown(socket.SHUT_WR)
                response = bytearray()
                while True:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    response.extend(chunk)
            server.shutdown()
            listener.join(timeout=2)
        self.assertFalse(remote.premature_close, "Bridge closed upload before reply")
        self.assertFalse(remote.unexpected_kind, "Bridge rewrote the framing protocol")
        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertEqual(response.split(b"\r\n\r\n", 1)[1], b"UPLOAD OK")
        self.assertEqual(hashlib.sha256(remote.last_uploaded).digest(),
                         hashlib.sha256(request).digest())

    def test_expect_100_continue_large_body_is_unmodified(self):
        """Simulate curl's Expect: 100-continue handshake on a large POST."""

        class ContinueThenCreated:
            def __init__(self):
                self.stop = threading.Event()
                self.clients = {}
                self.lock = threading.RLock()
                self.received = None

            def add(self, handler):
                sid = id(handler)
                with self.lock:
                    self.clients[sid] = [handler, bytearray(), False]
                return sid

            def send(self, kind, sid=0, data=b""):
                with self.lock:
                    item = self.clients.get(sid)
                    if item is None:
                        return True
                    handler, request, signalled = item
                    if kind == bridge.DATA:
                        request.extend(data)
                        if not signalled and b"\r\n\r\n" in request:
                            item[2] = True
                            handler.incoming.put(
                                (bridge.DATA, b"HTTP/1.1 100 Continue\r\n\r\n")
                            )
                        if item[2] and request.count(b"\r\n\r\n") >= 1:
                            headers, body = request.split(b"\r\n\r\n", 1)
                            for field in headers.split(b"\r\n"):
                                if field.lower().startswith(b"content-length:"):
                                    expected = int(field.split(b":", 1)[1])
                                    break
                            else:
                                expected = None
                            if expected is not None and len(body) == expected:
                                self.received = bytes(body)
                                handler.incoming.put(
                                    (bridge.DATA,
                                     b"HTTP/1.1 201 Created\r\n"
                                     b"Content-Length: 2\r\n"
                                     b"Connection: close\r\n\r\nOK")
                                )
                                handler.incoming.put((bridge.CLOSE, b""))
                    elif kind == bridge.CLOSE:
                        self.clients.pop(sid, None)
                return True

            def remove(self, sid):
                self.send(bridge.CLOSE, sid)

        remote = ContinueThenCreated()
        body = os.urandom(2 * 1024 * 1024)
        with bridge.LocalServer(("127.0.0.1", 0), bridge.ClientHandler) as server:
            server.bridge = remote
            listener = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01}, daemon=True,
            )
            listener.start()
            with socket.create_connection(server.server_address, timeout=6) as sock:
                sock.settimeout(15)
                sock.sendall(
                    b"POST /server/files/upload HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Expect: 100-continue\r\nContent-Length: "
                    + str(len(body)).encode("ascii")
                    + b"\r\n\r\n"
                )
                interim = bytearray()
                while b"\r\n\r\n" not in interim:
                    interim.extend(sock.recv(1024))
                self.assertEqual(
                    bytes(interim).split(b"\r\n", 1)[0],
                    b"HTTP/1.1 100 Continue",
                )
                sock.sendall(body)
                sock.shutdown(socket.SHUT_WR)
                final = bytearray()
                while True:
                    block = sock.recv(8192)
                    if not block:
                        break
                    final.extend(block)
            server.shutdown()
            listener.join(timeout=2)
        self.assertEqual(remote.received, body)
        self.assertEqual(
            bytes(final).split(b"\r\n", 1)[0],
            b"HTTP/1.1 201 Created",
        )
        self.assertEqual(bytes(final).split(b"\r\n\r\n", 1)[1], b"OK")

    def test_reject_oversize_upload_before_body_without_empty_reply(self):
        """An early 413 from nginx must reach the HTTP client unchanged."""

        class EarlyReject:
            def __init__(self):
                self.stop = threading.Event()
                self.channels = {}

            def add(self, handler):
                sid = id(handler)
                self.channels[sid] = handler
                return sid

            def remove(self, sid):
                self.channels.pop(sid, None)

            def send(self, kind, sid=0, data=b""):
                handler = self.channels.get(sid)
                if handler and kind == bridge.DATA and b"\r\n\r\n" in data:
                    handler.incoming.put((bridge.DATA,
                        b"HTTP/1.1 413 Request Entity Too Large\r\n"
                        b"Content-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"))
                    handler.incoming.put((bridge.CLOSE, b""))
                return True

        with bridge.LocalServer(("127.0.0.1", 0), bridge.ClientHandler) as server:
            server.bridge = EarlyReject()
            listener = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01}, daemon=True)
            listener.start()
            with socket.create_connection(server.server_address, timeout=5) as sock:
                sock.settimeout(5)
                sock.sendall(b"POST /server/files/upload HTTP/1.1\r\n"
                             b"Host: localhost\r\nContent-Length: 2097152\r\n\r\n")
                response = sock.recv(2048)
            server.shutdown()
            listener.join(timeout=2)
        self.assertTrue(response.startswith(b"HTTP/1.1 413 "), response)

    def test_closed_upstream_yields_explicit_502_not_empty_reply(self):
        """A dead upstream must not produce curl 52's zero-byte reply."""

        class ClosedUpstream:
            def __init__(self):
                self.stop = threading.Event()
                self.clients = {}

            def add(self, handler):
                sid = id(handler)
                self.clients[sid] = handler
                return sid

            def send(self, kind, sid=0, data=b""):
                if kind == bridge.DATA and sid in self.clients:
                    self.clients[sid].incoming.put((bridge.CLOSE, b""))
                return True

            def remove(self, sid):
                self.clients.pop(sid, None)

        with bridge.LocalServer(("127.0.0.1", 0), bridge.ClientHandler) as server:
            server.bridge = ClosedUpstream()
            listener = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01}, daemon=True,
            )
            listener.start()
            with socket.create_connection(server.server_address, timeout=5) as sock:
                sock.settimeout(5)
                sock.sendall(b"GET /server/info HTTP/1.1\r\n"
                             b"Host: localhost\r\n\r\n")
                received = bytearray()
                while True:
                    chunk = sock.recv(2048)
                    if not chunk:
                        break
                    received.extend(chunk)
            server.shutdown()
            listener.join(timeout=2)
        self.assertEqual(bytes(received).split(b"\r\n", 1)[0],
                         b"HTTP/1.1 502 Bad Gateway")
        self.assertIn(b"Content-Length: 0\r\n", received)

    def test_upstream_error_yields_explicit_502(self):
        class RefusedUpstream:
            def __init__(self):
                self.stop = threading.Event()
                self.channels = {}

            def add(self, handler):
                sid = id(handler)
                self.channels[sid] = handler
                handler.incoming.put((bridge.ERROR, b"mock connection refused"))
                return sid

            def send(self, kind, sid=0, data=b""):
                return True

            def remove(self, sid):
                self.channels.pop(sid, None)

        with bridge.LocalServer(("127.0.0.1", 0), bridge.ClientHandler) as server:
            server.bridge = RefusedUpstream()
            listener = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01}, daemon=True,
            )
            listener.start()
            with socket.create_connection(server.server_address, timeout=5) as sock:
                sock.settimeout(5)
                sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                result = sock.recv(1024)
            server.shutdown()
            listener.join(timeout=2)
        self.assertTrue(result.startswith(b"HTTP/1.1 502 "), result)


if __name__ == "__main__":
    unittest.main()
