'''
Find paths between two stations for Minecraft Transit Railway.
'''

from array import array
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import gcd, sqrt
from operator import itemgetter
from time import gmtime, strftime, time
from typing import Optional, Union
import hashlib
import json
import mmap
import os
import pickle
import re

import datetime as dt
import requests

CACHE_VERSION = '130'
MAX_INT = 2 ** 64 - 1

RUNNING_SPEED: float = 5.612          # 站内换乘速度，单位 block/s
TRANSFER_SPEED: float = 4.317         # 出站换乘速度，单位 block/s
WILD_WALKING_SPEED: float = 2.25      # 非出站换乘（越野）速度，单位 block/s


# From https://github.com/trainline-eu/csa-challenge/blob/2aa0fa55e466692d404d87aa2dcaf5b83bca5920/csa.py
# class Connection:
#     def __init__(self, departure_station_id: str, arrival_station_id: str,
#                  departure_timestamp: int, arrival_timestamp: int,
#                  route_detail):
#         self[0] = int('0x' + departure_station_id, 16)
#         self[1] = int('0x' + arrival_station_id, 16)
#         self[2] = departure_timestamp
#         self[3] = arrival_timestamp
#         self.riding_time = arrival_timestamp - departure_timestamp
#         self[4] = route_detail
#         self.station_count = 1

#     def __str__(self):
#         return ' '.join([str(x) for x in
#                          (self[4],
#                           self[0], self[1],
#                           self[2], self[3],
#                           self.station_count)])


# From https://github.com/trainline-eu/csa-challenge/blob/2aa0fa55e466692d404d87aa2dcaf5b83bca5920/csa.py and https://ljn.io/posts/connection-scan-algorithm-with-interchange-time
class CSA:
    def __init__(self, max_stations, connections: list[tuple], timeout_min=2):
        self.in_connection = array('L')
        self.earliest_arrival = array('L')
        self.max_stations = max_stations
        self.connections: list[tuple] = connections
        self.timeout_min = timeout_min

    def main_loop(self, arrival_station):
        earliest = MAX_INT
        for i, c in enumerate(self.connections):
            if c[2] >= self.earliest_arrival[c[0]] and c[3] < self.earliest_arrival[c[1]]:
                self.earliest_arrival[c[1]] = c[3]
                self.in_connection[c[1]] = i

                if c[1] == arrival_station:
                    earliest = min(earliest, c[3])

            elif c[2] >= earliest:
                return

            if i % 20000 == 0:
                if time() > self.start_time + 60 * self.timeout_min:
                    raise TimeoutError('Pathfinding timeout')

    def find_path(self, arrival_station):
        route = []
        if self.in_connection[arrival_station] != MAX_INT:
            last_connection_index = self.in_connection[arrival_station]
            while last_connection_index != MAX_INT:
                connection = self.connections[last_connection_index]
                route.append(connection)
                last_connection_index = self.in_connection[connection[0]]

            route.reverse()

        return route

    def compute(self, departure_station, arrival_station, departure_time) -> list[tuple]:
        self.in_connection = array('Q', [MAX_INT for _ in range(self.max_stations)])
        self.earliest_arrival = array('Q', [MAX_INT for _ in range(self.max_stations)])
        self.earliest_arrival[departure_station] = departure_time

        if departure_station <= self.max_stations and arrival_station <= self.max_stations:
            self.start_time = time()
            self.main_loop(arrival_station)

        return self.find_path(arrival_station)


class RouteType(Enum):
    '''
    An Enum class to define the types of the route.
    '''
    IN_THEORY = 0
    WAITING = 1
    REAL_TIME = 2


class ImagePattern(Enum):
    '''
    An Enum class to define the patterns of the image.
    Number -> x offset
    THUMB -> need to -20
    '''
    OR = 0
    FAKE_STATION = 1
    TEXT = 40.2
    STATION = 40  # 圆圈 + 黑体字 -> 车站
    THUMB_TEXT = 60  # 路线种类图标 + 灰字 -> 路线名
    THUMB_INDENT_TEXT = 80
    GREY_TEXT = 40.1
    GREY_INDENT_TEXT = 60.1


def round_ten(n: float) -> int:
    '''
    Round the number in ten.
    '''
    ans = round(n / 10) * 10
    return ans if ans > 0 else 0


def atoi(text: str) -> Union[str, int]:
    '''
    Convert a string to a digit.
    '''
    return int(text) if text.isdigit() else text


def natural_keys(text: str) -> list:
    '''
    A sorting key in number order.
    '''
    return [atoi(c) for c in re.split(r'(\d+)', text)]


def lcm(a: int, b: int) -> int:
    '''
    Calculate LCM of two integers.
    '''
    return a * b // gcd(a, b)


def get_distance(a_dict: dict, b_dict: dict, square: bool = False) -> float:
    '''
    Get the distance of two stations.
    '''
    dist_square = (a_dict['x'] - b_dict['x']) ** 2 + \
        (a_dict['z'] - b_dict['z']) ** 2
    if square is True:
        return dist_square

    return sqrt(dist_square)


def fetch_data(link: str, LOCAL_FILE_PATH, MAX_WILD_BLOCKS,
               need_input=True) -> dict:
    '''
    Fetch all the route data and station data.
    '''
    link = link.rstrip('/') + '/mtr/api/map/stations-and-routes?dimension=0'
    data = requests.get(link).json()['data']
    data_new = {'stations': {}, 'routes': {},
                'station_coords': {}, 'station_routes': {},
                'transfer_time': {}, 'transfer_dist': {}}
    for d in data['routes']:
        data_new['routes'][d['id']] = d
        lengths = []
        last_x = None
        for x in d['stations']:
            if x['id'] in data_new['station_routes']:
                data_new['station_routes'][x['id']] += [d['id']]
            else:
                data_new['station_routes'][x['id']] = [d['id']]

            if last_x is not None:
                x1 = last_x['x']
                y1 = last_x['y']
                z1 = last_x['z']
                x2 = x['x']
                y2 = x['y']
                z2 = x['z']
                lengths.append(((x1 - x2) ** 2 + (y1 - y2) ** 2 +
                                (z1 - z2) ** 2) ** 0.5)

            last_x = x

        data_new['routes'][d['id']]['lengths'] = lengths

    i = 0
    for d in data['stations']:
        if d['id'] not in data_new['station_routes']:
            continue

        d['station'] = hex(i)[2:]
        data_new['stations'][d['id']] = d
        i += 1

    x_dict = {x['id']: [] for x in data['stations']}
    y_dict = {x['id']: [] for x in data['stations']}
    z_dict = {x['id']: [] for x in data['stations']}
    for route in data['routes']:
        for station in route['stations']:
            x_dict[station['id']] += [station['x']]
            y_dict[station['id']] += [station['y']]
            z_dict[station['id']] += [station['z']]

    for station in data['stations']:
        x_list = x_dict[station['id']]
        y_list = y_dict[station['id']]
        z_list = z_dict[station['id']]
        if len(x_list) == 0:
            continue

        data_new['station_coords'][station['id']] = \
            {'x': sum(x_list) / len(x_list),
             'y': sum(y_list) / len(y_list),
             'z': sum(z_list) / len(z_list)}

    for x, dict1 in data_new['station_coords'].items():
        for y, dict2 in data_new['station_coords'].items():
            if x == y:
                continue

            distance = get_distance(dict1, dict2)

            if x == y:
                speed = RUNNING_SPEED
            elif x in data_new['stations'][y]['connections'] or \
                    y in data_new['stations'][x]['connections']:
                speed = TRANSFER_SPEED
            else:
                speed = WILD_WALKING_SPEED
                if distance > MAX_WILD_BLOCKS:
                    continue

                if abs(dict1['x'] - dict2['x']) > MAX_WILD_BLOCKS or \
                        abs(dict1['z'] - dict2['z']) > MAX_WILD_BLOCKS:
                    continue

            time = distance / speed
            if x not in data_new['transfer_time']:
                data_new['transfer_time'][x] = {}

            data_new['transfer_time'][x][y] = time

            if x not in data_new['transfer_dist']:
                data_new['transfer_dist'][x] = {}

            data_new['transfer_dist'][x][y] = distance

    with open(LOCAL_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data_new, f)

    return data_new


def gen_departure(link: str, DEP_PATH, need_input=True) -> None:
    '''
    Download the departures.
    '''
    link = link.rstrip('/') + '/mtr/api/map/departures?dimension=0'
    data = requests.get(link).json()['data']
    departures = data['departures']
    offset = data['cachedResponseTime']
    dep_dict: dict[str, list[int]] = {}
    for x in departures:
        dep_list = set()
        for y in x['departures']:
            # deviation = y['deviation']
            for z in y['departures']:
                # dep = round((z + offset % 86400000 + deviation) / 1000)
                dep = round((z + offset % 86400000) / 1000) % 86400
                dep_list.add(dep)

        dep_list = list(sorted(dep_list))
        dep_dict[x['id']] = dep_list

    with open(DEP_PATH, 'w', encoding='utf-8') as f:
        json.dump(dep_dict, f)


def station_num_to_id(data: dict, sta: str) -> str:
    '''
    Convert one station's code (str of base-10 int) to its name.
    '''
    sta = hex(int(sta))[2:]
    for station in data['stations'].values():
        if station['station'] == sta:
            return station['id']


def sta_id(station: str) -> int:
    return int('0x' + station, 16)


def gen_timetable(data: dict, IGNORED_LINES: list[str], ONLY_LINES: list[str],
                  CALCULATE_HIGH_SPEED: bool, CALCULATE_BOAT: bool,
                  CALCULATE_WALKING_WILD: bool, ONLY_LRT: bool,
                  AVOID_STATIONS: list, route_type: RouteType,
                  original_ignored_lines: list[str], DEP_PATH: str,
                  version1: str, version2: str,
                  WILD_ADDITION, TRANSFER_ADDITION
                  ) -> dict[str, list[tuple]]:
    '''
    Generate the timetable of all routes.
    '''
    with open(DEP_PATH, 'r', encoding='utf-8') as f:
        dep_data: dict[str, list[int]] = json.load(f)

    filename = ''
    m = hashlib.md5()
    if IGNORED_LINES == original_ignored_lines and \
            CALCULATE_BOAT is True and ONLY_LRT is False and \
            AVOID_STATIONS == [] and ONLY_LINES == [] and \
            route_type == RouteType.REAL_TIME:
        for s in original_ignored_lines:
            m.update(s.encode('utf-8'))

        filename = f'mtr_pathfinder_temp{os.sep}' + \
            f'4{int(CALCULATE_HIGH_SPEED)}{int(CALCULATE_WALKING_WILD)}' + \
            f'-{version1}-{version2}-{m.hexdigest()}-{CACHE_VERSION}.dat'
        if os.path.exists(filename):
            with open(filename, 'r+b') as f:
                mmapped_file = mmap.mmap(f.fileno(), 0)
                tt_dict = pickle.load(mmapped_file)

            return tt_dict

    # 添加普通路线
    tt_dict = {}
    for route_id in dep_data.keys():
        if route_id not in data['routes']:
            continue

        route = data['routes'][route_id]
        # 禁路线
        if route_id in IGNORED_LINES:
            continue

        if (not CALCULATE_HIGH_SPEED) and route['type'] == 'train_high_speed':
            continue

        if (not CALCULATE_BOAT) and 'boat' in route['type']:
            continue

        if ONLY_LRT and route['type'] != 'train_light_rail':
            continue

        durations = route['durations']
        if durations == []:
            continue

        station_ids = [data['stations'][x['id']]['station']
                       for x in route['stations']]
        if len(station_ids) - 1 < len(durations):
            durations = durations[:len(station_ids) - 1]

        if len(station_ids) - 1 > len(durations):
            continue

        real_ids = [x['id'] for x in route['stations']]
        platforms = [x['name'] for x in route['stations']]
        dwells = [x['dwellTime'] for x in route['stations']]
        if len(dwells) > 0:
            dep = -round(dwells[-1] / 1000)
        else:
            dep = 0

        tt = []
        for i in range(len(station_ids) - 1, 0, -1):
            station1 = station_ids[i - 1]
            station2 = station_ids[i]
            _station1 = real_ids[i - 1]
            _station2 = real_ids[i]
            platform = platforms[i - 1]
            dur = round(durations[i - 1] / 1000)
            arr_time = dep
            dep_time = dep - dur
            dwell = round(dwells[i - 1] / 1000)
            dep -= dur
            dep -= dwell
            if station1 == station2:
                continue

            if _station2 in AVOID_STATIONS:
                continue

            if _station1 not in AVOID_STATIONS and _station2 not in AVOID_STATIONS:
                tt.append((sta_id(station1), sta_id(station2),
                           dep_time, arr_time,
                           [route_id, route['stations'][-1]['id'], platform]))

            # 添加出站换乘
            connections = data['stations'][_station2]['connections']
            if _station2 in TRANSFER_ADDITION:
                connections += TRANSFER_ADDITION[_station2]
            for con in connections:
                if con in AVOID_STATIONS:
                    continue

                if _station2 not in data['transfer_time']:
                    continue

                if con not in data['transfer_time'][_station2]:
                    continue

                t2 = round(data['transfer_time'][_station2][con])
                dist = data['transfer_dist'][_station2][con]
                con = data['stations'][con]['station']
                tt.append((sta_id(station2), sta_id(con),
                           arr_time, arr_time + t2,
                           [f'w{round(dist, 2)}', '', None]))

            if CALCULATE_WALKING_WILD is True:
                # 添加非出站换乘（越野）
                connections = list(data['stations'].keys())
                if _station2 in WILD_ADDITION:
                    connections += data['stations'][WILD_ADDITION[_station2]]

                transfer = data['transfer_time'].get(_station2, {})
                for con in connections:
                    if con in AVOID_STATIONS:
                        continue

                    if con not in transfer:
                        continue

                    t2 = round(transfer[con])
                    dist = data['transfer_dist'][_station2][con]
                    con = data['stations'][con]['station']
                    tt.append((sta_id(station2), sta_id(con),
                               arr_time, arr_time + t2,
                               [f'w{round(dist, 2)}', '', None]))

        tt_dict[route_id] = tt

    if not os.path.exists('mtr_pathfinder_temp'):
        os.makedirs('mtr_pathfinder_temp')

    if filename != '':
        if not os.path.exists(filename):
            try:
                with open(filename, 'wb') as f:
                    pickle.dump(tt_dict, f)
            except PermissionError:
                pass

    return tt_dict


def gen_all_caches(original_ignored_lines: list[str], LOCAL_FILE_PATH,
                   DEP_PATH, WILD_ADDITION, TRANSFER_ADDITION):
    with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
        data = json.load(f)

    version1 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(LOCAL_FILE_PATH)))
    version2 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(DEP_PATH)))

    IGNORED_LINES = original_ignored_lines
    gen_timetable(data, IGNORED_LINES, [], True, True, False, False, [],
                  RouteType.REAL_TIME, original_ignored_lines,
                  DEP_PATH, version1, version2,
                  WILD_ADDITION, TRANSFER_ADDITION)

    gen_timetable(data, IGNORED_LINES, [], True, True, True, False, [],
                  RouteType.REAL_TIME, original_ignored_lines,
                  DEP_PATH, version1, version2,
                  WILD_ADDITION, TRANSFER_ADDITION)

    gen_timetable(data, IGNORED_LINES, [], False, True, True, False, [],
                  RouteType.REAL_TIME, original_ignored_lines,
                  DEP_PATH, version1, version2,
                  WILD_ADDITION, TRANSFER_ADDITION)

    gen_timetable(data, IGNORED_LINES, [], False, True, False, False, [],
                  RouteType.REAL_TIME, original_ignored_lines,
                  DEP_PATH, version1, version2,
                  WILD_ADDITION, TRANSFER_ADDITION)


def load_tt(tt_dict: dict[str, list[tuple]], data, start, end,
            departure_time: int, DEP_PATH, TRANSFER_ADDITION,
            CALCULATE_WALKING_WILD, WILD_ADDITION, MAX_HOUR):
    with open(DEP_PATH, 'r', encoding='utf-8') as f:
        dep_data: dict[str, list[int]] = json.load(f)

    timetable: list[tuple | list] = []
    if not (start and end):
        return []

    # 添加起点出站换乘
    ss = data['stations'][start]['station']
    connections = data['stations'][start]['connections']
    if start in TRANSFER_ADDITION:
        connections += data['stations'][TRANSFER_ADDITION[start]]
    for con in connections:
        if con not in data['transfer_time'][start]:
            continue

        t2 = round(data['transfer_time'][start][con])
        dist = data['transfer_dist'][start][con]
        con = data['stations'][con]['station']
        timetable.append(
            (sta_id(ss), sta_id(con),
             departure_time, departure_time + t2,
             [f'w{round(dist, 2)}', '', None]))

    if CALCULATE_WALKING_WILD is True:
        # 添加起点非出站换乘（越野）
        connections = list(data['stations'].keys())
        if start in WILD_ADDITION:
            connections += data['stations'][WILD_ADDITION[start]]

        transfer = data['transfer_time'].get(start, {})
        for con in connections:
            if con not in transfer:
                continue

            t2 = round(transfer[con])
            dist = data['transfer_dist'][start][con]
            con = data['stations'][con]['station']
            timetable.append(
                (sta_id(ss), sta_id(con),
                 departure_time, departure_time + t2,
                 [f'w{round(dist, 2)}', '', None]))

    max_time = departure_time + MAX_HOUR * 60 * 60
    trips: dict[str, dict[str, int]] = {}
    trip_no = 0
    for route_id, departures in dep_data.items():
        if route_id not in tt_dict:
            continue

        if max_time > 86400:
            departures += [x + 86400 for x in departures
                           if x <= max_time - 86400]

        tt = tt_dict[route_id]
        for departure in departures:
            if departure >= max_time:
                break

            trips[str(trip_no)] = {}

            for t in tt:
                _t = list(t)
                _t[2] += departure
                _t[3] += departure
                if _t[2] < 0:
                    _t[2] += 86400
                    _t[3] += 86400

                if max_time - 86400 < _t[2] < departure_time:
                    continue

                if _t[4][1] != '':  # Not walking
                    _t += [trip_no]
                    trips[str(trip_no)][str(_t[0])] = _t[2]

                timetable.append(_t)

                # if max_time > 86400 and departure <= max_time - 86400:
                #     _t[2] += 86400
                #     _t[3] += 86400
                #     if _t[4][1] != '':  # Not walking
                #         trips[str(trip_no)][str(_t[0])] = _t[2]

                #     timetable.append(_t)

            trip_no += 1

    # IMPORTANT !!! Connections must be sorted by departure/arrival time.
    timetable.sort(key=itemgetter(2))
    return timetable, trips


def process_path(result: list[tuple], start: str, end: str,
                 trips: dict[str, dict[str, int]], data: dict,
                 detail: bool
                 ) -> Union[list[str, int, int, int, list], tuple]:
    '''
    Process the path, change it into human readable form.
    '''
    if not (start and end):
        return None, None, None, None, None

    if start == end:
        return None, None, None, None, None

    path = []
    last_detail = None
    route_new = []
    low_i = MAX_INT
    # 合并连续乘坐同一辆车的路段
    for i in range(len(result) - 1, -1, -1):
        new_leg = result[i]

        if len(new_leg) < 6:
            # 步行路线
            route_new.append(new_leg)
            continue

        if i >= low_i:
            continue

        trip = trips[str(new_leg[5])]
        for j in range(i - 1, -1, -1):
            old_leg = result[j]
            if len(old_leg) < 6:
                # 步行路线
                break

            trip_index = str(old_leg[0])
            if trip_index not in trip:
                continue

            if trip[trip_index] >= old_leg[2]:
                new_leg = [trip_index, new_leg[1], trip[trip_index],
                           new_leg[3], new_leg[4], new_leg[5]]
                low_i = j

        route_new.append(new_leg)

    route_new.reverse()
    for con in route_new:
        if con[4][:2] != last_detail or detail is True:
            path.append(con)
        else:
            last_con = path[-1]
            last_con[3] = con[3]
            last_con[1] = con[1]

        last_detail = con[4]

    stations = data['stations']
    every_route_time = []
    for x in path:
        station_1 = station_num_to_id(data, x[0])
        station_2 = station_num_to_id(data, x[1])
        route = x[4][0]
        timestamp = get_timestamp_from_seconds(x[2])

        if route in data['routes']:
            # EVERYTHING IS FOR THIS UGLY FORMAT
            route_stations = []
            route_details = []
            for station in data['routes'][route]['stations']:
                route_stations.append(station['id'])
                route_details.append(station)
            indices = find_shortest_sublist_indices(route_stations, station_1, station_2)
            route_stations = route_stations[indices[0]:indices[-1] + 1]
            route_details = route_details[indices[0]:indices[-1] + 1]
            if len(every_route_time) > 0:
                # Go to the platform first
                if every_route_time[-1]['routeId'] == "":
                    every_route_time[-1]['endPlatformName'] = route_details[0]['name']
                elif every_route_time[-1]['endPlatformName'] != route_details[0]['name'] or (
                        last_x and last_z and (route_details[0]['x'] != last_x or route_details[0]['z'] != last_z)):
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
            for y in range(len(route_stations) - 1):
                # Intermediate stations
                every_route_time.append({
                    "routeId": route,
                    "startStationId": route_stations[y],
                    "endStationId": route_stations[y + 1],
                    "startPlatformName": route_details[y]['name'],
                    "endPlatformName": route_details[y + 1]['name'],
                    "startTime": timestamp,
                    "endTime": timestamp,
                    "walkingDistance": 0,
                })
            every_route_time[-1]['endTime'] = get_timestamp_from_seconds(x[3])
            last_x = route_details[-1]['x']
            last_z = route_details[-1]['z']
        else:
            every_route_time.append({
                "routeId": "",
                "startStationId": station_1,
                "endStationId": station_2,
                "startPlatformName": every_route_time[-1]['endPlatformName'] if len(every_route_time) > 0 else "",
                "endPlatformName": "",
                "startTime": timestamp,
                "endTime": get_timestamp_from_seconds(x[3]),
                "walkingDistance": round(float(route[1:]))}
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


def get_timestamp_from_seconds(seconds, base_datetime=None):
    """
    灵活版本，可以指定任意基础时间

    参数:
    seconds: float - 从基础时间开始的秒数
    base_datetime: datetime or None - 基础时间，默认为今天的零点

    返回:
    int - 毫秒级时间戳
    """
    EPOCH = datetime(1970, 1, 1)
    if base_datetime is None:
        # 默认为今天零点
        today = datetime.now(dt.UTC).date()
        base_datetime = datetime.combine(today, datetime.min.time())
    elif isinstance(base_datetime, (int, float)):
        base_datetime = datetime.fromtimestamp(base_datetime / 1000.0, dt.UTC)

    # 直接加上秒数
    target_datetime = base_datetime + timedelta(seconds=seconds)

    # 转换为毫秒时间戳
    return int((target_datetime - EPOCH).total_seconds() * 1000)


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


def main(station1: str, station2: str, LINK: str,
         LOCAL_FILE_PATH, DEP_PATH,
         MAX_WILD_BLOCKS: int = 1500,
         TRANSFER_ADDITION: Optional[dict[str, list[str]]] = None,
         WILD_ADDITION: Optional[dict[str, list[str]]] = None,
         ORIGINAL_IGNORED_LINES: Optional[list] = None,
         UPDATE_DATA: bool = False,
         GEN_DEPARTURE: bool = False,
         IGNORED_LINES: Optional[list] = None,
         ONLY_LINES: Optional[list] = None,
         AVOID_STATIONS: Optional[list] = None,
         CALCULATE_HIGH_SPEED: bool = True, CALCULATE_BOAT: bool = True,
         CALCULATE_WALKING_WILD: bool = False, ONLY_LRT: bool = False, INCLUDE_HIDDEN_ROUTES: bool = False,
         DETAIL: bool = False, MAX_HOUR=3, timetable=None, departure_time=None, tz=0, timeout_min=2,
         need_input=True):
    '''
    Find the shortest path between two stations.
    Args:
        station1 (`str`): Origin
        station2 (`str`): Destination
        link (`str`): The url of the Transport System Map.
        LOCAL_FILE_PATH (`str`): stations-and-routes data file path.
        DEP_PATH (`str`): departures data file path.
        MAX_WILD_BLOCKS (`int`, optional): The maximum distance of walking outside of the station areas.
        TRANSFER_ADDITION (`dict`, optional): Add out-of-station transfer options between these stations. "{station1: [station2, station3, ...], ...}"
        WILD_ADDITION (`dict`, optional): Add walk options between these stations, regardless of MAX_WILD_BLOCKS. "{station1: [station2, station3, ...], ...}"
        ORIGINAL_IGNORED_LINES (`list`, optional): Ignore these routes globally.
        UPDATE_DATA (`bool`, optional): Update stations-and-routes data.
        GEN_DEPARTURE (`bool`, optional): Generate the departure of all routes.
        IGNORED_LINES (`list`, optional): Ignore these routes. (User input)
        ONLY_LINES (`list`, optional): Ignore all the routes except the routes listed.
        AVOID_STATIONS (`list`, optional): Avoid these stations.
        CALCULATE_HIGH_SPEED (`bool`, optional): Take into account High Speed Rail routes.
        CALCULATE_BOAT (`bool`, optional): Take into account all boat routes.
        CALCULATE_WALKING_WILD (`bool`, optional): Take into account paths that require walking between two stations.
        ONLY_LRT (`bool`, optional): Ignore all the routes except Light Rail routes during the trip.
        IN_THEORY (`bool`, optional): Ignore the time spent waiting for the train.
        DETAIL (`bool`, optional): Show more detailed information in the image.
        MAX_HOUR (`int`, optional): The maximum hour considered since pathfinding.
        timetable (`list`, optional): The timetable for pathfinding.
        gen_image (`bool`, optional): Generated an image of the route.
        show (`bool`, optional): Show the generated image.
        departure_time (`int`, optional): The departure time in seconds in a day.
        tz (`float`, optional): The timezone of the departure time in hours.
        need_input (`bool`, optional): Use input() function when downloading data, which will block the thread.
    Returns:
        A tuple including the generated image.

        (PIL Image.Image, Converted base64 code)

        If the path cannot be found, it will return False.

        If the stations do not exist, it will return None.
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

    if TRANSFER_ADDITION is None:
        TRANSFER_ADDITION = {}
    if WILD_ADDITION is None:
        WILD_ADDITION = {}
    if ORIGINAL_IGNORED_LINES is None:
        ORIGINAL_IGNORED_LINES = []
    if IGNORED_LINES is None:
        IGNORED_LINES = []
    if ONLY_LINES is None:
        ONLY_LINES = []
    if AVOID_STATIONS is None:
        AVOID_STATIONS = []

    IGNORED_LINES += ORIGINAL_IGNORED_LINES
    if LINK.endswith('/index.html'):
        LINK = LINK.rstrip('/index.html')

    if UPDATE_DATA is True or (not os.path.exists(LOCAL_FILE_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        data = fetch_data(LINK, LOCAL_FILE_PATH, MAX_WILD_BLOCKS, need_input)
    else:
        with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
            data = json.load(f)

    new_ignored_lines = []
    new_original_ignored_lines = []
    for route_id, route in data['routes'].items():
        route_key = f"{route['color']}_{route['name'].split('||')[0]}_{route['number']}"
        if route_key not in ONLY_LINES and (route_key in IGNORED_LINES or (route['hidden'] and not INCLUDE_HIDDEN_ROUTES)):
            new_ignored_lines.append(route_id)
        if route_key in ORIGINAL_IGNORED_LINES or route['hidden']:
            new_original_ignored_lines.append(route_id)

    if GEN_DEPARTURE is True or (not os.path.exists(DEP_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        gen_departure(LINK, DEP_PATH, need_input)

    version1 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(LOCAL_FILE_PATH)))
    version2 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(DEP_PATH)))

    route_type = RouteType.REAL_TIME
    if timetable is None:
        timetable = gen_timetable(
            data, IGNORED_LINES, ONLY_LINES,
            CALCULATE_HIGH_SPEED, CALCULATE_BOAT, CALCULATE_WALKING_WILD,
            ONLY_LRT, AVOID_STATIONS, route_type, ORIGINAL_IGNORED_LINES,
            DEP_PATH, version1, version2,
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
    if result == []:
        return []

    ert = process_path(result, station1, station2, trips,
                       data, DETAIL)

    return ert


def run():
    # 地图设置
    # 在线线路图网址，结尾删除"/"
    LINK: str = 'https://lps.gteh.top'
    # 旅途的最长时间，默认值为3
    MAX_HOUR: int = 3
    # 从A站到B站，非出站换乘（越野）的最远步行距离，默认值为1500
    MAX_WILD_BLOCKS: int = 1500
    # 手动增加出站换乘
    # "车站: [出站换乘的车站, ...], ..."
    TRANSFER_ADDITION: dict[str, list[str]] = {"6C779DAE0A5090BF": ["E918CA5DCB128E39", "B250C507ABBCEBF9"]}
    # 手动增加非出站换乘（越野）
    # "车站: [非出站换乘的车站, ...], ..."
    WILD_ADDITION: dict[str, list[str]] = {}
    # 禁止乘坐的路线（未开通的路线）
    ORIGINAL_IGNORED_LINES: list = []

    # 文件设置
    link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
    LOCAL_FILE_PATH = f'mtr-original-data-{link_hash}-mtr4-v4.json'
    DEP_PATH = f'mtr-route-departure-{link_hash}-mtr4-v4.json'

    # 是否更新车站数据
    UPDATE_DATA: bool = True
    # 是否更新路线数据
    GEN_DEPARTURE: bool = True

    # 寻路设置
    # 避开的路线
    IGNORED_LINES: list = []
    # 仅使用指定路线（如启用，将忽略"避开的路线"参数）
    ONLY_LINES: list = []
    # 避开的车站
    AVOID_STATIONS: list = []
    # 允许高铁，默认值为True
    CALCULATE_HIGH_SPEED: bool = True
    # 允许船，默认值为True
    CALCULATE_BOAT: bool = True
    # 允许非出站换乘（越野），默认值为False
    CALCULATE_WALKING_WILD: bool = False
    # 仅允许轻轨，默认值为False
    ONLY_LRT: bool = False
    INCLUDE_HIDDEN_ROUTES: bool = False

    # 出发时间（秒，0-86400），默认值为None，即当前时间后10秒
    DEP_TIME = None
    # 输出的图片中是否显示详细信息（每站的到站、出发时间）
    DETAIL: bool = False

    # 出发、到达车站
    station1 = '6A3F938669582F91'
    station2 = '0F92228EB5D81A77'

    print(main(station1, station2, LINK, LOCAL_FILE_PATH, DEP_PATH,
         MAX_WILD_BLOCKS,
         TRANSFER_ADDITION, WILD_ADDITION,
         ORIGINAL_IGNORED_LINES, UPDATE_DATA, GEN_DEPARTURE,
         IGNORED_LINES, ONLY_LINES, AVOID_STATIONS, CALCULATE_HIGH_SPEED,
         CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT, INCLUDE_HIDDEN_ROUTES, DETAIL, MAX_HOUR, departure_time=DEP_TIME))


if __name__ == '__main__':
    run()
