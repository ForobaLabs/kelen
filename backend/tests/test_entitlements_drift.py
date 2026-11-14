"""Plan pre-beta 5.2 — entitlement registries must not drift across the suite.

Foroba's REGISTRY is the source of truth. Pazuru, Yiriba (Tsumi) and @foroba/ui mirror it.
The sibling repos are parsed statically (ast / regex) so this runs inside Foroba's own venv;
when a sibling checkout is absent (Foroba-only CI) the matching test is skipped.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from auth.entitlements import REGISTRY

ROOT = Path(__file__).resolve().parents[3]
PAZURU = ROOT / "pazuru/backend/app/entitlements.py"
TSUMI = ROOT / "tsumi/api/entitlements.py"
UI = ROOT / "foroba-ui/src/entitlements/registry.ts"

# Sibling key -> Foroba key, for features whose name differs between apps.
PAZURU_RENAMES = {"banking.ai_chat": "banking.chat"}
TSUMI_RENAMES = {
    "portfolio.top_movers_basic": "markets.top_movers_basic",
    "portfolio.news_headlines_basic": "markets.news_headlines",
    "portfolio.sentiment": "markets.sentiment",
    "portfolio.market_snapshot": "markets.market_snapshot",
    "portfolio.comparator": "markets.comparator",
    "portfolio.simulations": "markets.simulations",
    "portfolio.backtesting": "markets.backtesting",
}
# Sibling-only keys with no Foroba counterpart (app-local features).
PAZURU_LOCAL = {
    "banking.transactions", "banking.recurring", "banking.spending", "banking.budgets",
    "banking.goals", "banking.forecasts",
}  # fmt: skip
TSUMI_LOCAL = {
    "portfolio.summary", "portfolio.etf_explorer", "portfolio.crypto_explorer",
    "portfolio.technical_analysis", "portfolio.screeners",
}  # fmt: skip


def _registry(path: Path, name: str) -> dict[str, dict[str, str]]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def _pairs(reg: dict[str, dict[str, str]], key: str) -> tuple[str, str]:
    return reg[key]["audience"], reg[key]["tier"]


def _check_mirror(sibling: dict, renames: dict, local: set, label: str) -> None:
    unmapped = set(sibling) - set(renames) - local - set(REGISTRY)
    assert not unmapped, f"{label}: new feature keys need a rename/local ruling: {sorted(unmapped)}"
    for key, spec in sibling.items():
        if key in local:
            continue
        canon = renames.get(key, key)
        assert canon in REGISTRY, f"{label}: {key} maps to unknown Foroba key {canon}"
        assert (spec["audience"], spec["tier"]) == _pairs(REGISTRY, canon), (
            f"{label}: {key} gating {spec} drifted from Foroba {canon} {REGISTRY[canon]}"
        )


@pytest.mark.skipif(not PAZURU.exists(), reason="pazuru checkout absent")
def test_pazuru_gating_matches_foroba():
    _check_mirror(_registry(PAZURU, "PAZURU_REGISTRY"), PAZURU_RENAMES, PAZURU_LOCAL, "pazuru")


@pytest.mark.skipif(not TSUMI.exists(), reason="tsumi checkout absent")
def test_tsumi_gating_matches_foroba():
    _check_mirror(_registry(TSUMI, "TSUMI_REGISTRY"), TSUMI_RENAMES, TSUMI_LOCAL, "tsumi")


@pytest.mark.skipif(not UI.exists(), reason="foroba-ui checkout absent")
def test_feature_keys_identical_across_apps():
    """@foroba/ui FeatureKey union must list exactly Foroba's REGISTRY keys."""
    src = UI.read_text()
    union = src.split("export type FeatureKey =")[1].split(";")[0]
    ui_keys = set(re.findall(r'"([a-z_]+\.[a-z_]+)"', union))
    ui_only, api_only = sorted(ui_keys - set(REGISTRY)), sorted(set(REGISTRY) - ui_keys)
    assert ui_keys == set(REGISTRY), f"UI-only: {ui_only}; Foroba-only: {api_only}"
