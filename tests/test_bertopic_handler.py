from app.primitives.knowledge.bertopic_handler import BertopicHandler


class FakeTopicModel:
    def get_topic(self, topic_id):
        if topic_id == 3:
            return [("roadmap", 0.9), ("feedback", 0.8), ("feature", 0.7)]
        return []


def build_handler():
    handler = BertopicHandler()
    handler.topic_model = FakeTopicModel()
    handler.has_model = True
    return handler


def test_enrich_chunks_preserves_existing_metadata():
    handler = build_handler()
    chunks = [{"text": "hello", "metadata": {"source": "gmail"}}]

    enriched = handler.enrich_chunks(chunks, [3], [0.91], clustered_at="2026-05-07T00:00:00Z")

    metadata = enriched[0]["metadata"]
    assert metadata["source"] == "gmail"
    assert metadata["topic_id"] == 3
    assert metadata["topic_label"] == "roadmap feedback feature"
    assert metadata["topic_probability"] == 0.91
    assert metadata["topic_keywords"] == ["roadmap", "feedback", "feature"]
    assert metadata["clustered_at"] == "2026-05-07T00:00:00Z"


def test_enrich_chunks_handles_outlier_topic():
    handler = build_handler()
    chunks = [{"text": "small isolated note", "metadata": {}}]

    enriched = handler.enrich_chunks(chunks, [-1], [0.0], clustered_at="2026-05-07T00:00:00Z")

    metadata = enriched[0]["metadata"]
    assert metadata["topic_id"] == -1
    assert metadata["topic_label"] == "Outlier"
    assert metadata["topic_probability"] == 0.0
    assert metadata["topic_keywords"] == []


def test_enrich_chunks_rejects_length_mismatch():
    handler = build_handler()
    chunks = [{"text": "one", "metadata": {}}, {"text": "two", "metadata": {}}]

    try:
        handler.enrich_chunks(chunks, [1], [0.5])
    except ValueError as exc:
        assert "matching lengths" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
