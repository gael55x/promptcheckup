"""Render benchmark CSVs from benchmarks/results/ as static SVG charts (light and dark aware).

Run after run_gate_benchmark.py and run_checkup_simulation.py:

    python benchmarks/charts.py
"""

import csv
from collections import defaultdict
from html import escape
from pathlib import Path
from types import MappingProxyType

RESULTS = Path(__file__).parent / "results"

NOISE_TITLES = MappingProxyType(
    {
        "normal": "Normal noise",
        "heavy_tailed": "Heavy-tailed noise",
        "judge": "LLM-judge (−1/0/+1)",
        "crash_skew": "P&L crash skew",
    }
)
INCIDENT_TITLES = MappingProxyType(
    {
        "harmful_provider": ("Harmful", "provider change"),
        "harmless_provider": ("Harmless", "provider change"),
        "real_fix": ("Real fix", "proposed"),
        "useless_fix": ("Useless fix", "proposed"),
        "unreviewed_regression": ("Unreviewed", "prompt regression"),
    }
)
PROPOSAL_MARKERS = (("kept", "s3", 376), ("rejected", "s2", 407))
SHIPPED, PLAIN, BOOT = (
    "shipped: paired t + skew guard",
    "paired t without skew guard",
    "percentile bootstrap (rejected)",
)

# Reference palette, validated for colour-vision deficiency in light and dark mode.
STYLE = """<style>
  svg { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .bg { fill: #fcfcfb; } .ink { fill: #0b0b0b; } .ink2 { fill: #52514e; } .mute { fill: #898781; }
  .grid { stroke: #e1e0d9; } .axis { stroke: #c3c2b7; } .ref { stroke: #898781; }
  .s1 { stroke: #2a78d6; fill: #2a78d6; } .s2 { stroke: #eb6834; fill: #eb6834; }
  .s3 { stroke: #1baf7a; fill: #1baf7a; }
  .o1 { stroke: #86b6ef; fill: #86b6ef; } .o2 { stroke: #2a78d6; fill: #2a78d6; }
  .o3 { stroke: #104281; fill: #104281; } .ring { stroke: #fcfcfb; }
  @media (prefers-color-scheme: dark) {
    .bg { fill: #1a1a19; } .ink { fill: #f0efec; } .ink2 { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; } .axis { stroke: #383835; }
    .s1 { stroke: #3987e5; fill: #3987e5; } .s2 { stroke: #d95926; fill: #d95926; }
    .s3 { stroke: #199e70; fill: #199e70; }
    .o1 { stroke: #184f95; fill: #184f95; } .o2 { stroke: #3987e5; fill: #3987e5; }
    .o3 { stroke: #9ec5f4; fill: #9ec5f4; } .ring { stroke: #1a1a19; }
  }
</style>"""


def svg(width: int, height: int, title: str, body: list[str]) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-label="{escape(title)}">',
            f"<title>{escape(title)}</title>",
            STYLE,
            f'<rect class="bg" width="{width}" height="{height}" rx="8"/>',
            *body,
            "</svg>",
        ]
    )


def text(x, y, s, cls="ink2", size=12, anchor="start", weight=400) -> str:
    return (
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{escape(s)}</text>'
    )


def line_panel(x0, y0, w, h, series, x_ticks, y_max, y_step, title, ref=None, y_label=True):
    """Draw one line-chart panel. series: list of (css_class, [(x, y), ...])."""
    x_min, x_max = x_ticks[0], x_ticks[-1]

    def sx(v):
        return x0 + (v - x_min) / (x_max - x_min) * w

    def sy(v):
        return y0 + h - v / y_max * h

    out = [
        text(x0, y0 - 12, title, cls="ink", size=13, weight=500),
        *_grid(x0, w, sy, y_max, y_step, y_label),
        *_axes(x0, y0, w, h, sx, x_ticks),
        *_reference_line(x0, w, sy, ref),
        *_series(series, sx, sy),
    ]
    return out, sx, sy


def _grid(x0, w, sy, y_max, y_step, y_label):
    out = []
    tick = 0.0
    while tick <= y_max + 1e-9:
        out.append(
            f'<line class="grid" x1="{x0}" x2="{x0 + w}" y1="{sy(tick):.1f}" '
            f'y2="{sy(tick):.1f}" stroke-width="1"/>'
        )
        if y_label:
            out.append(text(x0 - 8, sy(tick) + 4, f"{tick:g}%", cls="mute", size=11, anchor="end"))
        tick += y_step
    return out


def _axes(x0, y0, w, h, sx, x_ticks):
    out = []
    out.append(
        f'<line class="axis" x1="{x0}" x2="{x0 + w}" y1="{y0 + h}" y2="{y0 + h}" stroke-width="1"/>'
    )
    for t in x_ticks:
        out.append(text(sx(t), y0 + h + 16, str(t), cls="mute", size=11, anchor="middle"))
    return out


def _reference_line(x0, w, sy, ref):
    out = []
    if ref is not None:
        value, label = ref
        out.append(
            f'<line class="ref" x1="{x0}" x2="{x0 + w}" y1="{sy(value):.1f}" '
            f'y2="{sy(value):.1f}" stroke-width="1.5" stroke-dasharray="5 4"/>'
        )
        if label:
            out.append(text(x0 + w, sy(value) - 5, label, cls="mute", size=11, anchor="end"))
    return out


def _series(series, sx, sy):
    out = []
    for cls, points in series:
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(points)
        )
        out.append(
            f'<path class="{cls}" d="{path}" style="fill:none" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, y in points:
            out.append(f'<circle class="{cls}" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4"/>')
            out.append(
                f'<circle class="ring" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="5" '
                f'fill="none" stroke-width="2"/>'
            )
    return out


def legend(x, y, items) -> list[str]:
    out, cursor = [], x
    for cls, label in items:
        out.append(
            f'<line class="{cls}" x1="{cursor}" x2="{cursor + 18}" y1="{y - 4}" '
            f'y2="{y - 4}" stroke-width="2"/>'
        )
        out.append(f'<circle class="{cls}" cx="{cursor + 9}" cy="{y - 4}" r="4"/>')
        out.append(text(cursor + 24, y, label, cls="ink2", size=12))
        cursor += 24 + 7 * len(label) + 20
    return out


def read(name: str) -> list[dict]:
    with (RESULTS / name).open() as f:
        return list(csv.DictReader(f))


def false_positive_chart() -> str:
    rows = read("false_positive.csv")
    by = defaultdict(list)
    for r in rows:
        by[(r["noise"], r["method"])].append((int(r["cases"]), 100 * float(r["false_better_rate"])))
    ticks = sorted({int(r["cases"]) for r in rows})
    body = [
        text(
            24,
            30,
            'False "better" verdicts when the prompt change did nothing',
            cls="ink",
            size=15,
            weight=500,
        ),
        *legend(
            24,
            56,
            [
                ("s1", "Shipped: t-interval + skew guard"),
                ("s3", "t-interval alone"),
                ("s2", "Percentile bootstrap (rejected)"),
            ],
        ),
    ]
    for i, noise in enumerate(["normal", "heavy_tailed", "judge", "crash_skew"]):
        panel, _, _ = line_panel(
            60 + i * 195,
            100,
            160,
            170,
            [("s2", by[(noise, BOOT)]), ("s3", by[(noise, PLAIN)]), ("s1", by[(noise, SHIPPED)])],
            ticks,
            y_max=16,
            y_step=4,
            title=NOISE_TITLES[noise],
            ref=(2.5, None),
            y_label=i == 0,
        )
        body += panel
    body += [
        text(430, 312, "Number of cases", cls="mute", size=12, anchor="middle"),
        text(
            24,
            340,
            "Dashed line: 2.5% target. t-interval: 4,000 simulations per point; bootstrap: "
            "1,000. Judge preferences run without the skew guard (bounded scores).",
            cls="mute",
            size=11,
        ),
    ]
    return svg(840, 356, 'False "better" rate by method, noise type and case count', body)


def power_chart() -> str:
    rows = read("power.csv")
    by = defaultdict(list)
    for r in rows:
        by[float(r["effect_sd"])].append((int(r["cases"]), 100 * float(r["detected_rate"])))
    ticks = sorted({int(r["cases"]) for r in rows})
    body = [
        text(
            24, 30, "How often the gate detects a real improvement", cls="ink", size=15, weight=500
        ),
        *legend(
            24, 56, [("o3", "Large (0.8 SD)"), ("o2", "Medium (0.5 SD)"), ("o1", "Small (0.2 SD)")]
        ),
    ]
    panel, _, _ = line_panel(
        70,
        100,
        560,
        220,
        [("o1", by[0.2]), ("o2", by[0.5]), ("o3", by[0.8])],
        ticks,
        y_max=100,
        y_step=20,
        title="Detected as “better”",
        ref=(80, "80% detection"),
    )
    body += panel
    body += [
        text(350, 360, "Number of cases", cls="mute", size=12, anchor="middle"),
        text(
            24,
            388,
            "Improvement size is in standard deviations of the per-case difference. "
            "2,000 simulations per point.",
            cls="mute",
            size=11,
        ),
    ]
    return svg(720, 404, "Detection rate by improvement size and case count", body)


def naive_vs_paired_chart() -> str:
    rows = {r["method"]: r for r in read("naive_vs_paired.csv")}
    items = [
        ("s2", "Naive (different weeks)", rows["naive (different weeks)"]),
        ("s1", "Paired replay", rows["paired replay"]),
    ]
    x0, w, bar_h = 200, 440, 30
    body = [
        text(
            24, 30, "How often each method picked the worse prompt", cls="ink", size=15, weight=500
        ),
        text(
            24,
            50,
            "Trading-like scenario: shared daily market noise, prompt B truly better "
            "(+0.3 per day), 20 days",
            cls="mute",
            size=11,
        ),
    ]
    for tick in (0, 10, 20, 30, 40, 50):
        x = x0 + tick / 50 * w
        body.append(
            f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="72" y2="172" stroke-width="1"/>'
        )
        body.append(text(x, 190, f"{tick}%", cls="mute", size=11, anchor="middle"))
    for i, (cls, label, r) in enumerate(items):
        y = 84 + i * 48
        value = 100 * float(r["wrong"])
        width = max(value / 50 * w, 0)
        body.append(text(x0 - 12, y + 20, label, cls="ink2", size=12, anchor="end"))
        if width:
            body.append(
                f'<rect class="{cls}" x="{x0}" y="{y}" width="{width:.1f}" '
                f'height="{bar_h}" rx="4"/>'
            )
        body.append(text(x0 + width + 8, y + 20, f"{value:.1f}%", cls="ink", size=12, weight=500))
    body.append(f'<line class="axis" x1="{x0}" x2="{x0}" y1="72" y2="172" stroke-width="1"/>')
    paired = rows["paired replay"]
    body.append(
        text(
            24,
            218,
            f"Naive always guesses. Paired replay picked B in "
            f"{100 * float(paired['correct']):.1f}% of runs and said “not sure "
            f"yet” in {100 * float(paired['no_call']):.1f}%. 2,000 runs.",
            cls="mute",
            size=11,
        )
    )
    return svg(720, 236, "Wrong-prompt rate: naive comparison vs paired replay", body)


def _timeline_bars(by_week, sx, sy):
    out = []
    for week, rows in sorted(by_week.items()):
        rate = 100 * sum(row["drift_decision"] == "worse" for row in rows) / len(rows)
        out.append(
            f'<rect class="s1" x="{sx(week) - 4:.1f}" y="{sy(rate):.1f}" '
            f'width="8" height="{sy(0) - sy(rate):.1f}">'
            f"<title>Week {week}: {rate:.1f}% flagged drift</title></rect>"
        )
    return out


def _incident_markers(rows, sx, bottom):
    incidents = {(int(row["week"]), row["incident"]) for row in rows if row["incident"] != "quiet"}
    out = []
    for week, kind in sorted(incidents):
        first, second = INCIDENT_TITLES[kind]
        x = sx(week)
        out.extend(
            [
                f'<g id="incident-{kind}"><title>Week {week}: {first} {second}</title>',
                text(x, 80, first, cls="ink", size=11, anchor="middle", weight=500),
                text(x, 95, second, size=11, anchor="middle"),
                f'<line class="ref" x1="{x:.1f}" x2="{x:.1f}" y1="140" y2="{bottom:.1f}" '
                'stroke-width="1" stroke-dasharray="3 4"/>',
                # Neutral ink: an incident is not a proposal outcome, so it gets no series colour.
                f'<path class="ink" d="M{x:.1f},103 l4,5 l-4,5 l-4,-5 Z"/>',
                "</g>",
            ]
        )
    return out


def _proposal_markers(by_week, sx):
    out = []
    for outcome, cls, y in PROPOSAL_MARKERS:
        out.append(f'<g id="proposal-{outcome}">')
        for week, rows in sorted(by_week.items()):
            count = sum(row["proposal_outcome"] == outcome for row in rows)
            if count:
                label = f"{100 * count / len(rows):.1f}% {outcome}"
                out.append(
                    f'<circle class="{cls}" cx="{sx(week):.1f}" cy="{y}" r="4">'
                    f"<title>Week {week}: {label}</title></circle>"
                )
                out.append(text(sx(week), y + 16, label, size=11, anchor="middle"))
        out.append("</g>")
    return out


def checkup_timeline_chart(path: Path = RESULTS / "checkup_simulation.csv") -> str:
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    by_week = defaultdict(list)
    for row in rows:
        by_week[int(row["week"])].append(row)
    weeks = sorted(by_week)
    ticks = sorted({weeks[0], weeks[-1], *range(8, weeks[-1] + 1, 8)})
    panel, sx, sy = line_panel(70, 140, 700, 190, [], ticks, 100, 20, "Runs flagged as drift")
    repeats = len({row["repeat"] for row in rows})
    body = [
        text(24, 30, "A simulated year of weekly check-ups", cls="ink", size=15, weight=500),
        text(24, 50, f"{repeats} seeded repeats; planted incidents, not real users", size=12),
        *panel,
        *_timeline_bars(by_week, sx, sy),
        *_incident_markers(rows, sx, sy(0)),
        text(420, 363, "Week", cls="mute", size=12, anchor="middle"),
        *_proposal_markers(by_week, sx),
        *legend(
            24, 450, [("s1", "Drift flagged"), ("s3", "Proposal kept"), ("s2", "Proposal rejected")]
        ),
        text(
            24,
            474,
            "Diamonds: incidents. Proposal labels: share of repeats. "
            "New prompts use the previous snapshot.",
            cls="mute",
            size=11,
        ),
    ]
    return svg(840, 490, "Weekly drift flags and proposal outcomes across a simulated year", body)


if __name__ == "__main__":
    for name, chart in (
        ("false_positive.svg", false_positive_chart()),
        ("power.svg", power_chart()),
        ("naive_vs_paired.svg", naive_vs_paired_chart()),
        ("checkup_timeline.svg", checkup_timeline_chart()),
    ):
        (RESULTS / name).write_text(chart + "\n")
        print(f"wrote {RESULTS / name}")
