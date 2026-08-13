"""Генерация субтитров в формате ASS.

Слова приходят из распознавания с индивидуальными таймкодами, поэтому можно
подсвечивать произносимое слово. Текст держится в безопасной зоне: снизу
интерфейс YouTube, сверху — баннер.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

FONT = "DejaVu Sans"

STYLES: dict[str, dict[str, Any]] = {
    "dynamic": {
        "font_size": 72,
        "bold": -1,
        "outline": 5,
        "shadow": 2,
        "primary": "&H00FFFFFF",
        "highlight": "&H0022DDFF",  # подсветка произносимого слова (BGR)
        "max_words": 3,
        "max_lines": 2,
    },
    "classic": {
        "font_size": 62,
        "bold": -1,
        "outline": 4,
        "shadow": 1,
        "primary": "&H00FFFFFF",
        "highlight": None,
        "max_words": 5,
        "max_lines": 2,
    },
    "minimal": {
        "font_size": 52,
        "bold": 0,
        "outline": 2,
        "shadow": 0,
        "primary": "&H00FFFFFF",
        "highlight": None,
        "max_words": 6,
        "max_lines": 2,
    },
}

# Отступ снизу: нижняя часть кадра перекрыта интерфейсом YouTube Shorts.
SAFE_BOTTOM_MARGIN = 420


def _timecode(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words: list[dict[str, Any]], max_words: int) -> list[list[dict[str, Any]]]:
    """Разбивает поток слов на короткие фразы для показа на экране."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for word in words:
        if current:
            gap = word["start"] - current[-1]["end"]
            ends_sentence = current[-1]["text"].endswith((".", "!", "?", "…"))
            if len(current) >= max_words or gap > 0.7 or (ends_sentence and len(current) >= 2):
                groups.append(current)
                current = []
        current.append(word)

    if current:
        groups.append(current)
    return groups


def build_ass(
    words: list[dict[str, Any]],
    style_name: str = "dynamic",
    bottom_margin: int = SAFE_BOTTOM_MARGIN,
) -> str:
    """Собирает ASS-файл. Таймкоды слов должны быть от начала фрагмента.

    Координаты всегда в эталонном кадре 1080×1920: libass сам отмасштабирует
    их под реальный размер видео, поэтому превью и финал выглядят одинаково.
    """
    style = STYLES.get(style_name, STYLES["dynamic"])

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT},{style['font_size']},{style['primary']},&H000000FF,&H00000000,&H80000000,{style['bold']},0,0,0,100,100,0,0,1,{style['outline']},{style['shadow']},2,90,90,{bottom_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    for group in group_words(words, style["max_words"]):
        if not group:
            continue
        if style["highlight"]:
            # Отдельное событие на каждое слово: подсвечивается произносимое.
            for i, word in enumerate(group):
                parts = []
                for j, other in enumerate(group):
                    text = _escape(other["text"])
                    if i == j:
                        parts.append(f"{{\\c{style['highlight']}}}{text}{{\\c{style['primary']}}}")
                    else:
                        parts.append(text)
                end = group[i + 1]["start"] if i + 1 < len(group) else word["end"]
                if end <= word["start"]:
                    end = word["start"] + 0.15
                lines.append(
                    f"Dialogue: 0,{_timecode(word['start'])},{_timecode(end)},Main,,0,0,0,,"
                    + " ".join(parts)
                )
        else:
            text = " ".join(_escape(w["text"]) for w in group)
            lines.append(
                f"Dialogue: 0,{_timecode(group[0]['start'])},{_timecode(group[-1]['end'])},Main,,0,0,0,,{text}"
            )

    return header + "\n".join(lines) + "\n"


def write_ass(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
