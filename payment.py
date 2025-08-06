
import os
import time
import uuid
import sqlite3
from web3 import Web3
from solana.rpc.api import Client as SolanaClient

class PaymentManager:
    def __init__(self):
        self.eth_address = os.getenv('ETH_WALLET_ADDRESS', '')
        self.bnb_address = os.getenv('BNB_WALLET_ADDRESS', '')
        self.sol_address = os.getenv('SOL_WALLET_ADDRESS', '')
        self.conn = sqlite3.connect('activity.db')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            chain TEXT,
            address TEXT,
            amount REAL,
            tx_hash TEXT,
            status TEXT,
            timestamp TEXT
        )''')
        self.conn.commit()

    def generate_payment_request(self, user_id, chain, amount):
        payment_id = str(uuid.uuid4())
        address = self.get_address(chain)
        timestamp = time.time()
        self.cursor.execute('INSERT INTO payments (id, user_id, chain, address, amount, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (payment_id, user_id, chain, address, amount, 'pending', timestamp))
        self.conn.commit()
        return {
            'payment_id': payment_id,
            'address': address,
            'amount': amount,
            'chain': chain
        }

    def get_address(self, chain):
        if chain == 'ETH':
            return self.eth_address
        elif chain == 'BNB':
            return self.bnb_address
        elif chain == 'SOL':
            return self.sol_address
        else:
            return None

    def verify_payment(self, payment_id, tx_hash, chain, min_confirmations=1):
        # This is a stub. In production, use Alchemy/Infura/QuickNode for real-time monitoring.
        # For ETH/BNB: use Web3 to check tx_hash, amount, confirmations, memo.
        # For SOL: use SolanaClient to check tx_hash, amount, memo.
        # Here, we just mark as confirmed for demo.
        self.cursor.execute('UPDATE payments SET tx_hash=?, status=? WHERE id=?', (tx_hash, 'confirmed', payment_id))
        self.conn.commit()
        return True

    def log_payment(self, user_id, details):
        # Log payment details in payments table
        self.cursor.execute('UPDATE payments SET status=?, tx_hash=? WHERE user_id=? AND id=?',
                            (details.get('status', 'confirmed'), details.get('tx_hash', ''), user_id, details.get('payment_id', '')))
        self.conn.commit()

    def get_payment_status(self, payment_id):
        self.cursor.execute('SELECT status, tx_hash FROM payments WHERE id=?', (payment_id,))
        return self.cursor.fetchone()

    def get_payment_history(self, user_id):
        self.cursor.execute('SELECT * FROM payments WHERE user_id=? ORDER BY timestamp DESC LIMIT 10', (user_id,))
        return self.cursor.fetchall()
