from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from runtime_guard import require_internal_context

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

MISSING_MARKERS = {"", "N/A", "NA", "n/a", "na", "---", None}
DEFAULT_COLORS = ["#0B5CAD", "#C95F1A", "#4F7D2A", "#8A4E9E", "#C9A227"]


def apply_font_defaults() -> None:
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "Noto Sans CJK SC",
        "Noto Serif CJK SC",
        "WenQuanYi Zen Hei",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def parse_number(value: Any) -> float | None:
    if value in MISSING_MARKERS:
        return None
    text = str(value).strip()
    if text in MISSING_MARKERS:
        return None
    if text.startswith(("<", ">", "<=", ">=", "≤", "≥", "＜", "＞")):
        return None
    cleaned = (
        text.replace(",", "")
        .replace("%", "")
        .replace("％", "")
        .replace("mg/ml", "")
        .replace("mg/mL", "")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        match = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned)
        if match:
            return float(match.group(0))
        embedded = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
        return float(embedded.group(0)) if embedded else None


def numeric_points(series: dict[str, Any]) -> tuple[list[float], list[float], list[str]]:
    x_values = series.get("x", [])
    y_values = series.get("y", [])
    x_out: list[float] = []
    y_out: list[float] = []
    skipped: list[str] = []

    for idx, (x_raw, y_raw) in enumerate(zip(x_values, y_values)):
        x = parse_number(x_raw)
        y = parse_number(y_raw)
        if x is None or y is None:
            skipped.append(f"index {idx}: x={x_raw!r}, y={y_raw!r}")
            continue
        x_out.append(x)
        y_out.append(y)
    return x_out, y_out, skipped


def safe_filename(chart: dict[str, Any], ordinal: int) -> str:
    explicit = chart.get("filename")
    if explicit:
        return str(explicit)
    figure_no = chart.get("figure_no", ordinal)
    try:
        return f"figure-{int(figure_no):02d}.png"
    except (TypeError, ValueError):
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", str(figure_no)).strip("-._")
        return f"{stem or f'figure-{ordinal:02d}'}.png"


def apply_axes_options(ax, chart: dict[str, Any], xlabel: str | None = None) -> None:
    ax.set_xlabel(xlabel or chart.get("xlabel") or "Month")
    ax.set_ylabel(chart.get("ylabel") or "")
    ylim = chart.get("ylim")
    if isinstance(ylim, list) and len(ylim) == 2:
        ax.set_ylim(float(ylim[0]), float(ylim[1]))
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False, fontsize=8, loc="best")


def plot_line_chart(chart: dict[str, Any], output_path: Path, dpi: int) -> dict[str, Any]:
    fig, ax = plt.subplots(figsize=tuple(chart.get("figsize", [7.2, 4.0])), dpi=dpi)
    skipped: list[dict[str, Any]] = []
    plotted = 0

    for idx, series in enumerate(chart.get("series", [])):
        x, y, skipped_points = numeric_points(series)
        label = series.get("label") or f"Series {idx + 1}"
        if skipped_points:
            skipped.append({"series": label, "points": skipped_points})
        if not x:
            continue
        ax.plot(
            x,
            y,
            marker=series.get("marker", "o"),
            linewidth=float(series.get("linewidth", 2.2)),
            label=label,
            color=series.get("color") or DEFAULT_COLORS[idx % len(DEFAULT_COLORS)],
        )
        plotted += 1

    ax.set_title(chart.get("title") or output_path.stem, fontsize=12, pad=10)
    apply_axes_options(ax, chart)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return {"plotted_series": plotted, "skipped_points": skipped}


def plot_panel_chart(chart: dict[str, Any], output_path: Path, dpi: int) -> dict[str, Any]:
    panels = chart.get("panels", [])
    if not panels:
        return plot_line_chart(chart, output_path, dpi)

    fig, axes = plt.subplots(
        len(panels),
        1,
        figsize=tuple(chart.get("figsize", [7.4, 5.8])),
        dpi=dpi,
        sharex=bool(chart.get("sharex", True)),
    )
    if len(panels) == 1:
        axes = [axes]

    skipped: list[dict[str, Any]] = []
    plotted = 0
    for panel_idx, (ax, panel) in enumerate(zip(axes, panels)):
        panel_chart = {**chart, **panel}
        for series_idx, series in enumerate(panel.get("series", [])):
            x, y, skipped_points = numeric_points(series)
            label = series.get("label") or f"Series {series_idx + 1}"
            if skipped_points:
                skipped.append({"panel": panel.get("title", panel_idx + 1), "series": label, "points": skipped_points})
            if not x:
                continue
            ax.plot(
                x,
                y,
                marker=series.get("marker", "o"),
                linewidth=float(series.get("linewidth", 2.2)),
                label=label,
                color=series.get("color") or DEFAULT_COLORS[series_idx % len(DEFAULT_COLORS)],
            )
            plotted += 1
        ax.set_ylabel(panel.get("ylabel") or panel.get("title") or chart.get("ylabel") or "")
        ax.grid(True, alpha=0.25)
        ax.legend(frameon=False, fontsize=8, loc="best")
        ylim = panel.get("ylim") or chart.get("ylim")
        if isinstance(ylim, list) and len(ylim) == 2:
            ax.set_ylim(float(ylim[0]), float(ylim[1]))

    axes[-1].set_xlabel(chart.get("xlabel") or "Month")
    fig.suptitle(chart.get("title") or output_path.stem, fontsize=12, y=0.99)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return {"plotted_series": plotted, "skipped_points": skipped}


def generate_charts(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plt.style.use(config.get("style", "seaborn-v0_8-whitegrid"))
    apply_font_defaults()
    dpi = int(config.get("dpi", 180))
    manifest: dict[str, Any] = {"charts": [], "warnings": []}

    for ordinal, chart in enumerate(config.get("charts", []), start=1):
        output_path = output_dir / safe_filename(chart, ordinal)
        chart_type = chart.get("type", "line")
        if chart_type in {"two_panel", "panel", "panels"}:
            stats = plot_panel_chart(chart, output_path, dpi)
        elif chart_type == "line":
            stats = plot_line_chart(chart, output_path, dpi)
        else:
            manifest["warnings"].append(f"Unsupported chart type {chart_type!r} for {output_path.name}; skipped.")
            continue

        record = {
            "figure_no": chart.get("figure_no", ordinal),
            "figure_ref": f"图3.2.S.7.3- {chart.get('figure_no', ordinal)}",
            "title": chart.get("title", output_path.stem),
            "type": chart_type,
            "path": str(output_path),
            **stats,
        }
        for key in ["study_key", "group", "section", "source_ref", "caption", "body_section"]:
            if chart.get(key):
                record[key] = chart[key]
        if stats["plotted_series"] == 0:
            manifest["warnings"].append(f"No numeric series plotted for {output_path.name}.")
        manifest["charts"].append(record)

    return manifest


def main() -> None:
    require_internal_context("generate_stability_trend_charts.py")
    parser = argparse.ArgumentParser(description="Generate CTD 3.2.S.7.3 stability trend charts from JSON data.")
    parser.add_argument("input_json", type=Path, help="Chart config JSON.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path)
    args = parser.parse_args()

    config = json.loads(args.input_json.read_text(encoding="utf-8"))
    manifest = generate_charts(config, args.output_dir)
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.manifest_out:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(manifest_text, encoding="utf-8")
    print(manifest_text)


# Internal module — do not invoke directly. Use skill_step.py as the public entry point.
if __name__ == "__main__":
    main()
