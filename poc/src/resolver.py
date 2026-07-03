"""
OpenAI 임베딩 기반 Entity Resolver.

neo4j-graphrag의 `BasePropertySimilarityResolver`를 상속해 `compute_similarity`만
OpenAI 임베딩 코사인 유사도로 구현한다. 나머지(DB에서 __Entity__ 조회 → 라벨별 그룹화
→ 쌍별 유사도 → APOC mergeNodes 병합)는 부모 클래스의 run()이 그대로 수행한다.

주의: 부모 run()은 같은 라벨 내 모든 노드 쌍에 대해 compute_similarity를 호출한다(O(n^2)).
매 호출마다 임베딩 API를 때리면 비용/지연이 폭발하므로, 텍스트별 임베딩을 dict에 캐시해
동일 텍스트는 한 번만 임베딩한다(SpaCySemanticMatchResolver의 embedding_cache와 동일 전략).
"""

from __future__ import annotations

from typing import List, Optional

import neo4j
import numpy as np

from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.experimental.components.resolver import (
    BasePropertySimilarityResolver,
)


class OpenAIEmbeddingResolver(BasePropertySimilarityResolver):
    """
    OpenAI 임베딩 코사인 유사도로 동일 인물/장소의 표기 변형을 병합하는 resolver.

    Args:
        driver: Neo4j 드라이버.
        embedding_model: 사용할 OpenAI 임베딩 모델명.
        similarity_threshold: 이 값 이상이면 병합(짧은 이름 오병합을 막기 위해 보수적으로 0.85).
        resolve_properties: 유사도 계산에 쓸 속성 목록(기본 name).
        filter_query: 해소 범위를 좁히는 선택적 Cypher WHERE 절.
        neo4j_database: 대상 DB 이름.
    """

    def __init__(
        self,
        driver: neo4j.Driver,
        embedding_model: str = "text-embedding-3-small",
        similarity_threshold: float = 0.85,
        resolve_properties: Optional[List[str]] = None,
        filter_query: Optional[str] = None,
        neo4j_database: Optional[str] = None,
    ) -> None:
        super().__init__(
            driver=driver,
            filter_query=filter_query,
            resolve_properties=resolve_properties or ["name"],
            similarity_threshold=similarity_threshold,
            neo4j_database=neo4j_database,
        )
        self.embeddings = OpenAIEmbeddings(model=embedding_model)
        # 텍스트 → 임베딩 벡터 캐시. 동일 텍스트의 중복 임베딩 호출을 막는다.
        self._cache: dict[str, np.ndarray] = {}

    def compute_similarity(self, text_a: str, text_b: str) -> float:
        vec_a = self._embed(text_a)
        vec_b = self._embed(text_b)
        return self._cosine_similarity(vec_a, vec_b)

    def _embed(self, text: str) -> np.ndarray:
        if text not in self._cache:
            # embed_query는 단일 문자열을 임베딩해 float 리스트를 반환한다.
            vector = self.embeddings.embed_query(text)
            self._cache[text] = np.asarray(vector, dtype=np.float64)
        return self._cache[text]

    @staticmethod
    def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
        """두 임베딩 벡터의 코사인 유사도. 영벡터면 0.0."""
        dot = float(np.dot(vec1, vec2))
        norm1 = float(np.linalg.norm(vec1))
        norm2 = float(np.linalg.norm(vec2))
        if not norm1 or not norm2:
            return 0.0
        return dot / (norm1 * norm2)
