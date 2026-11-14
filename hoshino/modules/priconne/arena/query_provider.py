"""Automatic augmentation of the ORIGINAL query; there is no user-facing mode."""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import time
from collections import OrderedDict
from typing import Any, Callable, Sequence

from .model_backend import ModelSettings, get_generator
from .mzq_client import MZQClient

logger = logging.getLogger(__name__)


class QueryResults(list):
    def __init__(self, rows: Sequence[dict] = (), notices: Sequence[str] = ()) -> None:
        super().__init__(rows)
        self.notices = list(dict.fromkeys(notices))


def _clone_result(value: QueryResults) -> QueryResults:
    # Host character objects can contain image/network handles; only immutable IDs are shared.
    rows = [{key: list(item) if key == "atk" else copy.deepcopy(item) for key, item in row.items()}
            for row in value]
    return QueryResults(rows, value.notices)


def team_key(row: dict) -> tuple[int, ...]:
    return tuple(sorted(int(character.id) for character in row["atk"]))


def time_text(row: dict) -> tuple[str, str]:
    """Both strings fit a 100px side column, without equipment graphics."""
    measured = row.get("simulation")
    if measured:
        if measured["wins"] == 0:
            return "模拟", "未获胜"
        return "模拟胜时", f'{measured["seconds"]:.1f}s'
    seconds = row.get("estimated_seconds")
    if seconds is not None and math.isfinite(seconds):
        return "旧库粗估", f"≈{seconds:.0f}s"
    if row.get("model_generated"):
        return "未模拟" if not row.get("simulation_error") else "模拟失败", "预估未知"
    return "", ""


def select_results(rows: Sequence[dict], limit: int) -> list[dict]:
    """Bound PJJC combinatorics and preserve a few actual model rows on degradation."""
    selected = list(range(min(limit, len(rows))))
    model_indices = [i for i, row in enumerate(rows) if row.get("model_generated")]
    missing = min(3, limit, len(model_indices)) - sum(rows[i].get("model_generated", False) for i in selected)
    additions = [i for i in model_indices if i not in selected]
    for index in additions[:max(0, missing)]:
        replacements = [i for i in selected if not rows[i].get("model_generated")]
        if replacements:
            selected.remove(replacements[-1])
            selected.append(index)
    return [rows[i] for i in sorted(selected)]


class ArenaModelProvider:
    def __init__(self, settings: ModelSettings, model_factory: Callable = get_generator,
                 client_factory: Callable = MZQClient) -> None:
        self.settings = settings
        self.model_factory, self.client_factory = model_factory, client_factory
        self._cache: OrderedDict[tuple, tuple[float, QueryResults]] = OrderedDict()
        self._gate = asyncio.Semaphore(1)

    def _get_cached(self, key: tuple) -> QueryResults | None:
        record = self._cache.get(key)
        if record is None or time.monotonic() - record[0] >= self.settings.cache_seconds:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return _clone_result(record[1])

    def _cache_result(self, key: tuple, value: QueryResults) -> None:
        self._cache[key] = (time.monotonic(), _clone_result(value))
        self._cache.move_to_end(key)
        while len(self._cache) > 128:
            self._cache.popitem(last=False)

    async def augment(self, defense: Sequence[int], region: int, database: Sequence[dict],
                      make_character: Callable[[int, int], Any],
                      only_use_cache: bool = False) -> QueryResults:
        database = list(database or [])
        if not self.settings.enabled:
            return QueryResults(database)
        # Four-unit approximation and PJJC unknown placeholders are NOT complete model inputs.
        if len(defense) != 5 or len(set(defense)) != 5 or any(unit == 1000 for unit in defense):
            return QueryResults(database, ["残缺或未知防守仅返回原查询结果，未生成或模拟。"])
        key = (tuple(sorted(defense)), int(region))
        cached = self._get_cached(key)
        if only_use_cache:
            return cached if cached is not None else QueryResults(database)
        async with self._gate:
            # Single-flight repeated screenshots; do not repeat the same battles on PJJC fallback.
            cached = self._get_cached(key)
            if cached is not None:
                return cached
            notices: list[str] = []
            full_defense = tuple(sorted(unit * 100 + 1 for unit in defense))
            try:
                model = await asyncio.to_thread(self.model_factory, self.settings)
                generated = await asyncio.to_thread(model.generate, full_defense, region)
            except Exception:
                logger.exception("Arena ONNX generation unavailable; original query is preserved")
                return QueryResults(database, ["模型暂不可用：请检查ONNX依赖及模型包；已保留原查询结果。"])

            # Database stars/equipment need not match the model's fixed maximum progression.
            # Preserve those records separately; NEVER attach full-progression results to them.
            database_rows = [dict(row, atk=list(row["atk"])) for row in database]
            records: OrderedDict[tuple[int, ...], dict] = OrderedDict()
            model_keys = []
            rejected = 0
            for prediction in generated:
                try:
                    team = tuple(sorted(int(unit) // 100 for unit in prediction["team"]))
                    if len(team) != 5 or len(set(team)) != 5:
                        raise ValueError("invalid generated team")
                    if team not in records:
                        rarity = model.manifest.get("rarities", {})
                        characters = [make_character(unit, int(rarity.get(str(unit * 100 + 1), 3))) for unit in team]
                        records[team] = {"atk": characters, "team_type": "model"}
                    row = records[team]
                    row.update(model_generated=True, generation_score=prediction["generation_score"],
                               critic_score=prediction.get("critic_score"),
                               estimated_seconds=prediction.get("seconds"),
                               estimate_kind=prediction.get("time_kind", "unknown"),
                               model_version=model.manifest["generator_sha256"])
                    model_keys.append(team)
                except (KeyError, TypeError, ValueError):
                    rejected += 1
            if rejected:
                notices.append(f"{rejected}支模型队伍不受本地角色资源支持，已明确跳过。")
            if any(row.get("estimation_error") for row in generated):
                notices.append("Critic 暂不可用，按生成概率筛选，未模拟队伍的秒数显示未知。")
            if not model_keys:
                return QueryResults(database, notices + ["模型未生成可用队伍，已保留原查询结果。"])

            def model_order(row: dict) -> tuple[float, float]:
                critic_score = row.get("critic_score")
                return (float(critic_score) if critic_score is not None else -math.inf,
                        float(row.get("generation_score", -math.inf)))

            if self.settings.simulate:
                selected = sorted(records, key=lambda team: model_order(records[team]),
                                  reverse=True)[:self.settings.simulate_count]
                known_errors = {(tuple(pair["attack"]), tuple(pair["defense"]))
                                for pair in model.manifest.get("excluded_matchups", [])}
                pairs = [(tuple(unit * 100 + 1 for unit in team), full_defense) for team in selected]
                eligible = []
                for team, pair in zip(selected, pairs):
                    if pair in known_errors:
                        records[team]["simulation_error"] = "训练刷新已留档的错误对局，不自动重试"
                    else:
                        eligible.append(pair)
                try:
                    client = self.client_factory(self.settings.endpoint, self.settings.timeout)
                    results, failures = await asyncio.to_thread(client.simulate, eligible, model.manifest,
                                                                self.settings.repeats)
                    for pair, result in results.items():
                        records[tuple(unit // 100 for unit in pair[0])]["simulation"] = result
                    for pair, error in failures.items():
                        records[tuple(unit // 100 for unit in pair[0])]["simulation_error"] = error
                    if failures or len(eligible) != len(pairs):
                        notices.append("部分模拟未成功或属于已留档错误：这些队伍未计作败绩，时间仅为粗估或未知。")
                except Exception:
                    logger.exception("Arena MZQ unavailable; candidates are not relabeled as losses")
                    for team in selected:
                        records[team]["simulation_error"] = "MZQ不可用"
                    notices.append("MZQ模拟不可用，已返回原查询和模型候选；未标为模拟结果。")
            else:
                notices.append("管理员关闭了MZQ，模型结果未模拟。")

            if len(model_keys) < self.settings.candidate_count:
                notices.append(f"模型实际生成{len(model_keys)}支不同合法队伍，未使用随机队补足。")

            def order(row: dict) -> tuple:
                simulation = row.get("simulation")
                if simulation:
                    if simulation["wins"]:
                        return (3, simulation["wins"] / simulation["runs"], -simulation["seconds"])
                    return (0, 0, 0)
                if row.get("team_type") != "model":
                    return (2, float(row.get("val", 0)), 0)
                return (1, *model_order(row))

            ordered = sorted(database_rows + list(records.values()), key=order, reverse=True)
            # Legacy PJJC treats val<-50 as a missing-team sentinel, NOT a model likelihood.
            for index, row in enumerate(ordered):
                row["val"] = 1.0 - index / max(1, len(ordered))
            value = QueryResults(ordered, notices)
            # Failed service calls have a short backoff rather than a 15-minute stale result.
            self._cache_result(key, value)
            if any(row.get("simulation_error") for row in ordered):
                self._cache[key] = (time.monotonic() - max(0, self.settings.cache_seconds - 15), _clone_result(value))
            return value


_provider: ArenaModelProvider | None = None


async def query_candidates(defense: Sequence[int], region: int,
                           only_use_cache: bool = False) -> QueryResults:
    """The single existing query path for text, images, delayed screenshots and PJJC."""
    from . import arena
    from .. import chara

    notices = []
    try:
        original = await arena.do_query(list(defense), region, -1 if only_use_cache else 1)
        original = original or []
    except Exception:
        logger.exception("Arena database lookup failed")
        original = []
        notices.append("PCRDFans 查询暂不可用。")
    global _provider
    try:
        if _provider is None:
            _provider = ArenaModelProvider(ModelSettings.from_env())
        result = await _provider.augment(defense, region, original, lambda unit, star: chara.fromid(unit, star),
                                         only_use_cache)
    except Exception:
        logger.exception("Arena model configuration invalid; preserving database results")
        result = QueryResults(original, ["模型配置异常，已保留原查询结果。"])
    result.notices = list(dict.fromkeys(notices + result.notices))
    return result
