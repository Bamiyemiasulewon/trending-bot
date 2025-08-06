import time
import uuid
from decimal import Decimal

class PaymentManager:
    def __init__(self):
        self.payments = {}
        self.campaigns = {}
        
    def create_payment(self, user_id, chain, token_address):
        """Create a new payment record"""
        payment_id = str(uuid.uuid4())
        amount = self._get_chain_amount(chain)
        
        payment = {
            'payment_id': payment_id,
            'user_id': user_id,
            'chain': chain,
            'token_address': token_address,
            'amount': amount,
            'status': 'pending',
            'created_at': time.time(),
            'address': self._get_payment_address(chain),
            'expires_at': time.time() + 3600  # 1 hour expiry
        }
        
        self.payments[payment_id] = payment
        return payment
        
    def _get_chain_amount(self, chain):
        """Get payment amount for each chain"""
        amounts = {
            'ETH': Decimal('0.1'),
            'BNB': Decimal('0.5'),
            'SOL': Decimal('5')
        }
        return amounts.get(chain, Decimal('0.1'))
        
    def _get_payment_address(self, chain):
        """Get payment address for each chain"""
        # In production, these would be actual wallet addresses
        addresses = {
            'ETH': '0x1234...5678',
            'BNB': '0x8765...4321',
            'SOL': 'SOL1234...5678'
        }
        return addresses.get(chain, addresses['ETH'])
        
    def verify_payment(self, payment_id):
        """Verify payment status"""
        if payment_id not in self.payments:
            return False
            
        payment = self.payments[payment_id]
        if payment['status'] != 'pending':
            return payment['status'] == 'confirmed'
            
        # Check if payment is expired
        if time.time() > payment['expires_at']:
            payment['status'] = 'expired'
            return False
            
        # In production, check blockchain for payment
        # For demo, simulate random confirmation
        if time.time() - payment['created_at'] > 60:  # Simulate 1-minute confirmation
            payment['status'] = 'confirmed'
            self.start_campaign(payment)
            return True
            
        return False
        
    def start_campaign(self, payment):
        """Start a new campaign after payment confirmation"""
        campaign = {
            'payment_id': payment['payment_id'],
            'user_id': payment['user_id'],
            'token_address': payment['token_address'],
            'chain': payment['chain'],
            'start_time': time.time(),
            'status': 'active',
            'platforms': []  # Will be updated during setup
        }
        
        self.campaigns[payment['token_address']] = campaign
        return campaign
        
    def get_campaign_by_token(self, token_address):
        """Get campaign details by token address"""
        return self.campaigns.get(token_address)
        
    def get_user_payments(self, user_id):
        """Get payment history for a user"""
        return [p for p in self.payments.values() if p['user_id'] == user_id]
        
    def update_token_address(self, payment_id, token_address):
        """Update token address for a payment"""
        if payment_id in self.payments:
            self.payments[payment_id]['token_address'] = token_address
            
    def update_payment_chain(self, payment_id, chain):
        """Update chain for a payment"""
        if payment_id in self.payments:
            old_payment = self.payments[payment_id]
            amount = self._get_chain_amount(chain)
            address = self._get_payment_address(chain)
            
            self.payments[payment_id].update({
                'chain': chain,
                'amount': amount,
                'address': address
            })
