#!/usr/bin/env python3
"""Single entry point for reproducing this artifact.

Reproduction happens at three tiers, and they cost very different amounts:

    verify  Re-derive every number in the report from the checked-in data.
            Needs Python only. Takes seconds. Works on Linux, macOS, Windows.
    site    Rebuild the static results explorer from the audit output.
            Needs Python only. Takes about a minute.
    run     Re-run the benchmarks from source. Needs Linux, a toolchain, a
            quiet dedicated machine, and up to 90.7 hours.

This script dispatches to the existing scripts. It computes no result of its
own, so the audit stays the single source of truth for every reported number.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
AUDIT = "scripts/audit_bare_metal_results.py"
SITE_BUILDER = "scripts/build_results_site.py"
FIGURE = "scripts/generate_distribution_figure.py"
DOCKER_RUNNER = "scripts/run_paper_closer_redis_experiment.sh"
BARE_METAL_RUNNER = "scripts/run_bare_metal_redis_experiment.sh"

MIN_PYTHON = (3, 10)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


# --------------------------------------------------------------------------
# Tier 1: the four audits documented in README.md
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class VerifyStep:
    key: str
    title: str
    raw_dir: str
    output_dir: str
    mode: str = "balanced"
    workload: str | None = None

    def argv(self) -> list[str]:
        argv = [
            sys.executable,
            AUDIT,
            "--mode",
            self.mode,
            "--skip-raw-files",
            "--raw-dir",
            self.raw_dir,
            "--output-dir",
            self.output_dir,
        ]
        if self.workload:
            argv[4:4] = ["--workload", self.workload]
        return argv


VERIFY_STEPS: tuple[VerifyStep, ...] = (
    VerifyStep(
        key="reported",
        title="Reported result, correction run, sequential workload",
        raw_dir="results/node85-rerun/raw",
        output_dir="results/processed/node85-rerun-sequential",
        workload="sequential",
    ),
    VerifyStep(
        key="combined",
        title="Correction run, combined EVAL workload",
        raw_dir="results/node85-rerun/raw",
        output_dir="results/processed/node85-rerun-combined",
        workload="combined",
    ),
    VerifyStep(
        key="first-bare-metal",
        title="First bare-metal series, July 2026",
        raw_dir="results/node85-import/raw",
        output_dir="results/processed/node85-audit",
    ),
    VerifyStep(
        key="sensitivity",
        title="Release-rate sensitivity, 16 / 64 / 256 MiB/s",
        raw_dir="results/node85-sensitivity-audit/raw",
        output_dir="results/processed/node85-sensitivity-audit",
        mode="sensitivity",
    ),
)


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------
@dataclass
class Check:
    status: str
    name: str
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.checks.append(Check(status, name, detail))

    @property
    def failed(self) -> bool:
        return any(c.status == FAIL for c in self.checks)

    def show(self) -> None:
        for c in self.checks:
            line = f"{c.status} {c.name}"
            if c.detail:
                line += f": {c.detail}"
            print(line)


def _tool(report: Report, name: str, *, required: bool, note: str = "") -> bool:
    found = shutil.which(name)
    if found:
        report.add(PASS, name, found)
        return True
    report.add(FAIL if required else WARN, name, note or "not found on PATH")
    return False


def check_verify(report: Report) -> None:
    """Checks for tiers 1 and 2. These must pass on any platform."""
    version = ".".join(str(p) for p in sys.version_info[:3])
    if sys.version_info >= MIN_PYTHON:
        report.add(PASS, "python", version)
    else:
        report.add(FAIL, "python", f"{version}, need 3.10 or later")

    for script in (AUDIT, SITE_BUILDER, FIGURE):
        path = REPO_ROOT / script
        report.add(PASS if path.is_file() else FAIL, script,
                   "" if path.is_file() else "missing")

    for raw_dir in dict.fromkeys(s.raw_dir for s in VERIFY_STEPS):
        raw = REPO_ROOT / raw_dir
        report.add(PASS if raw.is_dir() else FAIL, raw_dir,
                   "" if raw.is_dir() else "missing, clone is incomplete")


def check_run(report: Report, path: str) -> None:
    """Checks for tier 3. Linux only."""
    if not sys.platform.startswith("linux"):
        report.add(FAIL, "operating system",
                   f"{sys.platform}, the benchmark needs Linux")
        return
    report.add(PASS, "operating system", sys.platform)

    _tool(report, "bash", required=True)

    if path == "docker":
        _tool(report, "docker", required=True)
    else:
        _tool(report, "git", required=True)
        _tool(report, "curl", required=True,
              note="needed to download Bazelisk unless sources are staged")
        if not (shutil.which("clang") or shutil.which("cc")):
            report.add(WARN, "compiler",
                       "no clang or cc found; setup_bare_metal_env.sh builds one")

    thp = Path("/sys/kernel/mm/transparent_hugepage/enabled")
    try:
        raw = thp.read_text(encoding="utf-8").strip()
        active = raw.split("[")[1].split("]")[0] if "[" in raw else raw
        report.add(PASS if active == "always" else WARN, "THP enabled", active
                   if active == "always" else f"{active}, the runs need always")
    except OSError:
        report.add(WARN, "THP enabled", f"cannot read {thp}")

    try:
        free_gib = shutil.disk_usage("/var/tmp").free / 2**30
        report.add(PASS if free_gib >= 50 else WARN, "/var/tmp free",
                   f"{free_gib:.0f} GiB")
    except OSError:
        report.add(WARN, "/var/tmp free", "cannot measure")

    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                gib = int(line.split()[1]) / 2**20
                report.add(PASS if gib >= 8 else WARN, "memory", f"{gib:.0f} GiB")
                break
    except OSError:
        report.add(WARN, "memory", "cannot read /proc/meminfo")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def dispatch(argv: list[str]) -> int:
    print("\n$ " + " ".join(argv), flush=True)
    return subprocess.call(argv, cwd=REPO_ROOT)


def cmd_check(args: argparse.Namespace) -> int:
    report = Report()
    print("Tier 1 and 2, verify and site")
    check_verify(report)
    report.show()
    verify_ok = not report.failed

    run_report = Report()
    print(f"\nTier 3, run, {args.path} path")
    check_run(run_report, args.path)
    run_report.show()

    print("\nverify : " + ("available" if verify_ok else "blocked"))
    print("site   : " + ("available" if verify_ok else "blocked"))
    print("run    : " + ("available" if not run_report.failed else "blocked"))
    return 0 if verify_ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    report = Report()
    check_verify(report)
    if report.failed:
        report.show()
        print("\nPreflight failed. Run 'reproduce.py check' for detail.")
        return 1

    steps = VERIFY_STEPS
    if args.which != "all":
        steps = tuple(s for s in VERIFY_STEPS if s.key == args.which)

    failures = 0
    for step in steps:
        print(f"\n=== {step.title} ===")
        if dispatch(step.argv()) != 0:
            failures += 1
            print(f"FAIL {step.key}")

    print(f"\n{len(steps) - failures}/{len(steps)} audits passed.")

    if args.which == "all":
        print("\n=== Distribution figure agrees with the trial data ===")
        if dispatch([sys.executable, FIGURE, "--check"]) != 0:
            failures += 1

    return 1 if failures else 0


def cmd_site(args: argparse.Namespace) -> int:
    if dispatch([sys.executable, SITE_BUILDER]) != 0:
        return 1
    if args.serve:
        print("\nServing site/ at http://localhost:8000/  (Ctrl-C to stop)")
        return dispatch([sys.executable, "-m", "http.server", "8000",
                         "--directory", "site"])
    print("\nOpen site/index.html, or re-run with --serve.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    report = Report()
    check_run(report, args.path)
    report.show()
    if report.failed:
        print("\nThis machine cannot run the benchmark. See the failures above.")
        return 1

    runner = DOCKER_RUNNER if args.path == "docker" else BARE_METAL_RUNNER
    argv = ["bash", runner, "--allocator-order", "balanced", *args.extra]

    print("\nThe full series takes up to 90.7 hours and needs a quiet machine.")
    print("Read 'Time and Resource Budget' in README.md before you start.")
    print("\nCommand:")
    print("  " + " ".join(argv))
    if not args.yes:
        print("\nThis was a dry run. Add --yes to execute it.")
        return 0
    return dispatch(argv)


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------
MENU = (
    ("1", "Verify every reported number (about 3 seconds)", ["verify"]),
    ("2", "Verify the headline result only (about 1 second)",
     ["verify", "--which", "reported"]),
    ("3", "Rebuild the results explorer (about a minute)", ["site"]),
    ("4", "Check what this machine can do", ["check"]),
    ("5", "Show the benchmark command (dry run)", ["run"]),
)


def menu() -> int:
    print(__doc__.strip())
    print("\nWhat do you want to do?\n")
    for key, label, _ in MENU:
        print(f"  {key}. {label}")
    print("  q. Quit\n")
    try:
        choice = input("Choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if choice in ("q", "quit", ""):
        return 0
    for key, _, argv in MENU:
        if choice == key:
            return main(argv)
    print(f"Unknown choice: {choice}")
    return 1


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reproduce.py",
        description="Reproduce the Temeraire Redis artifact.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with no arguments for an interactive menu.",
    )
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="report what this machine can do")
    p_check.add_argument("--path", choices=("bare-metal", "docker"),
                         default="bare-metal")
    p_check.set_defaults(func=cmd_check)

    p_verify = sub.add_parser("verify", help="re-derive the reported numbers")
    p_verify.add_argument(
        "--which", default="all",
        choices=("all", *(s.key for s in VERIFY_STEPS)),
        help="audit to run (default: all four)")
    p_verify.set_defaults(func=cmd_verify)

    p_site = sub.add_parser("site", help="rebuild the results explorer")
    p_site.add_argument("--serve", action="store_true",
                        help="serve site/ on http://localhost:8000/ afterwards")
    p_site.set_defaults(func=cmd_site)

    p_run = sub.add_parser("run", help="re-run the benchmarks (Linux only)")
    p_run.add_argument("--path", choices=("bare-metal", "docker"),
                       default="bare-metal")
    p_run.add_argument("--yes", action="store_true",
                       help="actually execute; without it this is a dry run")
    p_run.add_argument("extra", nargs="*",
                       help=("extra arguments for the runner script. Put them "
                             "after a -- separator, for example: "
                             "run --yes -- --balanced-run-number 3"))
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return menu()
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
