import os
import psycopg2
import sqlite3

class Logger:
    def __init__(self):
        # Use SQLite for local, PostgreSQL for production
        self.conn = sqlite3.connect('activity.db')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            activity TEXT,
            details TEXT
        )''')
        self.conn.commit()

    def log(self, activity, details):
        from datetime import datetime
        self.cursor.execute('INSERT INTO logs (timestamp, activity, details) VALUES (?, ?, ?)',
                            (datetime.now().isoformat(), activity, details))
        self.conn.commit()

    def get_report(self):
        self.cursor.execute('SELECT * FROM logs')
        return self.cursor.fetchall()
