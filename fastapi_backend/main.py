from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from dex_trend import DexTrendManager
import os

app = FastAPI()
dex_manager = DexTrendManager()

class TrendRequest(BaseModel):
    token_address: str
    chain: str = "BNB"
    duration: int = 86400  # 24h default
    wallets: List[str]  # private keys
    min_gas: Optional[int] = None
    max_gas: Optional[int] = None

@app.post("/trend/start")
def start_trend(req: TrendRequest, background_tasks: BackgroundTasks):
    try:
        trend_id = dex_manager.start_trend(req, background_tasks)
        return {"status": "started", "trend_id": trend_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/trend/stop")
def stop_trend(trend_id: str):
    dex_manager.stop_trend(trend_id)
    return {"status": "stopped", "trend_id": trend_id}

@app.get("/trend/status/{token}")
def trend_status(token: str):
    return dex_manager.get_status(token)

@app.get("/trend/status")
def all_status():
    return dex_manager.get_all_status()
