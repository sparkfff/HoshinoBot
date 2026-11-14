"""Small CPU-only ONNX generator. Importing this module never imports Torch."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


# Same outcome order and utility as Autopcr's FAST_ATTACK goal.
FAST_ATTACK_UTILITY = np.array([1.0, 0.8, 0.6, 0.4, -1.0, -0.8], dtype=np.float32)


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, str(int(default))).strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ModelSettings:
    model_dir: Path = Path(__file__).parent / "models"
    enabled: bool = True
    simulate: bool = True
    endpoint: str = "http://127.0.0.1:8080/simulate"
    candidate_count: int = 64
    simulate_count: int = 32
    repeats: int = 10
    timeout: float = 30.0
    cache_seconds: float = 900.0
    threads: int = 2

    @classmethod
    def from_env(cls) -> "ModelSettings":
        return cls(
            model_dir=Path(os.environ.get("ARENA_MODEL_DIR", str(cls.model_dir))).expanduser(),
            enabled=_flag("ARENA_MODEL_ENABLED", True),
            simulate=_flag("ARENA_MZQ_ENABLED", True),
            endpoint=os.environ.get("ARENA_MZQ_URL", cls.endpoint),
            candidate_count=max(1, min(256, int(os.environ.get("ARENA_MODEL_CANDIDATES", "64")))),
            simulate_count=max(1, min(64, int(os.environ.get("ARENA_MZQ_CANDIDATES", "32")))),
            repeats=max(1, min(20, int(os.environ.get("ARENA_MZQ_REPEATS", "10")))),
            timeout=max(1.0, min(120.0, float(os.environ.get("ARENA_MZQ_TIMEOUT", "30")))),
            cache_seconds=max(0.0, float(os.environ.get("ARENA_MODEL_CACHE_SECONDS", "900"))),
            threads=max(1, min(8, int(os.environ.get("ARENA_MODEL_THREADS", "2")))),
        )


def canonical_team(values: Sequence[int]) -> tuple[int, ...]:
    team = tuple(sorted(int(value) for value in values))
    if len(team) != 5 or len(set(team)) != 5 or any(value < 100001 for value in team):
        raise ValueError("模型需要五名不同的六位角色ID")
    return team


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _softmax(logits: np.ndarray) -> np.ndarray:
    values = np.asarray(logits, dtype=np.float64)
    maximum = values.max(axis=-1, keepdims=True)
    if not np.all(np.isfinite(maximum)):
        raise ValueError("没有可以组成完整五人队的合法角色")
    weights = np.exp(values - maximum)
    return weights / weights.sum(axis=-1, keepdims=True)


class OnnxGenerator:
    def __init__(self, settings: ModelSettings) -> None:
        import onnxruntime as ort

        self.settings = settings
        root = settings.model_dir
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("format") != "arena_legacy_onnx_v1":
            raise ValueError("不支持的模型包格式")
        for filename, expected in self.manifest["files"].items():
            if Path(filename).name != filename or _digest(root / filename) != expected:
                raise ValueError("模型包文件校验失败")
        self.units = tuple(json.loads((root / "vocabulary.json").read_text(encoding="utf-8"))["unit_ids"])
        if self.units != tuple(sorted(set(self.units))) or len(self.units) < 5:
            raise ValueError("模型词表无效")
        self.lookup = {unit: index + 2 for index, unit in enumerate(self.units)}
        self.size = len(self.units) + 2
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(root / "generator.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.critic = None
        self.critic_lookup: dict[int, int] = {}
        if "critic.onnx" in self.manifest["files"]:
            critic_units = json.loads((root / "critic_vocabulary.json").read_text(encoding="utf-8"))["unit_ids"]
            self.critic_lookup = {unit: index + 2 for index, unit in enumerate(critic_units)}
            self.critic = ort.InferenceSession(str(root / "critic.onnx"), sess_options=options,
                                             providers=["CPUExecutionProvider"])

    def roster_for(self, region: int) -> set[int]:
        # An operator can supply actual region-release/owned lists. Never invent unknown embeddings.
        rosters = self.manifest.get("region_rosters", {})
        values = rosters.get(str(region))
        return set(self.units) if values is None else set(values) & set(self.units)

    def generate(self, defense: Sequence[int], region: int,
                 available: Sequence[int] | None = None) -> list[dict[str, Any]]:
        defense = canonical_team(defense)
        if any(unit not in self.lookup for unit in defense):
            raise ValueError("防守含模型词表尚未支持的角色")
        roster = self.roster_for(region)
        if available is not None:
            roster.intersection_update(available)
        allowed = np.asarray(sorted(self.lookup[unit] for unit in roster), dtype=np.int64)
        if len(allowed) < 5:
            raise ValueError("可用角色不足五名")
        identity = self.manifest["generator_sha256"] + str(region) + repr(defense) + repr(allowed.tolist())
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "little"))
        unique: dict[tuple[int, ...], float] = {}
        count = self.settings.candidate_count
        for _ in range(max(20, math.ceil(count / 32) * 10)):
            batch = min(32, count - len(unique) + 16)
            opponents = np.tile([self.lookup[unit] for unit in defense], (batch, 1))
            tokens = np.zeros((batch, 5), dtype=np.int64)
            tokens[:, 0] = 1
            selected = np.zeros((batch, 5), dtype=np.int64)
            score = np.zeros(batch, dtype=np.float64)
            for step in range(5):
                outputs = self.session.run(["logits"], {
                    "opponents": opponents.astype(np.int64),
                    "decoder_tokens": tokens,
                    "goals": np.zeros(batch, dtype=np.int64),
                })
                logits = np.asarray(outputs[0][:, step, :], dtype=np.float64).copy()
                if logits.shape != (batch, self.size) or not np.isfinite(logits).all():
                    raise ValueError("模型输出维度或数值异常")
                legal = np.zeros((batch, self.size), dtype=bool)
                feasible = allowed[:len(allowed) - (4 - step)]
                legal[:, feasible] = True
                if step:
                    legal &= np.arange(self.size)[None] > selected[:, step - 1, None]
                logits[~legal] = -np.inf
                probabilities = _softmax(logits)
                # Keep the original temperature=1/top-p=.95 sampling, outside the ONNX graph.
                order = np.argsort(-probabilities, axis=-1, kind="stable")
                sorted_p = np.take_along_axis(probabilities, order, axis=-1)
                sorted_p[np.cumsum(sorted_p, axis=-1) - sorted_p >= .95] = 0
                sorted_p /= sorted_p.sum(axis=-1, keepdims=True)
                cumulative = np.cumsum(sorted_p, axis=-1)
                ranks = (cumulative < rng.random((batch, 1))).sum(axis=-1).clip(max=self.size - 1)
                chosen = order[np.arange(batch), ranks]
                score += np.log(probabilities[np.arange(batch), chosen].clip(min=1e-300))
                selected[:, step] = chosen
                if step < 4:
                    tokens[:, step + 1] = chosen
            for row, likelihood in zip(selected, score):
                team = tuple(self.units[int(index) - 2] for index in row)
                if canonical_team(team) != team or not set(team) <= roster:
                    raise ValueError("模型生成了非法队伍")
                unique[team] = max(unique.get(team, -math.inf), float(likelihood))
            if len(unique) >= count:
                break
        # Return only actual generated unique teams. Never random-fill a short pool.
        result = [{"team": team, "generation_score": score, "seconds": None, "time_kind": "unknown"}
                  for team, score in sorted(unique.items(), key=lambda item: item[1], reverse=True)[:count]]
        try:
            self.estimate(defense, result)
        except Exception:
            logging.getLogger(__name__).exception("Optional Critic failed; generated candidates are preserved")
            for row in result:
                row.update(critic_score=None, seconds=None, time_kind="unknown", estimation_error=True)
        return result

    def estimate(self, defense: tuple[int, ...], rows: list[dict[str, Any]]) -> None:
        if self.critic is None or any(unit not in self.critic_lookup for unit in defense):
            return
        indices = [i for i, row in enumerate(rows) if all(unit in self.critic_lookup for unit in row["team"])]
        if not indices:
            return
        attack = np.asarray([[self.critic_lookup[unit] for unit in rows[i]["team"]] for i in indices], dtype=np.int64)
        defenders = np.tile([self.critic_lookup[unit] for unit in defense], (len(indices), 1)).astype(np.int64)
        logits = np.asarray(self.critic.run(["outcomes"], {"attacks": attack, "defends": defenders})[0])
        if logits.shape != (len(indices), 6) or not np.isfinite(logits).all():
            raise ValueError("Critic 输出维度或数值异常")
        probabilities = _softmax(logits)
        utilities = probabilities.astype(np.float32) @ FAST_ATTACK_UTILITY
        for i, p, utility in zip(indices, probabilities, utilities):
            rows[i]["critic_score"] = float(utility)
            win_mass = float(p[:4].sum())
            # A low-confidence conditional winning time is not a usable recommendation.
            if win_mass < .1:
                continue
            rows[i]["seconds"] = float(p[:4] @ np.asarray([7.5, 22.5, 45., 75.]) / win_mass)
            rows[i]["time_kind"] = "legacy_estimate"


_lock = threading.Lock()
_models: dict[ModelSettings, OnnxGenerator] = {}


def get_generator(settings: ModelSettings) -> OnnxGenerator:
    with _lock:
        if settings not in _models:
            _models[settings] = OnnxGenerator(settings)
        return _models[settings]
