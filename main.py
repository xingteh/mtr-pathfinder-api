# coding=utf-8
import json
import time
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Body
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from mtr_pathfinder_api_v4 import main as find_path

LINK = "https://letsplay.minecrafttransitrailway.com/system-map"
LOCAL_FILE_PATH = 'mtr-station-data-v4.json'
DEP_PATH = 'mtr-route-data-v4.json'
MAX_HOUR: int = 3
MAX_WILD_BLOCKS: int = 1500
TRANSFER_ADDITION: dict[str, list[str]] = {}
WILD_ADDITION: dict[str, list[str]] = {}
ORIGINAL_IGNORED_LINES: list = ["15765905_花越綫|Hana-Koshi Line_[各停]|[Local]", "15765905_花越綫|Hana-Koshi Line_[快速]|[Rapid]", "15765905_花越綫|Hana-Koshi Line_"]
#ORIGINAL_IGNORED_LINES: list = []

# 出发时间（秒，0-86400），默认值为None，即当前时间后10秒
DEP_TIME = None
# 输出的图片中是否显示详细信息（每站的到站、出发时间）
DETAIL: bool = False

class Direction(BaseModel):
    startStationId: str
    endStationId: str
    enableWalkingWild: bool = False
    noHSR: bool = False
    noBoats: bool = False
    onlyLightRail: bool = False
    ignoredLines: list[str] = []
    avoidStations: list[str] = []
    inTheory: bool = False
    startTime: int | None = None

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

origins = [
    "http://localhost:4200"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_seconds_since_midnight():
    now = datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second

@app.post("/")
async def root(raw_data: str = Body(..., media_type="text/plain")):
    directions = []
    sec = get_seconds_since_midnight()
    try:
        json_data = json.loads(raw_data)
        direction = Direction(**json_data)
        directions = find_path(direction.startStationId, direction.endStationId, LINK, LOCAL_FILE_PATH, DEP_PATH,
                  MAX_WILD_BLOCKS, TRANSFER_ADDITION, WILD_ADDITION, ORIGINAL_IGNORED_LINES,
                  False, False,
                  direction.ignoredLines, direction.avoidStations, not direction.noHSR,
                  not direction.noBoats, direction.enableWalkingWild, direction.onlyLightRail,
                  False, MAX_HOUR, departure_time=sec, in_theory=direction.inTheory)
    except:
        pass
    return {
        "code": 200,
        "currentTime": round(time.time() * 1000),
        "text": "OK - pathfinder_api",
        "version": 1,
        "data": {
            "connections": directions,
        }
    }

if __name__ == "__main__":
    uvicorn.run(app="main:app", host="0.0.0.0", port=8194)