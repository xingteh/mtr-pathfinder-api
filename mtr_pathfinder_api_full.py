'''
Find paths between two stations for Minecraft Transit Railway.
'''
from datetime import datetime
from difflib import SequenceMatcher
from enum import Enum
from math import gcd, sqrt
from operator import itemgetter
from statistics import median_low
from threading import Thread, BoundedSemaphore
from time import gmtime, strftime, time
from typing import Union
from queue import Queue
import datetime as dt
import hashlib
import json
import os
import pickle
import re

import networkx as nx
import requests

SERVER_TICK: int = 20

DEFAULT_AVERAGE_SPEED: dict = {
    'train_normal': 14,
    'train_light_rail': 11,
    'train_high_speed': 40,
    'boat_normal': 10,
    'boat_light_rail': 10,
    'boat_high_speed': 13,
    'cable_car_normal': 8,
    'airplane_normal': 70
}                                   # 列车平均速度，单位 block/s
RUNNING_SPEED: int = 5.612          # 站内换乘速度，单位 block/s
TRANSFER_SPEED: int = 4.317         # 出站换乘速度，单位 block/s
WILD_WALKING_SPEED: int = 2.25      # 非出站换乘（越野）速度，单位 block/s

ROUTE_INTERVAL_DATA = Queue()
semaphore = BoundedSemaphore(25)
original = {}
tmp_names = {}


def get_close_matches(words, possibilities, cutoff=0.2):
    result = [(-1, None)]
    s = SequenceMatcher()
    for word in words:
        s.set_seq2(word)
        for x, y in possibilities:
            s.set_seq1(x)
            if s.real_quick_ratio() >= cutoff and \
                    s.quick_ratio() >= cutoff:
                ratio = s.ratio()
                if ratio >= cutoff:
                    result.append((ratio, y))

    return max(result)[1]


class RouteType(Enum):
    '''
    An Enum class to define the types of the route.
    '''
    IN_THEORY = 0
    WAITING = 1


def round_ten(n: float) -> int:
    '''
    Round the number in ten.
    '''
    ans = round(n / 10) * 10
    return ans if ans > 0 else 10


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


def fetch_interval_data(station_id: str, LINK) -> None:
    '''
    Fetch the interval data of a station.
    '''
    global ROUTE_INTERVAL_DATA
    with semaphore:
        link = LINK + f'/arrivals?worldIndex=0&stationId={station_id}'
        try:
            data = requests.get(link).json()
        except Exception:
            pass
        else:
            ROUTE_INTERVAL_DATA.put([station_id, [time(), data]])


def gen_route_interval(LOCAL_FILE_PATH, INTERVAL_PATH, LINK, MTR_VER) -> None:
    '''
    Generate all the interval data.
    '''
    with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
        data = json.load(f)

    if MTR_VER == 3:
        threads: list[Thread] = []
        for station_id in data[0]['stations']:
            t = Thread(target=fetch_interval_data, args=(station_id, LINK))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        interval_data_list = []
        while not ROUTE_INTERVAL_DATA.empty():
            interval_data_list.append(ROUTE_INTERVAL_DATA.get())

        arrivals = dict(interval_data_list)
        dep_dict_per_route: dict[str, list] = {}
        dep_dict_per_route_: dict[str, list] = {}
        for t, arrivals in arrivals.values():
            dep_dict_per_station: dict[str, list] = {}
            for arrival in arrivals[:-1]:
                name = arrival['name']
                if name in dep_dict_per_station:
                    dep_dict_per_station[name] += [arrival['arrival']]
                else:
                    dep_dict_per_station[name] = [arrival['arrival']]

            for x, item in dep_dict_per_station.items():
                dep_s_list = []
                if len(item) == 1:
                    if x not in dep_dict_per_route_:
                        dep_dict_per_route_[x] = [(item[0] / 1000 - t) * 1.25]
                else:
                    for y in range(len(item) - 1):
                        dep_s_list.append((item[y + 1] - item[y]) / 1000)
                    if x in dep_dict_per_route:
                        dep_dict_per_route[x] += [sum(dep_s_list) /
                                                  len(dep_s_list)]
                    else:
                        dep_dict_per_route[x] = [sum(dep_s_list) /
                                                 len(dep_s_list)]

        for x in dep_dict_per_route_:
            if x not in dep_dict_per_route:
                dep_dict_per_route[x] = dep_dict_per_route_[x]

        freq_dict: dict[str, list] = {}
        for route, arrivals in dep_dict_per_route.items():
            if len(arrivals) == 1:
                freq_dict[route] = round_ten(arrivals[0])
            else:
                freq_dict[route] = round_ten(sum(arrivals) / len(arrivals))

    elif MTR_VER == 4:
        link = LINK.rstrip('/') + '/mtr/api/map/departures?dimension=0'
        departures = requests.get(link).json()['data']['departures']
        dep_dict: dict[str, list[int]] = {}
        for x in departures:
            dep_list = set()
            for y in x['departures']:
                for z in y['departures']:
                    dep = round(z / 1000)
                    while dep < 0:
                        dep += 86400

                    dep_list.add(dep)

            dep_list = list(sorted(dep_list))
            dep_dict[x['id']] = dep_list

        freq_dict: dict[str, list] = {}
        for route_id, stats in dep_dict.items():
            if len(stats) == 0:
                continue

            for route_stats in data[0]['routes']:
                if route_stats['id'] == route_id:
                    break
            else:
                print(f'Route {route_id} not found')
                continue

            route_id = route_stats['id']
            freq_list = []
            for i1 in range(len(stats)):
                i2 = i1 + 1
                if i2 == len(stats):
                    i2 = 0
                    dep_2 = stats[i2] + 86400
                else:
                    dep_2 = stats[i2]

                dep_1 = stats[i1]
                freq = dep_2 - dep_1
                freq_list.append(freq)

            median_freq = median_low(freq_list)
            freq_dict[route_id] = round_ten(median_freq)

    else:
        return

    with open(INTERVAL_PATH, 'w', encoding='utf-8') as f:
        json.dump(freq_dict, f)


def fetch_data(link: str, LOCAL_FILE_PATH, MTR_VER) -> str:
    '''
    Fetch all the route data and station data.
    '''
    if MTR_VER == 3:
        link = link.rstrip('/') + '/data'
        data = requests.get(link).json()
    else:
        link = link.rstrip('/') + \
            '/mtr/api/map/stations-and-routes?dimension=0'
        data = requests.get(link).json()['data']

        data_new = {'routes': [], 'stations': {}}
        i = 0
        for d in data['stations']:
            d['station'] = hex(i)[2:]
            data_new['stations'][d['id']] = d
            i += 1

        x_dict = {x['id']: [] for x in data['stations']}
        z_dict = {x['id']: [] for x in data['stations']}
        for route in data['routes']:
            # if route['hidden'] is True:
            #     continue

            if route['circularState'] == 'CLOCKWISE':
                route['circular'] = 'cw'
            elif route['circularState'] == 'ANTICLOCKWISE':
                route['circular'] = 'ccw'
            else:
                route['circular'] = ''

            route['durations'] = [round(x / 1000) for x in route['durations']]
            for station in route['stations']:
                x_dict[station['id']] += [station['x']]
                z_dict[station['id']] += [station['z']]

            # route['stations'] = [f'{x}_{route["color"]}'
            #                      for x in route['stations']]

            data_new['routes'].append(route)

        for station in data['stations']:
            x_list = x_dict[station['id']]
            z_list = z_dict[station['id']]
            if len(x_list) == 0:
                continue

            data_new['stations'][station['id']]['x'] = \
                sum(x_list) / len(x_list)
            data_new['stations'][station['id']]['z'] = \
                sum(z_list) / len(z_list)

        data = [data_new]

    with open(LOCAL_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f)

    return data


def get_distance(a_dict: dict, b_dict: dict, square: bool = False) -> float:
    '''
    Get the distance of two stations.
    '''
    dist_square = (a_dict['x'] - b_dict['x']) ** 2 + \
        (a_dict['z'] - b_dict['z']) ** 2
    if square is True:
        return dist_square
    return sqrt(dist_square)


def get_route_station_index(route: dict, station_1_id: str, station_2_id: str,
                            MTR_VER=3) -> tuple:
    '''
    Get the index of the two stations in one route.
    '''
    if MTR_VER == 3:
        st = [x.split('_')[0] for x in route['stations']]
    else:
        st = [x['id'] for x in route['stations']]

    check_station_2 = False
    for i, station in enumerate(st):
        if station == station_1_id:
            index1 = i
            check_station_2 = True
        if check_station_2 and station == station_2_id:
            index2 = i
            break
    else:
        index1 = index2 = None

    return index1, index2


def get_approximated_time(route: dict, station_1_id: str, station_2_id: str,
                          data: list, tick: bool = False, MTR_VER=3) -> float:
    '''
    Get the approximated time of the two stations in one route.
    '''
    if MTR_VER == 4:
        return get_app_time_v4(route, station_1_id, station_2_id)

    index1, index2 = get_route_station_index(route,
                                             station_1_id, station_2_id)
    if index2 is None:
        return None

    station_1_position = {}
    station_2_position = {}
    t = 0
    stations = route['stations'][index1:index2 + 1]
    for i, station_1 in enumerate(stations):
        try:
            station_2 = stations[i + 1]
        except IndexError:
            break

        station_1_check = False
        station_2_check = False
        for k, position_dict in data[0]['positions'].items():
            if k == station_1:
                station_1_position['x'] = position_dict['x']
                station_1_position['z'] = position_dict['y']
                station_1_check = True
            elif k == station_2:
                station_2_position['x'] = position_dict['x']
                station_2_position['z'] = position_dict['y']
                station_2_check = True
            if station_1_check and station_2_check:
                t += get_distance(station_1_position, station_2_position) \
                    / DEFAULT_AVERAGE_SPEED[route['type']]
                break

    if tick is True:
        t *= 20

    return t


def get_app_time_v4(route: dict,
                    station_1_id: str, station_2_id: str) -> float:
    '''
    Get the approximated time of the two stations in one route.
    '''
    index1, index2 = get_route_station_index(route,
                                             station_1_id, station_2_id, 4)
    if index2 is None:
        return None

    t = 0
    stations = route['stations'][index1:index2 + 1]
    for i, station_1 in enumerate(stations):
        try:
            station_2 = stations[i + 1]
        except IndexError:
            break

        t += get_distance(station_1, station_2) / \
            DEFAULT_AVERAGE_SPEED[route['type']]

    return t

def create_graph(data: list, IGNORED_LINES: list[str],
                 CALCULATE_HIGH_SPEED: bool, CALCULATE_BOAT: bool,
                 CALCULATE_WALKING_WILD: bool, ONLY_LRT: bool,
                 AVOID_STATIONS: list, route_type: RouteType,
                 original_ignored_lines: list[str],
                 INTERVAL_PATH: str,
                 version1: str, version2: str,
                 LOCAL_FILE_PATH,
                 WILD_ADDITION, TRANSFER_ADDITION, include_hidden_routes,
                 MAX_WILD_BLOCKS, MTR_VER, cache) -> nx.MultiDiGraph:
    '''
    Create the graph of all routes.
    '''
    global original, intervals
    with open(INTERVAL_PATH, 'r', encoding='utf-8') as f:
        intervals = json.load(f)

    if not os.path.exists('mtr_pathfinder_temp'):
        os.makedirs('mtr_pathfinder_temp')

    filename = ''
    m = hashlib.md5()
    for s in original_ignored_lines:
        m.update(s.encode('utf-8'))
    new_ignored_lines = []
    new_original_ignored_lines = []
    for route in data[0]['routes']:
        route_key = f"{route['color']}_{route['name'].split('||')[0]}_{route['number']}"
        if route_key in IGNORED_LINES or (route['hidden'] and not include_hidden_routes):
            new_ignored_lines.append(route['id'])
        if route_key in original_ignored_lines or route['hidden']:
            new_original_ignored_lines.append(route['id'])
    if cache is True and new_ignored_lines == new_original_ignored_lines and \
            CALCULATE_BOAT is True and ONLY_LRT is False and \
            AVOID_STATIONS == [] and route_type == RouteType.WAITING:

        filename = f'mtr_pathfinder_temp{os.sep}' + \
            f'3{int(CALCULATE_HIGH_SPEED)}{int(CALCULATE_WALKING_WILD)}' + \
            f'-{version1}-{version2}-{m.hexdigest()}.dat'
        if os.path.exists(filename):
            with open(filename, 'rb') as f:
                tup = pickle.load(f)
                G = tup[0]
                original = tup[1]

            return G

    routes = data[0]['routes']
    new_durations = {}
    for it0, route in enumerate(routes):
        old_durations = route['durations']
        if 0 in old_durations or old_durations == []:
            stations = route['stations']
            new_dur = []
            for it1 in range(len(route['stations']) - 1):
                if old_durations != [] and old_durations[it1] != 0:
                    new_dur.append(old_durations[it1])
                    continue

                it2 = it1 + 1
                if MTR_VER == 3:
                    station_1 = stations[it1].split('_')[0]
                    station_2 = stations[it2].split('_')[0]
                else:
                    station_1 = stations[it1]['id']
                    station_2 = stations[it2]['id']

                app_time = get_approximated_time(route, station_1, station_2,
                                                 data, True, MTR_VER)
                if app_time == 0:
                    app_time = 0.01
                new_dur.append(app_time)

            if sum(new_dur) == 0:
                continue

            new_durations[str(it0)] = new_dur

    if len(new_durations) > 0:
        for route_id, new_duration in new_durations.items():
            route_id = int(route_id)
            old_route_data = data[0]['routes'][route_id]
            old_route_data['durations'] = new_duration
            data[0]['routes'][route_id] = old_route_data

        with open(LOCAL_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    all_stations = data[0]['stations']
    G = nx.MultiDiGraph()
    edges_dict = {}
    edges_attr_dict = {}
    original = {}
    waiting_walking_dict = {}

    # 添加出站换乘
    for station, station_dict in all_stations.items():
        if 'x' not in station_dict or 'z' not in station_dict:
            continue

        if station in AVOID_STATIONS:
            continue

        for transfer in station_dict['connections']:
            if transfer not in all_stations:
                continue

            if transfer in AVOID_STATIONS:
                continue

            transfer_dict = all_stations[transfer]
            if 'x' not in transfer_dict or 'z' not in transfer_dict:
                continue

            dist = get_distance(station_dict, transfer_dict)
            duration = dist / TRANSFER_SPEED

            if (station, transfer) in edges_attr_dict:
                edges_attr_dict[(station, transfer)].append(
                    (f'w{round(dist, 2)}', duration, 0))
            else:
                edges_attr_dict[(station, transfer)] = [
                    (f'w{round(dist, 2)}', duration, 0)]
            waiting_walking_dict[(station, transfer)] = \
                (duration, f'w{round(dist, 2)}')

        additions1 = set()
        if station_dict['id'] in TRANSFER_ADDITION:
            for x in TRANSFER_ADDITION[station_dict['id']]:
                additions1.add(x)

        for x in additions1:
            for station2, station2_dict in all_stations.items():
                if station2 in AVOID_STATIONS:
                    continue

                if station2_dict['id'] == x:
                    if station2 not in station_dict['connections']:
                        try:
                            dist = get_distance(station_dict, station2_dict)
                            duration = dist / TRANSFER_SPEED
                            if (station, station2) not in edges_attr_dict:
                                edges_attr_dict[(station, station2)] = []
                            edges_attr_dict[(station, station2)].append(
                                (f'w{round(dist, 2)}',
                                 duration, 0))
                            waiting_walking_dict[(station, station2)] = \
                                (duration, f'w{round(dist, 2)}')
                        except KeyError:
                            pass

                        break

        additions2 = set()
        if station_dict['id'] in WILD_ADDITION and \
                CALCULATE_WALKING_WILD is True:
            for x in WILD_ADDITION[station_dict['id']]:
                additions2.add(x)

        for x in additions2:
            for station2, station2_dict in all_stations.items():
                if station2 in AVOID_STATIONS:
                    continue

                if station2_dict['id'] == x:
                    if station2 not in station_dict['connections']:
                        try:
                            dist = get_distance(station_dict, station2_dict)
                            duration = dist / WILD_WALKING_SPEED
                            if (station, station2) not in edges_attr_dict:
                                edges_attr_dict[(station, station2)] = []

                            edges_attr_dict[(station, station2)].append(
                                (f'w{round(dist, 2)}', duration, 0))
                            waiting_walking_dict[(station, station2)] = \
                                (duration, f'w{round(dist, 2)}')
                        except KeyError:
                            pass

                        break

    # 添加普通路线
    for route in data[0]['routes']:
        n = route['id']
        if n in new_ignored_lines:
            continue

        if (not CALCULATE_HIGH_SPEED) and route['type'] == 'train_high_speed':
            continue

        if (not CALCULATE_BOAT) and 'boat' in route['type']:
            continue

        if ONLY_LRT and route['type'] != 'train_light_rail':
            continue

        if route_type == RouteType.WAITING:
            if route['type'] == 'cable_car_normal':
                intervals[n] = 2

            if n not in intervals:
                continue

        stations = route['stations']
        durations = route['durations']
        if len(stations) < 2:
            continue

        if len(stations) - 1 < len(durations):
            durations = durations[:len(stations) - 1]

        if len(stations) - 1 > len(durations):
            continue

        # if route_type == RouteType.WAITING:
        for i in range(len(durations)):
            for i2 in range(len(durations[i:])):
                i2 += i + 1
                if MTR_VER == 3:
                    station_1 = stations[i].split('_')[0]
                    station_2 = stations[i2].split('_')[0]
                    dur_list = durations[i:i2]
                    station_list = stations[i:i2 + 1]
                    c = False
                    for sta in station_list:
                        if sta.split('_')[0] in AVOID_STATIONS:
                            c = True
                    if c is True:
                        continue

                    if 0 in dur_list:
                        t = get_approximated_time(route, station_1, station_2,
                                                  data, MTR_VER)
                        if t is None:
                            continue
                        dur = t
                    else:
                        dur = sum(durations[i:i2]) / SERVER_TICK

                else:
                    station_1 = stations[i]
                    station_2 = stations[i2]
                    dur_list = durations[i:i2]
                    station_list = stations[i:i2 + 1]
                    dwell = sum([x['dwellTime'] / 1000
                                 for x in station_list][1:-1])
                    c = False
                    for sta in station_list:
                        if sta['id'] in AVOID_STATIONS:
                            c = True
                    if c is True:
                        continue

                    if 0 in dur_list:
                        t = get_app_time_v4(route, station_1, station_2,
                                            data, MTR_VER)
                        if t is None:
                            continue
                        dur = round(t + dwell)
                    else:
                        dur = round(sum(durations[i:i2]) + dwell)

                    station_1 = station_1['id']
                    station_2 = station_2['id']

                if route_type == RouteType.WAITING:
                    wait = float(intervals[n])
                    if (station_1, station_2) not in edges_dict:
                        edges_dict[(station_1, station_2)] = []

                    edges_dict[(station_1, station_2)].append(
                        (dur, wait, route['id']))

                    original_tuple = (route['id'], station_1, station_2)
                    if original_tuple in original:
                        dur1 = original[original_tuple]
                        if dur < dur1:
                            original[original_tuple] = dur
                    else:
                        original[original_tuple] = dur
                else:
                    if (station_1, station_2) in edges_attr_dict:
                        edges_attr_dict[(station_1, station_2)].append(
                            (route['id'], dur, 0))
                    else:
                        edges_attr_dict[(station_1, station_2)] = [
                            (route['id'], dur, 0)]
        # else:
            # for i, duration in enumerate(durations):
            #     station_1 = stations[i].split('_')[0]
            #     station_2 = stations[i + 1].split('_')[0]
            #     station_list = stations[i:i + 2]
            #     c = False
            #     for sta in station_list:
            #         if sta.split('_')[0] in avoid_ids:
            #             c = True
            #     if c is True:
            #         continue

            #     add_edge = False
            #     if duration == 0:
            #         t = get_approximated_time(route, station_1, station_2,
            #                                   data, MTR_VER)
            #         if t is not None:
            #             add_edge = True
            #     else:
            #         add_edge = True
            #         t = duration / SERVER_TICK

            #     if add_edge is True:
            #         if (station_1, station_2) in edges_attr_dict:
            #             edges_attr_dict[(station_1, station_2)].append(
            #                 (route['name'], t, 0))
            #         else:
            #             edges_attr_dict[(station_1, station_2)] = [
            #                 (route['name'], t, 0)]

    if route_type == RouteType.WAITING:
        for tup, dur_tup in edges_dict.items():
            dur = [x[0] for x in dur_tup]
            wait = [x[1] for x in dur_tup]
            routes = [x[2] for x in dur_tup]
            final_wait = []
            final_routes = []
            min_dur = min(dur)
            for i, x in enumerate(dur):
                if abs(x - min_dur) <= 60:
                    final_wait.append(wait[i])
                    final_routes.append(routes[i])

            s1 = tup[0]
            s2 = tup[1]
            lcm_sum = 1
            sum_interval = 0
            for x in final_wait:
                if x != 0:
                    lcm_sum = lcm(lcm_sum, round(x))
            for x in final_wait:
                if x != 0:
                    sum_interval += (lcm_sum / round(x))

            if sum_interval == 0:
                sum_int = 0
            else:
                sum_int = lcm_sum / sum_interval / 2

            if (s1, s2) in waiting_walking_dict:
                t = waiting_walking_dict[(s1, s2)][0]
                if abs(t - min_dur) <= 60:
                    route_id = waiting_walking_dict[(s1, s2)][1]
                    dur = waiting_walking_dict[(s1, s2)][0]
                    final_routes.append(route_id)
                    original[(route_id, s1, s2)] = dur

            edges_attr_dict[(s1, s2)] = [(final_routes, min_dur, sum_int)]

    for edge in edges_attr_dict.items():
        u, v = edge[0]
        min_time = min(e[1] + e[2] for e in edge[1])
        for r in edge[1]:
            route_id = r[0]
            duration = r[1]
            waiting_time = r[2]
            weight = duration + waiting_time
            if abs(weight - min_time) <= 60 and weight > 0:
                G.add_edge(u, v, weight=weight, name=route_id,
                           waiting=waiting_time)

    # 添加野外行走 (无铁路连接)
    if CALCULATE_WALKING_WILD is True:
        edges_attr_dict = {}
        for station, station_dict in all_stations.items():
            if station in AVOID_STATIONS:
                continue

            if 'x' not in station_dict or 'z' not in station_dict:
                continue

            for station2, station2_dict in all_stations.items():
                if station2 in AVOID_STATIONS:
                    continue

                if 'x' not in station2_dict or 'z' not in station2_dict:
                    continue

                if station == station2:
                    continue

                if (station, station2) in waiting_walking_dict:
                    continue

                dist = get_distance(station_dict, station2_dict, True)
                if dist <= (MAX_WILD_BLOCKS ** 2):
                    dist = sqrt(dist)
                    duration = dist / WILD_WALKING_SPEED
                    if G.has_edge(station, station2) and \
                            duration - G[station][station2][0]['weight'] > 60:
                        continue

                    edges_attr_dict[(station, station2)] = [
                        (f'w{round(dist, 2)}', duration, 0)]
                    if G.has_edge(station, station2) and \
                            duration + 120 < \
                            G[station][station2][0]['weight']:
                        G.remove_edge(station, station2)

        for edge in edges_attr_dict.items():
            u, v = edge[0]
            for r in edge[1]:
                route_id = r[0]
                duration = r[1]
                waiting_time = r[2]
                G.add_edge(u, v, weight=duration, name=route_id,
                           waiting=waiting_time)

    if filename != '':
        if not os.path.exists(filename):
            with open(filename, 'wb') as f:
                pickle.dump((G, original), f)

    return G


def find_shortest_route(G: nx.MultiDiGraph, start: str, end: str,
                        data: list, MTR_VER) -> list[str, int, int, int, list]:
    '''
    Find the shortest route between two stations.
    '''
    if not (start and end):
        return None, None, None, None, None

    if start == end:
        return None, None, None, None, None

    shortest_path = []
    shortest_distance = -1
    try:
        shortest_path = nx.all_shortest_paths(G, start,
                                              end, weight='weight')
        shortest_path = list(sorted(shortest_path, key=lambda x: len(x)))[0]
        shortest_distance = nx.shortest_path_length(G, start,
                                                    end,
                                                    weight='weight')
    except nx.exception.NetworkXNoPath:
        return False, False, False, False, False
    except nx.exception.NodeNotFound:
        return False, False, False, False, False

    return process_path(G, shortest_path, shortest_distance, data, MTR_VER)


def process_path(G: nx.MultiDiGraph, path: list, shortest_distance: int,
                 data: list, MTR_VER) -> list[str, int, int, int, list]:
    '''
    Process the path, change it into ~~human~~ system map readable form.
    '''
    stations = data[0]['stations']
    station_ids = [stations[path[0]]['id']]
    every_route_time = []
    each_route_time = []
    waiting_time = 0
    for i in range(len(path) - 1):
        station_1 = path[i]
        station_2 = path[i + 1]
        edge = G[station_1][station_2]
        duration_list = []
        waiting_list = []
        route_id_list = []
        for v in edge.values():
            duration = v['weight']
            route_id = v['name']
            waiting = v['waiting']
            duration_list.append((route_id, duration))
            waiting_list.append((route_id, waiting))
            if isinstance(route_id, list):
                route_id_list.extend(route_id)
            elif isinstance(route_id, str):
                route_id_list.append(route_id)
            waiting_time += waiting

        if len(route_id_list) == 1:
            route_id = route_id_list[0]
        else:
            route_id = '(' + ' / '.join(route_id_list) + ')'

        station_ids.append(route_id)
        station_ids.append(stations[path[i + 1]]['id'])

        sta1_id = stations[station_1]['id']
        sta2_id = stations[station_2]['id']
        for route_id in route_id_list:
            for x in duration_list:
                if route_id == x[0]:
                    duration = x[1]
                    break
            else:
                for x in duration_list:
                    for y in x[0]:
                        if route_id == y:
                            duration = original[(route_id,
                                                 station_1, station_2)]
                            break

            for x in waiting_list:
                if route_id == x[0]:
                    waiting = x[1]
                    break
            else:
                for x in waiting_list:
                    for y in x[0]:
                        if route_id == y:
                            waiting = x[1]
                            break

            r = (sta1_id, sta2_id, route_id, duration, waiting)

            if len(each_route_time) > 0:
                old_r = each_route_time[-1]
                if old_r[:2] != r[:2]:
                    each_route_time.append(r)

            if len(each_route_time) == 0:
                each_route_time.append(r)

        each_route_time.sort(key=itemgetter(3))
        every_route_time.extend(each_route_time)

        each_route_time = []
        duration = 0
        waiting = 0

    end_ = stations[station_2]['name']
    if station_ids[-1] != end_:
        station_ids += end_

    return ' ->\n'.join(station_ids), shortest_distance, \
        waiting_time, shortest_distance - waiting_time, every_route_time


def process_output(ert: list, data: list):
    every_route_time = []
    last_x = None
    last_z = None
    start_time = int(datetime.now(dt.UTC).timestamp() * 1000)
    for x in ert:
        station_1 = x[0]
        station_2 = x[1]
        route_id = x[2]

        if route_id in [y['id'] for y in data[0]['routes']]:
            # EVERYTHING IS FOR THIS UGLY FORMAT
            route_stations = []
            route_details = []
            for y in data[0]['routes']:
                if route_id == y['id']:
                    for station in y['stations']:
                        route_stations.append(station['id'])
                        route_details.append(station)
                    break
            indices = find_shortest_sublist_indices(route_stations, station_1, station_2)
            route_stations = route_stations[indices[0]:indices[-1] + 1]
            route_details = route_details[indices[0]:indices[-1] + 1]
            if len(every_route_time) > 0:
                # Go to the platform first
                if every_route_time[-1]['routeId'] == "":
                    every_route_time[-1]['endPlatformName'] = route_details[0]['name']
                if every_route_time[-1]['endPlatformName'] != route_details[0]['name'] or (
                        last_x and last_z and (route_details[0]['x'] != last_x or route_details[0]['z'] != last_z)):
                    dist = round(get_distance(route_details[0], {'x': last_x, 'z': last_z}))
                    duration = dist / RUNNING_SPEED
                    middle_time = get_timestamp_from_seconds(duration, start_time)
                    every_route_time.append({
                        "routeId": "",
                        "startStationId": every_route_time[-1]['endStationId'],
                        "endStationId": route_stations[0],
                        "startPlatformName": every_route_time[-1]['endPlatformName'],
                        "endPlatformName": route_details[0]['name'],
                        "startTime": every_route_time[-1]['endTime'],
                        "endTime": middle_time,
                        "walkingDistance": round(get_distance(route_details[0], {'x': last_x, 'z': last_z})),
                    })
                else:
                    middle_time = start_time
            else:
                middle_time = start_time
            end_time = get_timestamp_from_seconds(x[3] + x[4], middle_time)
            for y in range(len(route_stations) - 1):
                # Intermediate stations
                every_route_time.append({
                    "routeId": route_id,
                    "startStationId": route_stations[y],
                    "endStationId": route_stations[y + 1],
                    "startPlatformName": route_details[y]['name'],
                    "endPlatformName": route_details[y + 1]['name'],
                    "startTime": start_time,
                    "endTime": start_time,
                    "walkingDistance": 0,
                })
            every_route_time[-1]['endTime'] = end_time
            last_x = route_details[-1]['x']
            last_z = route_details[-1]['z']
        else:
            end_time = get_timestamp_from_seconds(x[3] + x[4], start_time)
            every_route_time.append({
                "routeId": "",
                "startStationId": station_1,
                "endStationId": station_2,
                "startPlatformName": every_route_time[-1]['endPlatformName'] if len(every_route_time) > 0 else "",
                "endPlatformName": "",
                "startTime": start_time,
                "endTime": end_time,
                "walkingDistance": round(float(route_id[1:]))}
            )
            last_x = None
            last_z = None
        start_time = end_time

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
    EPOCH = datetime(1970, 1, 1, tzinfo=dt.UTC)
    if base_datetime is None:
        # 默认为今天零点
        today = datetime.now(dt.UTC).date()
        base_datetime = datetime.combine(today, datetime.min.time())
    elif isinstance(base_datetime, (int, float)):
        base_datetime = datetime.fromtimestamp(base_datetime / 1000.0, dt.UTC)

    # 直接加上秒数
    target_datetime = base_datetime + dt.timedelta(seconds=seconds)

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
         LOCAL_FILE_PATH, INTERVAL_PATH,
         MAX_WILD_BLOCKS: int = 1500,
         TRANSFER_ADDITION: dict[str, list[str]] = {},
         WILD_ADDITION: dict[str, list[str]] = {},
         ORIGINAL_IGNORED_LINES: list = [], UPDATE_DATA: bool = False,
         GEN_ROUTE_INTERVAL: bool = False, IGNORED_LINES: list = [],
         AVOID_STATIONS: list = [], CALCULATE_HIGH_SPEED: bool = True,
         CALCULATE_BOAT: bool = True, CALCULATE_WALKING_WILD: bool = False,
         ONLY_LRT: bool = False, IN_THEORY: bool = False, INCLUDE_HIDDEN_ROUTES: bool = False,
         MTR_VER: int = 3, G=None, cache=True):
    '''
    Main function. You can call it in your own code.
    Output:
    False -- Route not found 找不到路线
    None -- Incorrect station name(s) 车站输入错误，请重新输入
    else 其他 -- tuple
    (image object, base64 str of the generated image)
    (图片对象, 生成图片的 base64 字符串)
    '''
    if MTR_VER not in [3, 4]:
        raise NotImplementedError('MTR_VER should be 3 or 4')

    IGNORED_LINES += ORIGINAL_IGNORED_LINES
    if LINK.endswith('/index.html'):
        LINK = LINK.rstrip('/index.html')

    if UPDATE_DATA is True or (not os.path.exists(LOCAL_FILE_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        data = fetch_data(LINK, LOCAL_FILE_PATH, MTR_VER)
    else:
        with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
            data = json.load(f)

    if GEN_ROUTE_INTERVAL is True or (not os.path.exists(INTERVAL_PATH)):
        # if MTR_VER == 4:
        #     raise NotImplementedError(
        #         'Please use the real-time pathfinder for MTR 4.0.0 '
        #         'or input interval data manually.')

        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        gen_route_interval(LOCAL_FILE_PATH, INTERVAL_PATH, LINK, MTR_VER)

    version1 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(LOCAL_FILE_PATH)))
    version2 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(INTERVAL_PATH)))

    if IN_THEORY is True:
        route_type = RouteType.IN_THEORY
    else:
        route_type = RouteType.WAITING

    if G is None:
        G = create_graph(data, IGNORED_LINES, CALCULATE_HIGH_SPEED,
                         CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT,
                         AVOID_STATIONS, route_type, ORIGINAL_IGNORED_LINES,
                         INTERVAL_PATH, version1, version2, LOCAL_FILE_PATH,
                         WILD_ADDITION, TRANSFER_ADDITION, INCLUDE_HIDDEN_ROUTES,
                         MAX_WILD_BLOCKS, MTR_VER, cache)

    shortest_path, shortest_distance, waiting_time, riding_time, ert = \
        find_shortest_route(G, station1, station2,
                            data, MTR_VER)

    return process_output(ert, data)


def run():
    # 地图设置
    # MTR模组版本（3/4），默认值为3
    MTR_VER: int = 4
    # 在线线路图网址，结尾删除"/"
    LINK: str = 'https://letsplay.minecrafttransitrailway.com/system-map'
    # 从A站到B站，非出站换乘（越野）的最远步行距离，默认值为1500
    MAX_WILD_BLOCKS: int = 1500
    # 手动增加出站换乘
    # "车站: [出站换乘的车站, ...], ..."
    TRANSFER_ADDITION: dict[str, list[str]] = {}
    # 手动增加非出站换乘（越野）
    # "车站: [非出站换乘的车站, ...], ..."
    WILD_ADDITION: dict[str, list[str]] = {}
    # 禁止乘坐的路线（未开通的路线）
    ORIGINAL_IGNORED_LINES: list = []

    link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
    # 文件设置
    LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-mtr{MTR_VER}-v3.json'
    INTERVAL_PATH = f'mtr-route-data-{link_hash}-mtr{MTR_VER}-v3.json'

    # 是否更新车站数据
    UPDATE_DATA: bool = True
    # 是否更新路线数据
    GEN_ROUTE_INTERVAL: bool = True

    # 寻路设置
    # 避开的路线
    IGNORED_LINES: list = []
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
    # 计算理论最快路线，不考虑等车时间，默认值为False
    IN_THEORY: bool = False
    INCLUDE_HIDDEN_ROUTES: bool = False

    # 出发、到达车站
    station1 = '6A3F938669582F91'
    station2 = 'A1373B7A50B564EB'

    print(main(station1, station2, LINK, LOCAL_FILE_PATH, INTERVAL_PATH,
         MAX_WILD_BLOCKS, TRANSFER_ADDITION, WILD_ADDITION,
         ORIGINAL_IGNORED_LINES, UPDATE_DATA, GEN_ROUTE_INTERVAL,
         IGNORED_LINES, AVOID_STATIONS, CALCULATE_HIGH_SPEED,
         CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT, IN_THEORY, INCLUDE_HIDDEN_ROUTES,
         MTR_VER))


if __name__ == '__main__':
    run()
