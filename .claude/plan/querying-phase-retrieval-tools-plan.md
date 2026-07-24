# Querying Phase — Import 가능한 Retrieval 도구 라이브러리 제공

## Context (왜 이 작업을 하는가)

LoreKeeper 저장소는 원고 → KG 인덱싱 계층만 구현돼 있고(`poc/src/`는 전부 쓰기 경로), 검색(querying)
코드는 전무하다. 이번 작업의 목표는 **단 하나**다:

> **이 프로젝트를 clone → import 하면, 다른 프로젝트에서 곧바로 쓸 수 있는 retrieval 도구 세트(+ 기존 indexing 진입점)를 제공한다.**

명시적 범위 제한(사용자 확정):
- **LangGraph / LangChain 통합은 여기서 구현하지 않는다.** (소비하는 쪽 프로젝트의 몫)
- **충돌 감지 전처리(신규 KG 추출·claim→쿼리 생성·Judge·답변 생성)도 여기서 다루지 않는다.**
- 이 저장소는 **retrieval 도구를 라이브러리로 노출**하는 것까지만 책임진다. 오케스트레이션은 import한 쪽이 한다.

따라서 산출물은 프레임워크 중립적인(langchain 비의존) **retrieval API**다. 소비 프로젝트는 이걸 import해
자신의 LangGraph 도구로 배선하거나 `.search()`를 직접 호출한다.

### 조사로 확정된 핵심 제약
- **임베딩은 `Chunk.embedding`에만 존재**(1536, cosine, 인덱스 `chunk_emb`; `poc/src/chunks.py:22-79`).
  도메인 노드엔 임베딩 없음 → **모든 벡터 검색은 Chunk를 앵커로 잡고 `EVIDENCED_BY`로 도메인 노드를 역방향 확장**해야 한다.
- **풀텍스트 인덱스 없음** → HybridCypher를 쓰려면 `Chunk.text`에 풀텍스트 인덱스가 DB에 존재해야 한다(선행 helper 제공).
- 도메인 스키마(`poc/src/schema.py`): `Character/Location/Event/CharacterState/Organization/Item` 6종, 전부
  `name`+`description`. provenance: `(Event|CharacterState)-[:EVIDENCED_BY]->(Chunk)-[:IN_CHAPTER]->(Chapter)`.
  기존 플랜의 `retrieval_query` 예시(`sourcespan-vector-rag-plan.md:96-110`)는 구스키마(`e.title`,
  `cs.attribute`) 기준이라 **현재 name/description 스키마로 재작성 필요**.
- `neo4j-graphrag[openai]>=1.18.0` 설치됨. **langgraph/langchain-core는 추가하지 않는다.**

---

## 구현 방안

프레임워크 중립 원칙: 반환은 라이브러리 네이티브 타입(`RetrieverResult`)과, 그걸 감싼
`neo4j_graphrag.tool.Tool`(langchain 아님)만 쓴다. 소비 쪽이 어떤 에이전트 프레임워크든 붙일 수 있게 한다.

### 1. `poc/src/retrieval.py` (신규 — 핵심 산출물)

**핵심 원칙 — content는 LLM-ready 텍스트**
소비 쪽은 **검색 결과를 LLM 프롬프트에 그대로 넣는다**(GraphRAG도 `"\n".join(item.content)`로 조립,
graphrag.py:157). 따라서 4개 도구 모두 `RetrieverResultItem.content`에 **그 자체로 자족적인 렌더 텍스트**
(원문 발췌 + 서브그래프 노드·관계)를 담는다. `metadata`에는 동일 정보의 **구조화 버전**을 담아 근거 추적·필터용으로 남긴다.

**(a) 정규화 result_formatter**
- 공통 `result_formatter` 콜백을 주입(base.py:183 `get_result_formatter` 활용).
- **`content`** = 사람이자 LLM이 읽을 렌더 텍스트. 예:
  ```
  [원문 발췌 · 3화]
  ...김남운은 맥가이버 칼을 꺼내 이민호의 어깨를 깊이 그었다...

  [관련 그래프]
  - (Event) 김남운이 이민호를 습격함 · 참여: 김남운, 이민호 · 장소: 옥상
  - (CharacterState) 어깨를 칼날에 깊이 베임 · 주체: 이민호 · 3화 성립
  - 김남운 —RELATED_TO(적대)→ 이민호
  ```
  → 노드·관계를 텍스트로 직렬화. **`context.py:dump_graph_text`(context.py:58-)의 엔티티 중심 렌더 스타일을
  per-subgraph 헬퍼로 재사용**(전체 DB 덤프가 아니라 record 하나의 nodes/relationships를 렌더).
- **`metadata`** = `{chapter, chunk_index, score, nodes, relationships}` 구조화 버전(아래 (b)의 RETURN과 1:1).
  `chunk_index`는 회차 내 몇 번째 Chunk인지(`Chunk.index`, 원문 `[C{index}]` 마커와 일치) → 근거 위치 정밀 추적.

**(b) 공유 retrieval_query 상수** (Chunk 앵커 → 1-hop + 타입별 선택적 2-hop, 현재 스키마)
```cypher
WITH node, score
OPTIONAL MATCH (node)-[:IN_CHAPTER]->(ch:Chapter)

-- 앵커: 이 Chunk를 근거로 삼는 Event / CharacterState
OPTIONAL MATCH (node)<-[:EVIDENCED_BY]-(fact)
WHERE fact:Event OR fact:CharacterState
WITH node, score, ch, collect(DISTINCT fact) AS facts

-- fact의 1-hop 이웃 + 타입별 선택적 확장
--   Character    → RELATED_TO → Character (1-hop, 계층 아님)
--   Location     → LOCATED_IN*1.. → Location (루트까지 모든 상위, 계층)
--   Organization → PART_OF*1..    → Organization (루트까지 모든 상위, 계층)
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

-- 전체 노드 집합 = facts + neighbors, DISTINCT 처리(빈 결과라도 행 보존)
WITH node, score, ch, facts + neighbors AS raw
CALL (raw) {
  UNWIND raw AS n
  RETURN collect(DISTINCT n) AS subgraph
}

-- 수집된 노드 집합 내부의 모든 관계(양끝이 subgraph에 포함된 것만)
--   RELATED_TO·LOCATED_IN·PART_OF 등 추가된 관계까지 자동 포함. props로 RELATED_TO.type 등 관계 속성도 확보.
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
```
- **확장 규칙**: 앵커 fact → 1-hop 이웃(6종 도메인 노드) → 이웃 타입별 선택적 확장. Character=RELATED_TO(1-hop),
  Location=LOCATED_IN·Organization=PART_OF는 **계층이라 `*1..`로 루트까지 모든 상위**를 수집. RELATED_TO는 계층이
  아니므로 1-hop만. (관계 타입이 대상 라벨을 한정하므로 라벨 가드 불필요. `*1..`는 같은 관계를 재방문하지 않아
  사이클 안전; 필요시 `*1..10` 등 깊이 상한.)
- **Chunk당 1개 item으로 집계**(팬아웃 없음) → top_k 의미 유지. dedup은 `CALL {}`로 감싸 **fact 없는 Chunk도 행 보존**.
- CharacterState 쪽도 자동 커버: 1-hop으로 Character(HAS_STATE)·Item/Organization(ABOUT)·Event(ESTABLISHED_IN),
  그 Character에 RELATED_TO·Organization에 PART_OF가 이어짐 → Event/CharacterState를 한 쿼리로 처리.
- 이 enriched 형태는 **VectorCypher·HybridCypher 전용**. EntityStateHistory·Text2Cypher는 RETURN 컬럼이
  다르므로 `{nodes, relationships}` 스키마를 강제하지 않고 **content=LLM-ready 텍스트 원칙만 공유**(best-effort 정규화).
- 실데이터로 검증·조정(특히 `CALL (var) {}` 스코프 구문은 Neo4j 5.23+ 전제).
- 남은 리스크: 허브 노드(주인공 등)의 국소적 팬아웃 — 필요시 이웃 개수 상한·관계 타입 화이트리스트로 통제(후속).

**(c) retriever 팩토리 4종** — **인자 없음**. 드라이버/embedder/llm은 팩토리 내부에서 기존 자산을 직접 호출해 확보
(`client.get_driver()`, `pipeline.build_embedder()`, `pipeline.build_llm()`). 드라이버는 모듈 레벨 lazy singleton으로
한 번만 생성해 재사용(중복 드라이버 방지):
- `build_vector_cypher_retriever()` — `VectorCypherRetriever(index_name="chunk_emb", retrieval_query=…)`.
- `build_hybrid_cypher_retriever()` — 단일 풀텍스트 인덱스 `chunk_text_ft`(**cjk analyzer**)를 쓰는
  `HybridCypherRetriever` 1종. 벡터 인덱스는 공통 `chunk_emb`. 고유명·수치·호칭 정확 매칭 보강.
  (A/B 벤치에서 cjk가 standard 대비 recall 93% vs 27%로 우위 → **cjk 확정, standard 폐기**.)
- `build_entity_state_history_retriever()` — **커스텀 클래스**(아래 (d)).
- `build_text2cypher_retriever()` — `Text2CypherRetriever(llm=build_llm(), neo4j_schema=…, examples=…)`.
  - **Cypher 생성**: LLM이 (스키마 + few-shot 예시 + 질의)로 Cypher 생성 → `extract_cypher()`(코드펜스 제거·다단어
    식별자 백틱) → `EXPLAIN`으로 read-only 확인 후 실행(쓰기 거부, text2cypher.py:217-238).
  - **스키마 소스**: 내부 메타 라벨(__Entity__/__KGBuilder__/Chunk/Chapter) 노이즈를 피하려 **schema.py 기반 curated
    스키마**(도메인 6종 + provenance 요약: EVIDENCED_BY·IN_CHAPTER·chapter) 권장. `get_schema(driver)` 자동은 대안.
  - **few-shot 예시 필수**: 정확도가 예시 품질에 크게 의존 → 도메인 대표 질의쌍 몇 개('X가 등장하는 사건'→APPEARS_IN,
    '3~7화 사건'→chapter 필터, '이 조직 구성원'→ABOUT).
  - **결과 format**: RETURN 컬럼이 가변이라 `{nodes, relationships}` 강제 불가 → result_formatter가 record를
    `key: value` 텍스트로 일반 렌더(content=LLM-ready), metadata엔 record 원본 dict, `RetrieverResult.metadata["cypher"]`에
    생성된 쿼리(라이브러리 자동, text2cypher.py:246-248).

**(d) `EntityStateHistoryRetriever` (커스텀 — `Retriever` 상속)**
- `get_search_results(entity_name: str, up_to_chapter: int | None = None)` 구현. 파라미터 Cypher(결정적·안전,
  Text2Cypher의 LLM 생성 리스크 없음):
```cypher
MATCH (c:Character) WHERE c.name = $entity_name OR $entity_name IN c.aliases

-- 관련 인물(RELATED_TO) — 방향 무관, 관계 type·description 포함(인물 레벨 컨텍스트)
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
```
- 성립 회차 판정은 `context.py:98-109`(ESTABLISHED_IN→Event.chapter, 폴백 EVIDENCED_BY→Chunk.chapter) 패턴 재사용.
- **`RELATED_TO` 인물 노드도 함께 반환**(관계 type·description 포함). 인물 레벨 컨텍스트라 상태 이력 각 item의
  metadata에 동일하게 실리며(인물 단위 상수), formatter가 헤더로 한 번만 렌더 가능.
- 인물명 매칭은 MVP에서 exact + alias만(fuzzy는 후속).

**(e) 프레임워크 중립 tool 노출**
- `build_retrieval_tools() -> list[Tool]`: 4개 retriever를 각각
  `retriever.convert_to_tool(name, description, parameter_descriptions)`(base.py:410)로 감싸 반환.
  → `neo4j_graphrag.tool.Tool`(langchain 무관). 소비 쪽이 자기 프레임워크로 어댑트.
- 함께 `build_retrievers() -> dict[str, Retriever]`도 노출(도구 없이 `.search()` 직접 호출 원하는 소비자용).

**(f) 도구 ↔ 사용 사례 매핑** — 이 4개 도구는 **충돌 감지와 일반 검색을 모두** 커버한다(오케스트레이션·답변 생성만 소비 쪽).
- 의미·서술형 질의("X에 대해 알려줘") → **VectorCypher / HybridCypher**
- 인물 상태 타임라인 → **EntityStateHistory**(결정적)
- 개방형 구조·집계 질의("이 아이템의 현재 소유자", "3~7화 서울에서 일어난 사건", "이 조직 구성원 전부") →
  **Text2Cypher**. 일반 검색은 질의가 예측 불가라 파라미터 Cypher 도구로 다 열거 불가 → **NL→Cypher catch-all이 필요**.
  정규화는 best-effort(`content`=반환 record의 `key: value` 렌더, `metadata.cypher`=생성 쿼리).

### 2. `poc/src/retrieval.py` 내 `ensure_search_indexes()` — cjk 풀텍스트 인덱스 보장(idempotent)
- 단일 풀텍스트 인덱스 `chunk_text_ft`를 **cjk analyzer 고정**으로 생성. 헬퍼 `create_fulltext_index`는 analyzer를
  못 넣으므로 raw Cypher로 만든다:
  ```cypher
  CREATE FULLTEXT INDEX chunk_text_ft IF NOT EXISTS
  FOR (n:Chunk) ON EACH [n.text]
  OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
  ```
- **cjk 확정 근거**: A/B 벤치에서 cjk 평균 recall 93% vs standard 27%(한국어 조사·어미 결합 때문). Neo4j는 같은
  `(Chunk,text)`에 풀텍스트 인덱스를 하나만 허용해 애초에 병존도 불가 → standard 폐기.
- **인덱싱이 자동 생성**: `chunks.write_chunk_layer`가 벡터 인덱스(`chunk_emb`)와 함께 이 풀텍스트 인덱스도 만든다
  (인덱스 이름·analyzer 상수는 `chunks.py`가 단일 출처, retrieval이 import). → **인덱싱한 DB면
  `ensure_search_indexes()` 호출 불필요**. 이 함수는 인덱싱 없이 검색만 붙이거나 인덱스가 삭제된 경우의 보조 유틸.

### 3. `poc/src/__init__.py` + `poc/pyproject.toml` (import 가능하게 패키징) — **indexing·querying 양쪽 노출**
- 공개 API를 패키지 최상위에서 노출:
  - **querying**: `build_retrieval_tools`, `build_retrievers`, `ensure_search_indexes`, `EntityStateHistoryRetriever`, 팩토리들.
  - **indexing**: 기존 진입점 `indexing(chapter, text)`(**async**, indexing.py:83) 재노출 →
    소비 쪽이 `from <package> import indexing` 후 `await indexing(1, text)`로 원고 인덱싱 호출.
- `pyproject.toml`의 `[project]`/패키징 설정 정리 — 현재 `name="poc"`, `poc/src/`가 **평면 모듈 레이아웃**(패키지
  디렉토리·`__init__.py` 없음, 모듈 간 top-level import)이라 **패키지 경계와 모듈 간 import 방식(절대/상대)을 정리**해야
  clone 후 import가 동작. **clone 후 `pip install -e .`(또는 경로 import)**로
  `from <package> import indexing, build_retrieval_tools`가 되게 한다.

### 재사용할 기존 자산
- `poc/src/client.py:get_driver` — 드라이버
- `poc/src/pipeline.py:build_llm / build_embedder` — LLM·임베딩(기본값)
- `poc/src/context.py:98-109` — 성립 회차 판정 패턴(EntityStateHistory)
- `poc/src/schema.py` + `neo4j_graphrag.schema.get_schema` — Text2Cypher 스키마

### 범위 밖 (명시)
- LangGraph/LangChain 배선, 에이전트, tool 라우팅 — **소비 프로젝트 담당**.
- 충돌 감지 전처리(신규 KG 추출·claim→쿼리 생성), Judge, 답변 생성(GraphRAG) — **소비 프로젝트 담당**.
- Ground Truth·F1 평가, 커뮤니티 기반 검색(MVP 이후), 인물명 fuzzy 매칭.
- 참고: **일반 검색의 "검색" 부분은 이 도구들로 커버**한다(범위 안). 범위 밖은 그 위의 라우팅·답변 생성뿐.

---

## 검증 (end-to-end)

전제: Neo4j 기동, 최소 1~2개 회차 인덱싱 완료(`chunk_emb`에 데이터 존재). 검증은 **소비 시나리오 모사**로,
`poc/` 밖(또는 scratch 스크립트)에서 import해 호출한다.

1. **패키징**: clone 상태에서 `from <package> import indexing, build_retrieval_tools` 성공.
   `await indexing(1, text)`로 1회차 인덱싱 → Chunk/도메인 노드 생성(기존 인덱싱 검증과 동일)까지 import 경로로 동작.
2. **인덱스 보장**: `ensure_search_indexes()` 호출 → `SHOW FULLTEXT INDEXES`에 `chunk_text_ft` 생성 확인.
3. **VectorCypher**: `build_retrievers()["vector_cypher"].search("<사건 관련 자연어>")` →
   정규화 `RetrieverResult.items`(content + metadata{chapter, chunk_index, score, nodes, relationships}), score 내림차순.
4. **HybridCypher**: 고유명 포함 쿼리 → 벡터만으로 놓치던 정확 매칭 결과가 섞이는지 VectorCypher와 비교.
5. **EntityStateHistory**: 상태 변화 인물명으로 `.search(entity_name="…", up_to_chapter=N)` →
   CharacterState가 성립 회차 오름차순 + ABOUT 대상·근거 Chunk 포함, 시점 제한 동작.
6. **Text2Cypher**: "X가 등장하는 모든 사건" → 생성 Cypher read-only·결과 정합(metadata `cypher` 확인),
   쓰기 쿼리 거부(EXPLAIN 가드).
7. **tool 노출**: `build_retrieval_tools()` → 4개 `Tool` 반환, 각 `.get_name()`/파라미터 스키마가
   시그니처대로 추론되는지(특히 EntityStateHistory의 `entity_name`, `up_to_chapter`) 확인.
