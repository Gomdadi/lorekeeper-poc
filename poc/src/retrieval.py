"""
Querying(조회) 단계 구현.

인덱싱으로 쌓인 KG를 여러 방식으로 조회하는 retriever들을 조립한다. 모든 retriever는
neo4j-graphrag의 base `Retriever`를 상속하거나 그 구현체이며, LLM 에이전트가 도구로 쓸 수
있도록 `convert_to_tool`로 감쌀 수 있다.

전략은 네 가지다.
  1. vector_cypher        : Chunk 임베딩으로 앵커를 찾고, 그 근거가 되는 사실(Event/
                            CharacterState)의 1-hop 이웃 서브그래프를 함께 반환.
  2. hybrid_cypher        : 위와 같되 벡터+풀텍스트(cjk analyzer, 한국어 형태 분해에 유리) 하이브리드 검색.
  3. entity_state_history : 특정 인물의 CharacterState 이력을 시간순으로 조회(커스텀 클래스).
  4. text2cypher          : 자연어 질의를 LLM이 Cypher로 번역해 실행(가변 컬럼).

핵심 설계:
  - 임베딩은 Chunk에만 존재하므로 벡터/하이브리드 검색의 앵커는 항상 Chunk다.
  - 앵커 Chunk에서 EVIDENCED_BY 역방향으로 사실 노드를 찾고, 거기서 도메인 이웃으로 확장해
    LLM이 바로 읽을 수 있는 텍스트(content)로 직렬화한다(정규화 result_formatter).
"""

from __future__ import annotations

import os

import neo4j

# 외부 라이브러리 — retriever/임베더/도구 타입.
from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.retrievers import (
    HybridCypherRetriever,
    Text2CypherRetriever,
    VectorCypherRetriever,
)
from neo4j_graphrag.retrievers.base import Retriever
from neo4j_graphrag.types import RawSearchResult, RetrieverResultItem

# 패키지 내부 모듈 — 곧 lorekeeper 패키지로 묶이므로 상대 import로 작성한다.
from .chunks import CHUNK_FULLTEXT_INDEX, CHUNK_VECTOR_INDEX
from .client import get_driver
from .pipeline import EMBEDDING_MODEL, build_llm

# ---------------------------------------------------------------------------
# 0) 내부 헬퍼 (드라이버·DB 이름·임베더)
# ---------------------------------------------------------------------------

# neo4j database 이름. indexing.py와 동일하게 NEO4J_DATABASE(기본 'neo4j')를 쓴다.
DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# 모듈 레벨 lazy singleton 캐시. retriever/인덱스 헬퍼가 매번 새 드라이버를 만들지 않도록
# 최초 호출 시 한 번만 만들어 재사용한다.
_DRIVER: neo4j.Driver | None = None
_EMBEDDER: OpenAIEmbeddings | None = None


def _driver() -> neo4j.Driver:
    """공유 Neo4j 드라이버(lazy singleton). 최초 호출 시 get_driver()로 생성 후 캐시."""
    global _DRIVER
    if _DRIVER is None:
        _DRIVER = get_driver()
    return _DRIVER


def _embedder() -> OpenAIEmbeddings:
    """
    질의 텍스트를 임베딩할 embedder(lazy singleton).

    ⚠️ pipeline.build_embedder()는 청킹용 TextChunkEmbedder를 반환하므로 retriever에는
    쓸 수 없다(embed_query 인터페이스가 아님). retriever가 요구하는 embed_query를 가진
    OpenAIEmbeddings를 직접 만들어야 한다. 임베딩 모델은 인덱싱 때 Chunk를 임베딩한 모델과
    같아야(EMBEDDING_MODEL) 벡터 공간이 일치한다.
    """
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    return _EMBEDDER


# ---------------------------------------------------------------------------
# 1) 공유 retrieval_query 상수 (VectorCypher·HybridCypher 공용)
# ---------------------------------------------------------------------------
# 벡터/하이브리드 검색이 찾은 앵커 Chunk(`node`)에서 시작해:
#   (1) IN_CHAPTER로 소속 Chapter를 잡고,
#   (2) EVIDENCED_BY 역방향으로 이 Chunk를 근거로 삼는 사실(Event/CharacterState)을 모은 뒤,
#   (3) 각 사실에서 1-hop 도메인 이웃으로 확장하고(추가로 RELATED_TO 인물, LOCATED_IN 상위
#       장소, PART_OF 상위 조직을 선택적으로 딸려온다),
#   (4) 그렇게 모은 서브그래프 노드 집합 내부의 관계만 추려 반환한다.
# `node`/`score`는 base 벡터 쿼리가 제공하는 변수다(retrieval_query 앞에서 바인딩됨).
_RETRIEVAL_QUERY = """
WITH node, score
OPTIONAL MATCH (node)-[:IN_CHAPTER]->(ch:Chapter)
OPTIONAL MATCH (node)<-[:EVIDENCED_BY]-(fact)
WHERE fact:Event OR fact:CharacterState
WITH node, score, ch, collect(DISTINCT fact) AS facts
CALL (facts) {
  UNWIND facts AS f
  MATCH (f)--(nbr)
  WHERE nbr:Character OR nbr:Location OR nbr:Organization
        OR nbr:Item OR nbr:Event OR nbr:CharacterState
  OPTIONAL MATCH (nbr)-[:RELATED_TO]-(rc:Character)
  OPTIONAL MATCH (nbr)-[:LOCATED_IN*1..]->(pl:Location)
  OPTIONAL MATCH (nbr)-[:PART_OF*1..]->(po:Organization)
  UNWIND [nbr, rc, pl, po] AS x
  WITH x WHERE x IS NOT NULL
  RETURN collect(DISTINCT x) AS neighbors
}
WITH node, score, ch, facts + neighbors AS raw
CALL (raw) {
  UNWIND raw AS n
  RETURN collect(DISTINCT n) AS subgraph
}
CALL (subgraph) {
  UNWIND subgraph AS a
  MATCH (a)-[r]->(b)
  WHERE b IN subgraph
  RETURN collect(DISTINCT {
    source: a.name, source_labels: labels(a),
    type: type(r), props: properties(r),
    target: b.name, target_labels: labels(b)
  }) AS relationships
}
RETURN
  node.text AS content,
  coalesce(node.chapter, ch.number) AS chapter,
  node.index AS chunk_index,
  score,
  [n IN subgraph | {labels: labels(n), name: n.name, description: n.description}] AS nodes,
  relationships
"""


# ---------------------------------------------------------------------------
# 2) 정규화 result_formatter (VectorCypher·HybridCypher 공용)
# ---------------------------------------------------------------------------
# 그래프 덤프에서 뺄 메타/lexical 라벨. content에는 도메인 라벨만 노출한다.
_META_LABELS = {"__Entity__", "__KGBuilder__", "Chunk", "Chapter", "Story"}


def _domain_label(labels: list[str] | None) -> str:
    """라벨 목록에서 메타 라벨을 뺀 도메인 라벨 하나를 고른다(없으면 'Node')."""
    if not labels:
        return "Node"
    domain = [lab for lab in labels if lab not in _META_LABELS]
    return domain[0] if domain else "Node"


def _props_summary(props: dict | None) -> str:
    """관계 속성(dict)을 'k=v, k=v'로 요약한다. 값이 비면 생략."""
    if not props:
        return ""
    return ", ".join(f"{k}={v}" for k, v in props.items() if v not in (None, ""))


def _render_subgraph(nodes: list, relationships: list) -> str:
    """
    record 하나의 서브그래프(nodes/relationships)를 사람이 읽기 좋은 여러 줄로 직렬화한다.

    context.dump_graph_text의 렌더 스타일을 참고하되, 여기서는 전체 DB가 아니라 record 하나의
    작은 서브그래프만 렌더한다. 노드는 '(라벨) 이름 — 설명', 관계는 'source —타입(속성)→ target'.
    """
    lines: list[str] = []

    # --- 노드 줄 ---
    for n in nodes or []:
        name = n.get("name") or "?"
        label = _domain_label(n.get("labels"))
        desc = n.get("description")
        line = f"- ({label}) {name}"
        if desc:
            line += f" — {desc}"
        lines.append(line)

    # --- 관계 줄 ---
    for r in relationships or []:
        src = r.get("source") or "?"
        tgt = r.get("target") or "?"
        rtype = r.get("type") or ""
        prop_str = _props_summary(r.get("props"))
        suffix = f"({prop_str})" if prop_str else ""
        lines.append(f"- {src} —{rtype}{suffix}→ {tgt}")

    return "\n".join(lines)


def _graph_result_formatter(record: neo4j.Record) -> RetrieverResultItem:
    """
    VectorCypher/HybridCypher record → RetrieverResultItem.

    _RETRIEVAL_QUERY가 RETURN하는 컬럼(content/chapter/chunk_index/score/nodes/relationships)을
    LLM이 바로 읽을 수 있는 텍스트로 렌더한다. content는 '원문 발췌 + 관련 그래프' 두 섹션으로,
    metadata에는 구조화 필드를 그대로 담아 후속 처리(인용·필터)에 쓸 수 있게 한다.
    """
    text = record.get("content")
    chapter = record.get("chapter")
    nodes = record.get("nodes") or []
    relationships = record.get("relationships") or []

    # 발췌 섹션 머리말(회차 표기는 있을 때만).
    chapter_label = f" · {chapter}화" if chapter is not None else ""
    parts = [f"[원문 발췌{chapter_label}]", text or ""]

    # 관련 그래프 섹션(노드/관계가 하나라도 있으면).
    graph_text = _render_subgraph(nodes, relationships)
    if graph_text:
        parts.append("\n[관련 그래프]\n" + graph_text)

    content = "\n".join(parts)

    return RetrieverResultItem(
        content=content,
        metadata={
            "chapter": chapter,
            "chunk_index": record.get("chunk_index"),
            "score": record.get("score"),
            "nodes": nodes,
            "relationships": relationships,
        },
    )


def _text2cypher_result_formatter(record: neo4j.Record) -> RetrieverResultItem:
    """
    Text2Cypher record → RetrieverResultItem.

    Text2Cypher는 LLM이 생성한 Cypher를 실행하므로 RETURN 컬럼이 질의마다 다르다. 따라서 특정
    컬럼을 가정하지 않고 record의 모든 key를 'key: value' 여러 줄로 렌더해 content로 삼고,
    metadata에는 record 전체를 dict로 담는다.
    """
    data = dict(record)
    content = "\n".join(f"{k}: {v}" for k, v in data.items())
    return RetrieverResultItem(content=content, metadata=data)


# ---------------------------------------------------------------------------
# 3) retriever 팩토리들 (전부 인자 없음)
# ---------------------------------------------------------------------------
# 풀텍스트 인덱스 이름은 인덱싱 생성처(chunks.py)와 단일 출처를 공유한다. 이 인덱스(cjk analyzer)는
# 인덱싱(chunks.write_chunk_layer)이 만들므로 여기서는 이름만 참조해 HybridCypher에 연결한다.
_FT_INDEX = CHUNK_FULLTEXT_INDEX


def build_vector_cypher_retriever() -> VectorCypherRetriever:
    """벡터 검색(Chunk 임베딩) + 그래프 확장 retriever."""
    return VectorCypherRetriever(
        driver=_driver(),
        index_name=CHUNK_VECTOR_INDEX,
        retrieval_query=_RETRIEVAL_QUERY,
        embedder=_embedder(),
        result_formatter=_graph_result_formatter,
        neo4j_database=DATABASE,
    )


def build_hybrid_cypher_retriever() -> HybridCypherRetriever:
    """
    벡터+풀텍스트 하이브리드 + 그래프 확장 retriever.

    풀텍스트 인덱스(_FT_INDEX)는 cjk analyzer로 인덱싱 때 생성된다(chunks.write_chunk_layer).
    """
    return HybridCypherRetriever(
        driver=_driver(),
        vector_index_name=CHUNK_VECTOR_INDEX,
        fulltext_index_name=_FT_INDEX,
        retrieval_query=_RETRIEVAL_QUERY,
        embedder=_embedder(),
        result_formatter=_graph_result_formatter,
        neo4j_database=DATABASE,
    )


def build_entity_state_history_retriever() -> "EntityStateHistoryRetriever":
    """특정 인물의 상태 이력을 시간순으로 조회하는 커스텀 retriever."""
    return EntityStateHistoryRetriever()


# Text2Cypher에 넘길 curated 스키마 문자열. 라이브러리 자동 스키마 추출(get_schema) 대신
# 도메인 6종 노드와 관계 패턴만 간결히 서술한다. 내부 메타 라벨(__Entity__/__KGBuilder__)과
# lexical/provenance 레이어(Chunk/Chapter)는 provenance 요약에서 최소한으로만 언급한다.
_TEXT2CYPHER_SCHEMA = """
노드 타입(모두 STRING 속성 name, description을 가진다):
- Character: 인물. (추가 속성) aliases: 변형 호칭(쉼표 나열).
- Location: 장소.
- Event: 사건. (추가 속성) chapter INTEGER=연재 회차, story_order FLOAT=작중 시간순.
- CharacterState: 인물의 상태·사실(신분·소속·능력·부상·생사·소유·역할 등). 시점별로 새 노드가 쌓인다.
- Organization: 조직·세력·단체.
- Item: 사물·작중 창작물.

관계 패턴:
- (Character)-[:APPEARS_IN]->(Event)                # 인물이 사건에 참여
- (Location)-[:HOSTS]->(Event)                      # 장소가 사건의 무대
- (Character)-[:HAS_STATE]->(CharacterState)        # 인물이 상태를 가짐
- (CharacterState)-[:ESTABLISHED_IN]->(Event)       # 상태가 성립한 사건(그 Event.chapter가 성립 시점)
- (Location)-[:LOCATED_IN]->(Location)              # 장소 상위 계층(한 단계씩)
- (Organization)-[:PART_OF]->(Organization)         # 조직 상위 계층(한 단계씩)
- (Character)-[:RELATED_TO]->(Character)            # 인물↔인물 관계. (속성) type, description
- (CharacterState)-[:ABOUT]->(Item)                 # 소유·역할 대상
- (CharacterState)-[:ABOUT]->(Organization)         # 소속 대상

근거(provenance) 레이어:
- (Event|CharacterState)-[:EVIDENCED_BY]->(Chunk)   # 사실의 근거 원문 조각
- (Chunk)-[:IN_CHAPTER]->(Chapter)                  # 조각이 속한 회차
- Chunk는 text(원문), chapter(회차 번호) 속성을 가진다. Chapter는 number(회차 번호).
"""

# Text2Cypher few-shot 예시. 각 원소는 질의/Cypher를 한 줄로 묶은 문자열(라이브러리가 그대로
# 프롬프트에 이어붙인다). 도메인 관계 패턴을 어떻게 Cypher로 옮기는지 보여준다.
_TEXT2CYPHER_EXAMPLES = [
    # 특정 인물이 등장하는 사건.
    "USER INPUT: '독자'가 등장하는 사건을 알려줘 "
    "QUERY: MATCH (c:Character)-[:APPEARS_IN]->(e:Event) "
    "WHERE c.name = '독자' OR '독자' IN split(c.aliases, ', ') "
    "RETURN e.name AS event, e.chapter AS chapter ORDER BY e.story_order",
    # N화~M화 사이 사건.
    "USER INPUT: 3화부터 5화 사이에 일어난 사건들 "
    "QUERY: MATCH (e:Event) WHERE e.chapter >= 3 AND e.chapter <= 5 "
    "RETURN e.name AS event, e.chapter AS chapter, e.description AS description "
    "ORDER BY e.story_order",
    # 특정 조직 구성원.
    "USER INPUT: '대한물산'에 소속된 인물은 누구야 "
    "QUERY: MATCH (c:Character)-[:HAS_STATE]->(s:CharacterState)-[:ABOUT]->(o:Organization) "
    "WHERE o.name = '대한물산' RETURN DISTINCT c.name AS character, s.name AS state",
]


def build_text2cypher_retriever() -> Text2CypherRetriever:
    """자연어 질의를 LLM이 Cypher로 번역해 실행하는 retriever."""
    return Text2CypherRetriever(
        driver=_driver(),
        llm=build_llm(),  # pipeline의 추출용 LLM 재사용(invoke 인터페이스 보유).
        neo4j_schema=_TEXT2CYPHER_SCHEMA,
        examples=_TEXT2CYPHER_EXAMPLES,
        result_formatter=_text2cypher_result_formatter,
        neo4j_database=DATABASE,
    )


# ---------------------------------------------------------------------------
# 4) 커스텀 EntityStateHistoryRetriever
# ---------------------------------------------------------------------------
# 특정 인물의 CharacterState 이력을 성립 회차 순으로 조회한다. 벡터/하이브리드 검색과 달리
# '이 인물의 상태 변화를 시간순으로'라는 정형 질의를 그래프 순회로 직접 처리한다.
_ENTITY_STATE_QUERY = """
MATCH (c:Character) WHERE c.name = $entity_name OR $entity_name IN c.aliases
OPTIONAL MATCH (c)-[rel:RELATED_TO]-(other:Character)
WITH c, collect(DISTINCT {name: other.name, type: rel.type, description: rel.description}) AS related_characters
MATCH (c)-[:HAS_STATE]->(s:CharacterState)
OPTIONAL MATCH (s)-[:ESTABLISHED_IN]->(ev:Event)
OPTIONAL MATCH (s)-[:EVIDENCED_BY]->(ck:Chunk)
OPTIONAL MATCH (s)-[:ABOUT]->(tgt)
WITH c, related_characters, s,
     coalesce(min(ev.chapter), min(ck.chapter)) AS est_chapter,
     collect(DISTINCT tgt.name) AS targets, collect(DISTINCT ck.text) AS evidence
WHERE $up_to_chapter IS NULL OR est_chapter <= $up_to_chapter
RETURN c.name AS character, related_characters,
       s.name AS content, s.description AS source_desc,
       est_chapter AS chapter, targets, evidence
ORDER BY est_chapter
"""


class EntityStateHistoryRetriever(Retriever):
    """
    특정 인물의 상태(CharacterState) 이력을 성립 회차 순으로 조회하는 retriever.

    동작 흐름:
      - $entity_name(이름 또는 aliases)으로 Character를 찾고,
      - 그 인물의 RELATED_TO 관계 인물을 모으고,
      - HAS_STATE로 걸린 각 상태의 성립 회차(ESTABLISHED_IN Event.chapter, 폴백: 근거 Chunk.chapter),
        ABOUT 대상, 근거 원문을 함께 반환한다.
      - up_to_chapter가 주어지면 그 회차까지 성립한 상태만 남긴다(특정 시점의 인물 상태 스냅샷).

    전제: KG에 Character/CharacterState/HAS_STATE/ESTABLISHED_IN이 채워져 있어야 한다.
    """

    def __init__(self) -> None:
        # get_driver()로 드라이버를 확보해 base Retriever를 초기화한다. base가 Neo4j 버전을
        # 검증하므로(VERIFY_NEO4J_VERSION=True) 유효한 드라이버가 필요하다.
        super().__init__(_driver(), neo4j_database=DATABASE)

    def get_search_results(
        self, entity_name: str, up_to_chapter: int | None = None
    ) -> RawSearchResult:
        """
        인물 상태 이력을 조회한다.

        Args:
            entity_name: 조회할 인물 이름(또는 aliases 중 하나).
            up_to_chapter: 이 회차까지 성립한 상태만 조회(None이면 전체).

        Returns:
            RawSearchResult: 상태별 record 리스트. 포맷팅은 default_record_formatter가 담당.
        """
        # vector.py와 동일한 execute_query 호출 방식(READ 라우팅).
        records, _, _ = self.driver.execute_query(
            _ENTITY_STATE_QUERY,
            {"entity_name": entity_name, "up_to_chapter": up_to_chapter},
            database_=self.neo4j_database,
            routing_=neo4j.RoutingControl.READ,
        )
        return RawSearchResult(records=records, metadata={})

    def default_record_formatter(self, record: neo4j.Record) -> RetrieverResultItem:
        """
        상태 record 하나를 LLM-ready 텍스트로 렌더한다.

        형식: '{chapter}화: {content}({source_desc}) [대상: ...] [관련인물: ...]'.
        metadata에는 구조화 필드(character/state/description/chapter/targets/
        related_characters)를 담는다.
        """
        chapter = record.get("chapter")
        state = record.get("content")  # CharacterState.name
        desc = record.get("source_desc")  # CharacterState.description
        targets = record.get("targets") or []
        related = record.get("related_characters") or []

        # 성립 회차 머리말(값이 없으면 '?화'로 방어).
        head = f"{chapter}화" if chapter is not None else "?화"
        line = f"{head}: {state}"
        if desc:
            line += f"({desc})"
        if targets:
            line += " [대상: " + ", ".join(str(t) for t in targets if t) + "]"
        if related:
            # 관련 인물은 '이름(관계종류)' 형태로 짧게 나열.
            rel_str = ", ".join(
                f"{r.get('name')}({r.get('type')})" if r.get("type") else str(r.get("name"))
                for r in related
                if r.get("name")
            )
            if rel_str:
                line += f" [관련인물: {rel_str}]"

        return RetrieverResultItem(
            content=line,
            metadata={
                "character": record.get("character"),
                "state": state,
                "description": desc,
                "chapter": chapter,
                "targets": targets,
                "related_characters": related,
            },
        )


# ---------------------------------------------------------------------------
# 5) 노출 함수 (retriever 묶음 · 도구 묶음)
# ---------------------------------------------------------------------------


def build_retrievers() -> dict[str, Retriever]:
    """
    네 가지 retriever를 이름 → 인스턴스 dict로 조립해 반환한다.

    키: vector_cypher / hybrid_cypher / entity_state_history / text2cypher.
    """
    return {
        "vector_cypher": build_vector_cypher_retriever(),
        "hybrid_cypher": build_hybrid_cypher_retriever(),
        "entity_state_history": build_entity_state_history_retriever(),
        "text2cypher": build_text2cypher_retriever(),
    }


def build_retrieval_tools() -> list:
    """
    각 retriever를 LLM 에이전트용 Tool(neo4j_graphrag.tool.Tool)로 감싼 리스트를 반환한다.

    convert_to_tool(name, description, parameter_descriptions)은 retriever의
    get_search_results 시그니처에서 파라미터를 자동 추론한다(base.py 참고). 여기서는 각 도구에
    한국어 name/description과 주요 파라미터 설명을 부여해 LLM이 도구 선택·인자 지정을 잘 하도록 한다.
    """
    retrievers = build_retrievers()

    tools = []

    # 벡터 검색 + 그래프 확장.
    tools.append(
        retrievers["vector_cypher"].convert_to_tool(
            name="vector_cypher_search",
            description=(
                "질문과 의미가 가까운 원문 조각을 벡터 검색으로 찾고, 그 근거가 되는 사건·상태의 "
                "관련 그래프까지 함께 반환한다. 자연어 질문에 대한 일반 검색에 쓴다."
            ),
            parameter_descriptions={
                "query_text": "검색할 자연어 질의(원문·사건·인물에 대한 질문).",
                "top_k": "반환할 상위 결과 개수(기본 5).",
            },
        )
    )

    # 하이브리드 검색(벡터 + 풀텍스트). 풀텍스트 인덱스(cjk)는 인덱싱이 생성한다.
    tools.append(
        retrievers["hybrid_cypher"].convert_to_tool(
            name="hybrid_search",
            description=(
                "벡터 검색과 풀텍스트 검색을 결합해 원문 조각을 찾고 관련 그래프를 함께 반환한다. "
                "고유명·정확한 키워드가 포함된 질의에 유리하다."
            ),
            parameter_descriptions={
                "query_text": "검색할 자연어 질의(키워드 포함).",
                "top_k": "반환할 상위 결과 개수(기본 5).",
            },
        )
    )

    # 인물 상태 이력.
    tools.append(
        retrievers["entity_state_history"].convert_to_tool(
            name="entity_state_history",
            description=(
                "특정 인물의 상태(신분·소속·능력·부상·생사·소유·역할 등) 변화를 성립 회차 순으로 "
                "조회한다. '누가 언제 어떤 상태가 되었는가', 특정 시점의 인물 상태를 물을 때 쓴다."
            ),
            parameter_descriptions={
                "entity_name": "조회할 인물의 이름 또는 별칭.",
                "up_to_chapter": "이 회차까지 성립한 상태만 조회한다(생략 시 전체 이력).",
            },
        )
    )

    # 자연어 → Cypher.
    tools.append(
        retrievers["text2cypher"].convert_to_tool(
            name="text2cypher_search",
            description=(
                "자연어 질의를 그래프 질의(Cypher)로 번역해 실행한다. 집계·정렬·조건(‘N화 이후 "
                "사건 수’, ‘특정 조직 구성원 목록’ 등) 같은 정형 질의에 쓴다."
            ),
            parameter_descriptions={
                "query_text": "그래프에서 답을 찾을 자연어 질의.",
            },
        )
    )

    return tools
