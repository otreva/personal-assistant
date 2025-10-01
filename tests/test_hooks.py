from datetime import datetime, timezone

from graphiti.config import GraphitiConfig
from graphiti.episodes import Episode
from graphiti.hooks import EpisodeProcessor, RedactionPipeline, RedactionRule


def test_redaction_pipeline_applies_rules_recursively():
    pipeline = RedactionPipeline(
        [
            RedactionRule.from_pattern(r"secret", "***", name="secret"),
            RedactionRule.from_pattern(r"(\d{4})", "0000", name="digits"),
        ]
    )
    payload = {
        "note": "secret token 1234",
        "items": ["1234", {"inner": "no secret"}],
    }
    redacted, stats = pipeline.apply_structure(payload)
    assert redacted["note"] == "*** token 0000"
    assert redacted["items"][0] == "0000"
    assert stats == {"secret": 2, "digits": 2}


def test_episode_processor_redacts_and_summarises(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "- pattern: secret\n  replacement: REDACTED\n",
        encoding="utf-8",
    )
    config = GraphitiConfig(
        group_id="g",
        redaction_rules=(
            (r"alice@example.com", "REDACTED"),
        ),
        redaction_rules_path=str(rules_path),
        summarization_threshold=10,
        summarization_max_chars=30,
        summarization_sentence_count=2,
    )
    processor = EpisodeProcessor(config)
    episode = Episode(
        group_id="g",
        source="gmail",
        native_id="n",
        version="1",
        valid_at=datetime.now(timezone.utc),
        text="Secret plans from alice@example.com. They are very long indeed!",
        json={"body": "secret content"},
        metadata={"owner": "alice@example.com"},
    )

    processed = processor.process(episode)
    assert processed.text != episode.text
    assert "REDACTED" in processed.text
    assert processed.json["body"] == "REDACTED content"
    assert processed.metadata["owner"] == "REDACTED"
    processing_meta = processed.metadata["graphiti_processing"]
    assert processing_meta["redactions"]["rules"][r"alice@example.com"] >= 1
    assert processing_meta["summarisation"]["summary_length"] <= 30

