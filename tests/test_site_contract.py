from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.assets: list[str] = []
        self.buttons_without_type: list[str] = []
        self.panel_tabs: list[tuple[str, str | None, str | None]] = []
        self.panels: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag in {"script", "link"}:
            asset = values.get("src") or values.get("href")
            if asset:
                self.assets.append(asset)
        if tag == "button" and "type" not in values:
            self.buttons_without_type.append(str(values.get("data-value", "button")))
        if tag == "button" and values.get("role") == "tab" and values.get("data-panel"):
            self.panel_tabs.append((
                str(values["data-panel"]),
                values.get("aria-controls"),
                values.get("aria-selected"),
            ))
        if values.get("role") == "tabpanel" and values.get("id"):
            self.panels.append((str(values["id"]), values.get("aria-labelledby")))


def read_data_bundle() -> dict[str, object]:
    contents = (SITE / "assets/results-data.js").read_text(encoding="utf-8")
    prefix = "window.TEMERAIRE_RESULTS = "
    if not contents.startswith(prefix) or not contents.endswith(";\n"):
        raise AssertionError("Unexpected result bundle wrapper")
    return json.loads(contents.removeprefix(prefix).removesuffix(";\n"))


class StaticSiteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (SITE / "index.html").read_text(encoding="utf-8")
        cls.app = (SITE / "assets/app.js").read_text(encoding="utf-8")
        cls.styles = (SITE / "assets/styles.css").read_text(encoding="utf-8")
        cls.parser = SiteParser()
        cls.parser.feed(cls.index)

    def test_every_script_lookup_has_a_matching_unique_html_id(self) -> None:
        requested_ids = set(re.findall(r'byId\("([^"]+)"\)', self.app))
        html_ids = set(self.parser.ids)

        self.assertEqual(len(self.parser.ids), len(html_ids), "HTML IDs must be unique")
        self.assertEqual(requested_ids - html_ids, set())

    def test_every_local_page_asset_exists(self) -> None:
        local_assets = [asset for asset in self.parser.assets if not asset.startswith(("http://", "https://"))]
        missing = [asset for asset in local_assets if not (SITE / asset).is_file()]

        self.assertEqual(missing, [])

    def test_site_has_no_external_runtime_dependencies(self) -> None:
        combined = "\n".join((self.index, self.app, self.styles))
        combined = combined.replace("http://www.w3.org/2000/svg", "")

        self.assertNotRegex(combined, r'https?://(?!127\.0\.0\.1)')
        self.assertEqual(self.parser.buttons_without_type, [])

    def test_primary_navigation_switches_between_four_result_panels(self) -> None:
        expected_panels = {"overview", "explorer", "sensitivity", "method"}
        tab_targets = {target for target, _, _ in self.parser.panel_tabs}
        panel_ids = {panel_id for panel_id, _ in self.parser.panels}

        self.assertEqual(tab_targets, expected_panels)
        self.assertEqual(panel_ids, expected_panels)
        self.assertEqual(
            {controls for _, controls, _ in self.parser.panel_tabs},
            expected_panels,
        )
        self.assertEqual(
            sum(selected == "true" for _, _, selected in self.parser.panel_tabs),
            1,
        )

    def test_desktop_layout_is_a_single_viewport_without_document_scrolling(self) -> None:
        self.assertIn("height: 100dvh", self.styles)
        self.assertRegex(self.styles, r"body\s*\{[^}]*overflow:\s*hidden")
        self.assertIn('[hidden] {', self.styles)

    def test_panels_are_intrinsically_sized_instead_of_stretched_to_the_viewport(self) -> None:
        self.assertRegex(self.styles, r"main\s*\{[^}]*align-content:\s*start")
        self.assertRegex(self.styles, r"\.app-panel\s*\{[^}]*height:\s*auto")
        self.assertRegex(self.styles, r"\.chart-stage\s*\{[^}]*height:\s*clamp\(")

    def test_dense_layout_uses_intrinsic_method_height_and_readable_base_type(self) -> None:
        compact_styles = re.sub(r"\s+", "", self.styles)

        self.assertIn("--font-size-base:16px", compact_styles)
        self.assertIn("font-size:var(--font-size-base)", compact_styles)
        self.assertRegex(self.styles, r"\.method-workspace\s*\{[^}]*height:\s*auto")
        self.assertIn("height:clamp(250px,36vh,330px)", compact_styles)

    def test_navigation_behavior_is_not_based_on_scroll_anchors(self) -> None:
        self.assertIn('data-panel="overview"', self.index)
        self.assertIn('document.querySelector(".primary-nav")', self.app)
        self.assertNotIn("IntersectionObserver", self.app)

    def test_method_panel_pairs_the_pipeline_with_its_aggregation_rule(self) -> None:
        self.assertIn('class="method-workspace"', self.index)
        self.assertIn('class="method-analysis"', self.index)
        self.assertIn('class="method-equation"', self.index)
        self.assertIn(".method-workspace {", self.styles)

    def test_generated_bundle_contains_each_visualization_layer(self) -> None:
        payload = read_data_bundle()
        environments = payload["environments"]

        self.assertEqual(payload["schemaVersion"], 3)
        self.assertEqual(payload["defaultEnvironment"], "correctionSequential")
        self.assertEqual(
            set(environments),
            {"correctionSequential", "correctionCombined", "baremetal", "wslDocker"},
        )
        self.assertEqual(len(environments["correctionSequential"]["historical"]), 8)
        self.assertEqual(len(environments["correctionCombined"]["historical"]), 8)
        self.assertEqual(len(environments["baremetal"]["historical"]), 8)
        self.assertEqual(len(environments["wslDocker"]["historical"]), 11)

        # Only the July and WSL runs carry a release-rate sweep.
        for name in ("baremetal", "wslDocker"):
            sensitivity = environments[name]["releaseSensitivity"]
            self.assertEqual(len(sensitivity), 12)
            self.assertEqual({record["rateMiB"] for record in sensitivity}, {16, 64, 256})

        for environment in environments.values():
            historical = environment["historical"]
            self.assertTrue(all(pair["legacy"]["distributions"] for pair in historical))
            self.assertTrue(all(pair["legacy"]["memory"] for pair in historical))
            self.assertTrue(all(pair["legacy"]["distributionSampleSize"] <= 128 for pair in historical))

    def test_only_the_correction_run_reports_the_papers_cpu_unit(self) -> None:
        environments = read_data_bundle()["environments"]

        for name in ("correctionSequential", "correctionCombined"):
            pairs = environments[name]["historical"]
            self.assertTrue(environments[name]["hasCpuRecord"])
            self.assertTrue(all(isinstance(pair["deltaPerCpuSecond"], (int, float)) for pair in pairs))
            self.assertTrue(all(pair["legacy"]["cpuSeconds"] for pair in pairs))

        for name in ("baremetal", "wslDocker"):
            pairs = environments[name]["historical"]
            self.assertFalse(environments[name]["hasCpuRecord"])
            self.assertTrue(all(pair["legacy"]["cpuSeconds"] is None for pair in pairs))

    def test_the_combined_workload_offers_no_per_operation_metrics(self) -> None:
        environments = read_data_bundle()["environments"]

        self.assertEqual(environments["correctionCombined"]["metrics"], ["combined"])
        for pair in environments["correctionCombined"]["historical"]:
            self.assertEqual(set(pair["deltaPercent"]), {"combined"})
            self.assertEqual(pair["legacy"]["workload"], "combined")
        self.assertEqual(
            environments["correctionSequential"]["metrics"], ["combined", "lpush", "lrange"]
        )

    def test_known_outlier_remains_explicit_in_the_bundle(self) -> None:
        historical = read_data_bundle()["environments"]["wslDocker"]["historical"]
        outlier = next(pair for pair in historical if pair["id"] == "balanced-4-on")

        self.assertAlmostEqual(outlier["deltaPercent"]["combined"], -12.02, places=2)

    def test_environment_control_switches_complete_result_sets(self) -> None:
        self.assertIn('id="environment-select"', self.index)
        self.assertIn('byId("environment-select").addEventListener("change"', self.app)
        self.assertIn("selectEnvironment", self.app)

    def test_dashboard_text_uses_short_direct_technical_sentences(self) -> None:
        self.assertIn("Compare allocator results", self.index)
        self.assertIn("Run LPUSH", self.index)
        self.assertNotIn("at a glance", self.index)
        self.assertNotIn("How to read these results", self.index)


if __name__ == "__main__":
    unittest.main()
