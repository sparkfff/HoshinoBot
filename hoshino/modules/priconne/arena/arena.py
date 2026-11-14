from hoshino import aiorequests, config
from .. import chara
from . import sv

import json

import asyncio
import re
import time
from random import random
from math import log
from pathlib import Path
import tempfile

_gs_data_dir = Path(__file__).parent / "buffer"

querylock = asyncio.Lock()
logger = sv.logger



def __get_auth_key():
    return config.priconne.arena.AUTH_KEY


class ArenaResponseError(ValueError):
    """The service returned an invalid arena result instead of usable data."""


def _validate_results(res):
    if not isinstance(res, dict) or type(res.get('code')) is not int:
        raise ArenaResponseError('Invalid response code')
    if res['code']:
        raise aiorequests.HTTPError(response=res)
    data = res.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('result'), list):
        raise ArenaResponseError('Invalid result list')
    results = data['result']
    for entry in results:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) \
                or not re.fullmatch(r'[0-9a-fA-F]{6,128}', entry['id']):
            raise ArenaResponseError('Invalid entry id')
        for key in ('up', 'down'):
            if type(entry.get(key)) is not int or entry[key] < 0:
                raise ArenaResponseError('Invalid vote count')
        for key in ('atk', 'def'):
            team = entry.get(key)
            if not isinstance(team, list) or len(team) > 5 or (key == 'atk' and not team):
                raise ArenaResponseError('Invalid team')
            for unit in team:
                if not isinstance(unit, dict) or type(unit.get('id')) is not int \
                        or unit['id'] <= 0 or type(unit.get('star')) is not int \
                        or not 0 <= unit['star'] <= 6 \
                        or type(unit.get('equip')) not in (int, bool) \
                        or unit['equip'] not in (0, 1):
                    raise ArenaResponseError('Invalid character')
    return results


def load_cache_index():
    try:
        with (_gs_data_dir / "buffer.json").open(encoding='utf8') as fp:
            index = json.load(fp)
    except (OSError, ValueError):
        return {}
    if not isinstance(index, dict):
        return {}
    return {key: value for key, value in index.items()
            if isinstance(key, str) and re.fullmatch(r'[0-9]{20}[1-4]', key)
            and type(value) in (int, float)}


def load_cached_results(key):
    if not re.fullmatch(r'[0-9]{20}[1-4]', key):
        return []
    try:
        with (_gs_data_dir / f'{key}.json').open(encoding='utf8') as fp:
            rows = json.load(fp)
        return _validate_results({'code': 0, 'data': {'result': rows}})
    except (OSError, ValueError):
        return []


def write_cache_file(name, value):
    with tempfile.NamedTemporaryFile(dir=_gs_data_dir, mode='w', encoding='utf8', delete=False) as fp:
        temporary = Path(fp.name)
        try:
            json.dump(value, fp, ensure_ascii=False)
        except BaseException:
            fp.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(_gs_data_dir / name)
    finally:
        temporary.unlink(missing_ok=True)


def find_approximate_results(defense, region):
    rows = []
    known = set(defense) - {1000}
    for key in load_cache_index():
        if region != 1 and int(key[-1]) != region:
            continue
        cached_defense = {int(key[i:i+4]) for i in range(0, 20, 4)}
        if len(known & cached_defense) >= 4:
            rows.extend(result2render(load_cached_results(key), defense))
    return sorted(rows, key=lambda row: row['val'], reverse=True)[:10]


def result2render(result, approximate_defense=None):
    rows = []
    for entry in result:
        kind = 'normal'
        if approximate_defense is not None:
            kind = 'approximation'
            requested = set(approximate_defense)
            actual = {unit['id'] // 100 for unit in entry['def']}
            missing, extra = requested - actual, actual - requested
            if len(requested) == 4:
                missing = {1000}
            if len(missing) == len(extra) == 1:
                kind += f' {next(iter(missing))} {next(iter(extra))}'
        up, down = entry['up'], entry['down']
        rows.append({
            'atk': [chara.fromid(u['id'] // 100, u['star'], u['equip']) for u in entry['atk']],
            'def': [chara.fromid(u['id'] // 100, u['star'], u['equip']) for u in entry['def']],
            'up': up, 'down': down, 'team_type': kind,
            'val': up / (up + down + 0.0001) * 2 - 1 + log(up + down + 0.01, 100) + random() / 1000,
        })
    return rows


async def do_query(id_list, region=1, try_cnt=1):
    if (not 4 <= len(id_list) <= 5 or region not in (1, 2, 3, 4)
            or any(type(unit) is not int or not 1000 <= unit < 1900 for unit in id_list)):
        return []
    if len(id_list) == 4 or 1000 in id_list:
        return await asyncio.to_thread(find_approximate_results, id_list, region)

    team_key = ''.join(str(unit) for unit in sorted(id_list))
    key = team_key + str(region)
    cache_order = {1: (2, 4, 3), 2: (1, 3, 4), 3: (1, 2, 4), 4: (1, 3, 2)}

    async with querylock:
        fallback = load_cached_results(key)
        now = int(time.time())
        index = load_cache_index()
        if fallback and now - index.get(key, 0) < 5 * 86400:
            return result2render(fallback)
        if not fallback:
            for other in cache_order[region]:
                fallback = load_cached_results(team_key + str(other))
                if fallback:
                    break
        if try_cnt == -1:
            return result2render(fallback)

        try:
            response = await aiorequests.post(
                'https://api.pcrdfans.com/x/v1/search',
                headers={'user-agent': 'Mozilla/5.0', 'authorization': __get_auth_key()},
                json={'_sign': 'a', 'def': [unit * 100 + 1 for unit in id_list],
                      'nonce': 'a', 'page': 1, 'sort': 1, 'ts': now, 'region': region},
                timeout=5,
            )
            try:
                response.raise_for_status()
                results = _validate_results(await response.json())
            finally:
                response.raw_response.close()
        except (aiorequests.RequestException, ValueError) as exc:
            logger.warning('竞技场数据库查询失败：%s', type(exc).__name__)
            if fallback:
                return result2render(fallback)
            nearby = await asyncio.to_thread(find_approximate_results, id_list, region)
            if nearby:
                return nearby
            raise

        if not results:
            return result2render(fallback) if fallback else await asyncio.to_thread(find_approximate_results, id_list, region)
        try:
            _gs_data_dir.mkdir(exist_ok=True)
            write_cache_file(f'{key}.json', results)
            index[key] = now
            write_cache_file('buffer.json', index)
        except OSError as exc:
            logger.warning('无法保存竞技场缓存：%s', exc)
        return result2render(results)
