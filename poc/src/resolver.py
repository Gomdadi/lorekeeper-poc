"""
커스텀 Entity Resolver 모음.

harness가 비교하는 resolver 3종:
- CombiningExactMatchResolver : 이름 완전일치(라이브러리 exact-match 기반) + combine 병합
- OpenAIEmbeddingResolver     : 이름 임베딩 코사인 유사도 + combine 병합
- CombiningFuzzyResolver      : 이름 문자열 유사도(RapidFuzz WRatio) + combine 병합

세 resolver 모두 병합 시 description을 배열로 합쳐(combine) 유실을 막는다. 라이브러리 기본
병합 전략은 properties:'discard'라 충돌 속성이 유실되는데, 병합 config가 run()에 하드코딩돼
훅이 없으므로 run()을 우리가 정의한다. 유사도 계열 두 resolver(임베딩·fuzzy)는 매칭 방식만
다르고 나머지 흐름(라벨별 그룹화 → 쌍별 유사도 → mergeNodes)이 동일하므로, 그 본문을
_run_combining_similarity() 헬퍼로 한 번만 두고 compute_similarity만 각자 오버라이드한다.
"""

from __future__ import annotations

from itertools import combinations
from typing import List, Optional

import neo4j
import numpy as np

from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.experimental.components.resolver import (
    BasePropertySimilarityResolver,
    SinglePropertyExactMatchResolver,
)
from neo4j_graphrag.experimental.components.types import ResolutionStats

# 병합 시 속성 처리 전략(APOC apoc.refactor.mergeNodes의 properties 옵션).
# 라이브러리 기본값은 'discard'라 충돌 속성이 조용히 유실된다. 여기서는 속성별 맵으로
# 바꿔 description·aliases는 값이 다르면 배열로 '합치고'(combine → 유실 방지), 나머지 속성은
# 첫 노드 값을 유지한다(백틱 `.*`는 나머지 전체를 매칭하는 catch-all 정규식).
# aliases를 combine에 넣는 이유: 같은 인물이 여러 노드로 갈렸다 병합될 때 한쪽 별칭이
# catch-all에 걸려 사라지면, 다음 회차에서 그 호칭을 같은 인물로 잇는 단서를 잃는다.
# 주의: 두 속성이 배열이 될 수 있어 스칼라 스키마와 어긋난다(유실 방지를 위한 의도적 트레이드오프).
_MERGE_PROPS = "{description:'combine', aliases:'combine', `.*`:'discard'}"


async def _run_combining_similarity(r: BasePropertySimilarityResolver) -> ResolutionStats:
    """
    BasePropertySimilarityResolver.run()의 combine 병합판. 라이브러리 run()을 복사하고
    apoc mergeNodes의 properties만 _MERGE_PROPS로 교체했다(라이브러리는 'discard' 하드코딩).

    r.compute_similarity로 매칭 방식이 주입되므로 임베딩·fuzzy resolver가 이 본문을 공유한다.
    """
    match_query = "MATCH (entity:__Entity__)"
    if r.filter_query:
        match_query += f" {r.filter_query}"

    # 요청 속성들을 모은 동적 맵(예: "name: entity.name, description: entity.description")
    props_map = ", ".join(f"{prop}: entity.{prop}" for prop in r.resolve_properties)

    # 엔티티를 라벨별로 묶고, 유사도 계산에 필요한 속성을 수집한다.
    query = f"""
        {match_query}
        UNWIND labels(entity) AS lab
        WITH lab, entity
        WHERE NOT lab IN ['__Entity__', '__KGBuilder__']
        WITH lab, collect({{ id: elementId(entity), {props_map} }}) AS labelCluster
        RETURN lab, labelCluster
    """
    records, _, _ = r.driver.execute_query(query, database_=r.neo4j_database)

    total_entities = 0
    total_merged_nodes = 0

    for row in records:
        entities = row["labelCluster"]
        # 각 엔티티의 텍스트 속성(non-null)을 한 문자열로 이어붙인다.
        node_texts = {}
        for ent in entities:
            texts = [
                str(ent[p]) for p in r.resolve_properties if p in ent and ent[p]
            ]
            combined_text = " ".join(texts).strip()
            if combined_text:
                node_texts[ent["id"]] = combined_text
        total_entities += len(node_texts)

        # 쌍별 유사도 계산 후 임계값 이상인 쌍을 병합 대상으로 표시한다.
        pairs_to_merge = []
        for (id1, text1), (id2, text2) in combinations(node_texts.items(), 2):
            if r.compute_similarity(text1, text2) >= r.similarity_threshold:
                pairs_to_merge.append({id1, id2})

        # 겹치는 쌍을 하나의 병합 집합으로 통합한다(부모의 static 메서드 재사용).
        merged_sets = r._consolidate_sets(pairs_to_merge)

        merged_count = 0
        for node_id_set in merged_sets:
            if len(node_id_set) > 1:
                merge_query = (
                    "MATCH (n) WHERE elementId(n) IN $ids "
                    "WITH collect(n) AS nodes "
                    "CALL apoc.refactor.mergeNodes(nodes, {properties: "
                    + _MERGE_PROPS
                    + ", mergeRels: true}) "
                    "YIELD node RETURN elementId(node)"
                )
                result, _, _ = r.driver.execute_query(
                    merge_query, {"ids": list(node_id_set)}, database_=r.neo4j_database
                )
                merged_count += len(result)
        total_merged_nodes += merged_count

    return ResolutionStats(
        number_of_nodes_to_resolve=total_entities,
        number_of_created_nodes=total_merged_nodes,
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

    async def run(self) -> ResolutionStats:
        # ComponentMeta가 이 클래스에 직접 정의된 run을 요구하므로, 공유 본문으로 위임한다.
        return await _run_combining_similarity(self)

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


class CombiningExactMatchResolver(SinglePropertyExactMatchResolver):
    """
    SinglePropertyExactMatchResolver와 매칭 로직은 동일하되, 병합 시 description을
    배열로 합쳐 유실을 막는다(라이브러리 기본은 properties:'discard'라 충돌 속성 유실).

    병합 config가 라이브러리 run()에 하드코딩돼 훅이 없으므로 run() 본문을 복사하고
    apoc mergeNodes의 properties만 _MERGE_PROPS로 교체했다.
    """

    async def run(self) -> ResolutionStats:
        match_query = "MATCH (entity:__Entity__) "
        if self.filter_query:
            match_query += self.filter_query
        # 먼저 해소 대상 노드 수를 센다(0이면 조기 반환).
        stat_query = f"{match_query} RETURN count(entity) as c"
        records, _, _ = self.driver.execute_query(
            stat_query, database_=self.neo4j_database
        )
        number_of_nodes_to_resolve = records[0].get("c")
        if number_of_nodes_to_resolve == 0:
            return ResolutionStats(number_of_nodes_to_resolve=0)
        # 같은 라벨 + 같은 resolve_property(name) 값끼리 묶어 한 노드로 병합한다.
        merge_nodes_query = (
            f"{match_query} "
            f"WITH entity, entity.{self.resolve_property} as prop "
            "WITH entity, prop WHERE prop IS NOT NULL "
            "UNWIND labels(entity) as lab  "
            "WITH lab, prop, entity WHERE NOT lab IN ['__Entity__', '__KGBuilder__'] "
            "WITH prop, lab, collect(entity) AS entities "
            "CALL apoc.refactor.mergeNodes(entities,{ properties:"
            + _MERGE_PROPS
            + ", mergeRels:true }) "
            "YIELD node "
            "RETURN count(node) as c "
        )
        records, _, _ = self.driver.execute_query(
            merge_nodes_query, database_=self.neo4j_database
        )
        number_of_created_nodes = records[0].get("c")
        return ResolutionStats(
            number_of_nodes_to_resolve=number_of_nodes_to_resolve,
            number_of_created_nodes=number_of_created_nodes,
        )


class CombiningFuzzyResolver(BasePropertySimilarityResolver):
    """
    이름 문자열 유사도(RapidFuzz WRatio)로 표기 변형을 병합하는 resolver + combine 병합.

    임베딩 대비 장점: 짧은 이름의 표기변형(김독자/독자/독자 씨)과 타인의 유사도 구간이
    더 깨끗이 갈려 오병합 위험이 낮고, 로컬·무료·초고속(임베딩 API 호출 없음)이다.
    한계: 대명사·별칭 coref(나/화자=김독자)는 문자가 안 겹쳐 문자열 유사도로도 불가.

    Args:
        similarity_threshold: WRatio를 0~1로 정규화한 값 기준. 측정상 표기변형 0.9 / 타인
            0~0.33이라 0.85면 표기변형만 안전하게 잡는다.
    """

    def __init__(
        self,
        driver: neo4j.Driver,
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

    async def run(self) -> ResolutionStats:
        # ComponentMeta가 이 클래스에 직접 정의된 run을 요구하므로, 공유 본문으로 위임한다.
        return await _run_combining_similarity(self)

    def compute_similarity(self, text_a: str, text_b: str) -> float:
        # 지연 import: rapidfuzz 미설치 환경에서도 모듈 로드는 되게 한다.
        from rapidfuzz import fuzz

        # WRatio는 0~100 점수. 임계값과 맞추기 위해 0~1로 정규화한다.
        return fuzz.WRatio(text_a, text_b) / 100.0
