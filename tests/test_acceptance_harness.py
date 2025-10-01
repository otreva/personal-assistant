from __future__ import annotations

from dataclasses import dataclass, field

from graphiti.config import GraphitiConfig
from graphiti.harness import AcceptanceTestHarness, build_fixture_dataset


@dataclass
class RecordingEpisodeStore:
    group_id: str = "group"
    episodes: list = field(default_factory=list)

    def upsert_episode(self, episode):
        self.episodes.append(episode)


def test_acceptance_harness_runs_pollers(tmp_path):
    config = GraphitiConfig(group_id="group")
    store = RecordingEpisodeStore()
    harness = AcceptanceTestHarness(store, config=config)

    dataset = build_fixture_dataset()
    metrics = harness.run(dataset)

    assert metrics["gmail"] == 3
    assert metrics["drive"] == 2
    assert metrics["calendar"] == 1
    assert metrics["slack"] == 1
    assert metrics["mcp"] == 1

    sources = {episode.source for episode in store.episodes}
    assert sources >= {"gmail", "gdrive", "calendar", "slack", "mcp"}
