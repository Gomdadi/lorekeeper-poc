# Indexing Phase 검증 계획 — 컴포넌트 스왑 harness

## Context

LoreKeeper는 웹소설의 설정·연속성 충돌을 탐지하는 KG 서비스다. 현재 PoC(`poc/`)는 매우 얇은 베이스라인만 있다: `client.py`(neo4j 드라이버), `schema.py`(Phase 1 확정 `SCHEMA` GraphSchema 객체), `seed_example.py`(수동 Cypher seed+검증). **추출 파이프라인은 아직 없다** — `main.py`는 `uv init` 스텁이고, `schema.py`의 `SCHEMA`는 아직 어디에서도 import되지 않는다.

이 계획의 목표는 **Indexing(텍스트→KG 추출) 파이프라인 베이스라인을 실제로 조립**하고, **각 컴포넌트 후보를 하나씩 바꿔가며(OFAT) 동일 입력 텍스트에 대한 추출 결과를 비교**하는 검증 harness를 만드는 것이다. 정답 라벨은 만들지 않는다 — 대신 **구조지표(노드/관계 수, PruningStats, resolver 병합 수)를 자동 집계**하고, **각 변형의 그래프를 단일 DB에서 순차 실행 후 `poc/output/<variant>.cypher` 덤프로 보존**해 human judge가 재적재·비교·판정한다.

확정 사항:
- 추출 모델: **`gpt-5.4-mini`** (OpenAI), 스키마·그래프 추출 모두 **V2 structured output** 사용.
- 임베딩: chunk embedding·커스텀 resolver 유사도 모두 `text-embedding-3-small`로 통일(짧은 이름 문자열에서 3-large 이득이 작고, PoC 부품/저장 단순화. retrieval 붙일 때 임베딩 정책 일괄 재검토).
- 반복 전달되는 프롬프트(스키마·시스템 프롬프트·few-shot)는 **OpenAI 자동 프롬프트 캐싱** + `prompt_cache_key`로 라우팅 안정화.
- 도메인은 무협 한정이 아니라 **웹소설 전반**으로 열어 few-shot 설계.

## 실행 전 확인해야 할 전제 (blocker 후보)

1. **Neo4j 실행 방식 — 단일 DB reset + Cypher 덤프 (확정)** — `docker-compose.yml`이 `neo4j:5.26-community`(**Community 확정**)를 띄움. 변형마다 같은 DB를 `MATCH (n) DETACH DELETE n`로 리셋 후 실행하고, 각 변형 그래프를 `poc/output/<variant>.cypher`로 덤프해 저장한다. 동시 브라우징은 포기. 덤프 후 다음 변형을 위해 다시 리셋.
2. **APOC — 이미 설치됨** — `docker-compose.yml`이 `NEO4J_PLUGINS:'["apoc"]'`로 APOC Core 자동 설치 + `apoc.export.file.enabled`/`apoc.import.file.enabled=true` + `dbms.security.procedures.unrestricted=apoc.*`까지 세팅됨. `apoc.refactor.mergeNodes`(resolver)·`apoc.export.cypher.all`(덤프) 모두 사용 가능. **덤프 경로 — 방식 (a) 확정:** `apoc.export.cypher.all`은 컨테이너 내부 import 디렉토리(`/var/lib/neo4j/import`)에 파일을 쓴다. compose에 `./poc/output:/var/lib/neo4j/import` 볼륨을 추가해 이 경로를 host `poc/output/`에 매핑 → `apoc.export.cypher.all('<variant>.cypher', ...)`가 쓴 파일이 host에 바로 떨어진다. (compose 수정 + `docker compose up -d` 재시작 필요.)
3. env: `OPENAI_API_KEY`, `NEO4J_*`는 이미 루트 `.env`에 존재. 단일 DB만 사용하므로 기본 DB(`NEO4J_DATABASE` 또는 `neo4j`) 하나만 사용.

## 아키텍처 — 커스텀 DAG (방법 A)

`SimpleKGPipeline` 대신 `Pipeline`을 직접 조립한다. 이유(소스 검증됨): (1) **few-shot `examples` 주입 불가** — `get_run_params`가 extractor에 `examples`를 안 넘김(`simple_kg_builder.py:413-450`), 우회하려면 `prompt_template` 전체를 override해야 함. (2) **resolver 교체 불가** — `_get_resolver()`가 `SinglePropertyExactMatchResolver`로 하드코딩(`:317-323`), 우리 `OpenAIEmbeddingResolver`로 스왑 불가. harness의 resolver 비교가 원천 차단됨. (참고: splitter는 `text_splitter`로 교체 가능하나, 변형 레지스트리 편의상 직접 조립이 나음. 스키마 추적(방법 A)·`additional_properties`·pruner 동작은 SimpleKGPipeline으로도 되므로 커스텀 근거 아님 — pruner 출력 `PruningStats`를 지표로 읽는 편의만 커스텀이 유리.)

```
splitter ──chunks──▶ embedder ──chunks──▶ extractor ──graph──▶ pruner ──graph──▶ writer ──(순서만)──▶ resolver
                                            ▲                     ▲
schema(SchemaBuilder) ──────────────────────┴─────────────────────┘
(resolver는 데이터 입력이 없음 → writer→resolver를 빈 input_config{}로 연결해 '실행 순서만' 강제. DAG에 포함되며 단일 pipe.run()으로 끝. SimpleKGPipeline과 동일 방식 — simple_kg_builder.py:402-409.)
```

배선 (검증된 input param → output field):
```python
pipe.connect("splitter",  "embedder",  {"text_chunks": "splitter"})   # TextChunks 전체
pipe.connect("embedder",  "extractor", {"chunks": "embedder"})
pipe.connect("schema",    "extractor", {"schema": "schema"})           # GraphSchema 전체
pipe.connect("schema",    "pruner",    {"schema": "schema"})           # pruner도 schema 필요(안 주면 무필터 통과)
pipe.connect("extractor", "pruner",    {"graph": "extractor"})
pipe.connect("pruner",    "writer",    {"graph": "pruner.graph"})
pipe.connect("writer",    "resolver",  {})                            # 빈 config = 데이터 없이 순서만 강제(writer 완료 후 실행)
```
run 데이터: `{"splitter": {"text": TEXT}, "schema": {node_types, relationship_types, patterns}, "extractor": {"examples": EXTRACTION_FEW_SHOT}}`

> **PruningStats는 pruner 산출물(`GraphPruningResult.pruning_stats`)**이므로 pruner를 반드시 DAG에 포함한다. harness는 pipeline 결과에서 이를 읽어 지표로 집계.

## 컴포넌트 후보 그리드 (OFAT — 한 번에 한 컴포넌트만 변경)

베이스라인: **FixedSizeSplitter + SchemaBuilder(additional_properties=False) + SinglePropertyExactMatchResolver**

(각 variant는 순차 실행되며 같은 DB를 리셋하고 결과를 `poc/output/<variant>.cypher`로 덤프)

| variant (덤프 파일명) | Splitter | Resolver | 비고 |
|---|---|---|---|
| `v_baseline` | FixedSizeSplitter | ExactMatch | 기준점 |
| `v_recursive` | RecursiveCharacterTextSplitter | ExactMatch | splitter 변경 |
| `v_kiwi` | KiwiSentenceSplitter(커스텀) | ExactMatch | splitter 변경 |
| `v_kss` | KSSSentenceSplitter(커스텀) | ExactMatch | splitter 변경 |
| `v_resolver_embed` | FixedSizeSplitter | OpenAIEmbeddingResolver(커스텀) | resolver 변경(추가 컴포넌트) |

- 스키마 컴포넌트는 그리드에서 변경하지 않음. **human이 그리드에서 best-fit을 고른 뒤(Phase B)**, 그 구성에서 `SchemaBuilder`→`SchemaFromTextExtractor`만 교체해 단일 실험(`v_schema_fromtext`) 실행 → LLM 추출 스키마 vs 설계 `SCHEMA` 비교.

## 파일별 변경 계획

### 신규
- **`poc/src/splitters.py`** — `KiwiSentenceSplitter(TextSplitter)`, `KSSSentenceSplitter(TextSplitter)`. 각각 `async def run(self, text: str) -> TextChunks` 구현: 문장 분리 후(kiwipiepy `Kiwi().split_into_sents` / `kss.split_sentences`) 목표 크기까지 문장을 묶어 `TextChunk(text=..., index=i)` 리스트 생성. `LangChainTextSplitterAdapter`(라이브러리 제공)로 `RecursiveCharacterTextSplitter`를 감싸는 헬퍼도 여기 배치. **`ChapterTaggingSplitter(TextSplitter)` 래퍼 추가**: 원문을 `【N화】` 마커 기준으로 화 단위 선분할 → 각 화 텍스트를 내부 splitter(모든 변형의 splitter를 이걸로 감쌈)로 자름 → 모든 청크 앞에 해당 화 마커를 prefix하고 전체 index 재부여. 마커 없는 중간 청크가 `Event.chapter`/`story_order`를 못 채우는 공백을 막고(문서 §4c 청크별 주입 규약과 일치), 청크가 화 경계를 넘지 않게 되며, 모든 변형에 동일 적용되어 OFAT 공정성 유지.
- **`poc/src/resolver.py`** — `OpenAIEmbeddingResolver(BasePropertySimilarityResolver)`. `compute_similarity(text_a, text_b) -> float`만 오버라이드(동기 메서드). **주의: `run()`이 모든 쌍에 대해 호출** → 블로킹 방지 위해 먼저 등장 이름을 배치로 `OpenAIEmbeddings(model="text-embedding-3-small").embed_query`로 임베딩해 dict 캐시, `compute_similarity`는 캐시 조회 후 코사인만. `similarity_threshold=0.85`(보수적), `resolve_properties=["name"]`.
- **`poc/src/extraction_examples.py`** — `EXTRACTION_FEW_SHOT` 문자열. **웹소설 전반**을 커버하는 2~3개 예시(예: 현대 회귀/판타지, 무협, 로판) 각각 Character/Location/Event/CharacterState 추출, 상태변화 시 **새 CharacterState 노드 생성** + `HAS_STATE`+`ESTABLISHED_IN`, `story_order` 채우기, chunk-local 임시 ID를 시연. 참고 원본은 `.claude/docs/kg-extraction-pipeline.md` §4c의 무협 few-shot.
- **`poc/src/pipeline.py`** — `build_pipeline(splitter, embedder, extractor, writer)` 로 위 DAG 조립(방법 A). `OpenAILLM(model_name="gpt-5.4-mini", model_params={"prompt_cache_key":"lorekeeper-extract"})` 생성 헬퍼 포함(그 외 샘플링·추론 파라미터는 전달하지 않고 모델 기본값 사용). extractor는 `LLMEntityRelationExtractor(llm, use_structured_output=True)`.
- **`poc/src/smoke.py`** — DB 없이 API만 검증하는 스모크(harness 전체를 돌리기 전 모델 호환 확정). [A] 실제 extractor 경로로 V2 structured output·파라미터 호환 확인(예시 청크 1~2개, `create_lexical_graph=False`), [B] openai 클라이언트 직접 호출로 `cached_tokens` 관찰(래퍼 `LLMResponse`가 cached_tokens를 노출하지 않기 때문).
- **`poc/src/indexing_eval.py`** — harness. 변형 레지스트리(위 그리드) 정의, 각 변형마다: 단일 DB 리셋(`MATCH (n) DETACH DELETE n`) → 해당 컴포넌트로 pipeline 조립(resolver 포함, `writer→resolver`는 빈 config) → `Neo4jWriter(driver, clean_db=True)` → `await pipe.run(data)`(splitter…writer→resolver 전체 DAG 실행) → PipelineResult에서 지표 수집(라벨별 노드 수, 타입별 관계 수, `pruning_stats`, resolver `ResolutionStats`, chunk 수) → `CALL apoc.export.cypher.all('<variant>.cypher', {format:'plain'})`로 덤프(볼륨 매핑 덕분에 host `poc/output/`에 저장됨) → 마크다운 비교표를 `poc/output/report.md`에 기록. (동시 브라우징 포기 — human은 판정할 변형의 덤프를 재적재해 확인.)

### 수정
- **`poc/src/schema.py`** — 각 `NodeType`/`RelationshipType`에 `additional_properties=False` 설정(베이스라인 "No additional_properties"). 이로써 GraphPruning의 `_filter_properties`가 스키마 미정의 속성을 제거하고 `pruning_stats.pruned_properties`에 `NOT_IN_SCHEMA`로 기록 → 지표로 확인. (참고: `SchemaBuilder.run`은 GraphSchema 레벨의 `additional_node_types` 등은 인자로 못 받음 — 노드/관계 타입 레벨 strict 필터까지 원하면 방법 B로 완성된 `GraphSchema`를 주입해야 하나, 이번 베이스라인 요구는 속성 레벨이므로 `additional_properties=False`로 충족.)
- **`poc/main.py`** — `indexing_eval` 실행을 트리거하는 얇은 CLI 엔트리포인트. (선택 — `python -m src.indexing_eval` 직접 실행도 가능.)
- **`poc/pyproject.toml`** — deps 추가: `openai`(또는 `neo4j-graphrag[openai]`), `langchain-text-splitters`, `kiwipiepy`, `kss`, `numpy`. `uv add`로 반영.
- **`docker-compose.yml`** — neo4j 서비스 `volumes`에 `- ./poc/output:/var/lib/neo4j/import` 추가(APOC export 파일을 host `poc/output/`로 노출). 반영 후 `docker compose up -d`로 재시작 필요.

### 입력 규약
- `poc/data/input.txt` — 사용자가 직접 준비하는 단일 텍스트(모든 변형 공통). 챕터 경계에 `【N화】` 마커를 인라인으로 포함. `ChapterTaggingSplitter`가 이 마커로 화 단위 선분할 후 **각 청크에 마커를 prefix**해 extractor가 모든 청크에서 `Event.chapter`/`story_order`를 채울 수 있게 함. few-shot이 이 마커 읽는 법을 가르침.

## 프롬프트 캐싱

- OpenAI 캐싱은 1024토큰 이상 프리픽스에 **자동** 적용. 추출 프롬프트는 (시스템 지시 + 스키마 + few-shot)이 앞, 가변 chunk 텍스트가 뒤 → 프리픽스가 안정적이라 chunk마다 캐시 히트.
- `prompt_cache_key`는 **`OpenAILLM(model_params={...})`에 넣어** 전달 — `model_params`는 V1(문자열)·V2(structured output, 메시지) 어느 호출 경로든 `create()`에 스프레드되므로 생성자 경유가 확실. 히트 여부는 응답의 `usage.prompt_tokens_details.cached_tokens`로 검증.

## 명시적 제외 (이번 범위 아님)

- **인물 명칭 사전 prefix 주입(레지스트리)** — 베이스라인 검증·판정 후에 붙일지 결정(이전 논의대로 지연).
- **정답 쿼리셋 자동 채점** — human judge로 대체하므로 미구현.
- **retcon 필드**(`retconned`, `retcon_note`) — Phase 1에서 이미 지연됨.

## 검증 방법 (end-to-end)

1. 전제 확인: compose 볼륨 반영 후(`docker compose up -d`) `RETURN apoc.version()`로 APOC 확인, `CALL apoc.export.cypher.all('smoke.cypher',{format:'plain'})`가 host `poc/output/smoke.cypher`로 떨어지는지 확인.
2. `poc/data/input.txt` 준비(사용자).
3. `cd poc && uv sync`로 의존성 설치 → **먼저 `uv run python src/smoke.py`로 gpt-5.4-mini 호환(V2 structured output·파라미터·cached_tokens)을 확정** → 초록불이면 `uv run python main.py`(또는 `uv run python src/indexing_eval.py`)로 전체 sweep 실행.
4. **자동 지표**: `poc/output/report.md`의 변형별 비교표 확인 — 베이스라인이 비어있지 않은 그래프를 만들고, `pruned_properties`가 실제로 기록되며(스키마 strict 동작 증명), `v_resolver_embed`에서 exact-match 대비 병합 수 차이가 나는지.
5. **human judge**: 판정할 변형의 덤프(`poc/output/v_baseline.cypher` 등)를 DB 리셋 후 재적재해 Neo4j Browser에서 하나씩 열어 노드 분열·상태변화 추출·이름 통합 품질을 육안 비교 → best-fit 선정.
6. **Phase B**: best-fit 구성에서 스키마만 `SchemaFromTextExtractor`로 교체해 `v_schema_fromtext` 실행, LLM 추출 스키마와 설계 `SCHEMA` 비교.
