# network.py - UDP broadcast/listen network layer for USDT LAN wallet.
# handles UDP message sending, receiving, and broadcast for peer discovery and wallet sync.

import socket
import threading
import json
import ipaddress
from typing import List, Set

import config
from utils import get_local_ip


class Network:
    def __init__(self, on_message_callback):
        self.ip = get_local_ip()
        self.udp_port = config.UDP_PORT
        self.on_message = on_message_callback
        self._stop_event = threading.Event()
        self._listener_thread = None

    def start_listener(self):
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        self._listener_thread = threading.Thread(
            target=self._listen_loop, 
            daemon=True
        )
        self._listener_thread.start()

    # alias for compatibility
    def start(self):
        self.start_listener()

    def stop(self):
        self._stop_event.set()
        if self._listener_thread:
            self._listener_thread.join(timeout=1.0)

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', self.udp_port))
        sock.settimeout(1.0)
        if config.VERBOSE:
            print(f"[network] UDP listener started on 0.0.0.0:{self.udp_port} (local ip {self.ip})")
        try:
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(config.NETWORK_BUFFER)
                    try:
                        msg = json.loads(data.decode('utf-8'))
                    except Exception:
                        if config.VERBOSE:
                            print("[network] received non-json message from", addr)
                        continue
                    try:
                        self.on_message(addr, msg)
                    except Exception as e:
                        if config.VERBOSE:
                            print("[network] error in on_message handler:", e)
                except socket.timeout:
                    continue
                except Exception as e:
                    if config.VERBOSE:
                        print("[network] listener exception:", e)
                    continue
        finally:
            sock.close()
            if config.VERBOSE:
                print("[network] listener stopped")

    def _send_json(self, ip: str, msg: dict):
        if not ip:
            return
        payload = json.dumps(msg, ensure_ascii=False).encode('utf-8')

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                sock.bind((self.ip, 0))  # macOS fix for broadcast
            except OSError as be:
                if config.VERBOSE:
                    print(f"[network] bind({self.ip}) warning: {be}")
            sock.settimeout(0.3)
            sock.sendto(payload, (ip, self.udp_port))
        finally:
            sock.close()

    # broadcast helpers

    def _candidate_broadcasts(self, ip_str: str) -> List[str]:
        try:
            ip = ipaddress.IPv4Address(ip_str)
        except Exception:
            return ["255.255.255.255"]
        masks = [16, 23, 24, 25, 26, 27, 28, 29, 30]
        candidates: Set[str] = set()
        for m in masks:
            try:
                net = ipaddress.IPv4Network(f"{ip}/{m}", strict=False)
                candidates.add(str(net.broadcast_address))
            except Exception:
                continue
        candidates.add("255.255.255.255")
        return list(candidates)

    def _bind_and_broadcast(self, data: bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.bind((self.ip, 0))
            except OSError as be:
                if config.VERBOSE:
                    print(f"[network] bcast bind({self.ip}) warning: {be}")
            sock.settimeout(0.2)

            sent_to = []
            last_err = None
            for bcast in self._candidate_broadcasts(self.ip):
                try:
                    sock.sendto(data, (bcast, self.udp_port))
                    sent_to.append(bcast)
                except OSError as e:
                    last_err = e
                    continue

            if config.VERBOSE:
                if sent_to:
                    print(f"[network] broadcast sent to: {', '.join(sent_to)}")
                else:
                    print(f"[network] broadcast failed; last_err: {last_err}")
        finally:
            sock.close()

    def broadcast_discovery(self):
        msg = {
            "type": "DISCOVERY",
            "ip": self.ip,
            "port": self.udp_port,
        }
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")

        # 1) broadcast
        self._bind_and_broadcast(data)

        # 2) unicast /24 — in case of client/hotspot isolation
        prefix = ".".join(self.ip.split(".")[:3])
        for i in range(1, 255):
            dst = f"{prefix}.{i}"
            if dst == self.ip:
                continue
            try:
                self._send_json(dst, msg)
            except Exception:
                pass

    def broadcast_message(self, msg: dict):
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self._bind_and_broadcast(data)

    # high-level send

    def send_to(self, ip: str, msg: dict, allow_self: bool = False):
        if not ip:
            return
        if (ip == self.ip) and (not allow_self):
            return
        try:
            self._send_json(ip, msg)
        except OSError as e:
            if config.VERBOSE:
                print(f"[network] send_to {ip} failed: {e}")
            if getattr(e, "errno", None) in (65, 101):  # macOS / linux fix for broadcast
                try:
                    msg_fb = dict(msg)
                    msg_fb.setdefault("_dst_ip", ip)
                    self.broadcast_message(msg_fb)
                except Exception as e2:
                    if config.VERBOSE:
                        print(f"[network] fallback broadcast failed: {e2}")
        except Exception as e:
            if config.VERBOSE:
                print(f"[network] unexpected send_to error: {e}")

    def send_to_all(self, nodes: dict, msg: dict, include_self: bool = False):
        if not nodes:
            return
        for _, info in nodes.items():
            ip = info.get("ip", "")
            if not ip:
                continue
            if (ip == self.ip) and (not include_self):
                continue
            self.send_to(ip, msg, allow_self=include_self)