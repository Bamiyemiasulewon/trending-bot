# Dexscreener Trending Bot (BNB Chain)

## Setup

1. Install dependencies:
   pip install -r requirements.txt

2. Configure `.env` with your BNB RPC URL and wallet private keys.

3. Run the FastAPI server:
   uvicorn main:app --reload

## API Endpoints

- POST /trend/start
  - Starts trending for a token
  - Body: {
      token_address: str,
      chain: "BNB",
      duration: int (seconds),
      wallets: [private_keys],
      min_gas: int,
      max_gas: int
    }

- POST /trend/stop
  - Stops a running trend
  - Body: { trend_id: str }

- GET /trend/status/{token}
  - Get status for a specific token

- GET /trend/status
  - Get all running/completed trends

## Logging
- All simulated activity is logged in `trend_activity.db`.

## Docker
- Add a Dockerfile for deployment (optional).

## Example Usage
1. Start a trend with POST /trend/start
2. Monitor status/logs
3. Stop trend with POST /trend/stop
