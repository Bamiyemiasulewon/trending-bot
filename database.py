import sqlite3
import json
from datetime import datetime
import os

class Database:
    def __init__(self, db_path='bot.db'):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create campaigns table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS campaigns (
            campaign_id TEXT PRIMARY KEY,
            token_address TEXT,
            chain TEXT,
            user_id INTEGER,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT,
            platforms TEXT,
            engagement_level TEXT,
            is_testnet BOOLEAN,
            payment_status TEXT,
            payment_id TEXT,
            central_wallet_address TEXT,
            central_wallet_funding_status TEXT,
            central_wallet_funding_tx TEXT
        )
        ''')

        # Create wallets table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS wallets (
            wallet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT,
            address TEXT,
            private_key TEXT,
            chain TEXT,
            is_funded BOOLEAN,
            funding_tx_hash TEXT,
            funding_amount REAL,
            funding_time TIMESTAMP,
            funding_status TEXT,
            FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
        )
        ''')

        # Create funding_transactions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS funding_transactions (
            tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT,
            from_address TEXT,
            to_address TEXT,
            amount REAL,
            tx_hash TEXT,
            tx_time TIMESTAMP,
            status TEXT,
            chain TEXT,
            is_central_wallet BOOLEAN,
            FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
        )
        ''')

        conn.commit()
        conn.close()

    def create_campaign(self, campaign_data):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO campaigns (
            campaign_id, token_address, chain, user_id, start_time, 
            status, platforms, engagement_level, is_testnet, payment_status,
            central_wallet_address, central_wallet_funding_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            campaign_data['campaign_id'],
            campaign_data['token_address'],
            campaign_data['chain'],
            campaign_data['user_id'],
            datetime.now(),
            'pending',
            json.dumps(campaign_data['platforms']),
            campaign_data['engagement_level'],
            campaign_data.get('is_testnet', False),
            'pending',
            campaign_data.get('central_wallet_address', ''),
            'pending'
        ))
        
        conn.commit()
        conn.close()

    def add_wallet(self, wallet_data):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO wallets (
            campaign_id, address, private_key, chain, 
            is_funded, funding_status
        ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            wallet_data['campaign_id'],
            wallet_data['address'],
            wallet_data['private_key'],
            wallet_data['chain'],
            False,
            'pending'
        ))
        
        conn.commit()
        conn.close()

    def update_wallet_funding(self, wallet_address, funding_data):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE wallets SET 
            is_funded = ?,
            funding_tx_hash = ?,
            funding_amount = ?,
            funding_time = ?,
            funding_status = ?
        WHERE address = ?
        ''', (
            True,
            funding_data['tx_hash'],
            funding_data['amount'],
            datetime.now(),
            'completed',
            wallet_address
        ))
        
        cursor.execute('''
        INSERT INTO funding_transactions (
            campaign_id, from_address, to_address, amount,
            tx_hash, tx_time, status, chain, is_central_wallet
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            funding_data['campaign_id'],
            funding_data['from_address'],
            wallet_address,
            funding_data['amount'],
            funding_data['tx_hash'],
            datetime.now(),
            'completed',
            funding_data['chain'],
            funding_data.get('is_central_wallet', True)
        ))
        
        conn.commit()
        conn.close()

    def update_campaign_funding(self, campaign_id, funding_data):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE campaigns SET 
            central_wallet_funding_status = ?,
            central_wallet_funding_tx = ?
        WHERE campaign_id = ?
        ''', (
            funding_data['status'],
            funding_data.get('tx_hash', ''),
            campaign_id
        ))
        
        conn.commit()
        conn.close()

    def get_campaign_wallets(self, campaign_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT address, is_funded, funding_status, funding_amount
        FROM wallets
        WHERE campaign_id = ?
        ''', (campaign_id,))
        
        wallets = cursor.fetchall()
        conn.close()
        
        return [
            {
                'address': w[0],
                'is_funded': w[1],
                'status': w[2],
                'amount': w[3]
            }
            for w in wallets
        ]

    def get_campaign_funding_status(self, campaign_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT c.central_wallet_funding_status, c.central_wallet_funding_tx,
               COUNT(w.wallet_id) as total_wallets,
               SUM(CASE WHEN w.is_funded THEN 1 ELSE 0 END) as funded_wallets,
               SUM(w.funding_amount) as total_funded
        FROM campaigns c
        LEFT JOIN wallets w ON c.campaign_id = w.campaign_id
        WHERE c.campaign_id = ?
        GROUP BY c.campaign_id
        ''', (campaign_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'funding_status': result[0],
                'funding_tx': result[1],
                'total_wallets': result[2],
                'funded_wallets': result[3],
                'total_funded': result[4]
            }
        return None

    def get_active_campaigns(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT campaign_id, token_address, chain, user_id, start_time,
               platforms, engagement_level, is_testnet,
               central_wallet_funding_status
        FROM campaigns
        WHERE status = 'active'
        ''')
        
        campaigns = cursor.fetchall()
        conn.close()
        
        return [
            {
                'campaign_id': c[0],
                'token_address': c[1],
                'chain': c[2],
                'user_id': c[3],
                'start_time': c[4],
                'platforms': json.loads(c[5]),
                'engagement_level': c[6],
                'is_testnet': c[7],
                'funding_status': c[8]
            }
            for c in campaigns
        ]
