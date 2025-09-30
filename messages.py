# this file defines message type constants used for communication and coordination

# discovery
MSG_DISCOVERY = "DISCOVERY"
MSG_DISCOVERY_RESPONSE = "DISCOVERY_RESP"

# table sync (optional)
MSG_TABLE_REQUEST = "TABLE_REQ"
MSG_TABLE_RESPONSE = "TABLE_RESP"

# global lock
MSG_LOCK = "LOCK"
MSG_UNLOCK = "UNLOCK"

# transactions
MSG_TRANSACTION = "TX"           # {tx:{tx_id,from,to,amount,ts}}
MSG_CONFIRM = "TX_CONFIRM"       # {tx_id, balances_snapshot}
MSG_ROLLBACK = "TX_ROLLBACK"     # {tx_id} initiator sends if not majority

# controller audit (not affects consensus)
MSG_CTRL_NOTIFY = "CTRL_NOTIFY"      # submit balances to controller
MSG_CTRL_ANNOUNCE = "CTRL_ANNOUNCE"  # announce controller presence