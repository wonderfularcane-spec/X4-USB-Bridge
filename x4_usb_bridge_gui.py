"""Windows Tkinter launcher for the USB-to-Moonraker serial bridge."""
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from serial.tools import list_ports

from x4_bridge_ports import port_identity, retry_seconds, select_com_port

FROZEN = bool(getattr(sys, "frozen", False))
HERE = Path(sys.executable if FROZEN else __file__).resolve().parent
CLI = HERE / "x4_usb_bridge.py"
WORKER = HERE / "X4_USB_Bridge_Worker.exe"
AUTO = "Auto-detect CH340"
MAX_LINES = 400


def bridge_command(com, baud, http, *, frozen=None, python=None, folder=None):
    """Construct a child-process command without ever embedding credentials."""
    freeze = FROZEN if frozen is None else frozen
    where = HERE if folder is None else Path(folder)
    if freeze:
        worker = where / "X4_USB_Bridge_Worker.exe"
        if not worker.is_file():
            raise FileNotFoundError(
                "Missing X4_USB_Bridge_Worker.exe next to the GUI executable."
            )
        argv = [str(worker)]
    else:
        script = where / "x4_usb_bridge.py"
        if not script.is_file():
            raise FileNotFoundError("Missing x4_usb_bridge.py next to the GUI.")
        argv = [python or sys.executable, "-u", str(script)]
    return argv + [
        "--com", com, "--baud", str(baud), "--port", str(http), "--control-stdin"
    ]


def http_port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return True
    except OSError:
        return False


class BridgeWindow:
    def __init__(self, root):
        self.root = root
        root.title("X4 USB Bridge v0.2 (experimental)")
        root.geometry("780x575")
        root.minsize(630, 440)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.com = tk.StringVar(value=AUTO)
        self.baud = tk.StringVar(value="1500000")
        self.http = tk.StringVar(value="8181")
        self.status = tk.StringVar(value="Stopped")
        self.choices = {}
        self.identity = None
        self.selected = None
        self.active = False
        self.closing = False
        self.proc = None
        self.started = 0
        self.stopping_at = 0
        self.retries = 0
        self.retry_at = 0
        self.lines = queue.Queue()

        parent = ttk.Frame(root, padding=16)
        parent.pack(fill="both", expand=True)
        ttk.Label(parent, text="X4 USB Bridge", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(parent, text="OrcaSlicer ↔ USB-C ↔ Fluidd / Moonraker").pack(anchor="w", pady=(0, 14))
        form = ttk.LabelFrame(parent, text="Connection", padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="USB serial port").grid(row=0, column=0, sticky="w")
        self.combo = ttk.Combobox(form, textvariable=self.com, state="readonly", width=40)
        self.combo.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(4, 10))
        ttk.Button(form, text="Refresh", command=self.refresh).grid(row=1, column=1)
        ttk.Label(form, text="Baud rate").grid(row=2, column=0, sticky="w")
        ttk.Label(form, text="Local HTTP port").grid(row=2, column=1, sticky="w")
        ttk.Entry(form, textvariable=self.baud, width=18).grid(row=3, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.http, width=18).grid(row=3, column=1, sticky="w")
        form.columnconfigure(0, weight=1)
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=12)
        self.start_button = ttk.Button(bar, text="Start / auto-reconnect", command=self.start)
        self.start_button.pack(side="left", padx=(0, 7))
        self.stop_button = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 7))
        ttk.Button(bar, text="Open Fluidd", command=self.open_fluidd).pack(side="left", padx=(0, 7))
        ttk.Button(bar, text="Copy Orca address", command=self.copy_address).pack(side="left")
        ttk.Label(parent, textvariable=self.status, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(parent, text="Local only: 127.0.0.1. Never expose Moonraker to the public network.").pack(
            anchor="w", pady=7
        )
        panel = ttk.Frame(parent)
        panel.pack(fill="both", expand=True, pady=(5, 0))
        self.logbox = tk.Text(
            panel, wrap="word", height=12, state="disabled",
            bg="#1b2027", fg="#f0f1f2", insertbackground="white",
            padx=8, pady=8, font=("Consolas", 10),
        )
        scroll = ttk.Scrollbar(panel, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=scroll.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log("The currently running stable bridge will not be interrupted.")
        self.refresh()
        root.after(250, self.tick)

    def log(self, line):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", time.strftime("[%H:%M:%S] ") + line.rstrip() + "\n")
        if int(self.logbox.index("end-1c").split(".")[0]) > MAX_LINES:
            self.logbox.delete("1.0", "40.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def refresh(self):
        ports = list(list_ports.comports())
        options = {AUTO: None}
        for p in sorted(ports, key=lambda item: item.device):
            options[p.device + " | " + (p.description or "Serial")] = p.device
        previous = self.com.get()
        self.choices = options
        self.combo["values"] = list(options)
        if previous not in options:
            self.com.set(AUTO)

    def settings(self):
        baud, port = int(self.baud.get()), int(self.http.get())
        if not 9600 <= baud <= 4000000 or not 1024 <= port <= 65535:
            raise ValueError("Invalid baud rate or HTTP port")
        return baud, port

    def start(self):
        if self.active:
            return
        try:
            self.settings()
        except ValueError:
            messagebox.showerror("Invalid settings", "Check the baud rate and local port.")
            return
        if http_port_in_use(int(self.http.get())):
            messagebox.showwarning(
                "Already in use",
                "This local port is already serving a connection.\n"
                "The running bridge will not be interrupted.",
            )
            return
        self.selected = self.choices.get(self.com.get())
        self.identity = None
        self.retries = 0
        self.retry_at = 0
        self.active = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Waiting for USB printer...")
        self.log("Automatic reconnect enabled.")

    def launch(self, port):
        if http_port_in_use(int(self.http.get())):
            self.status.set("Local HTTP port busy — waiting")
            self.retry_at = time.monotonic() + 3
            return
        baud, http = self.settings()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            argv = bridge_command(port, baud, http)
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self.proc = subprocess.Popen(
                argv, cwd=str(HERE), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags, env=env,
            )
        except FileNotFoundError as err:
            self.log("Incomplete installation: " + str(err))
            self.active = False
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status.set("Worker executable missing")
            return
        except (OSError, ValueError) as err:
            self.log("Launch failed: " + str(err))
            self.schedule_retry()
            return
        self.started = time.monotonic()
        self.status.set("Connecting over " + port)
        self.log("Launching bridge on " + port)
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc):
        try:
            for line in proc.stdout:
                self.lines.put((proc, line.rstrip("\r\n")))
        except OSError:
            pass

    def process_line(self, proc, line):
        if proc is not self.proc:
            return
        if line == "[X4_AUTH_REQUIRED]":
            answer = simpledialog.askstring(
                "Printer Linux login",
                "Enter the printer's Linux login secret (not saved):",
                show="*", parent=self.root,
            )
            if not answer:
                self.log("Login cancelled. Auto-reconnect disabled.")
                self.active = False
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.status.set("Login cancelled")
                # The worker is blocked waiting for a secret on stdin.
                # Do not send STOP as if it were a login secret.
                if proc.poll() is None:
                    proc.terminate()
                return
            try:
                proc.stdin.write(answer + "\n")
                proc.stdin.flush()
                self.log("Linux login supplied through local process input.")
            except (OSError, ValueError):
                self.log("Could not send Linux login input.")
            return
        self.log(line)
        if "AUTH_FAILED:" in line or "Authentification Linux annulee" in line:
            # Never try a rejected password repeatedly in the background.
            self.active = False
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status.set("Linux login failed — check credentials")
        elif "CONSOLE_NOT_READY:" in line:
            # After physical USB reconnection the old RAM relay may keep the
            # Linux tty in raw mode for up to 30 seconds. Leave auto-retry on.
            self.status.set("Linux console busy — will retry")
        elif "Tunnel actif" in line:
            self.status.set("Connected — Fluidd ready")
            self.retries = 0

    def schedule_retry(self):
        self.retries += 1
        delay = retry_seconds(self.retries)
        self.retry_at = time.monotonic() + delay
        self.status.set("Reconnecting in " + str(delay) + " seconds")
        self.log("Disconnected. Retrying in " + str(delay) + " seconds.")

    def tick(self):
        for _ in range(100):
            try:
                proc, line = self.lines.get_nowait()
            except queue.Empty:
                break
            self.process_line(proc, line)
        if self.proc is not None:
            code = self.proc.poll()
            if code is not None:
                self.log("Bridge process exited, code " + str(code))
                self.proc = None
                self.stopping_at = 0
                if self.active:
                    self.schedule_retry()
                else:
                    self.status.set("Stopped")
            elif not self.active and self.stopping_at and time.monotonic() - self.stopping_at > 10:
                self.log("Graceful stop timed out; ending child process.")
                self.proc.terminate()
                self.stopping_at = 0

        if self.active and self.proc is None and time.monotonic() >= self.retry_at:
            port = select_com_port(list_ports.comports(), self.selected, self.identity)
            if port is None:
                self.status.set("Waiting for USB printer...")
                self.retry_at = time.monotonic() + 2
            else:
                if self.identity is None:
                    self.identity = port_identity(port)
                self.launch(port.device)
        if self.closing and self.proc is None:
            self.root.destroy()
            return
        self.root.after(250, self.tick)

    def stop(self, ask=True):
        if ask and self.proc is not None and not messagebox.askyesno(
            "Disconnect bridge?",
            "OrcaSlicer monitoring will disconnect. A running print can continue.\n"
            "Only stop the bridge when it is safe. Continue?",
        ):
            return
        self.active = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Stopping...")
        self.log("Automatic reconnect disabled.")
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.write("STOP\n")
                self.proc.stdin.flush()
                self.stopping_at = time.monotonic()
            except (OSError, ValueError):
                self.proc.terminate()
        else:
            self.status.set("Stopped")

    def close(self):
        if self.proc is not None or self.active:
            if not messagebox.askyesno(
                "Close USB bridge?",
                "This disconnects remote monitoring. A printing job may continue.\n"
                "Stop and close?",
            ):
                return
            self.closing = True
            self.stop(ask=False)
        else:
            self.root.destroy()

    def open_fluidd(self):
        try:
            webbrowser.open("http://127.0.0.1:" + str(self.settings()[1]) + "/")
        except ValueError:
            messagebox.showerror("Invalid port", "Set a valid local HTTP port.")

    def copy_address(self):
        try:
            address = "127.0.0.1:" + str(self.settings()[1])
            self.root.clipboard_clear()
            self.root.clipboard_append(address)
            self.log("Copied print-host address: " + address)
        except ValueError:
            messagebox.showerror("Invalid port", "Set a valid local HTTP port.")


def main():
    window = tk.Tk()
    BridgeWindow(window)
    window.mainloop()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        # Portable executable smoke test: Tcl works without showing a window.
        tk.Tcl()
        assert callable(bridge_command)
    else:
        main()
