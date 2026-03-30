"""A/B experiment definitions — load from YAML, resolve to scenarios.

An experiment pairs a scenario (the user message to replay) with a set of
tier/model overrides so you can measure how changing the LLM configuration
affects cost, latency, and output quality.

Example YAML::

    name: upgrade-research-to-medium
    description: Does bumping research from low to medium improve output?
    base_scenario: research_and_note
    overrides:
      subagent_research:
        tier: medium

Usage::

    from tests.integration.experiment import load_experiment, find_scenario

    exp = load_experiment(Path("experiments/upgrade_research.yaml"))
    scenario = find_scenario(exp.scenario)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tests.integration.scenarios import SCENARIOS, Scenario

_EXPERIMENTS_DIR = Path(__file__).parent / "experiments"


@dataclass
class Experiment:
    """An A/B experiment: same scenario, different tier configuration."""

    name: str
    scenario: str  # references Scenario.name
    description: str = ""
    overrides: dict = field(default_factory=dict)


def load_experiment(path: Path) -> Experiment:
    """Load an Experiment from a YAML file."""
    data = yaml.safe_load(path.read_text())
    return Experiment(
        name=data["name"],
        scenario=data["base_scenario"],
        description=data.get("description", ""),
        overrides=data.get("overrides", {}),
    )


def discover_experiments() -> list[Path]:
    """Find all experiment YAML files in the experiments directory."""
    if not _EXPERIMENTS_DIR.exists():
        return []
    return sorted(_EXPERIMENTS_DIR.glob("*.yaml"))


def find_scenario(name: str) -> Scenario:
    """Look up a Scenario by name.  Raises KeyError if not found."""
    for s in SCENARIOS:
        if s.name == name:
            return s
    raise KeyError(
        f"Unknown scenario '{name}'. "
        f"Available: {[s.name for s in SCENARIOS]}"
    )
