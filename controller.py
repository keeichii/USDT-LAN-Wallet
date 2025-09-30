# controller for USDT LAN Wallet: receives node balance tables, checks for illegal state,
# and provides HTTP API for web UI to display current network state.

import threading
import time
import config
from utils import now_ts
import messages
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json

class Controller:
    def __init__(self, wallet_ref=None, network=None):
        self.wallet = wallet_ref
        self.network = network
        self._lock = threading.Lock()
        self.illegal_status = 0
        self._http_thread = None
        self._http_server = None

    def on_ctrl_notify(self, from_ip: str, balances: dict):
        # handle incoming balance table from a node.
        if balances is None:
            return
        with self._lock:
            total_nodes = len(balances)
            total_sum = sum(int(v.get("balance", 0)) for v in balances.values())
            expected = total_nodes * int(config.INITIAL_BALANCE)
            # illegal when total supply deviates from theoretical supply
            self.illegal_status = 1 if total_sum != expected else 0
        # optionally, broadcast status so nodes can display illegal flag
        status_msg = {
            "type": messages.MSG_CTRL_NOTIFY,
            "ctrl_ts": now_ts(),
            "illegal": self.illegal_status,
        }
        try:
            self.network.broadcast_message(status_msg)
        except Exception:
            pass

    def announce(self):
        # announce controller presence so nodes can know there is one.
        msg = {
            "type": messages.MSG_CTRL_ANNOUNCE,
            "ts": now_ts(),
        }
        try:
            self.network.broadcast_message(msg)
        except Exception:
            pass

    # HTTP server
    def start_http(self):
        if self._http_thread and self._http_thread.is_alive():
            return

        controller_ref = self

        class Handler(BaseHTTPRequestHandler):
            def _set_headers(self, code=200, ctype='application/json'):
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

            def do_GET(self):
                if self.path == '/' or self.path == '/index.html' or self.path == '/main.html':
                    try:
                        with open('main.html', 'rb') as f:
                            data = f.read()
                        self._set_headers(200, 'text/html; charset=utf-8')
                        self.wfile.write(data)
                    except Exception:
                        # fallback minimal HTML (for main.html)
                        html = (
                            "<!DOCTYPE html><html><body>"
                            "<p>controller UI file missing. API at /api/state</p>"
                            "</body></html>"
                        ).encode('utf-8')
                        self._set_headers(200, 'text/html; charset=utf-8')
                        self.wfile.write(html)
                    return

                if self.path.startswith('/api/state'):
                    with controller_ref._lock:
                        balances = controller_ref.wallet.balances if controller_ref.wallet else {}
                        illegal = controller_ref.illegal_status
                    body = json.dumps({
                        'ts': now_ts(),
                        'illegal': illegal,
                        'balances': balances
                    }).encode('utf-8')
                    self._set_headers(200, 'application/json')
                    self.wfile.write(body)
                    return

                if self.path.startswith('/api/ping'):
                    self._set_headers(200, 'application/json')
                    self.wfile.write(b'{"ok":true}')
                    return

                self._set_headers(404, 'text/plain; charset=utf-8')
                self.wfile.write(b'not found')

            def log_message(self, format, *args):
                # silence default HTTP logging
                return

        def run_server():
            try:
                httpd = HTTPServer(('', config.HTTP_PORT), Handler)
                controller_ref._http_server = httpd
                httpd.serve_forever()
            except Exception:
                pass

        self._http_thread = threading.Thread(target=run_server, daemon=True)
        self._http_thread.start()