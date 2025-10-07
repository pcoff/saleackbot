import sqlite3
from typing import List, Tuple

class Database:
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        
        # Create accounts table
        c.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                details TEXT NOT NULL,
                price REAL NOT NULL,
                available BOOLEAN DEFAULT TRUE
            )
        ''')
        # Create credentials table (multiple credentials per account/lot)
        c.execute('''
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                details TEXT NOT NULL,
                sold BOOLEAN DEFAULT FALSE,
                sold_at DATETIME,
                sold_to INTEGER,
                FOREIGN KEY (account_id) REFERENCES accounts (id)
            )
        ''')
        
        # Create orders table
        c.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                credential_id INTEGER,
                price REAL NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts (id),
                FOREIGN KEY (credential_id) REFERENCES credentials (id)
            )
        ''')
        
        # Create gift_requests table
        c.execute('''
            CREATE TABLE IF NOT EXISTS gift_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                links TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                processed_at DATETIME,
                processed_by INTEGER
            )
        ''')
        
        # Create gifts table
        c.execute('''
            CREATE TABLE IF NOT EXISTS gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_type TEXT NOT NULL,
                content TEXT NOT NULL,
                file_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create purchase_queue table
        c.execute('''
            CREATE TABLE IF NOT EXISTS purchase_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                payment_type TEXT NOT NULL,
                price_usdt REAL NOT NULL,
                price_rub INTEGER,
                username TEXT,
                invoice_id TEXT,
                payment_status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts (id)
            )
        ''')
        
        conn.commit()
        conn.close()

    def add_account(self, details: str, price: float) -> int:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('INSERT INTO accounts (details, price) VALUES (?, ?)', (details, price))
        account_id = c.lastrowid
        conn.commit()
        conn.close()
        return account_id

    def get_available_accounts(self) -> List[Tuple[int, str, float]]:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT id, details, price FROM accounts WHERE available = TRUE')
        accounts = c.fetchall()
        conn.close()
        return accounts

    def get_account(self, account_id: int) -> Tuple[int, str, float, bool]:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT id, details, price, available FROM accounts WHERE id = ?', (account_id,))
        account = c.fetchone()
        conn.close()
        return account

    def add_credential(self, account_id: int, details: str) -> int:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('INSERT INTO credentials (account_id, details) VALUES (?, ?)', (account_id, details))
        credential_id = c.lastrowid
        # Ensure account is marked available when it has at least one unsold credential
        c.execute('UPDATE accounts SET available = TRUE WHERE id = ?', (account_id,))
        conn.commit()
        conn.close()
        return credential_id

    def count_available_credentials(self, account_id: int) -> int:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ? AND sold = FALSE', (account_id,))
        (count,) = c.fetchone()
        conn.close()
        return int(count)

    def pop_next_credential(self, account_id: int, user_id: int) -> Tuple[int, str]:
        """Atomically pick the next unsold credential, mark it sold, and return (credential_id, details)."""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        try:
            c.execute('BEGIN IMMEDIATE')
            c.execute('SELECT id, details FROM credentials WHERE account_id = ? AND sold = FALSE ORDER BY id LIMIT 1', (account_id,))
            row = c.fetchone()
            if not row:
                conn.rollback()
                return (0, '')
            credential_id, details = row
            c.execute('UPDATE credentials SET sold = TRUE, sold_at = CURRENT_TIMESTAMP, sold_to = ? WHERE id = ?', (user_id, credential_id))
            # If no more credentials left, mark account unavailable
            c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ? AND sold = FALSE', (account_id,))
            (remaining,) = c.fetchone()
            if remaining == 0:
                c.execute('UPDATE accounts SET available = FALSE WHERE id = ?', (account_id,))
            conn.commit()
            return (credential_id, details)
        except sqlite3.Error:
            conn.rollback()
            return (0, '')
        finally:
            conn.close()

    def update_account_price(self, account_id: int, new_price: float) -> bool:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('UPDATE accounts SET price = ? WHERE id = ?', (new_price, account_id))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_account(self, account_id: int) -> bool:
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def mark_account_sold(self, account_id: int, user_id: int, price: float) -> Tuple[bool, str, bool]:
        """Pick and mark one credential as sold; return (success, details, accounts_depleted)."""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        try:
            c.execute('BEGIN IMMEDIATE')
            c.execute('SELECT id, details FROM credentials WHERE account_id = ? AND sold = FALSE ORDER BY id LIMIT 1', (account_id,))
            row = c.fetchone()
            if not row:
                conn.rollback()
                return (False, '', False)
            credential_id, details = row
            c.execute('UPDATE credentials SET sold = TRUE, sold_at = CURRENT_TIMESTAMP, sold_to = ? WHERE id = ?', (user_id, credential_id))
            c.execute('INSERT INTO orders (user_id, account_id, credential_id, price) VALUES (?, ?, ?, ?)',
                      (user_id, account_id, credential_id, price))
            # If no more credentials left, mark account unavailable
            c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ? AND sold = FALSE', (account_id,))
            (remaining,) = c.fetchone()
            accounts_depleted = False
            if remaining == 0:
                c.execute('UPDATE accounts SET available = FALSE WHERE id = ?', (account_id,))
                accounts_depleted = True
            conn.commit()
            return (True, details, accounts_depleted)
        except sqlite3.Error:
            conn.rollback()
            return (False, '', False)
        finally:
            conn.close()
    
    def get_lot_statistics(self, account_id: int) -> dict:
        """Get statistics for a specific lot"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        
        # Get account info
        c.execute('SELECT id, details, price, available FROM accounts WHERE id = ?', (account_id,))
        account = c.fetchone()
        
        if not account:
            conn.close()
            return {}
        
        # Count total, sold and available credentials
        c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ?', (account_id,))
        total_count = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ? AND sold = TRUE', (account_id,))
        sold_count = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ? AND sold = FALSE', (account_id,))
        available_count = c.fetchone()[0]
        
        # Get total revenue
        c.execute('SELECT SUM(price) FROM orders WHERE account_id = ?', (account_id,))
        revenue_result = c.fetchone()[0]
        total_revenue = revenue_result if revenue_result else 0
        
        conn.close()
        
        return {
            'id': account[0],
            'name': account[1],
            'price': account[2],
            'available': account[3],
            'total_logs': total_count,
            'sold_logs': sold_count,
            'available_logs': available_count,
            'total_revenue': total_revenue
        }
    
    def get_all_lots_statistics(self) -> list:
        """Get statistics for all lots"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        
        c.execute('SELECT id FROM accounts ORDER BY id')
        account_ids = [row[0] for row in c.fetchall()]
        conn.close()
        
        statistics = []
        for account_id in account_ids:
            stats = self.get_lot_statistics(account_id)
            if stats:
                statistics.append(stats)
        
        return statistics
    
    def create_gift_request(self, user_id: int, username: str, links: str) -> int:
        """Create a new gift request"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('INSERT INTO gift_requests (user_id, username, links) VALUES (?, ?, ?)',
                  (user_id, username, links))
        request_id = c.lastrowid
        conn.commit()
        conn.close()
        return request_id
    
    def get_pending_gift_requests(self) -> List[Tuple]:
        """Get all pending gift requests"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT id, user_id, username, links, created_at FROM gift_requests WHERE status = "pending" ORDER BY created_at')
        requests = c.fetchall()
        conn.close()
        return requests
    
    def get_gift_request(self, request_id: int) -> Tuple:
        """Get specific gift request"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT id, user_id, username, links, created_at FROM gift_requests WHERE id = ?', (request_id,))
        request = c.fetchone()
        conn.close()
        return request
    
    def process_gift_request(self, request_id: int, status: str, processed_by: int) -> bool:
        """Process gift request (approve/reject)"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('UPDATE gift_requests SET status = ?, processed_at = CURRENT_TIMESTAMP, processed_by = ? WHERE id = ?',
                  (status, processed_by, request_id))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def save_gift(self, gift_type: str, content: str, file_id: str = None) -> int:
        """Save gift content"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        # Delete previous gift
        c.execute('DELETE FROM gifts')
        # Save new gift
        c.execute('INSERT INTO gifts (gift_type, content, file_id) VALUES (?, ?, ?)',
                  (gift_type, content, file_id))
        gift_id = c.lastrowid
        conn.commit()
        conn.close()
        return gift_id
    
    def get_current_gift(self) -> Tuple:
        """Get current gift"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT gift_type, content, file_id FROM gifts ORDER BY created_at DESC LIMIT 1')
        gift = c.fetchone()
        conn.close()
        return gift
    
    def add_to_purchase_queue(self, user_id: int, account_id: int, payment_type: str, 
                             price_usdt: float, price_rub: int = None, username: str = None, 
                             invoice_id: str = None, payment_status: str = 'pending') -> int:
        """Add user to purchase queue for lot with 0 accounts"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('''
            INSERT INTO purchase_queue 
            (user_id, account_id, payment_type, price_usdt, price_rub, username, invoice_id, payment_status) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, account_id, payment_type, price_usdt, price_rub, username, invoice_id, payment_status))
        queue_id = c.lastrowid
        conn.commit()
        conn.close()
        return queue_id
    
    def get_queue_size(self, account_id: int) -> int:
        """Get number of people in queue for specific lot"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM purchase_queue WHERE account_id = ? AND payment_status IN ("pending", "paid")', 
                  (account_id,))
        (count,) = c.fetchone()
        conn.close()
        return int(count)
    
    def get_next_from_queue(self, account_id: int) -> Tuple:
        """Get next person from queue for specific lot"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('''
            SELECT id, user_id, payment_type, price_usdt, price_rub, username, invoice_id 
            FROM purchase_queue 
            WHERE account_id = ? AND payment_status = "pending" 
            ORDER BY created_at 
            LIMIT 1
        ''', (account_id,))
        queue_entry = c.fetchone()
        conn.close()
        return queue_entry
    
    def mark_queue_entry_fulfilled(self, queue_id: int) -> bool:
        """Mark queue entry as fulfilled"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('UPDATE purchase_queue SET payment_status = "fulfilled" WHERE id = ?', (queue_id,))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def update_queue_payment_status(self, user_id: int, account_id: int, invoice_id: str, status: str) -> bool:
        """Update payment status in queue"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute('''
            UPDATE purchase_queue 
            SET payment_status = ? 
            WHERE user_id = ? AND account_id = ? AND invoice_id = ?
        ''', (status, user_id, account_id, invoice_id))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def process_queue_for_lot(self, account_id: int) -> List[Tuple]:
        """Process queue when new credentials are added to lot"""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        
        # Get available credentials count
        c.execute('SELECT COUNT(*) FROM credentials WHERE account_id = ? AND sold = FALSE', (account_id,))
        (available_count,) = c.fetchone()
        
        if available_count == 0:
            conn.close()
            return []
        
        # Get pending queue entries (paid ones first)
        c.execute('''
            SELECT id, user_id, payment_type, price_usdt, price_rub, username, invoice_id, payment_status
            FROM purchase_queue 
            WHERE account_id = ? AND payment_status IN ("paid", "pending") 
            ORDER BY 
                CASE WHEN payment_status = "paid" THEN 0 ELSE 1 END,
                created_at
            LIMIT ?
        ''', (account_id, available_count))
        
        queue_entries = c.fetchall()
        conn.close()
        
        return queue_entries
