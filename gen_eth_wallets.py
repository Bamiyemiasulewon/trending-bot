import json
from eth_account import Account


def generate_eth_wallets(count: int = 34):
    Account.enable_unaudited_hdwallet_features()
    wallets = []
    for _ in range(count):
        acct = Account.create()
        wallets.append({
            "address": acct.address,
            "private_key": acct.key.hex()
        })
    return wallets


def main():
    wallets = generate_eth_wallets()
    with open("eth_wallet.json", "w", encoding="utf-8") as f:
        json.dump(wallets, f, indent=2)
    print(f"Generated {len(wallets)} ETH wallets to eth_wallet.json")


if __name__ == "__main__":
    main()
