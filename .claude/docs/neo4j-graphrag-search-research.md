# neo4j-graphrag-python의 Search(Retrieval) 처리 방식 조사

> 조사 대상: 프로젝트에 클론되어 있는 `neo4j-graphrag-python` v1.18.0
> 소스 경로: `neo4j-graphrag-python/src/neo4j_graphrag/`
> 목적: querying phase 설계를 위한 라이브러리의 검색(retrieval) 아키텍처 파악

---

## 1. 전체 구조 개요

이 라이브러리에서 "search"는 두 개의 계층으로 나뉜다.

| 계층 | 역할 | 핵심 클래스 |
| --- | --- | --- |
| **Retriever** | Neo4j에서 컨텍스트(레코드)를 **검색**만 수행 | `VectorRetriever`, `HybridRetriever`, `Text2CypherRetriever` 등 |
| **GraphRAG** | Retriever로 검색한 컨텍스트를 LLM 프롬프트에 넣어 **답변 생성** | `GraphRAG` |

즉, **Retriever = 검색 전용**, **GraphRAG = 검색 + 프롬프트 조립 + LLM 생성**의 오케스트레이터다.
querying phase는 이 두 계층 중 어디까지를 직접 구현/커스터마이즈할지 먼저 결정해야 한다.

```
사용자 질문
   │
   ▼
GraphRAG.search()  ──(1) retriever.search() 로 컨텍스트 검색
   │                ──(2) 프롬프트 템플릿에 context 주입
   │                ──(3) LLM.invoke() 로 답변 생성
   ▼
RagResultModel { answer, retriever_result? }
```

---

## 2. Retriever 계층: 공통 아키텍처

파일: `retrievers/base.py`

### 2.1 핵심 계약 (Template Method 패턴)

모든 retriever는 추상 클래스 `Retriever`(base.py:101)를 상속하며, 딱 하나의 메서드만 구현하면 된다.

- **`get_search_results(*args, **kwargs) -> RawSearchResult`** (base.py:166, `@abstractmethod`)
  - 각 하위 클래스가 **반드시 구현**. 실제 Cypher 실행 로직이 여기 들어간다.
  - 반환값: `RawSearchResult` = `list[neo4j.Record]` + 선택적 `metadata` dict.

- **`search(*args, **kwargs) -> RetrieverResult`** (base.py:151)
  - 사용자가 실제로 호출하는 **공개 인터페이스**. base가 제공하며 보통 오버라이드하지 않는다.
  - 내부 동작: `get_search_results()` 호출 → 각 record를 formatter로 변환 → `RetrieverResult`로 감싸서 반환.
  - `metadata["__retriever"]`에 클래스명을 자동 기록한다.

```python
# base.py:151-164 (search 메서드의 핵심)
raw_result = self.get_search_results(*args, **kwargs)   # 하위 클래스 구현 호출
formatter = self.get_result_formatter()                 # record -> item 변환 함수
search_items = [formatter(record) for record in raw_result.records]
return RetrieverResult(items=search_items, metadata=metadata)
```

> **설계 함의**: 새로운 검색 방식을 만들려면 `Retriever`를 상속하고 `get_search_results`만 구현하면 된다. `search()`의 포맷팅/메타데이터 파이프라인은 자동으로 재사용된다.

### 2.2 결과 포맷팅 (record → LLM에 넣을 텍스트)

- `get_result_formatter()` (base.py:183): 인스턴스에 `result_formatter`가 지정돼 있으면 그걸, 없으면 `default_record_formatter`를 사용.
- `default_record_formatter` (base.py:191): `RetrieverResultItem(content=str(record), metadata=...)` — 노드를 그냥 문자열화.
- **각 retriever가 이 formatter를 오버라이드**해서 자기만의 텍스트 표현을 만든다. 예를 들어 `VectorRetriever.default_record_formatter`(vector.py:140)는 `content=str(node)`, `metadata={score, nodeLabels, id}`로 구성.
- 생성자에 `result_formatter` 콜백을 넘기면 record → item 변환을 **완전히 커스터마이즈**할 수 있다. (querying phase에서 노드 텍스트 표현을 우리 스키마에 맞게 다듬을 지점)

### 2.3 결과 타입 (types.py)

```python
RawSearchResult  # get_search_results가 반환. 내부용.
  records:  list[neo4j.Record]
  metadata: Optional[dict]

RetrieverResultItem  # search가 반환하는 아이템. LLM에 들어갈 단위.
  content:  Any        # LLM 컨텍스트로 제공될 텍스트
  metadata: Optional[dict]  # score, 노드 프로퍼티 등 부가 정보

RetrieverResult  # search의 최종 반환
  items:    list[RetrieverResultItem]
  metadata: Optional[dict]  # 예: Text2Cypher가 생성한 Cypher 쿼리
```

### 2.4 초기화 시 공통 동작

- 생성자에서 Neo4j 버전을 검증(`VERIFY_NEO4J_VERSION`). 벡터 인덱스 지원·메타데이터 필터링 지원 여부를 확인하고, 안 되면 `Neo4jVersionError`.
- `_fetch_index_infos()` (base.py:122): 벡터 인덱스 이름으로 `SHOW VECTOR INDEXES`를 실행해 **노드 라벨, 임베딩 프로퍼티, 차원, filterable_properties**를 자동으로 알아낸다. → 사용자가 이 정보를 직접 넘길 필요 없음.

---

## 3. 내장 Retriever 종류

`retrievers/__init__.py` 기준 공개되는 retriever:

| Retriever | 검색 방식 | 임베딩 필요 | 특징 |
| --- | --- | --- | --- |
| **VectorRetriever** | 벡터 유사도 | O (또는 query_vector 직접 전달) | 가장 기본. 벡터 인덱스만 사용 |
| **VectorCypherRetriever** | 벡터 유사도 + Cypher 그래프 탐색 | O | 벡터로 진입점 노드를 찾고 **retrieval_query로 그래프를 추가 순회** |
| **HybridRetriever** | 벡터 + 풀텍스트 | O | 벡터 인덱스 + 풀텍스트 인덱스 결과를 결합 |
| **HybridCypherRetriever** | 벡터 + 풀텍스트 + Cypher | O | Hybrid에 그래프 탐색을 추가 |
| **Text2CypherRetriever** | 자연어 → Cypher 생성 | X | LLM이 스키마 기반으로 Cypher를 만들어 실행 |
| **ToolsRetriever** | LLM이 여러 retriever 중 선택 | 경우에 따라 | 위 retriever들을 tool로 등록하고 LLM이 라우팅 |
| (외부) Pinecone/Weaviate/Qdrant | 외부 벡터DB + Neo4j | O | 임베딩은 외부 벡터DB, 노드 조회는 Neo4j |

### 3.1 VectorRetriever

파일: `retrievers/vector.py:57`

- **입력**: `query_text` **또는** `query_vector` 중 하나(둘 다 주면 검증 에러), `top_k=5`, `effective_search_ratio=1`, `filters`.
- `query_text`를 주면 생성자에 넘긴 `embedder.embed_query()`로 벡터화. 임베더 없이 text를 주면 `EmbeddingRequiredError`.
- **실행 방식 2가지** (Neo4j 버전에 따라 자동 분기):
  1. **SEARCH clause** (신버전, `supports_search_clause`): 인덱스 내 필터링(in-index filtering) 가능. 성능 유리.
  2. **procedure 기반** (`db.index.vector.queryNodes`): 구버전 또는 SEARCH clause 비호환 필터일 때 fallback.
- 필터가 인덱스의 `filterable_properties`에 없으면 자동으로 procedure 방식으로 fallback (경고 로그 출력). PropertyNotFound 에러가 나도 재시도 fallback.
- 반환 record: `node`, `score`, `nodeLabels`, `id`.

핵심 Cypher (neo4j_queries.py):
```cypher
CALL db.index.vector.queryNodes($vector_index_name, $top_k * $effective_search_ratio, $query_vector)
YIELD node, score
WITH node, score LIMIT $top_k
```
> `effective_search_ratio`는 후보 풀 크기 배수. 값을 키우면 더 많은 후보를 훑어 정확도↑, 성능↓.

### 3.2 VectorCypherRetriever (그래프 탐색이 핵심)

파일: `retrievers/vector.py:319`

- VectorRetriever와 동일하게 벡터로 진입점 `node`를 찾은 뒤, **생성자에 넘긴 `retrieval_query`(Cypher)를 이어붙여 그래프를 추가로 순회**한다.
- `retrieval_query` 안에서 `node` 변수를 그대로 참조 가능.
- `query_params`로 Cypher에 추가 파라미터 주입 가능.

```python
retrieval_query = "MATCH (node)-[:AUTHORED_BY]->(author:Author) RETURN author.name"
retriever = VectorCypherRetriever(driver, "vector-index-name", retrieval_query, embedder)
retriever.search(query_text="Find me a book about Fremen", top_k=5)
```

> **querying phase에서 가장 중요한 retriever일 가능성이 높다.** KG의 관계를 타고 들어가 컨텍스트를 확장하는 GraphRAG의 본질이 여기 있다. 벡터로 "어디서 시작할지"를 정하고, Cypher로 "무엇을 더 가져올지"를 정의한다.

### 3.3 HybridRetriever / HybridCypherRetriever

파일: `retrievers/hybrid.py`

- **벡터 인덱스 + 풀텍스트 인덱스** 두 개를 함께 사용 (`vector_index_name`, `fulltext_index_name`).
- `query_text`는 벡터 검색용으로 임베딩되고, 동시에 풀텍스트(Lucene) 검색에도 사용된다. `query_vector`를 따로 주면 벡터 검색은 그걸 우선 사용.
- **Ranker (결과 결합 방식)** — `HybridSearchRanker` enum (types.py:143):
  - `NAIVE` (기본): 벡터·풀텍스트 점수를 단순 정규화 결합.
  - `LINEAR`: `alpha` 가중치로 선형 결합. **`alpha` 필수(0~1)**, 벡터 점수 × alpha + 풀텍스트 점수 × (1-alpha).
- 풀텍스트 쿼리가 잘못되면(Lucene ParseException) `SearchQueryParseError`.
- `HybridCypherRetriever`는 여기에 `retrieval_query` 그래프 탐색을 추가.

핵심 Cypher:
```cypher
CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $top_k})
YIELD node, score
```

> **함의**: 고유명사·정확한 용어 매칭이 중요한 도메인(인물명, 지명, 고유 설정 용어 등)에서는 벡터만으로는 놓치는 걸 풀텍스트가 잡아준다. 웹소설 로어 도메인에 적합할 수 있음.

### 3.4 Text2CypherRetriever (LLM이 Cypher 생성)

파일: `retrievers/text2cypher.py:95`

- 임베딩/벡터 인덱스 **불필요**. 대신 **LLM**이 필요.
- 동작 흐름:
  1. 생성 시 `get_schema(driver)`로 그래프 스키마를 자동 조회(또는 `neo4j_schema` 직접 지정).
  2. `search(query_text)` 시 스키마 + 예시(examples) + 질문을 프롬프트(`Text2CypherTemplate`)에 넣어 LLM에게 Cypher 생성 요청.
  3. `extract_cypher()`로 응답에서 Cypher 추출 (코드블록 제거, 공백 포함 식별자 백틱 처리).
  4. **`EXPLAIN`으로 먼저 쿼리 타입 검사** → **read-only가 아니면 실행 거부**(`Text2CypherRetrievalError`). 즉 쓰기/삭제 쿼리 방어.
  5. 검증 통과 시 실제 실행, `metadata["cypher"]`에 생성된 쿼리를 담아 반환.
- `examples`(입력/쿼리 쌍)를 주면 few-shot으로 정확도 향상. `custom_prompt`로 프롬프트 전체 교체 가능.

기본 프롬프트 (prompts.py):
```
Task: Generate a Cypher statement for querying a Neo4j graph database from a user input.
Schema: {schema}
Examples (optional): {examples}
Input: {query_text}
Do not use any properties or relationships not included in the schema.
...
Cypher query:
```

> **함의**: "특정 인물이 등장하는 모든 사건" 같은 **구조적/집계형 질의**에 강하다. 반면 벡터 검색은 "분위기가 비슷한 장면" 같은 **의미적 유사도**에 강하다. querying phase는 질문 유형에 따라 둘을 나눠 쓰거나 ToolsRetriever로 라우팅하는 설계를 고려할 수 있다.

### 3.5 ToolsRetriever (LLM 기반 라우팅)

파일: `retrievers/tools_retriever.py:28`

- 여러 retriever를 `convert_to_tool()`로 tool화한 뒤 리스트로 등록.
- `search(query_text)` 시 LLM이 `invoke_with_tools`로 **어떤 tool(들)을 쓸지 스스로 선택**하고 실행, 결과를 합쳐서 반환.
- 각 record에 어떤 tool이 생성했는지(`tool_name`) 귀속 정보를 붙인다.
- tool을 하나도 선택 안 하면 빈 결과. 예외는 삼켜서 metadata에 error로 담음.
- `convert_to_tool()`(base.py:410)은 `get_search_results`의 **시그니처를 자동 분석**해 tool 파라미터 스키마를 생성한다. (`top_k`는 minimum=1, `alpha`는 0~1 등 특수 처리)

> **함의**: 질문 유형별 라우팅을 LLM에 위임하는 상위 오케스트레이션 옵션. querying phase에서 "라우팅을 룰 기반으로 직접 짤지 / LLM tool-calling에 맡길지"의 선택지가 된다.

---

## 4. 메타데이터 필터링 (pre-filtering)

파일: `filters.py`

Vector 계열 retriever의 `filters` 인자는 MongoDB 스타일 연산자를 지원한다.

| 연산자 | 의미 | Cypher |
| --- | --- | --- |
| `$eq` / `$ne` | 같음/다름 | `=` / `<>` |
| `$lt` `$lte` `$gt` `$gte` | 비교 | `< <= > >=` |
| `$in` / `$nin` | 포함/미포함 | `IN` |
| `$between` | 범위 | (범위 조건) |
| `$like` / `$ilike` | 부분 문자열(대소문자) | `CONTAINS` |
| `$and` / `$or` | 논리 결합 | `AND` / `OR` |

예:
```python
filters = {"$and": [{"chapter": {"$gte": 10}}, {"character": {"$eq": "주인공"}}]}
retriever.search(query_text="...", filters=filters, top_k=5)
```

> 신버전 Neo4j에서는 SEARCH clause로 **인덱스 내 필터링**이 되어 빠르다. 단, 필터 대상 프로퍼티가 인덱스 생성 시 `filterable_properties`로 선언돼 있어야 한다. 아니면 procedure 방식 brute-force로 fallback (경고). → **indexing phase에서 어떤 프로퍼티를 filterable로 선언할지**가 querying 성능에 직결됨.

---

## 5. GraphRAG 계층: 검색 + 생성

파일: `generation/graphrag.py:44`

Retriever가 "검색"이라면 `GraphRAG`는 "검색 → 프롬프트 조립 → LLM 답변 생성"의 완성형 파이프라인이다.

```python
retriever = VectorRetriever(driver, "vector-index-name", embedder)
llm = OpenAILLM()
graph_rag = GraphRAG(retriever, llm)
graph_rag.search(query_text="Find me a book about Fremen")
```

### 5.1 `GraphRAG.search()` 3단계 (graphrag.py:93)

1. **Retrieval**: `retriever.search(query_text=query, **retriever_config)` 호출. `retriever_config`로 `top_k` 등을 그대로 전달.
2. **Augmentation**: 검색된 items의 `content`를 개행으로 합쳐 `context`를 만들고 `RagTemplate`에 주입.
   - 기본 템플릿(prompts.py:97): `Context: {context}\n Examples: {examples}\n Question: {query_text}\n Answer:`
   - 기본 시스템 지시: `"Answer the user question using the provided context."`
3. **Generation**: LLM `invoke()`로 답변 생성. LangChain chat model / LLMInterfaceV2 / 레거시 LLMInterface 모두 지원.

### 5.2 주요 옵션

- `retriever_config`: retriever.search로 전달되는 파라미터 (예: `{"top_k": 5}`).
- `return_context`: True면 결과에 `retriever_result`(검색 컨텍스트)도 포함. **주의: 현재 기본 False지만 향후 True로 바뀔 예정(DeprecationWarning)** → 명시적으로 넘기는 게 안전.
- `response_fallback`: 검색 결과가 비면 LLM 호출 없이 이 메시지를 반환.
- `message_history`: 대화 이력. 있으면 **먼저 이력을 요약(300단어 이내)**한 뒤 현재 질문과 합쳐 검색 쿼리를 만든다(graphrag.py:192 `_build_query`). → 멀티턴 대화 시 검색 품질을 위한 컨텍스트 압축.
- 반환: `RagResultModel { answer: str, retriever_result: Optional[RetrieverResult] }`.

> **함의**: querying phase를 GraphRAG로 끝까지 갈지, 아니면 Retriever까지만 쓰고 프롬프트/생성은 직접 제어할지 결정 필요. 멀티턴·fallback·컨텍스트 반환 같은 기능이 이미 제공되므로 재구현보다 활용을 검토할 것.

---

## 6. querying phase 설계에 참고할 핵심 포인트

1. **검색 방식 선택이 첫 결정.** 의미 유사도(Vector) / 정확 매칭 병행(Hybrid) / 구조적 질의(Text2Cypher) / 그래프 순회(VectorCypher) 중 도메인 질문 유형에 맞는 조합을 정한다.
2. **VectorCypherRetriever의 `retrieval_query`가 GraphRAG의 핵심 레버.** 벡터로 진입점을 찾고 KG 관계를 타고 컨텍스트를 확장하는 설계를 이 Cypher로 표현한다.
3. **`result_formatter` 콜백으로 노드 → LLM 텍스트 표현을 우리 스키마(자기서술 노드)에 맞게 커스터마이즈**할 수 있다.
4. **indexing phase와의 결합**: 벡터 인덱스명, 풀텍스트 인덱스명, `filterable_properties` 선언이 querying 성능·기능을 좌우한다.
5. **라우팅 전략**: 질문 유형 분기를 룰 기반으로 직접 짤지, `ToolsRetriever`로 LLM tool-calling에 맡길지.
6. **Text2Cypher는 read-only 강제·EXPLAIN 검증**이 내장돼 안전하지만, 스키마 프롬프트 품질과 few-shot examples에 정확도가 크게 의존한다.
7. **GraphRAG를 그대로 쓸지 / Retriever만 쓸지**: 멀티턴 요약·fallback·컨텍스트 반환 기능을 재활용할지 여부.

---

## 부록: 파일별 위치 참조

| 관심사 | 파일 |
| --- | --- |
| Retriever 추상 계약, search 파이프라인, tool 변환 | `retrievers/base.py` |
| Vector / VectorCypher | `retrievers/vector.py` |
| Hybrid / HybridCypher, Ranker | `retrievers/hybrid.py` |
| Text2Cypher, EXPLAIN 검증 | `retrievers/text2cypher.py` |
| ToolsRetriever (LLM 라우팅) | `retrievers/tools_retriever.py` |
| 외부 벡터DB 연동 | `retrievers/external/{pinecone,weaviate,qdrant}` |
| Cypher 쿼리 빌더 | `neo4j_queries.py` |
| 메타데이터 필터 연산자 | `filters.py` |
| 결과/검색 파라미터 타입 | `types.py` |
| GraphRAG 오케스트레이터 | `generation/graphrag.py` |
| RAG/Text2Cypher 프롬프트 | `generation/prompts.py` |
