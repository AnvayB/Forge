"""Chart rendering for progress reviews, from already-computed review metrics.

Renders static PNGs only - no LLM involvement, so this is deterministic and cheap
to run on every `!progress` / `!lastreview` reply. Each chart depicts one 30-day
period as a snapshot (no cross-period trend yet, since only one review has ever
been generated) - the module works from whatever a single `metrics` dict has, and
skips a chart entirely when its section is empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

_SURFACE = "#fcfcfb"
_INK_PRIMARY = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_GRIDLINE = "#e1e0d9"
_BASELINE = "#c3c2b7"
_SERIES_BLUE = "#2a78d6"


def _style_axes(ax: plt.Axes) -> None:
    ax.set_facecolor(_SURFACE)
    ax.figure.set_facecolor(_SURFACE)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_BASELINE)
    ax.tick_params(colors=_INK_SECONDARY, labelsize=9)
    ax.grid(axis="x", color=_GRIDLINE, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def _horizontal_bar_chart(
    *, title: str, labels: list[str], values: list[float], value_format: str, path: Path
) -> None:
    """Render one single-series magnitude-by-category bar chart to `path`.

    Single hue throughout (per the data-viz color formula: one measure across
    named categories is a magnitude job, not an identity job) - category names
    are direct labels, since a static image has no room for hover/legend.
    """

    fig, ax = plt.subplots(figsize=(6, 0.5 * len(labels) + 1.2), dpi=160)
    y_positions = list(range(len(labels)))
    ax.barh(y_positions, values, color=_SERIES_BLUE, height=0.6, zorder=3)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    _style_axes(ax)

    max_value = max(values) if values else 0.0
    ax.set_xlim(0, max_value * 1.2 if max_value else 1)
    for y, value in zip(y_positions, values, strict=True):
        ax.text(
            value + max_value * 0.02,
            y,
            value_format.format(value),
            va="center",
            ha="left",
            color=_INK_PRIMARY,
            fontsize=9,
        )

    ax.set_title(title, color=_INK_PRIMARY, fontsize=12, loc="left", pad=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=_SURFACE)
    plt.close(fig)


def build_progress_charts(metrics: dict[str, Any], output_dir: Path) -> list[Path]:
    """Render chart PNGs for one review's metrics, skipping sections with no data."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    volume_by_exercise: dict[str, float] = metrics.get("workouts", {}).get("volume_by_exercise") or {}
    if volume_by_exercise:
        top = sorted(volume_by_exercise.items(), key=lambda item: item[1], reverse=True)[:8]
        top.reverse()
        path = output_dir / "volume_by_exercise.png"
        _horizontal_bar_chart(
            title="Volume by exercise (sets x reps x weight)",
            labels=[name.title() for name, _ in top],
            values=[value for _, value in top],
            value_format="{:,.0f}",
            path=path,
        )
        paths.append(path)

    modalities: dict[str, int] = metrics.get("cardio", {}).get("modalities") or {}
    if modalities:
        items = sorted(modalities.items(), key=lambda item: item[1], reverse=True)
        items.reverse()
        path = output_dir / "cardio_sessions_by_modality.png"
        _horizontal_bar_chart(
            title="Cardio sessions by modality",
            labels=[name for name, _ in items],
            values=[float(value) for _, value in items],
            value_format="{:.0f}",
            path=path,
        )
        paths.append(path)

    adherence: dict[str, Any] = metrics.get("adherence") or {}
    nutrition: dict[str, Any] = metrics.get("nutrition") or {}
    adherence_items: list[tuple[str, float]] = []
    if "activity_completion_rate" in adherence:
        adherence_items.append(("Activity completion", adherence["activity_completion_rate"] * 100))
    if "protein_goal_adherence" in nutrition:
        adherence_items.append(("Protein goal met", nutrition["protein_goal_adherence"] * 100))
    if adherence_items:
        adherence_items.reverse()
        path = output_dir / "adherence.png"
        _horizontal_bar_chart(
            title="Adherence over the period",
            labels=[name for name, _ in adherence_items],
            values=[value for _, value in adherence_items],
            value_format="{:.0f}%",
            path=path,
        )
        paths.append(path)

    return paths
