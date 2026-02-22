# coding=utf-8
import json
import os
from datetime import datetime, timedelta, timezone
from time import strftime, gmtime

from mtr_pathfinder_v4 import MAX_INT, fetch_data, gen_departure, RouteType, gen_timetable, load_tt, CSA


def get_timestamp_from_seconds(seconds, base_datetime=None):
    if base_datetime is None:
        today = datetime.now().date()
        base_datetime = datetime.combine(today, datetime.min.time())

    target_datetime = base_datetime + timedelta(seconds=seconds)

    return round(target_datetime.timestamp() * 1000)


def find_shortest_sublist_indices(lst, a, b):
    last_a = None
    best = None  # (length, i, j)

    for j, x in enumerate(lst):
        if x == a:
            last_a = j
        elif x == b and last_a is not None:
            length = j - last_a
            if best is None or length < best[0]:
                best = (length, last_a, j)

    if best is None:
        return None

    _, i, j = best
    return i, j

def station_num_to_id(data: dict, sta: str) -> str:
    '''
    Convert one station's code (str of base-10 int) to its ID.
    '''
    sta = hex(int(sta))[2:]
    for station in data['stations'].values():
        if station['station'] == sta:
            return station['id']

def process_path(result: list[tuple], start: str, end: str,
                 trips: dict[str, dict[str, int]], data: dict, detail: bool) -> list[str, int, int, int, list]:
    '''
    Process the path, change it into ~~human~~ system map readable form.
    '''
    if not (start and end):
        return None, None, None, None, None

    if start == end:
        return None, None, None, None, None

    path: list[tuple] = []
    last_detail: tuple = None
    route_new = []
    low_i = MAX_INT
    for i in range(len(result) - 1, -1, -1):
        if i >= low_i:
            continue

        new_leg = result[i]
        if len(new_leg) < 6:
            route_new.append(new_leg)
            continue

        trip = trips[str(new_leg[5])]
        for j in range(i - 1, -1, -1):
            trip_index = str(result[j][0])
            if trip_index not in trip:
                continue

            if trip[trip_index] >= result[j][2]:
                new_leg = [trip_index, new_leg[1], trip[trip_index],
                           new_leg[3], new_leg[4], new_leg[5]]
                low_i = j

        route_new.append(new_leg)

    route_new.reverse()
    for con in route_new:
        if con[4] != last_detail or detail is True:
            path.append(con)
        else:
            last_con = path[-1]
            last_con[3] = con[3]
            last_con[1] = con[1]

        last_detail = con[4]

    every_route_time = []
    last_x = None
    last_z = None
    for x in path:
        station_1 = station_num_to_id(data, x[0])
        station_2 = station_num_to_id(data, x[1])
        route = x[4][0]
        timestamp = get_timestamp_from_seconds(x[2])

        if route in data['routes']:
            # EVERYTHING IS FOR THIS UGLY FORMAT
            route_stations = []
            route_details = []
            for route_current in data['routes'][route]['stations']:
                route_stations.append(route_current['id'])
                route_details.append(route_current)
            indices = find_shortest_sublist_indices(route_stations, station_1, station_2)
            route_stations = route_stations[indices[0]:indices[-1]+1]
            route_details = route_details[indices[0]:indices[-1]+1]
            if len(every_route_time) > 0:
                # Go to the platform first
                if every_route_time[-1]['routeId'] == "":
                    every_route_time[-1]['endPlatformName'] = route_details[0]['name']
                elif every_route_time[-1]['endPlatformName'] != route_details[0]['name'] or (last_x and last_z and (route_details[0]['x'] != last_x or route_details[0]['z'] != last_z)):
                    every_route_time.append({
                        "routeId": "",
                        "startStationId": every_route_time[-1]['endStationId'],
                        "endStationId": route_stations[0],
                        "startPlatformName": every_route_time[-1]['endPlatformName'],
                        "endPlatformName": route_details[0]['name'],
                        "startTime": every_route_time[-1]['endTime'],
                        "endTime": timestamp,
                        "walkingDistance": 0,
                    })
            for y in range(len(route_stations)-1):
                # Intermediate stations
                every_route_time.append({
                    "routeId": route,
                    "startStationId": route_stations[y],
                    "endStationId": route_stations[y+1],
                    "startPlatformName": route_details[y]['name'],
                    "endPlatformName": route_details[y+1]['name'],
                    "startTime": timestamp,
                    "endTime": timestamp,
                    "walkingDistance": 0,
                })
            every_route_time[-1]['endTime'] = get_timestamp_from_seconds(x[3])
            last_x = route_details[-1]['x']
            last_z = route_details[-1]['z']
        else:
            if route[:4] == "出站换乘":
                distance = route[12:-1]
            else:
                distance = route[8:-1]
            every_route_time.append({
                "routeId": "",
                "startStationId": station_1,
                "endStationId": station_2,
                "startPlatformName": every_route_time[-1]['endPlatformName'] if len(every_route_time) > 0 else "",
                "endPlatformName": "",
                "startTime": timestamp,
                "endTime": get_timestamp_from_seconds(x[3]),
                "walkingDistance": round(distance)}
            )
            last_x = None
            last_z = None

    if len(every_route_time) > 0:
        # Go to destination at last
        every_route_time[-1]['endStationId'] = ""
        # Go to the station first
        every_route_time.insert(0, {
            "routeId": "",
            "startStationId": "",
            "endStationId": every_route_time[0]['startStationId'],
            "startPlatformName": "",
            "endPlatformName": every_route_time[0]['startPlatformName'],
            "startTime": every_route_time[0]["startTime"],
            "endTime": every_route_time[0]["startTime"],
            "walkingDistance": 0,
        })

    return every_route_time

def main(station1: str, station2: str, LINK: str,
         LOCAL_FILE_PATH, DEP_PATH, MAX_WILD_BLOCKS: int = 1500,
         TRANSFER_ADDITION: dict[str, list[str]] = {},
         WILD_ADDITION: dict[str, list[str]] = {},
         ORIGINAL_IGNORED_LINES: list = [], UPDATE_DATA: bool = False,
         GEN_DEPARTURE: bool = False, IGNORED_LINES: list = [],
         AVOID_STATIONS: list = [],
         CALCULATE_HIGH_SPEED: bool = True, CALCULATE_BOAT: bool = True,
         CALCULATE_WALKING_WILD: bool = False, ONLY_LRT: bool = False,
         DETAIL: bool = False, MAX_HOUR=3, timetable=None, departure_time=None, tz=0,
         timeout_min=2, in_theory=False) -> dict:
    '''
    Main function. You can call it in your own code.
    Output:
    False -- Route not found 找不到路线
    None -- Incorrect station name(s) 车站输入错误，请重新输入
    else 其他 -- base64 str of the generated image 生成图片的 base64 字符串
    '''
    if departure_time is None:
        dtz = timezone(timedelta(hours=tz))
        t1 = datetime.now().replace(year=1970, month=1, day=1)
        try:
            t1 = t1.astimezone(dtz).replace(tzinfo=timezone.utc)
        except OSError:
            t1 = t1.replace(tzinfo=timezone.utc)

        departure_time = round(t1.timestamp())
        departure_time += 10  # 寻路时间

    departure_time %= 86400

    if LINK.endswith('/index.html'):
        LINK = LINK.rstrip('/index.html')

    if UPDATE_DATA is True or (not os.path.exists(LOCAL_FILE_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        data = fetch_data(LINK, LOCAL_FILE_PATH, MAX_WILD_BLOCKS)
    else:
        with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
            data = json.load(f)

    new_ignored_lines = []
    new_origin_ignored_lines = []
    for route_id, route in data['routes'].items():
        route_key = f"{route['color']}_{route['name'].split('||')[0]}_{route['number']}"
        if route_key in IGNORED_LINES or (route['hidden'] and not in_theory):
            new_ignored_lines.append(route_id)
        if route_key in ORIGINAL_IGNORED_LINES or (route['hidden'] and not in_theory):
            new_origin_ignored_lines.append(route_id)
    new_ignored_lines += new_origin_ignored_lines

    if GEN_DEPARTURE is True or (not os.path.exists(DEP_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        gen_departure(LINK, DEP_PATH)

    version1 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(LOCAL_FILE_PATH)))
    version2 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(DEP_PATH)))

    route_type = RouteType.REAL_TIME
    if timetable is None:
        timetable = gen_timetable(
            data, new_ignored_lines, CALCULATE_HIGH_SPEED, CALCULATE_BOAT,
            CALCULATE_WALKING_WILD, ONLY_LRT, AVOID_STATIONS, route_type,
            new_origin_ignored_lines, DEP_PATH, version1, version2,
            WILD_ADDITION, TRANSFER_ADDITION)

    tt, trips = load_tt(timetable, data, station1, station2, departure_time,
                        DEP_PATH, TRANSFER_ADDITION,
                        CALCULATE_WALKING_WILD, WILD_ADDITION, MAX_HOUR)

    csa = CSA(len(data['stations']), tt, timeout_min)
    if station1 is None or station2 is None:
        return []

    station1 = int('0x' + data['stations'][station1]['station'], 16)
    station2 = int('0x' + data['stations'][station2]['station'], 16)
    result = csa.compute(station1, station2, departure_time)
    if not result:
        return []

    ert = process_path(result, station1, station2, trips,
                       data, DETAIL)

    return ert

