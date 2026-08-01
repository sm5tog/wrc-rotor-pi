## ==========================================================
## rotor_daemon — WRC rotor network daemon
## Runs on the Pi on the same LAN as the WRC (ramnas-station).
## Owns the single GS-232/TCP connection to the WRC and the N1MM
## UDP listener; GUI clients (rotor_klient.py) connect over
## TCP/JSON-lines to send goto/stop and receive telemetry.
## ==========================================================

import json
import logging
import os
import socket
import socketserver
import sys
import threading
import time
import xml.etree.ElementTree as ET

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rotord_config.json")

DEFAULT_CONFIG = {
    "wrc_host":    "192.168.68.105",
    "wrc_port":    23,
    "udp_port":    12040,
    "listen_host": "0.0.0.0",
    "listen_port": 5561,
    "poll_interval": 0.3,
}

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(saved)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)

def save_config():
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

cfg = load_config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("rotor_daemon")

# ──────────────────────────────────────────────────────────
# WRC TCP connection (GS-232) — identisk logik med rotor_gui.py:s Rotor-klass
# ──────────────────────────────────────────────────────────

class Rotor:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self._connect()

    def _connect(self):
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((self.host, self.port))
            self.sock = s
            return True
        except Exception:
            self.sock = None
            return False

    def _send(self, cmd, expect_reply=True):
        with self.lock:
            for _ in range(2):
                try:
                    if self.sock is None:
                        self._connect()
                    self.sock.send((cmd + "\r\n").encode())
                    if not expect_reply:
                        return ""
                    self.sock.settimeout(1)
                    return self.sock.recv(64).decode(errors="replace").strip()
                except Exception:
                    self.sock = None
            return None

    def get_azimuth(self):
        resp = self._send("C")
        if resp is None:
            return None
        try:
            return int(resp.strip().lstrip("+"))
        except ValueError:
            return None

    def goto(self, degrees):
        self._send(f"M{max(0, min(360, int(degrees))):03d}", expect_reply=False)

    def stop(self):
        self._send("S", expect_reply=False)

rotor = Rotor(cfg["wrc_host"], cfg["wrc_port"])

# ──────────────────────────────────────────────────────────
# STATE — delad av alla klientanslutningar
# ──────────────────────────────────────────────────────────

class RotorState:
    def __init__(self):
        self.az_target = None
        self.clients = set()
        self.clients_lock = threading.Lock()

    def broadcast(self, obj):
        line = (json.dumps(obj) + "\n").encode("utf-8")
        with self.clients_lock:
            dead = []
            for c in self.clients:
                try:
                    c.send_raw(line)
                except Exception:
                    dead.append(c)
            for c in dead:
                self.clients.discard(c)

    def goto(self, degrees):
        self.az_target = degrees
        rotor.goto(degrees)

    def stop(self):
        self.az_target = None
        rotor.stop()

state = RotorState()

# ──────────────────────────────────────────────────────────
# N1MM / DXLab / PstRotator UDP-lyssnare (samma parsning som rotor_gui.py)
# ──────────────────────────────────────────────────────────

class UDPListener:
    def __init__(self, port, on_bearing, on_stop):
        self.port = port
        self.on_bearing = on_bearing
        self.on_stop = on_stop
        self._sock = None
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def restart(self, port):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass
        self.port = port
        self.start()

    def _run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("", self.port))
            self._sock.settimeout(1.0)
            while self._running:
                try:
                    data, _ = self._sock.recvfrom(4096)
                    self._parse(data)
                except socket.timeout:
                    continue
        except Exception as e:
            log.error("UDP listener failed on port %d: %s", self.port, e)
        finally:
            if self._sock:
                try: self._sock.close()
                except Exception: pass

    def _parse(self, data):
        try:
            text = data.decode(errors="replace").strip()
            if not text.startswith("<"):
                text = text[text.index("<"):]
            root = ET.fromstring(text)
            stop_el = root.find("STOP")
            if stop_el is not None:
                self.on_stop()
                return
            for tag in ("AZIMUTH", "bearing", "Bearing"):
                el = root.find(tag)
                if el is not None:
                    self.on_bearing(float(el.text))
                    return
        except Exception:
            pass

def _on_udp_bearing(bearing):
    log.info("N1MM/PstRotator bearing: %.1f", bearing)
    state.goto(bearing)
    state.broadcast({"type": "n1mm_bearing", "value": bearing})

def _on_udp_stop():
    log.info("N1MM/PstRotator stop")
    state.stop()
    state.broadcast({"type": "n1mm_stop"})

udp = UDPListener(cfg["udp_port"], _on_udp_bearing, _on_udp_stop)

# ──────────────────────────────────────────────────────────
# POLL LOOP — läser azimut från WRC, broadcastar telemetri
# ──────────────────────────────────────────────────────────

def _poll_loop():
    # WRC:n stänger GS-232-anslutningen efter ett par frågor (verifierat 2026-08-01
    # med en fristående testsocket, oberoende av vår återanslutningslogik) — Rotor
    # återansluter då automatiskt och får ändå rätt azimut. Det är alltså normalt
    # WRC-beteende, inte ett faktiskt avbrott, så en enstaka missad poll ska INTE
    # loggas/broadcastas som "disconnected". Bara flera missar i rad (verklig
    # förlust, t.ex. WRC:n strömlös eller nätet nere) räknas som status-ändring.
    FAIL_THRESHOLD = 10   # ~3 s vid poll_interval=0.3
    wrc_ok = None
    fail_streak = 0
    while True:
        az = rotor.get_azimuth()
        if az is None:
            fail_streak += 1
            if wrc_ok is not False and fail_streak >= FAIL_THRESHOLD:
                wrc_ok = False
                log.warning("WRC connection lost (%s:%d)", cfg["wrc_host"], cfg["wrc_port"])
                state.broadcast({"type": "wrc_status", "value": "disconnected"})
        else:
            fail_streak = 0
            if wrc_ok is not True:
                wrc_ok = True
                log.info("WRC connected (%s:%d)", cfg["wrc_host"], cfg["wrc_port"])
                state.broadcast({"type": "wrc_status", "value": "connected"})
            state.broadcast({"type": "telemetry", "az": az, "target": state.az_target})
        time.sleep(cfg["poll_interval"])

# ──────────────────────────────────────────────────────────
# TCP SERVER — JSON-lines protokoll
# ──────────────────────────────────────────────────────────

class ClientHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self._send_lock = threading.Lock()
        with state.clients_lock:
            state.clients.add(self)
        log.info("Client connected: %s", self.client_address)
        self.send_raw((json.dumps({"type": "config", "udp_port": cfg["udp_port"]}) + "\n").encode("utf-8"))

    def send_raw(self, line_bytes):
        with self._send_lock:
            self.request.sendall(line_bytes)

    def handle(self):
        buf = b""
        self.request.settimeout(1.0)
        while True:
            try:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(msg)
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError):
                break

    def _dispatch(self, msg):
        cmd = msg.get("cmd")
        if cmd == "goto":
            az = msg.get("az")
            if isinstance(az, (int, float)):
                state.goto(az)
        elif cmd == "stop":
            state.stop()
        elif cmd == "get_config":
            self.send_raw((json.dumps({"type": "config", "udp_port": cfg["udp_port"]}) + "\n").encode("utf-8"))
        elif cmd == "set_udp_port":
            port = msg.get("port")
            if isinstance(port, int) and 1 <= port <= 65535:
                cfg["udp_port"] = port
                save_config()
                udp.restart(port)
                log.info("N1MM UDP port changed to %d", port)
                state.broadcast({"type": "config", "udp_port": port})

    def finish(self):
        with state.clients_lock:
            state.clients.discard(self)
        log.info("Client disconnected: %s", self.client_address)

class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    udp.start()
    threading.Thread(target=_poll_loop, daemon=True).start()
    server = ThreadingTCPServer((cfg["listen_host"], cfg["listen_port"]), ClientHandler)
    log.info("rotor_daemon listening on %s:%d (WRC=%s:%d, N1MM UDP=%d)",
              cfg["listen_host"], cfg["listen_port"],
              cfg["wrc_host"], cfg["wrc_port"], cfg["udp_port"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
