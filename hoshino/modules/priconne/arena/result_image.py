"""PCRCalculator-style arena result cards, using only Pillow and Hoshino portraits."""
from __future__ import annotations

from functools import lru_cache
from inspect import isawaitable
import logging
from math import ceil, isfinite
import os
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFont

LOGGER = logging.getLogger(__name__)
MARGIN = 20
ICON_SIZE = 64
ICON_GAP = 6
ROW_HEIGHT = ICON_SIZE + 10  # No EX equipment strip.
TEXT_X = MARGIN + 5 * (ICON_SIZE + ICON_GAP) + 40
FOOTER_HEIGHT = 46
GROUP_GAP = 20
STRIPE_COLOR = "#EEEEEE"
FOOTER_TEXT = "Power By PCRCalculator"


@lru_cache(maxsize=8)
def result_font(size: int):
    configured = os.environ.get("ARENA_RESULT_FONT")
    candidates = (
        configured,
        "msyh.ttc",
        "msyh.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "DejaVuSans.ttf",
    )
    for candidate in candidates:
        if candidate:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                if candidate == configured:
                    LOGGER.warning("Cannot load ARENA_RESULT_FONT; using fallback")
    return ImageFont.load_default()


def _seconds(value) -> str:
    return f"({value:.1f}s)" if isinstance(value, (int, float)) and isfinite(value) and value >= 0 else ""


def result_text(row: dict, character_name: Callable[[int], str] = str) -> str:
    """Never describe an estimate or execution error as a measured battle."""
    simulation = row.get("simulation")
    if simulation:
        grouped = {0: [], 1: []}
        for outcome, frame in zip(simulation.get("results", ()), simulation.get("frames", ())):
            if outcome in grouped:
                grouped[outcome].append(frame)
        parts = []
        for code, key, label in ((0, "wins", "胜"), (1, "losses", "负")):
            if count := simulation.get(key, 0):
                frames = grouped[code]
                mean = sum(frames) / len(frames) / 60 if frames else simulation.get("seconds") if code == 0 else None
                parts.append(f"{count}{label}{_seconds(mean)}")
        if count := simulation.get("timeouts", 0):
            parts.append(f"{count}超时")
        return " ".join(parts) or "模拟结果未知"
    kind = row.get("team_type")
    if kind == "normal":
        return f"赞 {row['up']}　踩 {row['down']}"
    if kind and kind.startswith("approximation"):
        parts = kind.split()
        if len(parts) == 3 and all(part.isdecimal() for part in parts[1:]):
            return f"近似解 · {character_name(int(parts[1]))} → {character_name(int(parts[2]))}"
        return "近似解"
    if kind == "frequency":
        return "高频解"
    if kind == "youshu":
        return "未知阵容"
    if row.get("model_generated") or kind == "model":
        prefix = "模拟失败 · " if row.get("simulation_error") else ""
        estimate = row.get("estimated_seconds")
        if _seconds(estimate):
            return f"{prefix}旧库粗估 ≈{estimate:.0f}s"
        return f"{prefix or '未模拟 · '}预估未知"
    return ""


async def _portrait(character, size: int) -> Image.Image | None:
    if character is not None:
        try:
            icon = character.render_icon(size)
            if isawaitable(icon):
                icon = await icon
            if isinstance(icon, Image.Image):
                return icon.convert("RGBA").resize((size, size), getattr(Image, "Resampling", Image).LANCZOS)
        except Exception:
            LOGGER.debug("Arena portrait unavailable for %s", getattr(character, "id", "?"), exc_info=True)
    return None


def _text_center_y(draw, text: str, x: int, y: int, height: int, font) -> None:
    _, top, _, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text((x, y + (height - (bottom - top)) // 2 - top), text, fill="black", font=font)


async def render_result_card(
    entries: Sequence[dict | list | str],
    *,
    defense_groups: Sequence[Sequence] = (),
    placeholder: Callable[[], object] = lambda: None,
    character_name: Callable[[int], str] = str,
) -> Image.Image:
    """Draw single JJC and grouped PJJC results using the reference's light layout."""
    font = result_font(30)
    small_font = result_font(20)
    rows = [
        (row, result_text(row, character_name)) if isinstance(row, dict)
        else (None, "未知阵容") if row == "placeholder" else (None, "")
        for row in entries
    ]
    multi = len(defense_groups) > 1
    width = max(800, ceil(TEXT_X + max((font.getlength(text) for _, text in rows), default=0) + MARGIN + 20))
    defense_height = len(defense_groups) * ROW_HEIGHT
    result_height = sum(ROW_HEIGHT if text or row else GROUP_GAP for row, text in rows)
    attack_y = MARGIN + defense_height
    footer_y = attack_y + result_height + MARGIN
    image = Image.new("RGB", (width, footer_y + FOOTER_HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    icons: dict[int, Image.Image | None] = {}

    async def draw_team(team: Sequence, x: int, y: int) -> None:
        for index, character in enumerate(team):
            left = x + index * (ICON_SIZE + ICON_GAP)
            key = id(character)
            if key not in icons:
                icons[key] = await _portrait(character, ICON_SIZE)
            if (icon := icons[key]) is not None:
                image.paste(icon, (left, y), icon)
            else:
                draw.rectangle((left, y, left + ICON_SIZE - 1, y + ICON_SIZE - 1), fill="#DCDCDC")
                _text_center_y(draw, "?", left + 25, y, ICON_SIZE, small_font)

    y = MARGIN
    for index, team in enumerate(defense_groups, 1):
        if multi:
            _text_center_y(draw, f"防守方 {index}", MARGIN, y, ICON_SIZE, small_font)
        team_width = len(team) * (ICON_SIZE + ICON_GAP) - ICON_GAP
        await draw_team(team, width - MARGIN - team_width, y)
        y += ROW_HEIGHT

    y = attack_y
    stripe = 0
    unknown_team = None
    for row, text in rows:
        if row is None and not text:
            y += GROUP_GAP
            stripe = 0
            continue
        if stripe % 2 == 0:
            draw.rectangle((0, y, width - 1, y + ROW_HEIGHT - 1), fill=STRIPE_COLOR)
        if row is None:
            if unknown_team is None:
                unknown_team = [placeholder()] * 5
            team = unknown_team
        else:
            team = row["atk"]
        await draw_team(team, MARGIN, y)
        if text:
            draw.line((TEXT_X - 7, y + 21, TEXT_X - 7, y + ICON_SIZE - 11), fill="black", width=2)
            _text_center_y(draw, text, TEXT_X + 5, y, ICON_SIZE, font)
        stripe += 1
        y += ROW_HEIGHT

    draw.line((0, footer_y, width - 1, footer_y), fill="black")
    _text_center_y(draw, FOOTER_TEXT, round((width - small_font.getlength(FOOTER_TEXT)) / 2),
                   footer_y, FOOTER_HEIGHT, small_font)
    draw.rectangle((0, 0, width - 1, image.height - 1), outline="black", width=2)
    draw.rectangle((4, 4, width - 5, image.height - 5), outline="black")
    return image
