"""
Test Script for DEX Trading Bot Setup

This script tests the basic functionality of the trading bot setup,
including wallet management and token information retrieval.
"""
import asyncio
import json
import os
from decimal import Decimal
from web3 import Web3
# POA middleware is included by default in web3.py v7 for BSC
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Web3
BSC_RPC = os.getenv('BSC_MAINNET_RPC', 'https://bsc-dataseed.binance.org/')
TOKEN_ADDRESS = os.getenv('DEFAULT_TOKEN_ADDRESS')

# ERC20 ABI (minimal for balance checking)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]

async def test_connection():
    """Test BSC node connection"""
    print("\n=== Testing BSC Node Connection ===")
    w3 = Web3(Web3.HTTPProvider(BSC_RPC))
    
    if w3.is_connected():
        print(f"✅ Connected to BSC Node (Chain ID: {w3.eth.chain_id})")
        print(f"📡 Latest block: {w3.eth.block_number}")
        return w3
    else:
        print("❌ Could not connect to BSC node")
        return None

async def test_token_info(w3):
    """Test token information retrieval"""
    if not TOKEN_ADDRESS:
        print("\n❌ No token address provided in .env")
        return
        
    print(f"\n=== Testing Token Information ===")
    token_contract = w3.eth.contract(
        address=w3.to_checksum_address(TOKEN_ADDRESS),
        abi=ERC20_ABI
    )
    
    try:
        # Get token symbol
        symbol = token_contract.functions.symbol().call()
        decimals = token_contract.functions.decimals().call()
        print(f"✅ Token Info:")
        print(f"   - Symbol: {symbol}")
        print(f"   - Decimals: {decimals}")
        print(f"   - Address: {TOKEN_ADDRESS}")
        return True
    except Exception as e:
        print(f"❌ Error getting token info: {str(e)}")
        return False

async def test_wallet_setup():
    """Test wallet creation and funding"""
    print("\n=== Testing Wallet Setup ===")
    from wallet_manager import WalletManager
    
    try:
        # Initialize wallet manager
        wallet_manager = WalletManager()
        
        # Create a test wallet
        print("Creating test wallet...")
        wallet = wallet_manager.create_bnb_wallet()
        print(f"✅ Created wallet: {wallet['address']}")
        
        # Check wallet balance
        balance = wallet_manager.get_wallet_balance(wallet['address'])
        print(f"   - BNB Balance: {balance} BNB")
        
        # Check token balance
        token_balance = await wallet_manager.get_token_balance(
            wallet['address'],
            TOKEN_ADDRESS
        )
        print(f"   - Token Balance: {token_balance} tokens")
        
        return True
    except Exception as e:
        print(f"❌ Wallet setup test failed: {str(e)}")
        return False

async def main():
    """Run all tests"""
    print("🚀 Starting DEX Trading Bot Setup Test\n")
    
    # Test BSC connection
    w3 = await test_connection()
    if not w3:
        return
    
    # Test token info
    token_ok = await test_token_info(w3)
    if not token_ok:
        print("\n⚠️  Token setup failed. Please check your token address.")
        return
    
    # Test wallet setup
    wallet_ok = await test_wallet_setup()
    if not wallet_ok:
        print("\n⚠️  Wallet setup failed. Please check wallet configuration.")
        return
    
    print("\n✅ All tests completed successfully!")
    print("\nYou can now run the main trading bot with:")
    print("python dex_trader.py")

if __name__ == "__main__":
    asyncio.run(main())
