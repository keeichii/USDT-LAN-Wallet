# utility functions for networking, time, and ID generation.

import socket
import time

def get_local_ip() -> str:
    # try to determine the primary local IPv4
    # falls back to 127.0.0.1
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # no packets actually sent, just OS routing decision
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def make_node_id(ip: str) -> str:
    # compatibility shim: node_id equals IP.
    return ip or "127.0.0.1"

def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())