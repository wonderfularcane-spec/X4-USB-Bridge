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

try:
    import serial
except ImportError:
    serial = None

MAGIC = b"X4B1"
HEADER = struct.Struct("!4sBII")
CRC = struct.Struct("!I")
MAX_PAYLOAD = 2048
OPEN, DATA, CLOSE, PING, PONG, STOP, ERROR = range(1, 8)
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


def pack_frame(kind, stream_id=0, payload=b""):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Frame too large")
    head = HEADER.pack(MAGIC, kind, stream_id, len(payload))
    return head + payload + CRC.pack(zlib.crc32(head + payload) & 0xffffffff)


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
            if length > MAX_PAYLOAD or kind < OPEN or kind > ERROR:
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
    ser.write(b'\r')
    seen = read_for(ser, 1.2)
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
        password = (getpass.getpass("Mot de passe Linux de l'imprimante (non enregistre) : " )
                    if sys.stdin.isatty() else 'makerbase')
        ser.write(password.encode('utf-8') + b'\r')
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
    raise RuntimeError("Impossible d'obtenir le shell Linux. Sortie console :\n" + tail)


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
        threading.Thread(target=self.reader, daemon=True, name='uart-read').start()
        threading.Thread(target=self.heartbeat, daemon=True, name='uart-ping').start()

    def send(self, kind, sid=0, data=b''):
        if self.stop.is_set():
            return False
        try:
            with self.lock:
                if data:
                    for i in range(0, len(data), MAX_PAYLOAD):
                        self.ser.write(pack_frame(kind, sid, data[i:i+MAX_PAYLOAD]))
                else:
                    self.ser.write(pack_frame(kind, sid))
            return True
        except Exception as exc:
            print('[ERREUR] Ecriture sur le port serie :', exc)
            self.stop.set()
            return False

    def add(self, channel):
        with self.clients_lock:
            sid = self.counter
            self.counter += 1
            self.clients[sid] = channel
        self.send(OPEN, sid)
        return sid

    def remove(self, sid):
        with self.clients_lock:
            self.clients.pop(sid, None)
        self.send(CLOSE, sid)

    def reader(self):
        try:
            while not self.stop.is_set():
                data = self.ser.read(self.ser.in_waiting or 1)
                if not data:
                    continue
                for typ, sid, body in self.parser.feed(data):
                    if typ == PONG:
                        self.pong = time.monotonic()
                    elif typ in (DATA, CLOSE, ERROR):
                        with self.clients_lock:
                            channel = self.clients.get(sid)
                        if channel is not None:
                            channel.incoming.put((typ, body))
        except Exception as exc:
            if not self.stop.is_set():
                print('[ERREUR] Lecture serie :', exc)
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

        def from_browser():
            try:
                while running.is_set() and not bridge.stop.is_set():
                    try:
                        data = self.request.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    if not bridge.send(DATA, sid, data):
                        break
            except (ConnectionError, OSError):
                pass
            finally:
                running.clear()
                self.incoming.put((CLOSE, b''))

        reader = threading.Thread(target=from_browser, daemon=True)
        reader.start()
        try:
            while running.is_set() and not bridge.stop.is_set():
                try:
                    typ, data = self.incoming.get(timeout=1)
                except queue.Empty:
                    continue
                if typ == DATA:
                    self.request.sendall(data)
                elif typ == ERROR:
                    print('[WARN] Ouverture refusee par nginx sur l\'imprimante :', data.decode('utf-8', 'replace'))
                    break
                else:
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            running.clear()
            bridge.remove(sid)
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            reader.join(timeout=0.2)


class LocalServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


def main():
    ap = argparse.ArgumentParser(description='Pont USB Artillery X4 → Fluidd/Moonraker pour OrcaSlicer')
    ap.add_argument('--com', default='COM3', help='Port serie physique Windows (defaut: COM3)')
    ap.add_argument('--baud', type=int, default=1500000, help='Debit UART (defaut: 1500000)')
    ap.add_argument('--port', type=int, default=8181, help='Port HTTP local (defaut: 8181)')
    ap.add_argument('--user', default='mks', help='Compte Linux sur l\'imprimante (defaut: mks)')
    ap.add_argument('--probe-only', action='store_true', help='Tester uniquement la console et nginx/Moonraker (aucun relais)')
    args = ap.parse_args()
    if serial is None:
        ap.error('pyserial manquant : executez "py -m pip install pyserial" sous Windows.')
    ser = None
    bridge = None
    try:
        print('X4 USB Bridge v0.1 - prototype experimental')
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
            print('[OK] Tunnel actif : http://127.0.0.1:%d' % args.port)
            print('OrcaSlicer : Host Type=Octo/Klipper, adresse=127.0.0.1:%d' % args.port)
            print('Tester /server/info dans le navigateur, puis Fluidd. Ctrl+C pour arreter.')
            server.serve_forever(poll_interval=0.25)
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
