#!/usr/bin/env python3
"""X4 USB Bridge: local HTTP/WebSocket tunnel to Artillery Linux over serial console.

Windows: Python 3.9+ and pyserial. Printer: Python 3.7+ available on the
existing Linux serial console. No disk writes on the printer.
"""
import argparse
import base64
import getpass
import os
import queue
import re
import sys
import socket
import socketserver
import struct
import threading
import time
import zlib

from x4_remote_reliable import REMOTE_SOURCE as RELIABLE_REMOTE_SOURCE

try:
    import serial
except ImportError:
    serial = None

MAGIC = b"X4B1"
HEADER = struct.Struct("!4sBII")
CRC = struct.Struct("!I")
MAX_PAYLOAD = 2048
OPEN, DATA, CLOSE, PING, PONG, STOP, ERROR, RDATA, ACK = range(1, 10)
READY = b"X4_USB_BRIDGE_READY_V1\n"

# This is the *entire* printer-side component. It is launched via the existing
# serial Linux shell from a compressed Python command held in a shell variable.
# It does not create or modify files on the printer.
REMOTE_SOURCE = r'''
import os,sys,termios,tty,struct,selectors,socket,zlib,time
MAGIC=b"X4B1"
H=struct.Struct('!4sBII'); C=struct.Struct('!I')
OPEN,DATA,CLOSE,PING,PONG,STOP,ERROR=range(1,8)
MAX=2048
sel=selectors.DefaultSelector()
ins=bytearray(); outs=bytearray()
channels={}; reverse={}
last_ping=time.monotonic(); running=True
old=termios.tcgetattr(0)
blockin=os.get_blocking(0);blockout=os.get_blocking(1)

def frame(t,i=0,p=b''):
    global outs
    for n in range(0,len(p),MAX):
        part=p[n:n+MAX]
        h=H.pack(MAGIC,t,i,len(part))
        outs.extend(h+part+C.pack(zlib.crc32(h+part)&0xffffffff))
    if not p:
        h=H.pack(MAGIC,t,i,0)
        outs.extend(h+C.pack(zlib.crc32(h)&0xffffffff))
    if 1 not in [k.fd for k in sel.get_map().values()]:
        sel.register(1,selectors.EVENT_WRITE,('ttyout',0))

def drop(i,notify=True):
    s=channels.pop(i,None)
    if s is not None:
        reverse.pop(s,None)
        try:sel.unregister(s)
        except Exception:pass
        try:s.close()
        except Exception:pass
    if notify:frame(CLOSE,i)

def handle(t,i,p):
    global running,last_ping
    last_ping=time.monotonic()
    if t==OPEN:
        if i in channels:drop(i)
        try:
            port=int(os.environ.get('X4_TARGET_PORT','80'))
            s=socket.create_connection(('127.0.0.1',port),2)
            s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            s.setblocking(False)
            channels[i]=s
            reverse[s]=[i,bytearray()]
            sel.register(s,selectors.EVENT_READ,('sock',i))
        except Exception:
            frame(ERROR,i,b'local target refused connection')
            frame(CLOSE,i)
    elif t==DATA:
        if i in channels:
            s=channels[i]
            b=reverse[s][1]
            b.extend(p)
            try:sel.modify(s,selectors.EVENT_READ|selectors.EVENT_WRITE,('sock',i))
            except Exception:drop(i)
    elif t==CLOSE:
        drop(i,False)
    elif t==PING:
        frame(PONG,0)
    elif t==STOP:
        running=False

def decode():
    while True:
        at=ins.find(MAGIC)
        if at<0:
            if len(ins)>len(MAGIC):del ins[:-len(MAGIC)]
            return
        if at:del ins[:at]
        if len(ins)<H.size:return
        mg,t,i,n=H.unpack(ins[:H.size])
        if n>MAX or t<OPEN or t>ERROR:
            del ins[0];continue
        end=H.size+n+C.size
        if len(ins)<end:return
        blob=bytes(ins[:H.size+n])
        expected=C.unpack(ins[H.size+n:end])[0]
        if zlib.crc32(blob)&0xffffffff!=expected:
            del ins[0];continue
        p=bytes(ins[H.size:H.size+n]);del ins[:end]
        handle(t,i,p)

try:
    tty.setraw(0)
    os.set_blocking(0,False)
    os.set_blocking(1,False)
    os.write(1,b'X4_USB_BRIDGE_READY_V1\n')
    sel.register(0,selectors.EVENT_READ,('ttyin',0))
    while running:
        if time.monotonic()-last_ping>30:break
        for key,mask in sel.select(0.35):
            kind,i=key.data
            if kind=='ttyin':
                try:b=os.read(0,8192)
                except BlockingIOError:continue
                if not b:running=False;break
                ins.extend(b);decode()
            elif kind=='ttyout':
                try:n=os.write(1,outs[:16384])
                except BlockingIOError:continue
                if n:del outs[:n]
                if not outs:
                    try:sel.unregister(1)
                    except Exception:pass
            elif kind=='sock':
                s=channels.get(i)
                if s is None:continue
                if mask&selectors.EVENT_READ:
                    # Bound the buffered remote->host data for slow receivers.
                    if len(outs)<262144:
                        try:d=s.recv(MAX)
                        except BlockingIOError:d=None
                        except OSError:d=b''
                        if d is None:pass
                        elif d:frame(DATA,i,d)
                        else:drop(i);continue
                if mask&selectors.EVENT_WRITE:
                    b=reverse[s][1]
                    if b:
                        try:n=s.send(b)
                        except BlockingIOError:n=0
                        except OSError:drop(i);continue
                        if n:del b[:n]
                    if not b and i in channels:
                        try:sel.modify(s,selectors.EVENT_READ,('sock',i))
                        except Exception:drop(i)
finally:
    for i in list(channels):drop(i,False)
    try:termios.tcsetattr(0,termios.TCSANOW,old)
    except Exception:pass
    try:os.set_blocking(0,blockin);os.set_blocking(1,blockout)
    except Exception:pass
'''

# The v0.3 experimental tunnel protects printer -> Windows data with
# stop-and-wait serial acknowledgements. The v0.1 protocol and code in main
# remain untouched. No G-code compression or HTTP body rewriting.
REMOTE_SOURCE = RELIABLE_REMOTE_SOURCE


def pack_frame(kind, stream_id=0, payload=b""):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Frame too large")
    head = HEADER.pack(MAGIC, kind, stream_id, len(payload))
    return head + payload + CRC.pack(zlib.crc32(head + payload) & 0xffffffff)


def serial_batches(kind, stream_id=0, payload=b"", frames_per_batch=8):
    """Yield bounded writes without altering the frames or application data.

    Multiple original X4B1 frames share one pyserial.write() syscall. Release
    the serial lock between batches so monitoring/PING can interleave with a
    long upload. No compression, HTTP parsing or G-code transformation.
    """
    if frames_per_batch < 1:
        raise ValueError("frames_per_batch must be positive")
    if not payload:
        yield pack_frame(kind, stream_id)
        return
    mv = memoryview(payload)
    stride = MAX_PAYLOAD * frames_per_batch
    for base in range(0, len(mv), stride):
        end = min(base + stride, len(mv))
        write = bytearray()
        for offset in range(base, end, MAX_PAYLOAD):
            write.extend(pack_frame(kind, stream_id,
                                    mv[offset:min(offset + MAX_PAYLOAD, end)]))
        yield bytes(write)


class FrameParser:
    def __init__(self):
        self.data = bytearray()
        self.dropped = 0

    def feed(self, raw):
        self.data.extend(raw)
        output = []
        while True:
            at = self.data.find(MAGIC)
            if at < 0:
                if len(self.data) > len(MAGIC):
                    self.dropped += len(self.data) - len(MAGIC)
                    del self.data[:-len(MAGIC)]
                return output
            if at:
                self.dropped += at
                del self.data[:at]
            if len(self.data) < HEADER.size:
                return output
            _mg, kind, sid, length = HEADER.unpack(self.data[:HEADER.size])
            if length > MAX_PAYLOAD or kind < OPEN or kind > ACK:
                self.dropped += 1
                del self.data[0]
                continue
            end = HEADER.size + length + CRC.size
            if len(self.data) < end:
                return output
            rawhead = bytes(self.data[:HEADER.size+length])
            checksum = CRC.unpack(self.data[HEADER.size+length:end])[0]
            if checksum != (zlib.crc32(rawhead) & 0xffffffff):
                self.dropped += 1
                del self.data[0]
                continue
            payload = bytes(self.data[HEADER.size:HEADER.size+length])
            del self.data[:end]
            output.append((kind, sid, payload))


def read_for(ser, seconds, until=None):
    end = time.monotonic() + seconds
    result = bytearray()
    while time.monotonic() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            result.extend(chunk)
            if until is not None and until in result:
                break
    return bytes(result)


def shell_probe(ser):
    token = "__X4_SHELL_PROBE_%d__" % (int(time.monotonic()*1000000) % 100000000)
    # Checking for a token on its own output line avoids accepting an echoed command.
    ser.write(("printf '\\n" + token + "\\n'\r").encode('ascii'))
    report = read_for(ser, 1.8)
    return bool(re.search(rb'\r?\n' + token.encode('ascii') + rb'\r?\n', report)), report


def login_if_needed(ser, username):
    # Clear any incomplete shell command left by a dropped USB session.
    # Ctrl-U is harmless in getty and ignored by an orphan raw-mode agent.
    ser.write(b'\x15\r')
    seen = read_for(ser, 1.2)
    attempted_password = False
    if b'login:' not in seen.lower() and b'password:' not in seen.lower():
        ok, more = shell_probe(ser)
        if ok:
            print('[OK] Console Linux deja connectee.')
            return
        seen += more
    if b'login:' not in seen.lower() and b'password:' not in seen.lower():
        # The probe may have been sent to the login prompt (username invalid).
        seen += read_for(ser, 1.0)
    if b'login:' in seen.lower() and b'password:' not in seen.lower():
        # Clear anything accidentally typed at the getty login prompt.
        ser.write(b'\x15')
        ser.write(username.encode('utf-8') + b'\r')
        seen = read_for(ser, 3, b'Password:')
    if b'password:' in seen.lower():
        if sys.stdin.isatty():
            password = getpass.getpass("Mot de passe Linux de l'imprimante (non enregistre) : ")
        else:
            # GUI supplies a one-time password via stdin. Never guess factory
            # credentials or expose a password in command-line arguments.
            print('[X4_AUTH_REQUIRED]', flush=True)
            password = sys.stdin.readline().rstrip('\r\n')
        if not password:
            raise RuntimeError('Authentification Linux annulee ou mot de passe vide.')
        attempted_password = True
        ser.write(password.encode('utf-8') + b'\r')
        password = None
        read_for(ser, 2.5)
        ok, more = shell_probe(ser)
        if ok:
            print('[OK] Session Linux ouverte.')
            return
        seen += more
    else:
        ok, more = shell_probe(ser)
        if ok:
            return
        seen += more
    tail = seen[-600:].decode('utf-8', errors='replace')
    if attempted_password:
        raise RuntimeError('AUTH_FAILED: Identifiants Linux refuses. ' + tail)
    # The serial port is back, but an old RAM-only relay can remain in raw
    # terminal mode until its 30-second heartbeat timeout. This is transient
    # and must NOT permanently disable automatic USB reconnection.
    raise RuntimeError('CONSOLE_NOT_READY: Console Linux temporairement indisponible. ' + tail)


def start_remote(ser):
    payload = base64.b64encode(zlib.compress(REMOTE_SOURCE.encode('utf-8'), 9)).decode('ascii')
    # Use short shell lines to stay below Linux tty canonical input line limits.
    commands = ["export X4CODE=''"]
    commands.extend('X4CODE="${X4CODE}' + payload[i:i+150] + '"'
                    for i in range(0, len(payload), 150))
    commands.append("python3 -u -c 'import os,base64,zlib;exec(zlib.decompress(base64.b64decode(os.environ[\"X4CODE\"])).decode())'; unset X4CODE")
    print('[...] Lancement du relais temporaire en RAM (aucun fichier cree sur l\'imprimante).')
    all_output = bytearray()
    for command in commands:
        ser.write(command.encode('ascii') + b'\r')
        time.sleep(0.045)  # Allow the shell to process commands at high UART speeds.
        all_output.extend(ser.read(ser.in_waiting or 1))
    all_output.extend(read_for(ser, 12, READY))
    if READY not in all_output:
        tail = all_output[-1600:].decode('utf-8', errors='replace')
        raise RuntimeError('Relais distant non demarre. Sortie :\n' + tail)
    ser.reset_input_buffer()  # All data before READY is shell echo, not protocol.
    print('[OK] Agent en memoire demarre sur l\'Artillery.')


class Session:
    def __init__(self, ser):
        self.ser = ser
        self.lock = threading.Lock()
        self.clients = {}
        self.clients_lock = threading.Lock()
        self.counter = 1
        self.stop = threading.Event()
        self.parser = FrameParser()
        self.errors = 0
        self.pong = time.monotonic()
        self.rx_seen_seq = 0
        self.rx_acked_seq = 0
        self.tx_lock = threading.Lock()
        self.tx_seq = 0
        self.tx_pending_seq = 0
        self.tx_ack = threading.Event()
        self.tx_retries = 0
        self.ack_timeout = 1.3
        # Read serial bytes as quickly as possible. CRC checking, decoding,
        # client lookup and logging run on another thread to avoid keeping
        # Windows CH340 receive buffers blocked under sustained downloads.
        self.rx_chunks = queue.Queue(maxsize=2048)
        threading.Thread(target=self.serial_pump, daemon=True,
                         name='uart-raw-read').start()
        threading.Thread(target=self.reader, daemon=True,
                         name='uart-frame-decode').start()
        threading.Thread(target=self.heartbeat, daemon=True,
                         name='uart-ping').start()

    def send(self, kind, sid=0, data=b''):
        if self.stop.is_set():
            return False
        try:
            if kind == DATA and data:
                # PC -> printer payloads use the same loss-recovery protocol
                # as printer -> PC replies. Only the serial frame changes;
                # Moonraker and OrcaSlicer see the original bytes unchanged.
                view = memoryview(data)
                stride = MAX_PAYLOAD - CRC.size
                for start in range(0, len(view), stride):
                    with self.tx_lock:
                        self.tx_seq = (self.tx_seq + 1) & 0xffffffff
                        seq = self.tx_seq
                        self.tx_pending_seq = seq
                        self.tx_ack.clear()
                        packet = pack_frame(
                            RDATA, sid,
                            CRC.pack(seq) + bytes(view[start:start + stride]),
                        )
                        for attempt in range(17):
                            if self.stop.is_set():
                                return False
                            with self.lock:
                                written = self.ser.write(packet)
                            if written != len(packet):
                                raise IOError('Short UART frame write')
                            if self.tx_ack.wait(self.ack_timeout):
                                break
                            self.tx_retries += 1
                            if self.tx_retries <= 4 or self.tx_retries % 32 == 0:
                                print('[UART RETRY] PC->printer frame %d, '
                                      'retry %d (total=%d)' %
                                      (seq, attempt + 1, self.tx_retries),
                                      flush=True)
                        else:
                            raise TimeoutError(
                                'No UART ACK after 17 attempts; '
                                'aborting link to avoid an incomplete upload'
                            )
                return True
            for batch in serial_batches(kind, sid, data):
                if self.stop.is_set():
                    return False
                with self.lock:
                    written = self.ser.write(batch)
                if written != len(batch):
                    raise IOError('Short UART control write')
            return True
        except Exception as exc:
            print('[UART ERROR] Serial transmit:', exc, flush=True)
            self.stop.set()
            return False

    def add(self, channel):
        with self.clients_lock:
            sid = self.counter
            self.counter += 1
            self.clients[sid] = channel
        # Use the unchanged v0.1 connection mapping (nginx port 80).
        self.send(OPEN, sid)
        return sid

    def remove(self, sid):
        with self.clients_lock:
            self.clients.pop(sid, None)
        self.send(CLOSE, sid)

    def ack_downlink(self, seq):
        """Confirm downstream bytes were forwarded once, not merely decoded."""
        self.rx_acked_seq = seq
        self.send(ACK, 0, CRC.pack(seq))

    def serial_pump(self):
        """Prioritize draining CH340 bytes over decoding or logging."""
        try:
            while not self.stop.is_set():
                # Read immediately when a reliable 2 KiB response frame
                # arrives. Waiting for a fixed 8 KiB would add the 100 ms
                # serial timeout to EACH ACK round-trip and cripple throughput.
                waiting = self.ser.in_waiting
                chunk = self.ser.read(min(waiting, 16384) if waiting else 1)
                if chunk:
                    try:
                        self.rx_chunks.put(chunk, timeout=1)
                    except queue.Full:
                        print('[UART ERROR] Decoder backlog exceeded safe limit.',
                              flush=True)
                        self.stop.set()
                        break
        except Exception as exc:
            if not self.stop.is_set():
                print('[UART ERROR] Raw serial read:', exc, flush=True)
                self.stop.set()

    def reader(self):
        violations = 0
        try:
            while not self.stop.is_set():
                try:
                    data = self.rx_chunks.get(timeout=0.3)
                except queue.Empty:
                    continue
                before_dropped = self.parser.dropped
                decoded = self.parser.feed(data)
                if self.parser.dropped > before_dropped:
                    violations += 1
                    if violations <= 4 or violations % 32 == 0:
                        print(
                            '[UART WARN] CRC/framing resync: %d bytes discarded '
                            'in %d events (large HTTP replies may be incomplete)' %
                            (self.parser.dropped, violations),
                            flush=True,
                        )
                for typ, sid, body in decoded:
                    if typ == PONG:
                        self.pong = time.monotonic()
                    elif typ == ACK:
                        if len(body) == CRC.size and (
                            CRC.unpack(body)[0] == self.tx_pending_seq
                        ):
                            self.tx_ack.set()
                    elif typ == RDATA:
                        if len(body) < CRC.size:
                            continue
                        seq = CRC.unpack(body[:CRC.size])[0]
                        if seq == (self.rx_seen_seq + 1) & 0xffffffff:
                            self.rx_seen_seq = seq
                            with self.clients_lock:
                                channel = self.clients.get(sid)
                            if channel is not None:
                                # ACK only after the Windows TCP socket accepts
                                # these bytes; keep reverse pressure bounded.
                                channel.incoming.put((RDATA, body))
                            else:
                                self.ack_downlink(seq)
                        elif seq == self.rx_acked_seq:
                            # The sender lost the previous ACK; do not deliver
                            # duplicate HTTP bytes, just acknowledge again.
                            self.send(ACK, 0, CRC.pack(seq))
                        # If this seq is still pending in a client queue,
                        # do not ACK duplicates until sendall completes.
                    elif typ in (DATA, CLOSE, ERROR):
                        with self.clients_lock:
                            channel = self.clients.get(sid)
                        if channel is not None:
                            channel.incoming.put((typ, body))
        except Exception as exc:
            if not self.stop.is_set():
                print('[UART ERROR] Frame decoder:', exc, flush=True)
        finally:
            self.stop.set()
            with self.clients_lock:
                for channel in self.clients.values():
                    channel.incoming.put((CLOSE, b''))

    def heartbeat(self):
        while not self.stop.wait(5):
            self.send(PING)
            if time.monotonic() - self.pong > 20:
                print('[ERREUR] Le relais ne repond plus (liaison serie interrompue).')
                self.stop.set()
                break

    def close(self):
        try:
            self.send(STOP)
            time.sleep(0.15)
        except Exception:
            pass
        self.stop.set()
        with self.clients_lock:
            for channel in self.clients.values():
                channel.incoming.put((CLOSE, b''))
        self.ser.close()


class ClientHandler(socketserver.BaseRequestHandler):
    def handle(self):
        bridge = self.server.bridge
        self.incoming = queue.Queue()
        sid = bridge.add(self)
        self.request.settimeout(2)
        running = threading.Event()
        running.set()
        upload_bytes = 0
        first_upload = None
        last_forwarded = None
        first_response = None
        write_eof_at = None

        def from_browser():
            nonlocal upload_bytes, first_upload, last_forwarded, write_eof_at
            peer_finished_writing = False
            try:
                while running.is_set() and not bridge.stop.is_set():
                    try:
                        data = self.request.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        # TCP FIN only closes the client's sending direction.
                        # Do not discard the pending HTTP response.
                        peer_finished_writing = True
                        break
                    if first_upload is None:
                        first_upload = time.monotonic()
                    if not bridge.send(DATA, sid, data):
                        break
                    upload_bytes += len(data)
                    # Time of the last serial write, not HTTP completion.
                    last_forwarded = time.monotonic()
            except (ConnectionError, OSError):
                pass
            finally:
                if peer_finished_writing and not bridge.stop.is_set():
                    # HTTP messages have Content-Length / chunked framing.
                    # The TCP FIN here is NOT forwarded to nginx: nginx may
                    # treat a half-closed downstream as an aborted HTTP client.
                    # Keep the response direction alive until upstream closes.
                    write_eof_at = time.monotonic()
                else:
                    running.clear()
                    self.incoming.put((CLOSE, b''))

        reader = threading.Thread(target=from_browser, daemon=True)
        reader.start()
        try:
            while running.is_set() and not bridge.stop.is_set():
                try:
                    typ, data = self.incoming.get(timeout=1)
                except queue.Empty:
                    if write_eof_at is not None and time.monotonic() - write_eof_at > 180:
                        print('[WARN] No upstream reply after client write EOF; closing stale tunnel.')
                        break
                    continue
                if typ == DATA:
                    if first_response is None:
                        first_response = time.monotonic()
                    self.request.sendall(data)
                elif typ == RDATA:
                    if len(data) < CRC.size:
                        break
                    if first_response is None:
                        first_response = time.monotonic()
                    seq = CRC.unpack(data[:CRC.size])[0]
                    self.request.sendall(data[CRC.size:])
                    bridge.ack_downlink(seq)
                elif typ == ERROR:
                    print('[WARN] Connexion HTTP refusee sur l\'imprimante :',
                          data.decode('utf-8', 'replace'))
                    if first_response is None:
                        self.request.sendall(
                            b'HTTP/1.1 502 Bad Gateway\r\n'
                            b'Content-Length: 0\r\n'
                            b'Connection: close\r\n\r\n'
                        )
                    break
                else:
                    # An upstream connection can close before delivering any
                    # HTTP response. Report an explicit gateway failure instead
                    # of curl 52 / an ambiguous empty reply. No application
                    # payloads are changed when upstream actually replies.
                    if first_response is None:
                        self.request.sendall(
                            b'HTTP/1.1 502 Bad Gateway\r\n'
                            b'Content-Length: 0\r\n'
                            b'Connection: close\r\n\r\n'
                        )
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            running.clear()
            bridge.remove(sid)
            try:
                # Shut down only the response direction. Forcing SHUT_RDWR
                # can reset a socket with queued request bytes on Windows,
                # hiding an upstream HTTP error (e.g. 502/413) from curl.
                self.request.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            reader.join(timeout=0.2)
            if first_upload is not None and upload_bytes >= 128 * 1024:
                # Distinguish time blocked on UART from HTTP response latency.
                # These counters do NOT prove Moonraker saved the file.
                serial_elapsed = max(
                    (last_forwarded or time.monotonic()) - first_upload, 0.001
                )
                wait_after_forward = (
                    max(first_response - last_forwarded, 0.0)
                    if first_response is not None and last_forwarded is not None
                    else None
                )
                print(
                    '[USB STATS] UART forwarded %.2f MiB in %.1fs (%.1f KiB/s); '
                    'response wait %s; response_received=%s' %
                    (upload_bytes / (1024 * 1024), serial_elapsed,
                     upload_bytes / 1024 / serial_elapsed,
                     ('%.1fs' % wait_after_forward)
                     if wait_after_forward is not None else 'n/a',
                     first_response is not None),
                    flush=True,
                )


class LocalServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


def serve_until_stopped(server, bridge, control_stop=None):
    """Run socket accept loop until USB link fails or the GUI requests stop.

    serve_forever() would otherwise keep listening after a lost COM port,
    preventing a supervising launcher from restarting the bridge.
    """
    server.timeout = 0.35
    while not bridge.stop.is_set() and (
        control_stop is None or not control_stop.is_set()
    ):
        server.handle_request()


def main():
    # Frozen console apps launched with hidden Windows pipes may default to
    # cp1252. Keep the GUI subprocess protocol and help output UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description='Pont USB Artillery X4 → Fluidd/Moonraker pour OrcaSlicer')
    ap.add_argument('--com', default='COM3', help='Port serie physique Windows (defaut: COM3)')
    ap.add_argument('--baud', type=int, default=1500000, help='Debit UART (defaut: 1500000)')
    ap.add_argument('--port', type=int, default=8181, help='Port HTTP local (defaut: 8181)')
    ap.add_argument('--user', default='mks', help='Compte Linux sur l\'imprimante (defaut: mks)')
    ap.add_argument('--probe-only', action='store_true', help='Tester uniquement la console et nginx/Moonraker (aucun relais)')
    ap.add_argument('--control-stdin', action='store_true')
    args = ap.parse_args()
    if serial is None:
        ap.error('pyserial manquant : executez "py -m pip install pyserial" sous Windows.')
    ser = None
    bridge = None
    try:
        print('X4 USB Bridge v0.2.0-beta.3 - bidirectional UART recovery (experimental)')
        print('Fermer PuTTY et HW VSP3 avant de lancer la connexion COM.')
        ser = serial.Serial(args.com, baudrate=args.baud, timeout=0.1,
                            write_timeout=8, xonxoff=False, rtscts=False, dsrdtr=False)
        print('[OK] Port serie %s ouvert a %d bauds.' % (args.com, args.baud))
        login_if_needed(ser, args.user)
        if args.probe_only:
            cmd = ('curl -sS --max-time 4 -o /dev/null -w \"nginx %{http_code}\\n\" http://127.0.0.1/; '
                   'curl -sS --max-time 4 -o /dev/null -w \"API %{http_code}\\n\" http://127.0.0.1/server/info')
            ser.write((cmd + '\r').encode('ascii'))
            print('[READ-ONLY] Reponse Linux :', read_for(ser, 9).decode('utf-8','replace')[-1200:])
            return 0
        start_remote(ser)
        bridge = Session(ser)
        with LocalServer(('127.0.0.1', args.port), ClientHandler) as server:
            server.bridge = bridge
            print('[OK] Tunnel actif : http://127.0.0.1:%d' % args.port, flush=True)
            print('OrcaSlicer : Host Type=Octo/Klipper, adresse=127.0.0.1:%d' % args.port)
            print('Tester /server/info dans le navigateur, puis Fluidd. Ctrl+C pour arreter.')
            control_stop = threading.Event()
            if args.control_stdin:
                def stdin_control():
                    while not control_stop.is_set():
                        line = sys.stdin.readline()
                        if not line or line.strip().upper() == 'STOP':
                            control_stop.set()
                            break
                threading.Thread(target=stdin_control, daemon=True,
                                 name='gui-stdin-control').start()
            serve_until_stopped(server, bridge, control_stop)
    except KeyboardInterrupt:
        print('\nArret demande.')
    except Exception as exc:
        print('[ERREUR]', exc)
        print('Si le terminal serie n\'est pas ferme proprement, reconnecter PuTTY sur COM3.')
        return 1
    finally:
        if bridge is not None:
            bridge.close()
        elif ser is not None:
            ser.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
