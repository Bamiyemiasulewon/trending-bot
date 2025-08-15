import json
from solders.keypair import Keypair
import base58


def generate_sol_wallets(count: int = 34):
    wallets = []
    for _ in range(count):
        kp = Keypair()
        address = str(kp.pubkey())
        # secret() returns 64-byte seed+pubkey; encode base58
        secret_b58 = base58.b58encode(kp.secret()).decode()
        wallets.append({
            "address": address,
            "private_key": secret_b58
        })
    return wallets


def main():
    wallets = generate_sol_wallets()
    with open("sol_wallet.json", "w", encoding="utf-8") as f:
        json.dump(wallets, f, indent=2)
    print(f"Generated {len(wallets)} SOL wallets to sol_wallet.json")


if __name__ == "__main__":
    main()
