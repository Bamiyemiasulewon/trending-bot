"""
Trading Bot Configuration

This mo# ===== TRADING PARAMETERS =====
# Percentage-based trading configuration
TRADE_PERCENTAGE = {
    'max': Decimal(os.getenv('MAX_TRADE_PERCENTAGE', '0.50')),  # 50% of available balance
    'min': Decimal(os.getenv('MIN_TRADE_PERCENTAGE', '0.35')),  # 35% of available balance
}

# Slippage tolerance (0.01 = 1%)tains all configuration parameters for the trading bot,
including wallet settings, trading parameters, and DEX configurations.
"""
import os
from dotenv import load_dotenv
from typing import Dict, List, Optional, Union
from decimal import Decimal

# Load environment variables
load_dotenv()

# ===== WALLET CONFIGURATION =====
# Number of wallets to use for trading
WALLET_COUNT = int(os.getenv('WALLET_COUNT', '5'))

# Minimum BNB balance required in each wallet (in BNB) - around $0.20 worth
MIN_WALLET_BALANCE = Decimal(os.getenv('MIN_WALLET_BALANCE', '0.0007'))

# Amount of BNB to top up when balance is low (in BNB) - $1 worth
TOP_UP_AMOUNT = Decimal(os.getenv('TOP_UP_AMOUNT', '0.003'))

# Reserve for gas fees (in BNB) - around $0.15 worth
GAS_RESERVE = Decimal(os.getenv('GAS_RESERVE', '0.0005'))

# ===== TRADING CONFIGURATION =====
# Default token to trade (BNB token address)
DEFAULT_TOKEN_ADDRESS = os.getenv('DEFAULT_TOKEN_ADDRESS', '0x32B407ee915432Be6D3F168bc1eFf2a6F8b2034C')  # HODL Token

# Trading pairs (token_address: router_address)
TOKEN_PAIRS = {
    'WBNB': '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',  # Wrapped BNB
    'BUSD': '0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56',  # BUSD
    'USDT': '0x55d398326f99059fF775485246999027B3197955',  # USDT
}

# ===== TRADING PARAMETERS =====
# Buy/Sell amount in BNB (adjusted for $0.77 total balance)
TRADE_AMOUNT = {
    'min': Decimal(os.getenv('MIN_TRADE_AMOUNT', '0.0008')),     # About $0.25 worth
    'max': Decimal(os.getenv('MAX_TRADE_AMOUNT', '0.0012')),    # About $0.35 worth
}

# Slippage tolerance (0.01 = 1%)
SLIPPAGE = Decimal(os.getenv('SLIPPAGE', '0.05'))  # 5% slippage

# Gas price settings (in gwei)
GAS_PRICE = int(os.getenv('GAS_PRICE', '0'))  # 0 for automatic
MAX_GAS_PRICE = int(os.getenv('MAX_GAS_PRICE', '8'))  # Maximum acceptable gas price in gwei
GAS_PRICE_CHECK_INTERVAL = int(os.getenv('GAS_PRICE_CHECK_INTERVAL', '60'))  # Seconds between gas checks

# Gas price alerts (in gwei)
GAS_PRICE_WARNING = int(os.getenv('GAS_PRICE_WARNING', '6'))  # Warning level
GAS_PRICE_CRITICAL = int(os.getenv('GAS_PRICE_CRITICAL', '8'))  # Stop trading level

# ===== TRADING MODES =====
# Enable/disable trading modes
TRADING_MODES = {
    'buy': True,           # Enable buy mode
    'sell': True,          # Enable sell mode
    'massive_buy': False,  # Enable massive buy mode (multiple wallets)
    'gradual_sell': True,  # Enable gradual selling
}

# ===== TIMING CONFIGURATION =====
# Time between trades in seconds (min and max for random selection)
TRADE_INTERVAL = {
    'min': int(os.getenv('MIN_TRADE_INTERVAL', '30')),   # 30 seconds
    'max': int(os.getenv('MAX_TRADE_INTERVAL', '300')),  # 5 minutes
}

# ===== DEX CONFIGURATION =====
# PancakeSwap Router V2
PANCAKE_ROUTER = '0x10ED43C718714eb63d5aA57B78B54704E256024E'

# ===== LOGGING CONFIGURATION =====
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = 'trading_bot.log'

# ===== DEXSCREENER INTEGRATION =====
# DEXScreener API endpoints
DEXSCREENER_API = 'https://api.dexscreener.com/latest/dex'

# Minimum volume and liquidity to consider a token
MIN_VOLUME_24H = Decimal(os.getenv('MIN_VOLUME_24H', '10000'))  # $10,000
MIN_LIQUIDITY = Decimal(os.getenv('MIN_LIQUIDITY', '50000'))     # $50,000

def validate_config() -> bool:
    """Validate the configuration."""
    if not DEFAULT_TOKEN_ADDRESS:
        print("Error: DEFAULT_TOKEN_ADDRESS is not set")
        return False
    
    if WALLET_COUNT < 1:
        print("Error: WALLET_COUNT must be at least 1")
        return False
    
    if TRADE_AMOUNT['min'] <= 0 or TRADE_AMOUNT['max'] <= 0:
        print("Error: Trade amounts must be greater than 0")
        return False
    
    if TRADE_AMOUNT['min'] > TRADE_AMOUNT['max']:
        print("Error: MIN_TRADE_AMOUNT cannot be greater than MAX_TRADE_AMOUNT")
        return False
    
    if SLIPPAGE < 0 or SLIPPAGE > 1:
        print("Error: SLIPPAGE must be between 0 and 1")
        return False
    
    return True
