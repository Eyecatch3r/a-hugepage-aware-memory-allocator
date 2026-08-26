from __future__ import annotations

import re
import statistics
import tempfile
import unittest
from pathlib import Path

from scripts import generate_distribution_figure as gen

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / gen.DEFAULT_RAW
COMMITTED = REPO_ROOT / gen.DEFAULT_OUTPUT


class PercentileTests(unittest.TestCase):
    def test_endpoints(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(gen.percentile(values, 0), 1.0)
        self.assertEqual(gen.percentile(values, 100), 5.0)

    def test_interpolates_between_ranks(self) -> None:
        self.assertAlmostEqual(gen.percentile([0.0, 10.0], 25), 2.5)

    def test_matches_the_median_at_the_midpoint(self) -> None:
        values = sorted([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0])
        self.assertAlmostEqual(gen.percentile(values, 50), statistics.median(values))


class GeometryTests(unittest.TestCase):
    def test_legacy_sits_left_of_temeraire_in_the_same_pair(self) -> None:
        legacy = gen.Box(5, "legacy", 0, 0, 1, 0, 0)
        temeraire = gen.Box(5, "temeraire", 0, 0, 1, 0, 0)
        self.assertLess(legacy.center, temeraire.center)

    def test_pairs_are_evenly_spaced(self) -> None:
        centers = [gen.Box(p, "legacy", 0, 0, 1, 0, 0).center for p in (5, 6, 7, 8)]
        gaps = {round(b - a, 6) for a, b in zip(centers, centers[1:])}
        self.assertEqual(gaps, {gen.PAIR_PITCH})

    def test_spread_is_a_percentage_of_the_median(self) -> None:
        box = gen.Box(5, "legacy", p5=950.0, q1=0, median=1000.0, q3=0, p95=1050.0)
        self.assertAlmostEqual(box.spread, 10.0)


class CollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RAW.is_dir():
            raise unittest.SkipTest(f"{RAW} is absent")
        cls.boxes = gen.collect(RAW)

    def test_four_pairs_of_two_blocks(self) -> None:
        self.assertEqual(len(self.boxes), 8)
        self.assertEqual([b.pair for b in self.boxes],
                         [5, 5, 6, 6, 7, 7, 8, 8])

    def test_each_pair_holds_one_of_each_allocator(self) -> None:
        self.assertEqual([b.allocator for b in self.boxes],
                         ["legacy", "temeraire"] * 4)

    def test_quartiles_are_ordered(self) -> None:
        for box in self.boxes:
            with self.subTest(pair=box.pair, allocator=box.allocator):
                self.assertLessEqual(box.p5, box.q1)
                self.assertLessEqual(box.q1, box.median)
                self.assertLessEqual(box.median, box.q3)
                self.assertLessEqual(box.q3, box.p95)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RAW.is_dir():
            raise unittest.SkipTest(f"{RAW} is absent")
        cls.text = gen.render(gen.collect(RAW))

    def test_it_draws_one_box_per_allocator_block(self) -> None:
        self.assertEqual(len(re.findall(r"rectangle", self.text)), 8)
        self.assertEqual(len(re.findall(r"medianline", self.text)), 8 + 1)

    def test_the_picture_is_balanced(self) -> None:
        self.assertEqual(self.text.count("\\begin{tikzpicture}"), 1)
        self.assertEqual(self.text.count("\\end{tikzpicture}"), 1)

    def test_it_ends_with_a_comment_character(self) -> None:
        """Without the trailing %, \\input adds a space and shifts the figure."""
        self.assertTrue(self.text.rstrip("\n").endswith("\\end{tikzpicture}%"))

    def test_it_says_that_it_is_generated(self) -> None:
        self.assertIn("Do not edit", self.text.splitlines()[0])

    def test_every_drawn_point_lies_inside_the_axis(self) -> None:
        """A whisker outside the axis draws over the labels and the legend."""
        top = float(re.search(r"\(0\.38,0\) -- \(0\.38,([\d.]+)\)", self.text).group(1))
        drawn = [
            float(v)
            for line in self.text.splitlines()
            if re.match(r"\\draw\[(whisker|box\w+|medianline)\]", line)
            for v in re.findall(r"\(-?[\d.]+,(-?[\d.]+)\)", line)
        ]
        self.assertTrue(drawn, "no drawing commands found")
        self.assertGreaterEqual(min(drawn), 0.0,
                                f"a point falls {-min(drawn):.2f} below the axis")
        self.assertLessEqual(max(drawn), top,
                             f"a point falls {max(drawn) - top:.2f} above the axis")

    def test_the_axis_is_not_wider_than_it_needs_to_be(self) -> None:
        """The floor and ceiling round outward by less than one tick."""
        boxes = gen.collect(RAW)
        floor, ceiling = gen.axis_bounds(boxes)
        self.assertLessEqual(min(b.p5 for b in boxes) - floor, gen.TICK_STEP)
        self.assertLessEqual(ceiling - max(b.p95 for b in boxes), gen.TICK_STEP)

    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(self.text, gen.render(gen.collect(RAW)))


class CommittedFigureTests(unittest.TestCase):
    def test_the_committed_figure_agrees_with_the_trial_data(self) -> None:
        if not RAW.is_dir() or not COMMITTED.is_file():
            self.skipTest("raw data or committed figure is absent")
        expected = gen.render(gen.collect(RAW))
        self.assertEqual(
            COMMITTED.read_text(encoding="utf-8"), expected,
            "notes/figure-distributions.tex is stale. Regenerate it with "
            "scripts/generate_distribution_figure.py",
        )

    def test_writing_then_checking_round_trips(self) -> None:
        if not RAW.is_dir():
            self.skipTest(f"{RAW} is absent")
        text = gen.render(gen.collect(RAW))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "figure.tex"
            out.write_text(text, encoding="utf-8", newline="\n")
            self.assertEqual(out.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
