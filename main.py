# main.py - entry point for USDT LAN wallet.
# handles startup, network/wallet initialization, and REPL loop.

import time
import config
from network import Network
from wallet import Wallet

wallet = None
net = None

def on_message(addr, msg):
    global wallet
    if wallet:
        wallet.on_network_message(addr, msg)

def main():
    global wallet, net
    
    print("="*30)
    print("     USDT LAN wallet")
    print("="*30)
    
    print("starting network and autoscan...")
    
    # start network first
    net = Network(on_message)
    net.start()

    # create wallet bound to local IP
    wallet = Wallet(net)
    wallet.scan_network()
    
    # show initial info
    wallet.show_my_info()
    
    # simplified REPL
    repl()

def repl():
    while True:
        try:
            bal = wallet.my_info().get('balance', 0)
            prompt = f"[{bal} {config.COIN_NAME}]> "
            line = input(prompt).strip()
            if not line:
                continue
            cmd = line.lower()
            
            if cmd == "s" or cmd == "scan":  # scan
                print("scanning network...")
                wallet.scan_network()
                print(f"scan complete. known nodes: {len(wallet.balances)}")
                
            elif cmd == "l" or cmd == "list":  # list
                wallet.show_table()
                
            elif cmd == "m" or cmd == "my":  # my info
                wallet.show_my_info()
                
            elif cmd == "i" or cmd == "im":  # become controller
                wallet.become_controller()
                
            elif cmd.startswith("send "):  # send transaction by IP
                parts = line.split()
                if len(parts) < 3:
                    print("usage: send <to_ip> <amount>")
                    continue
                to_id = parts[1]
                try:
                    amt = int(parts[2])
                except ValueError:
                    print("amount must be integer")
                    continue
                print(f"sending {amt} {config.COIN_NAME} to {to_id}...")
                ok = wallet.send_transaction(to_id, amt)
                print("✓ success" if ok else "✗ failed")
                
            elif cmd == "h":  # help
                print("\ncommands:")
                print(" s    - scan network")
                print(" l    - list balances")
                print(" m    - my info")
                print(" i    - become controller (starts web dashboard)")
                print(" send <to_ip> <amount> - send money")
                print(" q    - quit")
                
            elif cmd in ("quit", "exit", "q"):
                print("goodbye!")
                break
                
            else:
                print("unknown command. Type 'h' for help.")
                
        except KeyboardInterrupt:
            print("\nuse 'quit' to exit")
        except Exception as e:
            print(f"error: {e}")

if __name__ == "__main__":
    main()