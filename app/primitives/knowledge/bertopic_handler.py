import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


OUTLIER_TOPIC_ID = -1
OUTLIER_TOPIC_LABEL = "Outlier"


class BertopicHandler:
    """
    Offline topic clustering for embedded knowledge chunks.

    BERTopic is intentionally kept out of request-time retrieval. The handler uses
    precomputed Gemini embeddings from the ingestion pipeline and only enriches
    vector metadata before Pinecone upsert.
    """

    def __init__(
        self,
        model_path: str = "models/bertopic_model",
        min_topic_size: int = 15,
        min_documents_to_fit: Optional[int] = None,
    ):
        self.model_path = Path(model_path)
        self.min_topic_size = min_topic_size
        self.min_documents_to_fit = min_documents_to_fit or min_topic_size
        self.topic_model = None
        self.has_model = False

    def load_or_initialize(self) -> None:
        """Load a saved BERTopic model if it exists, otherwise initialize one."""
        if self.model_path.exists():
            from bertopic import BERTopic

            self.topic_model = BERTopic.load(str(self.model_path))
            self.has_model = True
            return

        self.topic_model = self._build_model()
        self.has_model = False

    def fit_transform(
        self,
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
    ) -> Tuple[List[int], List[float]]:
        """Fit BERTopic with precomputed embeddings and return topics/probabilities."""
        self._ensure_model()
        self._validate_inputs(documents, embeddings)

        if len(documents) < self.min_documents_to_fit:
            return self._outlier_assignments(len(documents))

        topics, probabilities = self.topic_model.fit_transform(
            list(documents),
            embeddings=self._as_embedding_matrix(embeddings),
        )
        self.has_model = True
        return list(topics), self._extract_topic_probabilities(topics, probabilities)

    def transform_new_documents(
        self,
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
    ) -> Tuple[List[int], List[float]]:
        """Assign topics to new chunks using an existing fitted model."""
        self._ensure_model()
        self._validate_inputs(documents, embeddings)

        if not self.has_model:
            return self.fit_transform(documents, embeddings)

        topics, probabilities = self.topic_model.transform(
            list(documents),
            embeddings=self._as_embedding_matrix(embeddings),
        )
        return list(topics), self._extract_topic_probabilities(topics, probabilities)

    def enrich_chunks(
        self,
        chunks: Sequence[Dict[str, Any]],
        topics: Sequence[int],
        probabilities: Sequence[float],
        clustered_at: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Attach topic metadata while preserving existing chunk metadata."""
        if len(chunks) != len(topics) or len(chunks) != len(probabilities):
            raise ValueError("chunks, topics, and probabilities must have matching lengths")

        clustered_at = clustered_at or datetime.now(timezone.utc).isoformat()
        enriched = []

        for chunk, topic_id, probability in zip(chunks, topics, probabilities):
            metadata = dict(chunk.get("metadata") or {})
            metadata.update(
                {
                    "topic_id": int(topic_id),
                    "topic_label": self.get_topic_label(int(topic_id)),
                    "topic_probability": float(probability or 0.0),
                    "topic_keywords": self.get_topic_keywords(int(topic_id)),
                    "clustered_at": clustered_at,
                }
            )

            next_chunk = dict(chunk)
            next_chunk["metadata"] = self._json_safe_metadata(metadata)
            enriched.append(next_chunk)

        return enriched

    def save_model(self) -> None:
        """Persist the fitted topic model if a real model has been fitted."""
        if not self.topic_model or not self.has_model:
            return

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.topic_model.save(str(self.model_path), serialization="pickle")

    def get_topic_info(self):
        """Return BERTopic topic info for monitoring and debugging."""
        self._ensure_model()
        if not self.has_model:
            return []
        return self.topic_model.get_topic_info()

    def compute_parent_topics(
        self,
        leaf_ids: List[int],
        phrases: List[str],
        phrase_embeddings: Sequence[Sequence[float]],
        nr_parents: int,
    ) -> Tuple[Dict[int, Optional[int]], Dict[int, str], Dict[int, List[str]]]:
        """
        Second-pass BERTopic: each leaf topic becomes a document (its keyword phrase)
        with a pre-computed embedding. BERTopic clusters those ~N leaf vectors into
        nr_parents groups. Parent IDs are offset by max(leaf_ids)+1 to avoid collision.

        Returns:
          leaf_to_parent  — {leaf_topic_id: parent_topic_id}
          parent_labels   — {parent_topic_id: label_string}
          parent_keywords — {parent_topic_id: [keyword, ...]}
        """
        n_leaves = len(leaf_ids)
        if n_leaves <= nr_parents:
            return {t: t for t in leaf_ids}, {}, {}

        import numpy as np
        from bertopic import BERTopic
        from bertopic.representation import MaximalMarginalRelevance
        from hdbscan import HDBSCAN
        from sklearn.feature_extraction.text import CountVectorizer
        from umap import UMAP

        n_neighbors = min(15, max(2, n_leaves // 10))
        n_components = min(5, n_leaves - 2)

        umap_model = UMAP(
            n_neighbors=n_neighbors,
            n_components=n_components,
            min_dist=0.0,
            metric="cosine",
            random_state=42,
        )
        hdbscan_model = HDBSCAN(
            min_cluster_size=2,
            min_samples=1,
            metric="euclidean",
            cluster_selection_method="eom",
            prediction_data=True,
        )
        vectorizer_model = CountVectorizer(
            stop_words="english",
            min_df=1,
            ngram_range=(1, 2),
        )
        representation_model = MaximalMarginalRelevance(diversity=0.35)

        second_model = BERTopic(
            embedding_model=None,
            umap_model=umap_model,
            hdbscan_model=hdbscan_model,
            vectorizer_model=vectorizer_model,
            representation_model=representation_model,
            nr_topics=nr_parents,
            calculate_probabilities=False,
            verbose=False,
        )

        parent_assignments, _ = second_model.fit_transform(
            phrases,
            embeddings=np.asarray(phrase_embeddings, dtype="float32"),
        )

        offset = max(leaf_ids) + 1

        leaf_to_parent: Dict[int, int] = {}
        for leaf_id, raw_parent in zip(leaf_ids, parent_assignments):
            leaf_to_parent[leaf_id] = None if raw_parent == -1 else raw_parent + offset

        parent_labels: Dict[int, str] = {}
        parent_keywords: Dict[int, List[str]] = {}
        for raw_id in set(parent_assignments):
            if raw_id == -1:
                continue
            pid = raw_id + offset
            topic_words = second_model.get_topic(raw_id) or []
            words = [w for w, _ in topic_words[:5]]
            parent_labels[pid] = " ".join(words) if words else f"Topic {pid}"
            parent_keywords[pid] = [w for w, _ in topic_words[:8]]

        return leaf_to_parent, parent_labels, parent_keywords

    def get_topic_label(self, topic_id: int) -> str:
        if topic_id == OUTLIER_TOPIC_ID or not self.topic_model or not self.has_model:
            return OUTLIER_TOPIC_LABEL

        topic_words = self.topic_model.get_topic(topic_id) or []
        words = [word for word, _ in topic_words[:5]]
        return " ".join(words) if words else f"Topic {topic_id}"

    def get_topic_keywords(self, topic_id: int, limit: int = 8) -> List[str]:
        if topic_id == OUTLIER_TOPIC_ID or not self.topic_model or not self.has_model:
            return []

        topic_words = self.topic_model.get_topic(topic_id) or []
        return [word for word, _ in topic_words[:limit]]

    def _build_model(self):
        from bertopic import BERTopic
        from bertopic.representation import MaximalMarginalRelevance
        from hdbscan import HDBSCAN
        from sklearn.feature_extraction.text import CountVectorizer
        from umap import UMAP

        umap_model = UMAP(
            n_neighbors=15,
            n_components=5,
            min_dist=0.0,
            metric="cosine",
            random_state=42,
        )
        hdbscan_model = HDBSCAN(
            min_cluster_size=self.min_topic_size,
            min_samples=5,
            metric="euclidean",
            cluster_selection_method="eom",
            prediction_data=True,
        )
        vectorizer_model = CountVectorizer(
            stop_words="english",
            min_df=2,
            ngram_range=(1, 2),
        )
        representation_model = MaximalMarginalRelevance(diversity=0.35)

        return BERTopic(
            embedding_model=None,
            umap_model=umap_model,
            hdbscan_model=hdbscan_model,
            vectorizer_model=vectorizer_model,
            representation_model=representation_model,
            calculate_probabilities=True,
            verbose=True,
        )

    def _ensure_model(self) -> None:
        if self.topic_model is None:
            self.load_or_initialize()

    @staticmethod
    def _validate_inputs(
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(documents) != len(embeddings):
            raise ValueError("documents and embeddings must have matching lengths")

    @staticmethod
    def _as_embedding_matrix(embeddings: Sequence[Sequence[float]]):
        import numpy as np

        return np.asarray(embeddings, dtype="float32")

    @staticmethod
    def _outlier_assignments(count: int) -> Tuple[List[int], List[float]]:
        return [OUTLIER_TOPIC_ID] * count, [0.0] * count

    @staticmethod
    def _extract_topic_probabilities(
        topics: Sequence[int],
        probabilities: Any,
    ) -> List[float]:
        if probabilities is None:
            return [0.0 for _ in topics]

        try:
            import numpy as np

            probs = np.asarray(probabilities)
            if probs.ndim == 1:
                return [float(value) for value in probs]

            extracted = []
            for index, topic_id in enumerate(topics):
                if topic_id == OUTLIER_TOPIC_ID:
                    extracted.append(0.0)
                else:
                    extracted.append(float(np.max(probs[index])))
            return extracted
        except Exception:
            return [0.0 for _ in topics]

    @staticmethod
    def _json_safe_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        safe: Dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                safe[key] = value
            elif isinstance(value, list):
                safe[key] = [item for item in value if isinstance(item, (str, int, float, bool))]
            else:
                safe[key] = str(value)
        return safe
