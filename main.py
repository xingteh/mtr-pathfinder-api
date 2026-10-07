# coding=utf-8
import datetime as dt
import hashlib
import json
import time
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Body
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from mtr_pathfinder import main as find_path
from mtr_pathfinder_v4 import main as find_path_v4

MTR_VER = 4
LINK = "https://letsplay.minecrafttransitrailway.com/system-map"
link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
LOCAL_FILE_PATH_v3 = f'mtr-original-data-{link_hash}-mtr{MTR_VER}-v3.json'
INTERVAL_PATH_v3 = f'mtr-interval-data-{link_hash}-mtr{MTR_VER}-v3.json'
LOCAL_FILE_PATH_v4 = f'mtr-original-data-{link_hash}-mtr{MTR_VER}-v4.json'
INTERVAL_PATH_v4 = f'mtr-interval-data-{link_hash}-mtr{MTR_VER}-v4.json'
MAX_HOUR: int = 3
MAX_WILD_BLOCKS: int = 1500
TRANSFER_ADDITION: dict[str, list[str]] = {"6C779DAE0A5090BF": ["E918CA5DCB128E39", "B250C507ABBCEBF9"]}
WILD_ADDITION: dict[str, list[str]] = {}
ORIGINAL_IGNORED_LINES: list = []

BASE_PATH = "mtr_pathfinder_data"
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
    onlyLines: list[str] = []
    avoidStations: list[str] = []
    mode: str = "DEFAULT"
    includeHiddenRoutes: bool = False
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
    now = datetime.now(dt.UTC)
    return now.hour * 3600 + now.minute * 60 + now.second

@app.post("/")
async def root(raw_data: str = Body(..., media_type="text/plain")):
    directions = []
    try:
        json_data = json.loads(raw_data)
        direction = Direction(**json_data)
        if direction.mode == "REALTIME":
            sec = get_seconds_since_midnight()
            directions = find_path_v4(direction.startStationId, direction.endStationId, LINK, LOCAL_FILE_PATH_v4, INTERVAL_PATH_v4, BASE_PATH,
                                      MAX_WILD_BLOCKS, TRANSFER_ADDITION, WILD_ADDITION, ORIGINAL_IGNORED_LINES,
                                      False, False,
                                      direction.ignoredLines, direction.onlyLines, direction.avoidStations, not direction.noHSR,
                                      not direction.noBoats, direction.enableWalkingWild, direction.onlyLightRail, direction.includeHiddenRoutes,
                                      DETAIL, MAX_HOUR, departure_time=sec)
        else:
            directions = find_path(direction.startStationId, direction.endStationId, LINK, LOCAL_FILE_PATH_v3, INTERVAL_PATH_v3,
                                   MAX_WILD_BLOCKS, TRANSFER_ADDITION, WILD_ADDITION, ORIGINAL_IGNORED_LINES,
                                   False, False, direction.ignoredLines, direction.onlyLines, direction.avoidStations, not direction.noHSR,
                                   not direction.noBoats, direction.enableWalkingWild, direction.onlyLightRail,
                                   direction.mode == "IN_THEORY", direction.includeHiddenRoutes, DETAIL, MTR_VER)
    except:
        pass
    return {
        "code": 200,
        "currentTime": int(time.time() * 1000),
        "text": "OK - pathfinder_api",
        "version": 1,
        "data": {
            "connections": directions,
        }
    }

if __name__ == "__main__":
    #with open("/home/myapp/lps/datas/pathfinder_ignored_lines.txt", 'r', encoding='UTF-8') as f:
    #    ORIGINAL_IGNORED_LINES = [line.rstrip() for line in f]
    uvicorn.run(app="main:app", host="0.0.0.0", port=8194)
