#!/usr/bin/env python3
"""Emit the per-trial distribution figure of the seminar report as TikZ.

The figure shows one box per allocator block for the four release-off pairs of
the correction run. Every coordinate comes from the archived per-trial files,
so the figure carries the same audit trail as the tables.

Write the figure:

    python3 scripts/generate_distribution_figure.py

Confirm that the committed figure still agrees with the data:

    python3 scripts/generate_distribution_figure.py --check

It needs no third-party package.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RAW = Path("results/node85-rerun/raw/redis")
DEFAULT_OUTPUT = Path("notes/figure-distributions.tex")

SEQUENTIAL_OPERATIONS = {"lpush5", "lrange5"}
RELEASE_OFF_SUFFIX = "-paper-release-off"

# Geometry. One y unit is one kRPS above the axis floor, and the floor and
# ceiling come from the data, so no whisker can fall outside the axis.
TICK_STEP = 20.0
Y_SCALE_CM = 0.036   # height of one kRPS in the picture
FIRST_CENTER = 0.870
PAIR_PITCH = 1.300
ALLOCATOR_PITCH = 0.560
BOX_HALF_WIDTH = 0.220
CAP_HALF_WIDTH = 0.090


@dataclass(frozen=True)
class Box:
    pair: int
    allocator: str
    p5: float
    q1: float
    median: float
    q3: float
    p95: float

    @property
    def center(self) -> float:
        offset = 0.0 if self.allocator == "legacy" else ALLOCATOR_PITCH
        return FIRST_CENTER + (self.pair - 5) * PAIR_PITCH + offset

    @property
    def spread(self) -> float:
        """Width from the 5th to the 95th percentile, as a percentage."""
        return (self.p95 - self.p5) / self.median * 100.0


def combined_krps(block: Path) -> list[float]:
    """Per-trial harmonic mean of the two operation rates, in kRPS."""
    per_trial: dict[int, dict[str, float]] = {}
    with (block / "trials.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            per_trial.setdefault(int(row["trial"]), {})[row["operation"]] = float(row["rps"])
    rates = []
    for operations in per_trial.values():
        if len(operations) == 2:
            first, second = operations.values()
            rates.append(2.0 / (1.0 / first + 1.0 / second) / 1000.0)
    return sorted(rates)


def percentile(values: list[float], point: float) -> float:
    """Linear interpolation between closest ranks."""
    rank = (len(values) - 1) * point / 100.0
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (rank - low)


def sequential_release_off_blocks(raw_dir: Path) -> list[Path]:
    """Release-off blocks of the sequential workload, oldest first."""
    blocks = []
    for candidate in sorted(raw_dir.iterdir()):
        if not candidate.is_dir() or not candidate.name.endswith(RELEASE_OFF_SUFFIX):
            continue
        trials = candidate / "trials.csv"
        if not trials.is_file():
            continue
        with trials.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            first = next(reader, None)
        if first and first["operation"] in SEQUENTIAL_OPERATIONS:
            blocks.append(candidate)
    return blocks


def collect(raw_dir: Path) -> list[Box]:
    """Group the blocks into consecutive pairs and reduce each to a Box."""
    blocks = sequential_release_off_blocks(raw_dir)
    if len(blocks) % 2:
        raise SystemExit(f"found {len(blocks)} release-off blocks, expected an even count")

    boxes = []
    for index in range(0, len(blocks), 2):
        pair_number = 5 + index // 2
        # The figure always draws legacy on the left, whatever the run order.
        couple = sorted(blocks[index:index + 2],
                        key=lambda p: "legacy" not in p.name)
        for block in couple:
            allocator = "legacy" if "legacy" in block.name else "temeraire"
            values = combined_krps(block)
            if not values:
                raise SystemExit(f"no paired trials in {block}")
            boxes.append(Box(
                pair=pair_number,
                allocator=allocator,
                p5=percentile(values, 5),
                q1=percentile(values, 25),
                median=statistics.median(values),
                q3=percentile(values, 75),
                p95=percentile(values, 95),
            ))
    return boxes


def axis_bounds(boxes: list[Box]) -> tuple[float, float]:
    """Round outward to a whole tick so every whisker lies inside the axis."""
    low = min(b.p5 for b in boxes)
    high = max(b.p95 for b in boxes)
    floor = math.floor(low / TICK_STEP) * TICK_STEP
    ceiling = math.ceil(high / TICK_STEP) * TICK_STEP
    return floor, ceiling


def y(value: float, floor: float) -> str:
    return f"{value - floor:.2f}"


def render_box(box: Box, floor: float) -> list[str]:
    style = "boxlegacy" if box.allocator == "legacy" else "boxtemeraire"
    center = box.center
    left, right = center - BOX_HALF_WIDTH, center + BOX_HALF_WIDTH
    cap_left, cap_right = center - CAP_HALF_WIDTH, center + CAP_HALF_WIDTH
    return [
        f"% pair {box.pair}, {box.allocator}",
        f"\\draw[whisker] ({center:.3f},{y(box.p5, floor)}) -- ({center:.3f},{y(box.q1, floor)});",
        f"\\draw[whisker] ({center:.3f},{y(box.q3, floor)}) -- ({center:.3f},{y(box.p95, floor)});",
        f"\\draw[whisker] ({cap_left:.3f},{y(box.p5, floor)}) -- ({cap_right:.3f},{y(box.p5, floor)});",
        f"\\draw[whisker] ({cap_left:.3f},{y(box.p95, floor)}) -- ({cap_right:.3f},{y(box.p95, floor)});",
        f"\\draw[{style}] ({left:.3f},{y(box.q1, floor)}) rectangle ({right:.3f},{y(box.q3, floor)});",
        f"\\draw[medianline] ({left:.3f},{y(box.median, floor)}) -- ({right:.3f},{y(box.median, floor)});",
    ]


def render(boxes: list[Box]) -> str:
    floor, ceiling = axis_bounds(boxes)
    height = ceiling - floor
    pairs = sorted({b.pair for b in boxes})
    ticks = ", ".join(
        f"{int(floor + n)}/{int(n)}"
        for n in range(0, int(height) + 1, int(TICK_STEP))
    )
    labels = ", ".join(
        f"{FIRST_CENTER + (p - 5) * PAIR_PITCH + ALLOCATOR_PITCH / 2:.2f}/{p}"
        for p in pairs
    )
    spread = statistics.median(b.spread for b in boxes)

    lines = [
        "% Generated by scripts/generate_distribution_figure.py. Do not edit.",
        f"\\begin{{tikzpicture}}[x=1cm, y={Y_SCALE_CM}cm]",
        "\\tikzset{",
        "  whisker/.style={draw=myborder, line width=0.5pt},",
        "  boxlegacy/.style={draw=myborder, fill=mygrayfill, line width=0.5pt},",
        "  boxtemeraire/.style={draw=myblue, fill=mybluefill, line width=0.5pt},",
        "  medianline/.style={draw=myborder, line width=1.0pt}",
        "}",
        "",
        f"\\draw[draw=myborder, line width=0.5pt] (0.38,0) -- (0.38,{height:.0f});",
        f"\\foreach \\value/\\offset in {{{ticks}}} {{",
        "  \\draw[draw=myborder, line width=0.5pt] (0.30,\\offset) -- (0.38,\\offset);",
        "  \\node[labelbox, anchor=east] at (0.28,\\offset) {\\value};",
        "}",
        f"\\node[labelbox, rotate=90, anchor=south] at (-0.30,{height / 2:.0f}) "
        "{combined kRPS};",
        "",
    ]
    for box in boxes:
        lines.extend(render_box(box, floor))
    lines.append(f"% median p05-p95 spread: {spread:.1f}%")
    lines.extend([
        "",
        f"\\foreach \\x/\\label in {{{labels}}} {{",
        "  \\node[labelbox] at (\\x,-7) {Pair \\label};",
        "}",
        "",
        "\\node[boxlegacy, minimum width=0.32cm, minimum height=0.20cm,",
        "      inner sep=0pt] at (1.55,-16) {};",
        "\\node[labelbox, anchor=west] at (1.75,-16) {legacy};",
        "\\node[boxtemeraire, minimum width=0.32cm, minimum height=0.20cm,",
        "      inner sep=0pt] at (3.35,-16) {};",
        "\\node[labelbox, anchor=west] at (3.55,-16) {Temeraire};",
        "",
        # The trailing % swallows the final newline. Without it \input emits a
        # space after the picture, and \centering shifts the figure sideways.
        "\\end{tikzpicture}%",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true",
                        help="compare with the existing file instead of writing it")
    args = parser.parse_args()

    if not args.raw_dir.is_dir():
        print(f"FAIL raw directory not found: {args.raw_dir}", file=sys.stderr)
        return 1

    figure = render(collect(args.raw_dir))

    if args.check:
        if not args.output.is_file():
            print(f"FAIL {args.output} does not exist")
            return 1
        current = args.output.read_text(encoding="utf-8")
        if current == figure:
            print(f"PASS {args.output} agrees with {args.raw_dir}")
            return 0
        print(f"FAIL {args.output} does not agree with {args.raw_dir}")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(figure, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
