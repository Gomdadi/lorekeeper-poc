# 검색(querying) 계층 함수 스펙

`poc/src/retrieval.py` — 인덱싱으로 쌓인 KG를 조회하는 **프레임워크 중립 retriever 계층**.
라우팅·답변 생성·충돌 판정 같은 오케스트레이션은 소비 프로젝트(LangGraph 등)의 몫이고, 이
계층은 **"검색"까지만** 책임진다. 모든 검색기는 `neo4j-graphrag`의 base `Retriever`를 상속하거나
그 구현체이며, 반환은 라이브러리 네이티브 타입뿐이라 어떤 에이전트 프레임워크에든 배선된다.

핵심 설계:

- **임베딩은 Chunk에만 존재** → 벡터/하이브리드 검색의 앵커는 항상 Chunk다. Chunk를 찾은 뒤
  `EVIDENCED_BY`를 **역방향**으로 타 사실(Event/CharacterState)로 확장한다.
- **content = LLM-ready 텍스트** — 각 결과를 "원문 발췌 + 관련 그래프" 한 덩어리로 렌더해 소비
  쪽이 프롬프트에 그대로 넣을 수 있다.
- **metadata = 구조화 필드** — 같은 정보의 구조화 버전(`chapter`/`chunk_index`/`score`/`nodes`/
  `relationships` 등)을 담아 인용·필터·후속 처리에 쓴다.

## 공개 API

```python
from lorekeeper import (
    build_retrievers,                        # dict[str, Retriever] — 4종 이름→인스턴스
    build_retrieval_tools,                   # list[neo4j_graphrag.tool.Tool] — 4종을 Tool로 래핑
    build_vector_cypher_retriever,           # VectorCypherRetriever
    build_hybrid_cypher_retriever,           # HybridCypherRetriever
    build_entity_state_history_retriever,    # (커스텀) EntityStateHistoryRetriever
    build_text2cypher_retriever,             # Text2CypherRetriever
)
```

## 반환 타입 (`RetrieverResult` / `RetrieverResultItem`)

`.search(...)`는 `RetrieverResult`를, 그 안의 `items`는 `RetrieverResultItem` 리스트를 담는다.

| 타입 | 필드 | 의미 |
| --- | --- | --- |
| `RetrieverResult` | `items: list[RetrieverResultItem]` | 결과 아이템 목록 |
| | `metadata: dict` | 검색 수준 메타(예: text2cypher의 `cypher`) |
| `RetrieverResultItem` | `content: str` | LLM 프롬프트에 그대로 넣는 자족 텍스트 |
| | `metadata: dict` | 아이템별 구조화 필드(근거 추적용) |

`content`는 프롬프트 주입용, `metadata`는 인용·필터·재순위용 — 같은 정보의 두 표현이다.

## 팩토리 시그니처 (전부 인자 없음)

```python
def build_vector_cypher_retriever()        -> VectorCypherRetriever
def build_hybrid_cypher_retriever()        -> HybridCypherRetriever
def build_entity_state_history_retriever() -> EntityStateHistoryRetriever
def build_text2cypher_retriever()          -> Text2CypherRetriever

def build_retrievers()      -> dict[str, Retriever]   # 키 4종(아래)
def build_retrieval_tools() -> list                    # neo4j_graphrag.tool.Tool 리스트
```

`build_retrievers()`의 키: `vector_cypher` / `hybrid_cypher` / `entity_state_history` /
`text2cypher`. `build_retrieval_tools()`는 각 retriever를 `convert_to_tool(...)`로 감싸
한국어 name/description과 파라미터 설명을 부여한다(LLM 도구 선택·인자 지정용). `convert_to_tool`은
`get_search_results` 시그니처에서 파라미터를 자동 추론한다.

## 내부 헬퍼 (모듈 상수)

| 이름 | 역할 |
| --- | --- |
| `_driver()` | 공유 Neo4j 드라이버(lazy singleton). 최초 호출 시 `get_driver()`로 생성 후 캐시 |
| `_embedder()` | 질의 임베더(lazy singleton). `OpenAIEmbeddings(model=EMBEDDING_MODEL)` — **인덱싱 때 Chunk를 임베딩한 모델(`text-embedding-3-small`)과 동일**해야 벡터 공간 일치. `pipeline.build_embedder()`는 청킹용이라 `embed_query`가 없어 재사용 불가 |
| `DATABASE` | `NEO4J_DATABASE`(기본 `neo4j`) — indexing과 동일 |

## 검색 도구 4종

| # | 도구 키 / Tool name | 방식 | 입력 파라미터 | 강점 |
| --- | --- | --- | --- | --- |
| 1 | `vector_cypher` / `vector_cypher_search` | 벡터 유사도 → 그래프 확장 | `query_text`, `top_k`(기본 5) | 의미가 가까운 원문·사건 |
| 2 | `hybrid_cypher` / `hybrid_search` | 벡터+풀텍스트(cjk) → 그래프 확장 | `query_text`, `top_k`(기본 5) | 고유명·정확 어휘 매칭 |
| 3 | `entity_state_history` / `entity_state_history` | 결정적 파라미터 Cypher | `entity_name`, `up_to_chapter`(선택) | 인물 상태 타임라인·특정 시점 |
| 4 | `text2cypher` / `text2cypher_search` | LLM 자연어→Cypher | `query_text` | 집계·개방형 구조 질의 |

### 1) vector_cypher

- **시그니처**: `.search(query_text: str, top_k: int = 5)` (base `VectorCypherRetriever`).
  내부 `get_search_results`는 `query_vector`도 받지만 도구는 `query_text`/`top_k`만 노출.
- **동작**: 질의 텍스트를 `_embedder()`로 임베딩 → 벡터 인덱스 `chunk_emb`에서 상위 `top_k`
  Chunk를 앵커로 확보 → 공유 `_RETRIEVAL_QUERY`(아래)로 서브그래프 확장.
- **반환 content**: `[원문 발췌 · N화]` + Chunk 원문, 그 뒤 `[관련 그래프]` 섹션에 노드
  `- (라벨) 이름 — 설명`과 관계 `- source —타입(속성)→ target` 여러 줄. 렌더는
  `_graph_result_formatter` → `_render_subgraph`.
- **반환 metadata**: `{chapter, chunk_index, score, nodes, relationships}`. `nodes`는
  `{labels, name, description}` 리스트, `relationships`는 `{source, source_labels, type,
  props, target, target_labels}` 리스트.

### 2) hybrid_cypher

- **시그니처**: `.search(query_text: str, top_k: int = 5)` (base `HybridCypherRetriever`).
- **동작**: 1)과 동일한 앵커→확장이되, 앵커 확보를 **벡터 검색 + 풀텍스트 검색**으로 병행한다.
  풀텍스트 인덱스 `chunk_text_ft`(cjk analyzer)를 함께 조회해 순위를 합친다.
- **반환 content/metadata**: vector_cypher와 동일(같은 `_RETRIEVAL_QUERY`·같은 formatter).

### 3) entity_state_history (커스텀 retriever)

- **시그니처**: `.search(entity_name: str, up_to_chapter: int | None = None)`. 커스텀
  `EntityStateHistoryRetriever.get_search_results(entity_name, up_to_chapter=None)`가 근거.
- **동작**: `_ENTITY_STATE_QUERY`(결정적 파라미터 Cypher, READ 라우팅)를 실행한다.
  - `$entity_name`(이름 또는 `aliases`)으로 `Character`를 찾고,
  - 그 인물의 `RELATED_TO` 관계 인물을 모으고,
  - `HAS_STATE`로 걸린 각 `CharacterState`의 **성립 회차**(`ESTABLISHED_IN`→`Event.chapter`,
    폴백: 근거 `Chunk.chapter`의 `min`), `ABOUT` 대상, 근거 Chunk 원문을 함께 반환,
  - `$up_to_chapter`가 주어지면 `est_chapter <= up_to_chapter`인 상태만 남기고(특정 시점
    스냅샷), `ORDER BY est_chapter`로 성립 회차 오름차순 정렬.
  벡터·LLM에 의존하지 않아 누락·생성 오류가 없다(시간축 모순 감지의 핵심).
- **반환 content**: 상태 한 줄 = `{chapter}화: {state}({description}) [대상: ...] [관련인물:
  이름(관계종류), ...]`. 렌더는 `default_record_formatter`.
- **반환 metadata**: `{character, state, description, chapter, targets, related_characters}`.

### 4) text2cypher

- **시그니처**: `.search(query_text: str)` (base `Text2CypherRetriever`).
- **동작**: curated 스키마(`_TEXT2CYPHER_SCHEMA`) + few-shot(`_TEXT2CYPHER_EXAMPLES`)를 주입해
  LLM(`build_llm()` — 추출용 LLM 재사용)이 Cypher를 생성 → 라이브러리가 **read-only 가드**로
  검사 후 실행. 쓰기 쿼리는 실행을 거부한다("Refusing to execute non-read-only Cypher").
- **반환 content**: RETURN 컬럼이 질의마다 다르므로 특정 컬럼을 가정하지 않고 record의 모든
  key를 `key: value` 여러 줄로 렌더(`_text2cypher_result_formatter`).
- **반환 metadata**: 아이템 metadata에는 **record 전체 dict**를 그대로 담는다. 생성된 Cypher는
  **검색 수준 메타**(`RetrieverResult.metadata["cypher"]`, 라이브러리가 채움)로 노출된다.

## 벡터·하이브리드 공유 확장 쿼리 (`_RETRIEVAL_QUERY`)

`VectorCypherRetriever`와 `HybridCypherRetriever`가 공유하는 `retrieval_query`. 앵커 Chunk를
가리키는 변수 `node`/`score`는 base 벡터/하이브리드 쿼리가 앞단에서 바인딩한다.

| # | 단계 | Cypher 요지 | 산출 |
| --- | --- | --- | --- |
| 1 | 회차 앵커 | `(node)-[:IN_CHAPTER]->(ch:Chapter)` | 소속 Chapter |
| 2 | 사실 역추적 | `(node)<-[:EVIDENCED_BY]-(fact)` where `fact:Event OR fact:CharacterState` | 이 Chunk를 근거로 삼는 사실들(`facts`) |
| 3 | 1-hop 이웃 | 각 `fact`에서 `(f)--(nbr)`, `nbr`이 Character/Location/Organization/Item/Event/CharacterState | 도메인 이웃 |
| 3a | 선택 확장 | `nbr`에서 `RELATED_TO`(Character, 1-hop), `LOCATED_IN*1..`(상위 Location), `PART_OF*1..`(상위 Organization) | 관련 인물·상위 장소·상위 조직 |
| 4 | 서브그래프 집계 | `facts + neighbors`를 DISTINCT 노드 집합(`subgraph`)으로 | 노드 집합 |
| 5 | 내부 관계 | `subgraph` 노드 사이의 `(a)-[r]->(b) WHERE b IN subgraph`만 | 서브그래프 내부 관계(속성 포함) |

**RETURN**: `content(=node.text)`, `chapter(=coalesce(node.chapter, ch.number))`,
`chunk_index(=node.index)`, `score`, `nodes`(`{labels,name,description}` 리스트),
`relationships`(`{source,source_labels,type,props,target,target_labels}` 리스트).
**Chunk당 1 item으로 집계**하므로 `top_k`의 의미(상위 Chunk 개수)가 유지된다.

## 하이브리드 풀텍스트 analyzer = cjk

`hybrid_cypher`가 쓰는 풀텍스트 인덱스 `chunk_text_ft`는 **cjk analyzer**로 만든다(인덱싱이
`chunks.write_chunk_layer`에서 생성, retrieval은 이름만 참조).

- 한국어는 조사·어미가 명사에 붙어(`유상아는`·`특성을`) 공백 토큰만 매칭하는 `standard`로는
  대부분 놓친다. cjk는 2글자 bigram으로 분해해 recall이 크게 높다.
- A/B 벤치: 평균 recall **cjk 93% vs standard 27%**.
- Neo4j는 같은 `(Chunk, text)`에 풀텍스트 인덱스를 **하나만** 허용하므로 인덱스는 단일(cjk 고정).
- 라이브러리 `create_fulltext_index`는 analyzer 지정을 못 해 인덱싱이 raw Cypher로 cjk를 준다.

## text2cypher — curated 스키마 + few-shot + read-only 가드

- **curated 스키마**(`_TEXT2CYPHER_SCHEMA`): 라이브러리 자동 스키마 추출(`get_schema`) 대신 도메인
  6종 노드(Character/Location/Event/CharacterState/Organization/Item)와 관계 패턴
  (`APPEARS_IN`/`HOSTS`/`HAS_STATE`/`ESTABLISHED_IN`/`LOCATED_IN`/`PART_OF`/`RELATED_TO`/
  `ABOUT`) + provenance 레이어(`EVIDENCED_BY`/`IN_CHAPTER`)를 간결히 서술.
- **few-shot**(`_TEXT2CYPHER_EXAMPLES`): "인물 등장 사건", "N~M화 사건", "조직 구성원" 등 도메인
  관계를 Cypher로 옮기는 예시 3종.
- **가변 RETURN**: 컬럼이 질의마다 달라 content는 `key: value` 렌더, 아이템 metadata는 record
  전체 dict, 생성 Cypher는 `RetrieverResult.metadata["cypher"]`.
- **read-only 가드**: 라이브러리가 생성 쿼리를 검사해 read-only만 실행(쓰기 거부).

## 전제조건·주의

- **인덱싱된 DB가 있어야 한다** — 검색은 `indexing()`으로 채운 그래프를 전제. 인덱싱하지 않은
  DB에서는 결과가 비거나(vector/hybrid/entity_state) text2cypher가 빈 결과를 준다.
- **벡터/풀텍스트 인덱스는 indexing이 자동 생성**한다(`chunk_emb` 벡터 · `chunk_text_ft` cjk
  풀텍스트). 소비 쪽이 검색 전에 인덱스를 따로 만들 필요가 없다.
- **임베딩 모델 일치** — 질의 임베딩(`_embedder()`)과 Chunk 임베딩은 같은 모델
  (`text-embedding-3-small`, 1536차원, cosine)이어야 벡터 공간이 맞다.
- **OpenAI 키 필요** — 벡터/하이브리드는 질의 임베딩에, text2cypher는 Cypher 생성 LLM에
  `OPENAI_API_KEY`를 쓴다. entity_state_history만 순수 Cypher라 임베딩·LLM이 필요 없다.
- **드라이버·DB·임베더는 lazy singleton**으로 공유된다(팩토리를 여러 번 불러도 드라이버는 하나).
- **환경변수**: `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`, `NEO4J_DATABASE`(기본 `neo4j`),
  `LOREKEEPER_MODEL`(text2cypher LLM, 기본 `gpt-5.6-luna`)을 인덱싱과 공유한다.
