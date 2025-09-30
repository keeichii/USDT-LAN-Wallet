# wallet.py - core logic for USDT LAN wallet.
# handles balance table management, transaction processing, and network communication.

import threading
import uuid
import time
import config
import messages
from utils import get_local_ip, now_ts

CONFIRM_WAIT = 4.0

class Wallet:
    def __init__(self, network, user_id=None):
        self.network = network
        self.ip = get_local_ip()
        # use IP as the node identifier
        self.node_id = self.ip

        self.balances = {}
        self.nodes = {}
        self.lock = threading.Lock()

        self.is_controller = False
        self.controller_id = None

        self.pending_confirmations = {}
        self.applied_tx_ids = set()
        self._seen_msgs = {}
        self.ctrl_illegal = 0

        # initialize self in balances
        with self.lock:
            self.balances[self.node_id] = {
                "id": self.node_id,
                "ip": self.ip,
                "balance": int(config.INITIAL_BALANCE),
                "block_status": 0
            }
            self.nodes[self.node_id] = {"id": self.node_id, "ip": self.ip}

    def show_my_info(self):
        # show detailed information about current user
        info = self.my_info()
        print(f"\nMy Wallet Information:")
        print(f" IP:       {self.ip}")
        print(f" Balance:  {info['balance']} {config.COIN_NAME}")
        print(f" Known nodes: {len(self.balances)}")

    def become_controller(self):
        # opt-in to controller role: start accepting tables and validating.
        from controller import Controller
        with self.lock:
            self.is_controller = True
            self.controller_id = self.node_id
            self.controller = Controller(wallet_ref=self, network=self.network)
        print("✓ You are now the controller")
        # announce presence so others know there is a controller
        try:
            self.controller.announce()
        except Exception:
            pass
        # start tiny HTTP server for LAN dashboard
        try:
            self.controller.start_http()
            print(f"HTTP dashboard on http://{self.ip}:{config.HTTP_PORT}/")
        except Exception:
            pass

    def _dedup(self, msg: dict) -> bool:
        # check for duplicate messages
        mid = msg.get("msg_id")
        if not mid:
            return False
        now = time.time()
        # cleanup old messages
        for k, t in list(self._seen_msgs.items()):
            if now - t > 10:
                del self._seen_msgs[k]
        if mid in self._seen_msgs:
            return True
        self._seen_msgs[mid] = now
        return False

    def _export_balances_minimal(self):
        # export balances for network transmission
        out = {}
        for nid, info in self.balances.items():
            out[nid] = {
                "ip": info.get("ip", ""),
                "balance": int(info.get("balance", 0)),
                # do not propagate transient lock state
                "block_status": 0
            }
        return out

    def scan_network(self):
        # manual network scan
        self.network.broadcast_discovery()
        time.sleep(config.SCAN_TIMEOUT)

    def on_network_message(self, addr, msg: dict):
        # handle incoming network messages
        if self._dedup(msg):
            return

        # filter by destination for fallback broadcast
        dst_ip = msg.get("_dst_ip")
        if dst_ip and dst_ip != self.ip:
            return

        mtype = msg.get("type")
        from_ip = (addr[0] if addr else msg.get("from_ip")) or msg.get("ip")

        # controller announce and status
        if mtype == messages.MSG_CTRL_ANNOUNCE:
            return
        if mtype == messages.MSG_CTRL_NOTIFY and 'illegal' in msg:
            try:
                self.ctrl_illegal = int(msg.get('illegal', 0))
            except Exception:
                self.ctrl_illegal = 0
            # optional: print a small notice when verbose
            if getattr(config, 'VERBOSE', False):
                state = "OK" if self.ctrl_illegal == 0 else "ILLEGAL"
                print(f"[controller] state: {state}")
            return
        if mtype == messages.MSG_CTRL_NOTIFY and self.is_controller and 'balances' in msg:
            try:
                self.controller.on_ctrl_notify(from_ip, msg.get('balances') or {})
            except Exception:
                pass
            return

        # ignore own DISCOVERY
        if mtype in (messages.MSG_DISCOVERY, "DISCOVERY") and (from_ip == self.ip or msg.get("ip") == self.ip):
            return

        # DISCOVERY -> RESPONSE (direct reply improves first-scan)
        if mtype in (messages.MSG_DISCOVERY, "DISCOVERY"):
            # register the sender immediately so the node list updates without manual scan
            if from_ip:
                with self.lock:
                    self.nodes.setdefault(from_ip, {"id": from_ip, "ip": from_ip})
                    # do NOT create zero-balance placeholders to avoid sending 0 for new nodes
            resp = {
                "type": messages.MSG_DISCOVERY_RESPONSE,
                "msg_id": str(uuid.uuid4()),
                "from_id": self.node_id,
                "from_ip": self.ip,
                "is_controller": False,
                "balances": self._export_balances_minimal()
            }
            if from_ip:
                self.network.send_to(from_ip, resp, allow_self=False)
                # also request their table, and send ours directly
                try:
                    treq = {"type": messages.MSG_TABLE_REQUEST, "msg_id": str(uuid.uuid4()), "from_id": self.node_id}
                    self.network.send_to(from_ip, treq, allow_self=False)
                    tresp = {"type": messages.MSG_TABLE_RESPONSE, "msg_id": str(uuid.uuid4()), "from_id": self.node_id, "balances": self._export_balances_minimal()}
                    self.network.send_to(from_ip, tresp, allow_self=False)
                except Exception:
                    pass
            else:
                self.network.broadcast_message(resp)
            return

        # DISCOVERY RESPONSE (peer responds to our discovery)
        if mtype in (messages.MSG_DISCOVERY_RESPONSE, "DISCOVERY_RESP"):
            rid = from_ip  # id == ip now
            rip = from_ip
            self._merge_remote_node(rid, rip, msg.get("balances", {}), False)
            return

        # TABLE RESP (peer proactively shares updated table)
        if mtype == messages.MSG_TABLE_RESPONSE:
            self._merge_remote_node(from_ip, from_ip, msg.get("balances", {}), False)
            return

        # TABLE REQ (peer requests our balances)
        if mtype == messages.MSG_TABLE_REQUEST:
            try:
                tresp = {
                    "type": messages.MSG_TABLE_RESPONSE,
                    "msg_id": str(uuid.uuid4()),
                    "from_id": self.node_id,
                    "balances": self._export_balances_minimal()
                }
                if from_ip:
                    self.network.send_to(from_ip, tresp, allow_self=False)
                else:
                    self.network.broadcast_message(tresp)
            except Exception:
                pass
            return

        # LOCK/UNLOCK (global lock)
        if mtype == messages.MSG_LOCK:
            with self.lock:
                self.global_lock_owner = from_ip or msg.get('from_id')
                for v in self.balances.values():
                    v['block_status'] = 1
            return

        if mtype == messages.MSG_UNLOCK:
            with self.lock:
                # unlock only if owner or timeout logic can be added later
                self.global_lock_owner = None
                for v in self.balances.values():
                    v['block_status'] = 0
            return

        # TRANSACTION (peer sends transaction) 
        if mtype == messages.MSG_TRANSACTION:
            tx = msg.get("tx")
            self._handle_incoming_transaction(from_ip, tx, from_ip)
            return

        if mtype == messages.MSG_CONFIRM:
            tx_id = msg.get("tx_id")
            sender_id = from_ip
            self._handle_confirm(tx_id, sender_id)
            return

        if mtype == messages.MSG_ROLLBACK:
            tx_id = msg.get("tx_id")
            self._handle_rollback(tx_id)
            return

    def _merge_remote_node(self, remote_id, remote_ip, remote_balances, is_controller_flag=False):
        # merge data from remote node (ids are IPs). If we rejoin, adopt table and keep our balance.
        with self.lock:
            if remote_ip:
                node = self.nodes.setdefault(remote_ip, {"id": remote_ip, "ip": remote_ip})
                node["ip"] = remote_ip

            # adopt/merge remote balances by IP
            for nid, info in (remote_balances or {}).items():
                ip_key = nid
                existing = self.balances.get(ip_key)
                incoming_balance = int(info.get("balance", 0))
                incoming_lock = int(info.get("block_status", 0))
                if existing is None:
                    # new peer discovered: if it's me, keep my local (likely 100) unless remote has higher
                    if ip_key == self.node_id:
                        self.balances[ip_key] = {
                            "id": ip_key,
                            "ip": self.ip,
                            "balance": max(int(self.balances.get(ip_key, {}).get("balance", config.INITIAL_BALANCE)), incoming_balance),
                            "block_status": int(self.balances.get(ip_key, {}).get("block_status", 0))
                        }
                    else:
                        # for other peers, if no info yet, assume remote's announced balance
                        self.balances[ip_key] = {
                            "id": ip_key,
                            "ip": info.get("ip", ip_key),
                            "balance": incoming_balance if incoming_balance > 0 else 0,
                            "block_status": int(self.balances.get(ip_key, {}).get("block_status", 0))
                        }
                else:
                    # merge by taking max to avoid downgrading a new node's initial 100 to 0
                    existing["balance"] = max(int(existing.get("balance", 0)), incoming_balance)
                    # keep local lock state
                self.nodes[ip_key] = {"id": ip_key, "ip": info.get("ip", ip_key)}

            # ensure our own record exists; if remote has our IP, adopt that balance (rejoin case)
            if self.node_id in (remote_balances or {}):
                adopted = int(remote_balances[self.node_id].get("balance", config.INITIAL_BALANCE))
                current = int(self.balances.get(self.node_id, {}).get("balance", config.INITIAL_BALANCE))
                self.balances[self.node_id] = {
                    "id": self.node_id,
                    "ip": self.ip,
                    "balance": max(current, adopted),  # keep 100 for new nodes, but adopt higher if rejoining (rejoin case)
                    "block_status": int(remote_balances[self.node_id].get("block_status", 0))
                }
            else:
                self.balances.setdefault(self.node_id, {
                    "id": self.node_id,
                    "ip": self.ip,
                    "balance": int(self.balances.get(self.node_id, {}).get("balance", config.INITIAL_BALANCE)),
                    "block_status": 0
                })

    def _handle_incoming_transaction(self, origin_id, tx: dict, origin_ip=None):
        # handle incoming transaction
        if not tx:
            return
        tx_id = tx.get("tx_id")
        amount = int(tx.get("amount", 0))
        from_id = tx.get("from")
        to_id = tx.get("to")

        # apply transaction if not already applied
        applied_now = False
        with self.lock:
            if from_id not in self.balances:
                self.balances[from_id] = {"id": from_id, "ip": from_id, "balance": 0, "block_status": 0}
            if to_id not in self.balances:
                self.balances[to_id] = {"id": to_id, "ip": to_id, "balance": 0, "block_status": 0}

            if tx_id not in self.applied_tx_ids and amount > 0 and self.balances[from_id]['balance'] >= amount:
                self.balances[from_id]['balance'] -= amount
                self.balances[to_id]['balance'] += amount
                self.applied_tx_ids.add(tx_id)
                applied_now = True
                # store metadata so rollback can be accurate on all nodes
                meta = getattr(self, "_applied_meta", {})
                meta[tx_id] = (from_id, to_id, amount)
                self._applied_meta = meta

        # if I am the recipient and we applied now, print a simple receipt
        if applied_now and to_id == self.node_id:
            print(f"got money from {from_id} amount {amount} balance {self.balances[self.node_id]['balance']}")

        # send confirmation
        confirm_msg = {
            "type": messages.MSG_CONFIRM,
            "msg_id": str(uuid.uuid4()),
            "from_id": self.node_id,
            "tx_id": tx_id
        }
        target_ip = origin_ip or self.nodes.get(origin_id, {}).get("ip")
        if target_ip:
            self.network.send_to(target_ip, confirm_msg)

        # Notify controller about updated table
        try:
            balances_min = self._export_balances_minimal()
            ctrl_msg = {
                "type": messages.MSG_CTRL_NOTIFY,
                "msg_id": str(uuid.uuid4()),
                "from_id": self.node_id,
                "balances": balances_min
            }
            # broadcast so a controller (if any) can compute illegal status
            self.network.broadcast_message(ctrl_msg)
        except Exception:
            pass

        # proactively broadcast updated table so peers refresh without manual scan
        try:
            table_sync = {
                "type": messages.MSG_TABLE_RESPONSE,
                "msg_id": str(uuid.uuid4()),
                "from_id": self.node_id,
                "balances": self._export_balances_minimal()
            }
            self.network.send_to_all(self.nodes, table_sync, include_self=False)
        except Exception:
            pass

    def _handle_confirm(self, tx_id: str, sender_id: str):
        # handle transaction confirmation
        if not tx_id:
            return
        if tx_id not in self.pending_confirmations:
            self.pending_confirmations[tx_id] = {"confirmations": set()}
        self.pending_confirmations[tx_id]['confirmations'].add(sender_id)

    def _handle_rollback(self, tx_id: str):
        # rollback transaction
        if not tx_id:
            return
        
        # only rollback if this node applied the tx
        if tx_id not in self.applied_tx_ids:
            return

        # find transaction info
        meta = getattr(self, "_applied_meta", {})
        info = meta.get(tx_id)
        if info:
            from_id, to_id, amount = info
        else:
            # fallback: try to extract from pending entry
            entry = None
            for e in self.pending_confirmations.values():
                if e.get("tx", {}).get("tx_id") == tx_id:
                    entry = e
                    break
            if not entry:
                return
            tx = entry.get("tx") or {}
            from_id, to_id, amount = tx.get("from"), tx.get("to"), int(tx.get("amount", 0))

        # rollback
        with self.lock:
            if from_id in self.balances and to_id in self.balances and self.balances[to_id]['balance'] >= amount:
                self.balances[to_id]['balance'] -= amount
                self.balances[from_id]['balance'] += amount
                if tx_id in self.applied_tx_ids:
                    self.applied_tx_ids.discard(tx_id)
                # clean meta after rollback
                if tx_id in meta:
                    try:
                        del meta[tx_id]
                    except Exception:
                        pass

    def send_transaction(self, to_id: str, amount: int) -> bool:
        # send transaction to another user
        with self.lock:
            my = self.balances.get(self.node_id)
            if not my:
                print("error: your node not in balances")
                return False
            if amount <= 0:
                print("amount must be > 0")
                return False
            if my['balance'] < amount:
                print("insufficient funds")
                return False

            # global lock: if another owner holds lock, refuse
            if any(v.get('block_status', 0) == 1 for v in self.balances.values()) and self.global_lock_owner not in (None, self.node_id, self.ip):
                print("another transaction in progress; try again later")
                return False
            for v in self.balances.values():
                v['block_status'] = 1
            self.global_lock_owner = self.node_id
            lock_msg = {"type": messages.MSG_LOCK, "msg_id": str(uuid.uuid4()), "from_id": self.node_id}
            self.network.send_to_all(self.nodes, lock_msg)

            if to_id not in self.balances:
                print("recipient not found")
                for v in self.balances.values():
                    v['block_status'] = 0
                unlock_msg = {"type": messages.MSG_UNLOCK, "msg_id": str(uuid.uuid4()), "from_id": self.node_id}
                self.network.send_to_all(self.nodes, unlock_msg)
                return False

            # apply locally
            my['balance'] -= int(amount)
            self.balances[to_id]['balance'] += int(amount)

            # create transaction
            tx = {
                "tx_id": str(uuid.uuid4()),
                "from": self.node_id,
                "to": to_id,
                "amount": int(amount),
                "ts": now_ts()
            }
            tx_msg = {
                "type": messages.MSG_TRANSACTION,
                "msg_id": str(uuid.uuid4()),
                "from_id": self.node_id,
                "tx": tx
            }

            # save for rollback
            meta = getattr(self, "_applied_meta", {})
            meta[tx["tx_id"]] = (tx["from"], tx["to"], tx["amount"])
            self._applied_meta = meta

            self.applied_tx_ids.add(tx['tx_id'])
            self.pending_confirmations[tx['tx_id']] = {
                "tx": tx,
                "confirmations": set([self.node_id])
            }
            self.network.send_to_all(self.nodes, tx_msg, include_self=False)

        # wait for confirmations
        start = time.time()
        success = False
        while time.time() - start < CONFIRM_WAIT:
            entry = self.pending_confirmations.get(tx['tx_id'])
            if not entry:
                time.sleep(0.1)
                continue
            # majority among known online nodes (known nodes list)
            total_online = len(self.nodes)
            need = total_online // 2
            if len(entry['confirmations']) >= need:
                success = True
                break
            time.sleep(0.1)

        # rollback if failed
        if not success:
            rb = {"type": messages.MSG_ROLLBACK, "msg_id": str(uuid.uuid4()), "from_id": self.node_id, "tx_id": tx["tx_id"]}
            self.network.send_to_all(self.nodes, rb, include_self=True)
            self._handle_rollback(tx["tx_id"])

        # unlock
        with self.lock:
            for v in self.balances.values():
                v['block_status'] = 0
            self.global_lock_owner = None
            unlock_msg = {"type": messages.MSG_UNLOCK, "msg_id": str(uuid.uuid4()), "from_id": self.node_id}
            self.network.send_to_all(self.nodes, unlock_msg)

        # after transaction completes (success or rollback), broadcast table so peers and controller recalc state
        try:
            table_sync = {
                "type": messages.MSG_TABLE_RESPONSE,
                "msg_id": str(uuid.uuid4()),
                "from_id": self.node_id,
                "balances": self._export_balances_minimal()
            }
            self.network.send_to_all(self.nodes, table_sync, include_self=False)
            ctrl_msg = {
                "type": messages.MSG_CTRL_NOTIFY,
                "msg_id": str(uuid.uuid4()),
                "from_id": self.node_id,
                "balances": self._export_balances_minimal()
            }
            self.network.broadcast_message(ctrl_msg)
        except Exception:
            pass

        return success

    def show_table(self):
        # show balances table
        with self.lock:
            print("| IP                        | Balance | lock |")
            for nid, info in sorted(self.balances.items()):
                b = info.get("balance", 0)
                l = info.get("block_status", 0)
                short = nid if len(nid) <= 25 else nid[:22] + "..."
                print(f"| {short:25} | {b:7} | {l:4} |")

    def my_info(self):
        # get current user info
        my = self.balances.get(self.node_id)
        if my:
            return {"id": self.node_id, "balance": my.get("balance", 0)}
        else:
            return {"id": self.node_id, "balance": 0}