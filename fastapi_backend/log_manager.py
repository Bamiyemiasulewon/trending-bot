import sqlite3
import time

class LogManager:
    def __init__(self):
        self.conn = sqlite3.connect('trend_activity.db')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT,
            tx_hash TEXT,
            timestamp REAL,
            amount REAL,
            action TEXT
        )''')
        self.conn.commit()

    def log_activity(self, wallet, tx_hash, timestamp, amount, action):
        self.cursor.execute('INSERT INTO logs (wallet, tx_hash, timestamp, amount, action) VALUES (?, ?, ?, ?, ?)',
                            (wallet, tx_hash, timestamp, amount, action))
        self.conn.commit()

    def get_logs(self, token=None):
        if token:
            self.cursor.execute('SELECT * FROM logs WHERE wallet=?', (token,))
        else:
            self.cursor.execute('SELECT * FROM logs')
        return self.cursor.fetchall()
