from web3 import Web3
import json
from decimal import Decimal

# Initialize Web3
w3 = Web3(Web3.HTTPProvider('https://bsc.publicnode.com'))

# Transaction hash to analyze
tx_hash = '0x08099c5dcd1d6dca4fea63287391b23f38eafa767d9beb5163a0d68a9c6f14ec'

def analyze_transaction(tx_hash):
    # Get transaction and receipt
    tx = w3.eth.get_transaction(tx_hash)
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    
    # Load PancakeSwap Router ABI for decoding
    with open('pancakeswap_router_abi.json', 'r') as f:
        router_abi = json.load(f)
    
    router = w3.eth.contract(address=tx['to'], abi=router_abi)
    
    # Decode the input data
    try:
        decoded = router.decode_function_input(tx['input'])
        function_name = decoded[0].fn_name
        params = decoded[1]
    except:
        function_name = "Unknown"
        params = {}
    
    # Calculate actual gas cost
    gas_cost = Decimal(receipt['gasUsed']) * Decimal(tx['gasPrice'])
    gas_cost_bnb = w3.from_wei(gas_cost, 'ether')
    
    analysis = {
        'basic_info': {
            'from': tx['from'],
            'to': tx['to'],
            'value': w3.from_wei(tx['value'], 'ether'),
            'gas_price_gwei': w3.from_wei(tx['gasPrice'], 'gwei'),
            'gas_used': receipt['gasUsed'],
            'gas_cost_bnb': float(gas_cost_bnb),
            'status': 'Success' if receipt['status'] == 1 else 'Failed',
            'block_number': tx['blockNumber']
        },
        'function_info': {
            'name': function_name,
            'parameters': params
        },
        'logs': receipt.get('logs', [])
    }
    
    return analysis

# Run analysis
try:
    result = analyze_transaction(tx_hash)
    print("\nTransaction Analysis:")
    print("====================")
    print(f"Status: {result['basic_info']['status']}")
    print(f"From: {result['basic_info']['from']}")
    print(f"To: {result['basic_info']['to']}")
    print(f"Value: {result['basic_info']['value']} BNB")
    print(f"Gas Price: {result['basic_info']['gas_price_gwei']} gwei")
    print(f"Gas Used: {result['basic_info']['gas_used']}")
    print(f"Total Gas Cost: {result['basic_info']['gas_cost_bnb']:.8f} BNB")
    print(f"Function Called: {result['function_info']['name']}")
    print("\nFunction Parameters:")
    for key, value in result['function_info']['parameters'].items():
        print(f"  {key}: {value}")
except Exception as e:
    print(f"Error analyzing transaction: {str(e)}")
