"""Reliable printer-to-PC UART relay (temporary RAM-only Linux source).

All traffic to nginx and Moonraker remains the original byte-for-byte TCP stream.
RDATA frames add a 32-bit serial transport sequence number and are ACKed by
the Windows bridge. Lost/corrupt serial frames are retried, not silently skipped.
"""
REMOTE_SOURCE = r'''
import os,sys,termios,tty,struct,selectors,socket,zlib,time
from collections import deque

MAGIC=b"X4B1"
H=struct.Struct("!4sBII")
C=struct.Struct("!I")
OPEN,DATA,CLOSE,PING,PONG,STOP,ERROR,RDATA,ACK=range(1,10)
MAX=2048
BLOCK=MAX-4
sel=selectors.DefaultSelector()
ins=bytearray()
outs=bytearray()
channels={}
reverse={}
tx_queue=deque()
tx_pending=None
tx_seq=0
tx_backlog=0
rx_seq=0
rx_pending=None
last_ping=time.monotonic()
running=True
old=termios.tcgetattr(0)
blockin=os.get_blocking(0)
blockout=os.get_blocking(1)

def emit(t,i=0,p=b''):
    if len(p)>MAX:raise ValueError('frame exceeds maximum')
    head=H.pack(MAGIC,t,i,len(p))
    outs.extend(head+p+C.pack(zlib.crc32(head+p)&0xffffffff))
    if 1 not in [k.fd for k in sel.get_map().values()]:
        sel.register(1,selectors.EVENT_WRITE,('ttyout',0))

def pump():
    global tx_seq,tx_pending
    while tx_pending is None and tx_queue:
        typ,i,part=tx_queue.popleft()
        if typ==CLOSE:
            emit(CLOSE,i)
            continue
        tx_seq=(tx_seq+1)&0xffffffff
        prefix=C.pack(tx_seq)
        tx_pending=[tx_seq,i,part,time.monotonic(),0]
        emit(RDATA,i,prefix+part)

def frame(t,i=0,p=b''):
    global tx_backlog
    if t==DATA:
        for at in range(0,len(p),BLOCK):
            part=p[at:at+BLOCK]
            tx_queue.append((DATA,i,part))
            tx_backlog+=len(part)
        pump()
    elif t==CLOSE:
        # A socket's EOF must be sent AFTER all its response bytes are ACKed.
        tx_queue.append((CLOSE,i,b''))
        pump()
    else:
        emit(t,i,p)

def drop(i,notify=True):
    sock=channels.pop(i,None)
    if sock is not None:
        reverse.pop(sock,None)
        try:sel.unregister(sock)
        except Exception:pass
        try:sock.close()
        except Exception:pass
    if notify:frame(CLOSE,i)

def handle(t,i,p):
    global running,last_ping,tx_pending,tx_backlog,rx_pending,rx_seq
    last_ping=time.monotonic()
    if t==OPEN:
        if i in channels:drop(i)
        try:
            port=int(os.environ.get('X4_TARGET_PORT','80'))
            sock=socket.create_connection(('127.0.0.1',port),2)
            sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            sock.setblocking(False)
            channels[i]=sock
            reverse[sock]=[i,bytearray()]
            sel.register(sock,selectors.EVENT_READ,('sock',i))
        except Exception:
            emit(ERROR,i,b'local target refused connection')
            frame(CLOSE,i)
    elif t==DATA:
        sock=channels.get(i)
        if sock is not None:
            queue=reverse[sock][1]
            queue.extend(p)
            try:sel.modify(sock,selectors.EVENT_READ|selectors.EVENT_WRITE,('sock',i))
            except Exception:drop(i)
    elif t==RDATA:
        if len(p)<C.size:return
        seq=C.unpack(p[:C.size])[0]
        if seq==rx_seq and rx_pending is None:
            # Lost Windows-bound ACK: acknowledge the duplicate, but never
            # write the payload to Moonraker/nginx a second time.
            emit(ACK,0,p[:C.size])
        elif seq==((rx_seq+1)&0xffffffff) and rx_pending is None:
            sock=channels.get(i)
            if sock is not None:
                output=reverse[sock][1]
                output.extend(p[C.size:])
                if output:
                    rx_pending=(i,seq)
                    try:sel.modify(sock,selectors.EVENT_READ|selectors.EVENT_WRITE,('sock',i))
                    except Exception:drop(i)
                else:
                    rx_seq=seq
                    emit(ACK,0,p[:C.size])
        # Otherwise a previous chunk is still draining or this packet is
        # out of order. The sender will retry instead of losing bytes.
    elif t==ACK:
        if len(p)==4 and tx_pending is not None:
            seq=C.unpack(p)[0]
            if seq==tx_pending[0]:
                tx_backlog-=len(tx_pending[2])
                tx_pending=None
                pump()
    elif t==CLOSE:
        drop(i,False)
    elif t==PING:
        emit(PONG,0)
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
        _,typ,sid,size=H.unpack(ins[:H.size])
        if size>MAX or typ<OPEN or typ>ACK:
            del ins[0];continue
        end=H.size+size+C.size
        if len(ins)<end:return
        blob=ins[:H.size+size]
        expected=C.unpack(ins[H.size+size:end])[0]
        if zlib.crc32(blob)&0xffffffff!=expected:
            del ins[0];continue
        payload=bytes(ins[H.size:H.size+size]);del ins[:end]
        handle(typ,sid,payload)

try:
    tty.setraw(0)
    os.set_blocking(0,False)
    os.set_blocking(1,False)
    os.write(1,b'X4_USB_BRIDGE_READY_V1\n')
    sel.register(0,selectors.EVENT_READ,('ttyin',0))
    while running:
        if time.monotonic()-last_ping>30:break
        now=time.monotonic()
        if tx_pending is not None and now-tx_pending[3]>1.3:
            tx_pending[4]+=1
            if tx_pending[4]>16:
                # Never silently declare a response successful if a frame
                # cannot be acknowledged. The host will disconnect/reconnect.
                running=False
                break
            tx_pending[3]=now
            emit(RDATA,tx_pending[1],C.pack(tx_pending[0])+tx_pending[2])
        for key,mask in sel.select(0.08):
            kind,i=key.data
            if kind=='ttyin':
                try:data=os.read(0,16384)
                except BlockingIOError:continue
                if not data:running=False;break
                ins.extend(data)
                decode()
            elif kind=='ttyout':
                try:n=os.write(1,outs[:16384])
                except BlockingIOError:continue
                if n:del outs[:n]
                if not outs:
                    try:sel.unregister(1)
                    except Exception:pass
            elif kind=='sock':
                sock=channels.get(i)
                if sock is None:continue
                if mask&selectors.EVENT_READ:
                    if tx_backlog<32768 and len(outs)<65536:
                        try:data=sock.recv(BLOCK)
                        except BlockingIOError:data=None
                        except OSError:data=b''
                        if data is None:pass
                        elif data:frame(DATA,i,data)
                        else:drop(i);continue
                if mask&selectors.EVENT_WRITE:
                    send_queue=reverse[sock][1]
                    if send_queue:
                        try:n=sock.send(send_queue)
                        except BlockingIOError:n=0
                        except OSError:drop(i);continue
                        if n:del send_queue[:n]
                    if not send_queue and i in channels:
                        if rx_pending is not None and rx_pending[0]==i:
                            rx_seq=rx_pending[1]
                            emit(ACK,0,C.pack(rx_seq))
                            rx_pending=None
                        try:sel.modify(sock,selectors.EVENT_READ,('sock',i))
                        except Exception:drop(i)
finally:
    for i in list(channels):drop(i,False)
    try:termios.tcsetattr(0,termios.TCSANOW,old)
    except Exception:pass
    try:os.set_blocking(0,blockin);os.set_blocking(1,blockout)
    except Exception:pass
'''
