"""Optional HTTP transport; independent of Hoshino, Torch and autopcr."""
from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request
from typing import Any, Sequence

from .model_backend import canonical_team


def build_request(attack: Sequence[int], defense: Sequence[int], manifest: dict,
                  repeats: int) -> dict[str, Any]:
    if not 1 <= repeats <= 20:
        raise ValueError("模拟次数必须在1至20之间")
    positions = manifest["positions"]

    def units(team: Sequence[int]) -> list[dict]:
        ordered = sorted(canonical_team(team), key=lambda unit: (positions[str(unit)], unit))
        result = []
        for unit in ordered:
            payload = copy.deepcopy(manifest["unit_template"])
            payload["unitId"] = unit
            result.append(payload)
        return result

    request = copy.deepcopy(manifest["request_template"])
    request.update(PlayerList=units(attack), EnemyList=units(defense), RepeatCount=repeats)
    return request


def parse_result(item: Any, repeats: int) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("Success") is not True:
        message = item.get("Message", "") if isinstance(item, dict) else ""
        raise ValueError("模拟器未成功执行：" + str(message)[:160])
    if repeats == 1:
        outcomes, frames, seeds = [item.get("Result")], [item.get("BattleFrames")], [item.get("CurrentSeed")]
    else:
        outcomes, frames, seeds = item.get("ResultList"), item.get("BattleFramesList"), item.get("CurrentSeedList")
    if not all(isinstance(values, list) and len(values) == repeats for values in (outcomes, frames, seeds)):
        raise ValueError("模拟器返回的重复次数/帧数/种子不完整")
    if any(type(value) is not int or value not in (0, 1, 2) for value in outcomes):
        raise ValueError("模拟器返回非法战果")
    if any(type(value) is not int or value < 0 for value in frames):
        raise ValueError("模拟器返回非法帧数")
    if any(type(value) is not int for value in seeds):
        raise ValueError("模拟器返回非法种子")
    winning = [frame / 60 for result, frame in zip(outcomes, frames) if result == 0]
    return {"wins": outcomes.count(0), "losses": outcomes.count(1), "timeouts": outcomes.count(2),
            "runs": repeats, "seconds": sum(winning) / len(winning) if winning else None,
            "results": outcomes, "frames": frames, "seeds": seeds}


class MZQClient:
    def __init__(self, endpoint: str, timeout: float = 30) -> None:
        url = urllib.parse.urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("MZQ地址必须是无内嵌凭据的HTTP(S)地址")
        self.endpoint, self.timeout = endpoint, timeout

    def simulate(self, pairs: Sequence[tuple[tuple[int, ...], tuple[int, ...]]],
                 manifest: dict, repeats: int) -> tuple[dict, dict]:
        if not pairs:
            return {}, {}
        if len(pairs) > 64:
            raise ValueError("单次最多模拟64组队伍")
        payload = [build_request(attack, defense, manifest, repeats) for attack, defense in pairs]
        request = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        # LAN simulator requests must not leak through an ambient HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=self.timeout) as response:
            content = response.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("模拟器响应过大")
        items = json.loads(content)
        if not isinstance(items, list) or len(items) != len(pairs):
            raise ValueError("模拟器批量结果与请求不对应")
        results, failures = {}, {}
        for pair, item in zip(pairs, items):
            try:
                results[pair] = parse_result(item, repeats)
            except ValueError as exc:
                failures[pair] = str(exc)
        # Explicit simulation errors are not losses and are not automatically retried.
        return results, failures
