# 텍스트 → Knowledge Graph 추출 파이프라인 & Entity Resolution 정리

> 소스: `neo4j-graphrag-python/src/neo4j_graphrag/experimental/`
> 버전: 1.18.0 (레포에 소스 직접 포함, `neo4j-graphrag-python.md` 참고)
> 목적: `SimpleKGPipeline` 내부에서 텍스트가 실제로 어떤 컴포넌트·코드를 거쳐 그래프가 되는지, 그리고 Entity Resolution(중복 해소)이 정확히 어떤 로직으로 동작하는지 **코드 라인 단위**로 파악한다. Phase 4(Indexing 품질 검증) 구현 시 그대로 참고 가능하도록 작성.

---

## 1. 전체 파이프라인 DAG (Directed Acyclic Graph - 방향성 비순환 그래프)

`SimpleKGPipeline`은 내부적으로 `Pipeline`(범용 DAG 실행 엔진)에 8개 컴포넌트를 연결해서 만든 것에 불과하다. 연결 관계는 `SimpleKGPipelineConfig._get_connections()`에 하드코딩되어 있다(`experimental/pipeline/config/template_pipeline/simple_kg_builder.py:325-411`).

```
file_loader ──text──▶ splitter ──chunks──▶ chunk_embedder ──chunks(+embedding)──▶ extractor
   │                                                                                  ▲
   └──document_info────────────────────────────────────────────────────────────────┘
(schema 컴포넌트: 사용자가 schema를 안 주면 file_loader.text를 받아 LLM으로 자동 추출)
schema ──schema──▶ extractor

extractor ──graph──▶ pruner ──graph(정리됨)──▶ writer ──(그래프 DB 반영 완료)──▶ resolver
```

- `from_file=True`(기본값)이면 `file_loader → splitter` 연결이 추가되고, `from_file=False`(텍스트 직접 입력)면 `text`가 `splitter`에 바로 들어간다.
- `perform_entity_resolution=True`(기본값)일 때만 `writer → resolver` 연결이 추가된다. resolver는 **DB에 이미 쓰여진 데이터**를 대상으로 동작하므로 `graph` 객체가 아니라 `writer`의 완료 시점에 의존한다(입력 파라미터 없음, `input_config={}`).
- 스키마가 없으면(`has_user_provided_schema()`가 False) `SchemaFromTextExtractor`가 `schema` 자리를 대신하고, `file_loader.text`를 입력받아 LLM으로 스키마를 자동 추출한다.

각 컴포넌트는 `Component`(`experimental/pipeline/component.py`)를 상속하고 `async def run(...) -> DataModel` 하나만 구현하면 되는 매우 얇은 추상화다. `Pipeline`은 이 DAG를 위상정렬해서 `asyncio`로 실행하고, 각 컴포넌트의 `run()` 반환값을 다음 컴포넌트의 입력으로 라우팅한다(`input_config` 매핑을 따라). 커스텀 파이프라인을 짤 때도 이 구조(컴포넌트 = 순수 함수 + Pydantic 입출력)만 지키면 된다.

---

## 2. 컴포넌트별 상세

### 2.1 TextSplitter — `FixedSizeSplitter` (`components/text_splitters/fixed_size_splitter.py`)

가장 단순한 청킹 전략: 고정 길이(`chunk_size`, 기본 4000자)로 자르고 `chunk_overlap`(기본 200자)만큼 겹치게 한다.

핵심 로직(`run()`, L106-159):
- `step = chunk_size - chunk_overlap` 만큼씩 `approximate_start`를 전진시키며 반복.
- `approximate=True`(기본값)면 단어 중간에서 잘리지 않도록 `_adjust_chunk_start`/`_adjust_chunk_end`로 앞뒤 공백까지 경계를 밀어준다. 공백을 못 찾으면(예: 매우 긴 URL) 원래 위치로 되돌아간다(fallback).
- 반환값은 `TextChunks(chunks=[TextChunk(text=..., index=...), ...])`. `TextChunk.uid`는 `uuid4()`로 자동 생성되며, 이후 모든 노드 ID의 prefix로 쓰인다(§2.3 참고).
- **주의**: 문장/문단 경계가 아니라 순수 글자 수 기준이라, 한 문장이 청크 경계에서 잘릴 수 있다. LangChain/LlamaIndex 어댑터(`text_splitters/langchain.py`, `llamaindex.py`)로 교체하면 더 정교한 분할(문장 단위 등)이 가능하다.

LoreKeeper 적용 시 고려사항: 웹소설 원고는 회차 단위 텍스트라 청크 경계가 `Event`/문단 경계와 어긋나면 LLM이 문맥을 놓칠 수 있음 — Phase 4에서 chunk_size를 회차 평균 길이나 문단 단위로 조정하는 실험이 필요할 수 있다.

#### 2.1.1 대안 스플리터 정리 (LangChain / LlamaIndex / Kiwi / KSS)

기본 `FixedSizeSplitter`는 **글자 수로만** 잘라 문장·대사가 청크 경계에서 끊긴다. 이를 개선하는 대안들을 정리한다.

**전제 — 라이브러리에 있는 건 "어댑터"뿐**: `text_splitters/langchain.py`·`llamaindex.py`는 실제 분할 로직이 아니라 **외부 스플리터를 파이프라인에 꽂는 15줄짜리 어댑터**다(`run()`이 `self.text_splitter.split_text(text)`를 호출해 `TextChunks`로 감싸는 게 전부). 실제 알고리즘은 외부 패키지 안에 있고, **repo에 구현이 들어있는 스플리터는 `FixedSizeSplitter` 하나뿐**이다. 나머지는 별도 `pip install` 필요(현재 `poc/.venv`에 미설치).

**모델 필요 여부**: 스플리터는 원칙적으로 **LLM을 부르지 않는다.** 문장 경계는 LLM이 아니라 정규식(단순) 또는 작은 통계 토크나이저(NLTK Punkt, Kiwi/KSS 형태소 통계 등)로 로컬에서 즉시 찾는다. 유일한 예외는 LlamaIndex `SemanticSplitterNodeParser`로, 이것만 **임베딩 모델**(생성 LLM 아님, API or 로컬)이 필요하다.

| 옵션 | 분할 기준 | 한국어 적합성 | 모델 필요 | 설치 |
|---|---|---|---|---|
| `FixedSizeSplitter` (기본) | 순수 글자 수 | △ 문장 잘림 | 없음 | 내장 |
| **LangChain `RecursiveCharacterTextSplitter`** | 구분자 우선순위(`\n\n`→`\n`→공백→글자) | ✅ **언어 무관, 문제없음** | 없음 | `langchain-text-splitters` |
| LangChain `TokenTextSplitter` | 토큰 수(tiktoken) | ⚠️ 경계는 무해하나 한국어 토큰 부풀려짐 | 없음(인코딩 테이블) | `langchain-text-splitters` |
| **LlamaIndex `SentenceSplitter`** | 문장 경계(기본 NLTK Punkt) | ⚠️ **기본값은 영어용이라 부적절** | 없음 | `llama-index-core` |
| LlamaIndex `SemanticSplitterNodeParser` | 임베딩 유사도 급변 지점 | △ 임베딩 품질·비용에 의존 | **임베딩 모델 O** | `llama-index-core` |
| **Kiwi** (`kiwipiepy`) | 한국어 형태소 기반 문장 분리 | ✅✅ **한국어 정밀, 빠름** | 없음(형태소 통계) | `kiwipiepy` |
| **KSS** (Korean Sentence Splitter) | 한국어 문장 분리 전용(대사·인용 특화) | ✅✅ **한국어 정밀** | 없음 | `kss` |

**핵심 구분**: LangChain/LlamaIndex를 통째로 못 쓰는 게 아니다. **구분자·글자·토큰 기반(RecursiveCharacter 등)은 문장 감지를 안 하므로 한국어에도 잘 맞는다.** 오직 **영어용 문장 감지에 의존하는 `SentenceSplitter` 기본 설정만** 한국어에 부적절하다.

**LoreKeeper 권장 우선순위**:
1. **LangChain `RecursiveCharacterTextSplitter`** — 문단·줄 경계 존중, 언어 무관, 추가 한국어 의존성 없음. `separators`에 `". "`, `"! "`, `"? "`, `'." '` 등 한국어 문장부호를 넣으면 문장 경계에 근접. `FixedSizeSplitter`보다 명확히 나은 현실적 1순위.
2. **Kiwi / KSS 커스텀 스플리터** — 진짜 문장 정밀도가 필요할 때. 어댑터가 인터페이스를 안 맞으므로 `TextSplitter`를 직접 상속해 구현하는 게 깔끔하다(base가 `run()` 하나뿐).
3. **비추천** — LlamaIndex `SentenceSplitter` 기본값(영어 문장 감지). 쓰려면 `chunking_tokenizer_fn`에 한국어 분리 함수를 주입해야 함.

Kiwi 기반 커스텀 스플리터 예시(문장으로 쪼갠 뒤 목표 크기까지 이어붙여 문장 중간을 안 자름):

```python
from kiwipiepy import Kiwi
from neo4j_graphrag.experimental.components.text_splitters.base import TextSplitter
from neo4j_graphrag.experimental.components.types import TextChunk, TextChunks

class KiwiSentenceSplitter(TextSplitter):
    def __init__(self, chunk_size: int = 1500, chunk_overlap: int = 100):
        self.kiwi = Kiwi()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    async def run(self, text: str) -> TextChunks:
        sentences = [s.text for s in self.kiwi.split_into_sents(text)]  # 로컬·즉시, LLM 아님
        chunks, buf = [], ""
        for sent in sentences:
            if buf and len(buf) + len(sent) > self.chunk_size:
                chunks.append(buf)
                buf = buf[-self.chunk_overlap:] + sent if self.chunk_overlap else sent
            else:
                buf = (buf + " " + sent).strip() if buf else sent
        if buf:
            chunks.append(buf)
        return TextChunks(chunks=[TextChunk(text=c, index=i) for i, c in enumerate(chunks)])
        # KSS를 쓰려면 split_into_sents 부분만 kss.split_sentences(text)로 교체
```

### 2.2 TextChunkEmbedder (`components/embedder.py`)

각 청크의 `text`를 임베딩해서 `TextChunk.metadata["embedding"]`에 넣는 게 전부다(L49-91). `asyncio.Semaphore(max_concurrency=5)`로 동시 요청 수를 제한한다. 이 임베딩은 나중에 `LexicalGraphBuilder.create_chunk_node()`에서 `Chunk` 노드의 `embedding_properties`로 옮겨진다(§2.4).

### 2.2b Schema 컴포넌트 — 수동(`SchemaBuilder`) vs 자동(`SchemaFromTextExtractor`) (`components/schema.py`)

`extractor`와 `pruner`가 공통으로 소비하는 `GraphSchema`를 **누가 만드느냐**를 결정하는 단계다. `SimpleKGPipelineConfig._get_schema()`(`simple_kg_builder.py:251-262`)가 분기한다:

- 사용자가 스키마를 하나라도 넘겼으면(`has_user_provided_schema()`가 True) → **`SchemaBuilder`** 사용.
- 아무것도 안 넘겼으면 → **`SchemaFromTextExtractor`** 사용 (LLM이 원문에서 스키마 자체를 추론). 이때 파이프라인에 `file_loader → schema` (또는 텍스트 입력 시 `run_params["schema"]["text"]`) 연결이 추가된다(`simple_kg_builder.py:337-344, 439-441`).

또한 `schema="FREE"` 문자열을 주면 빈 스키마(`GraphSchema.create_empty()`)로 취급되어 **아무 제약 없이 자유 추출**되고, `schema="EXTRACTED"`를 주면 자동 추출 모드로 강제된다(`simple_kg_builder.py:172-179`).

#### (1) 수동 스키마 — `SchemaBuilder` (L1093-1212)

거의 아무 일도 안 한다. `create_schema_model()`(L1152-1184)이 사용자가 준 `node_types`/`relationship_types`/`patterns`/`constraints`를 그대로 `GraphSchema.model_validate(...)`에 넣어 Pydantic 검증만 통과시키고 반환한다. **즉 LoreKeeper처럼 스키마가 이미 확정돼 있으면 `SchemaBuilder`조차 거칠 필요 없이, 정적으로 만든 `GraphSchema` 인스턴스를 `extractor.run(schema=...)`에 바로 넘겨도 된다**(PoC의 `poc/src/schema.py` 방식). `create_schema_model`은 `@staticmethod`라 컴포넌트 인스턴스 없이도 호출 가능.

`GraphSchema` 자체의 구조(`NodeType`/`RelationshipType`/`PropertyType`/`Pattern`/`ConstraintType`)와 문자열→모델 자동 변환 규칙은 `schema-components.md`에 정리돼 있으므로 여기선 생략. 핵심만: `node_types`에 문자열 `"Character"`만 줘도 `{label, name:STRING 속성, additional_properties:True}`로 자동 확장된다(`NodeType.validate_input_if_string`, `schema.py:168-200`).

#### (2) 자동 스키마 추출 — `SchemaFromTextExtractor` (L1564-1866)

원문을 LLM에 넣어 **"이 텍스트에는 어떤 노드/관계/제약이 있을 법한가"** 하는 추상 스키마를 뽑는다. 추출 대상은 인스턴스가 아니라 **타입 정의**다(프롬프트 규칙 1: "Return only abstract schema information, not concrete instances").

- 프롬프트: `SchemaExtractionTemplate`(`generation/prompts.py:205-323`). `{text}`, `{examples}` 두 입력. LLM에게 11개 규칙을 강제한다 — PascalCase 노드 라벨, UPPER_SNAKE_CASE 관계 라벨, 확신 있는 속성만 정의, `patterns`의 모든 라벨은 반드시 `node_types`/`relationship_types`에 존재, `__` prefix/suffix 금지, 그리고 **UNIQUENESS / EXISTENCE / KEY 제약을 Neo4j Cypher 제약 규칙 그대로** 뽑도록 상세 지침(규칙 8~10)을 준다.
- V1(기본)/V2(structured output) 이중 모드는 extractor와 동일 패턴(`run()`, L1849-1866):
  - **V2** `_run_with_structured_output`(L1768-1815): `llm.ainvoke(messages, response_format=GraphSchemaExtractionOutput)`로 JSON Schema를 강제한 뒤 `GraphSchema.from_extraction_output(dto)`로 변환. OpenAI/VertexAI 전용.
  - **V1** `_run_with_prompt_based_extraction`(L1817-1847): 프롬프트로 JSON을 받아 후처리 파이프를 탄다 → `_clean_json_content`(markdown ```` ```json ```` 펜스 제거, L1663-1670) → `_parse_and_normalize_schema`(list로 오면 첫 dict 채택, L1690-1725) → `_apply_v1_filters`(라벨 없는 노드/관계 제거, 속성 0개 노드 제거 — `NodeType.properties`의 `min_length=1` 검증 에러 방지, L1727-1766) → `validate_extraction_dict_to_graph_schema`.
- 결과물 `GraphSchema`는 그대로 `extractor`(추출 가이드)와 `pruner`(정제 기준) 양쪽에 흘러간다.

**LoreKeeper 관점**: 우리는 스키마를 이미 Phase 1에서 확정했으므로 이 자동 추출 컴포넌트는 **런타임 경로에선 쓰지 않는다.** 다만 (a) 새 장르/신규 IP를 온보딩할 때 초안 스키마를 빠르게 뽑는 도구로, (b) "우리가 정의한 스키마가 실제 원문 분포와 얼마나 어긋나는지"를 대조하는 검증 도구로 활용 가치가 있다(자동 추출 결과 vs 수동 스키마 diff). 이 경우 `SchemaExtractionTemplate`의 한국어 few-shot `examples`를 채워 넣어야 품질이 나온다.

---

### 2.3 LLMEntityRelationExtractor — 핵심 추출 로직 (`components/entity_relation_extractor.py`)

**입력**: `TextChunks` + `GraphSchema` + few-shot `examples` 문자열.
**출력**: `Neo4jGraph`(전체 청크를 합친 노드/관계 목록).

#### (1) 청크 하나를 프롬프트로 변환 — `extract_for_chunk()` (L230-286)

`ERExtractionTemplate.format()`(`generation/prompts.py:196-202`)이 `{schema}`, `{examples}`, `{text}` 세 플레이스홀더를 채운 프롬프트를 만든다. 기본 템플릿(`ERExtractionTemplate.DEFAULT_TEMPLATE`, `prompts.py:163-193`)은 LLM에게:
- JSON 형식 명시: `{"nodes": [{"id": "0", "label": "Person", "properties": {...}}], "relationships": [{"type": "KNOWS", "start_node_id": "0", "end_node_id": "1", ...}]}`
- `schema.model_dump(exclude_none=True)`로 직렬화된 스키마를 "이 타입만 써라"는 제약으로 주입 (`GraphSchema`가 왜 LLM 프롬프트 가이드 역할만 하는지는 `schema-components.md` 참고)
- ID는 청크 내에서만 유일하면 되는 임시 문자열("0", "1"...)이라고 안내

두 가지 추출 모드가 있다(`use_structured_output` 플래그, 생성자 L203-228에서 LLM이 `supports_structured_output`을 지원하는지 검증):

| 모드 | 방식 | 코드 위치 |
|---|---|---|
| **V1 (기본)** | 프롬프트로 JSON 형식을 지시 → LLM 응답 문자열을 직접 파싱 | L263-286 |
| **V2 (구조화 출력)** | `llm.ainvoke(messages, response_format=Neo4jGraph)`로 OpenAI/VertexAI의 native structured output(JSON Schema 강제) 사용 | L240-261 |

V1 파싱 과정이 실무적으로 중요하다:
1. `fix_invalid_json()`(L104-112) → 내부적으로 `json_repair.repair_json()` 호출. LLM이 트레일링 콤마, 따옴표 누락 등 흔한 JSON 실수를 해도 복구를 시도한다. 복구 결과가 빈 문자열/`""`이면 `InvalidJSONError`.
2. `balance_curly_braces()`(L55-101)도 존재하지만 **현재 `extract_for_chunk`에서 직접 호출되지 않는다** — 과거엔 쓰였을 수 있는 유틸(중괄호 짝 안 맞는 것 보정)로, 스택 기반으로 문자열 리터럴 내부는 건드리지 않고 `{`/`}` 균형을 맞춘다. 필요 시 커스텀 후처리에 재사용 가능.
3. `json.loads()` → `Neo4jGraph.model_validate(result)`로 Pydantic 검증까지 통과해야 최종 `chunk_graph`가 된다.
4. 각 단계에서 실패하면 `on_error` 설정에 따라 분기:
   - `OnError.RAISE`(라이브러리 기본값이지만 `SimpleKGPipeline`은 `OnError.IGNORE`를 기본으로 씀) → `LLMGenerationError` 예외 발생, 파이프라인 중단.
   - `OnError.IGNORE` → 로그만 남기고 해당 청크는 빈 그래프(`Neo4jGraph()`)로 취급 — **즉 그 청크는 통째로 유실**된다. Phase 2(Indexing 검증 인프라)에서 "이 회차가 실제로 얼마나 유실됐는가"를 측정하려면 이 로그(`logger.error(f"... chunk_index={chunk.index}")`)를 수집해야 한다.

#### (2) 청크별 후처리 — `post_process_chunk()` / `update_ids()` (L142-158, L288-303)

LLM은 청크 안에서만 유일한 임시 ID("0", "1"...)를 준다. 여러 청크의 그래프를 하나로 합치면 ID가 충돌하므로, `update_ids()`가 모든 노드/관계의 ID 앞에 `chunk.chunk_id`(=`TextChunk.uid`)를 prefix로 붙인다: `"0"` → `"<chunk-uuid>:0"`. 이게 전역적으로 고유한 노드 ID가 되고, 이후 `Neo4jWriter`의 MERGE 키로 쓰인다.

`create_lexical_graph=True`(기본값)면 이어서 `LexicalGraphBuilder.process_chunk_extracted_entities()`를 호출해 추출된 각 엔티티 노드와 그 출처 `Chunk` 노드 사이에 `FROM_CHUNK` 관계를 만든다(§2.4).

#### (3) 동시 실행 & 병합 — `run()` (L337-390)

```python
sem = asyncio.Semaphore(self.max_concurrency)  # 기본 5
tasks = [self.run_for_chunk(sem, chunk, schema, examples, lexical_graph_builder) for chunk in chunks.chunks]
chunk_graphs = list(await asyncio.gather(*tasks))
graph = self.combine_chunk_graphs(lexical_graph, chunk_graphs)
```

청크마다 **독립적인 LLM 호출**이 병렬로(최대 `max_concurrency`개 동시) 나간다 — 즉 청크 간 문맥 공유가 없다. "3화에서 오른팔을 잃은 인물이 5화에서 다시 언급"되는 식의 **크로스 청크 추론은 LLM 추출 단계에서 발생하지 않는다.** 이는 Phase 4/6 설계에 중요한 함의다: 상태 변화 추적(`CharacterState`)이 제대로 되려면 청크 A에서 "오른팔 상실" 사실이 노드로 남고, 청크 B의 언급은 결국 Entity Resolution(§3)이나 이후 Cypher 조회로 연결해야 하지, 추출 단계 LLM이 두 청크를 동시에 보고 판단해주지 않는다.

`combine_chunk_graphs()`(L305-316)는 단순히 리스트를 이어붙이는 것(`extend`)이다 — 이 시점엔 아직 중복 제거가 전혀 없다. 같은 인물이 여러 청크에서 각각 새 노드로 추출된 채로 그대로 쌓인다. 이 중복을 없애는 게 바로 Entity Resolution(§3)의 역할이다.

### 2.4 LexicalGraphBuilder (`components/lexical_graph.py`)

원문 추적을 위한 "문서 골격" 그래프를 자동으로 만든다. LLM 추출과 무관하게 결정적으로(deterministic) 생성됨.

```
Document --[FROM_DOCUMENT]--> Chunk --[NEXT_CHUNK]--> Chunk --[NEXT_CHUNK]--> ...
                                 ▲
                              [FROM_CHUNK]
                                 │
                          (LLM이 추출한 엔티티 노드들)
```//주석: 실제 관계 방향은 Chunk→Document, Chunk→NextChunk, Entity→Chunk (아래 코드 참고)

- `create_document_node()`(L106-124): `Document` 노드 하나, `path`/`createdAt`/커스텀 metadata를 속성으로.
- `create_chunk_node()`(L126-150): `Chunk` 노드마다 `text`, `index` 속성 + (있으면) `embedding_properties`에 벡터 이동(§2.2에서 만든 `metadata["embedding"]`을 여기서 꺼내 씀, L140-143).
- 관계는 3종류, 모두 `Neo4jRelationship(start_node_id=..., end_node_id=..., type=...)`:
  - `chunk -[FROM_DOCUMENT]-> document` (`create_chunk_to_document_rel`, L152-162)
  - `chunk -[NEXT_CHUNK]-> next_chunk` (`create_next_chunk_relationship`, L164-174, `zip_longest`로 인접 청크 쌍 순회)
  - `entity_node -[FROM_CHUNK]-> chunk` (`create_node_to_chunk_rel`, L176-184; 호출부는 `process_chunk_extracted_entities`, L186-203 — `Chunk`/`Document` 레이블 자신은 제외하고 나머지 모든 추출 노드에 대해 생성)
- 레이블/관계 타입 이름은 전부 `LexicalGraphConfig`(`components/types.py:239-266`)로 커스터마이징 가능 (기본값: `Document`, `Chunk`, `FROM_DOCUMENT`, `NEXT_CHUNK`, `FROM_CHUNK`).

이 lexical graph 덕분에 나중에 "이 `CharacterState`의 `evidence`가 정확히 어느 원문 청크에서 나왔는가"를 `(entity)-[:FROM_CHUNK]->(chunk)`로 역추적할 수 있다 — `phase1-kg-schema.md`에서 "원문 근거는 lexical graph로 이미 추적되므로 별도 속성 불필요"라고 한 부분이 바로 이 메커니즘이다.

### 2.5 GraphPruning — 스키마 기반 정제 (`components/graph_pruning.py`)

`extractor`가 뱉은 원시 그래프(`Neo4jGraph`)를 실제 DB에 쓰기 전에 **스키마와 대조해서 걸러내는** 단계. `GraphSchema`가 없으면(`schema=None`) 아무것도 안 하고 그대로 통과시킨다(`run()`, L138-156).

스키마가 있으면 `_clean_graph()`(L158-195)가 순서대로:

1. **노드 검증** `_enforce_nodes()` → `_validate_node()` (L197-270)
   - lexical graph 노드(`Document`/`Chunk`)는 무조건 통과.
   - `label`/`id`가 비었으면 각각 `MISSING_LABEL`/`MISSING_REQUIRED_PROPERTY` 사유로 pruning.
   - `schema.node_type_from_label(label)`로 스키마에 정의된 타입인지 조회. 없으면:
     - `schema.additional_node_types=True`(기본값)면 그대로 유지 (스키마 밖 타입도 허용).
     - `False`면 `NOT_IN_SCHEMA` 사유로 제거.
   - 정의돼 있으면 `_enforce_properties()`로 속성 필터링(아래 3번 참고). 필터링 후 속성이 하나도 안 남으면 `NO_PROPERTY_LEFT`로 **노드 전체**를 제거.
2. **관계 검증** `_enforce_relationships()` → `_validate_relationship()` (L272-406)
   - 시작/끝 노드가 1번 단계에서 살아남은 노드 집합(`valid_nodes`)에 없으면 `INVALID_START_OR_END_NODE`로 제거 — **노드가 잘리면 그 노드에 연결된 관계도 연쇄적으로 사라진다**(docstring에 명시된 설계 의도).
   - 관계 타입이 스키마에 없고 `additional_relationship_types=False`면 `NOT_IN_SCHEMA`.
   - **패턴(`Pattern`) 검증 + 자동 방향 보정**(L309-332): `(start_label, rel.type, end_label)`이 허용 패턴 목록에 없으면, **반대 방향**(`end_label, rel.type, start_label`)이 패턴에 있는지 한 번 더 확인한다. 있으면 `reverse_tuple_valid=True`로 표시해 두었다가, 최종 반환 시 `start_node_id`/`end_node_id`를 서로 바꿔서 저장한다(L352-358). 즉 LLM이 관계 방향을 거꾸로 추출해도 스키마에 정의된 반대 방향 패턴만 있으면 자동으로 고쳐서 살린다. 정방향도 역방향도 없고 `additional_patterns=False`면 `INVALID_PATTERN`으로 제거.
3. **속성 필터링/필수값 검사** `_enforce_properties()` (L408-448)
   - `_ensure_property_types()`(L497-511): dict 타입 값(중첩 객체)은 `json.dumps`로 문자열화 — Neo4j 속성은 원시 타입/배열만 허용되므로 타입 에러 방지용 안전장치.
   - `_filter_properties()`(L450-474): `additional_properties=False`면 스키마에 없는 속성명은 `NOT_IN_SCHEMA`로 버림(값 자체는 `pruning_stats`에 기록되어 나중에 확인 가능).
   - `_check_required_properties()`(L476-495): `GraphSchema.mandatory_property_names_for_node/relationship()`(EXISTENCE 또는 KEY 제약이 걸린 속성, `schema.py:968-978`)가 채워졌는지 확인. 하나라도 null이면 `MISSING_REQUIRED_PROPERTY`로 **필터링 결과를 통째로 `{}`로 만들어** 상위 단계에서 해당 노드/관계 자체가 제거되게 유도한다.
4. 모든 사유는 `PruningReason` enum(L40-46: `NOT_IN_SCHEMA`, `MISSING_REQUIRED_PROPERTY`, `NO_PROPERTY_LEFT`, `INVALID_START_OR_END_NODE`, `INVALID_PATTERN`, `MISSING_LABEL`)으로 분류되고 `PruningStats`(L59-129)에 누적된다. `PipelineResult`를 통해 최종적으로 몇 개가 왜 잘렸는지 집계 가능 — **Phase 2/4의 "Indexing 품질 평가"에서 곧바로 재사용할 수 있는 기존 계측 포인트**다(예: `story_order` 같은 required 속성이 자주 `MISSING_REQUIRED_PROPERTY`로 잡히는지 모니터링).

### 2.6 KGWriter — `Neo4jWriter` (`components/kg_writer.py`)

정제된 `Neo4jGraph`를 실제 Neo4j에 MERGE(upsert)하는 단계.

- `_db_setup()`(L217-220): `__KGBuilder__` 레이블 노드에 대해 내부용 인덱스(`__tmp_internal_id`)를 생성 — MERGE 쿼리에서 노드를 빠르게 찾기 위한 임시 인덱스로 추정.
- `_nodes_to_rows()`(L222-234): 각 노드에 실제 레이블 외에 **`__Entity__` 레이블을 추가**한다(단, lexical graph 노드인 `Document`/`Chunk`는 제외, L229). 이 `__Entity__`가 바로 **Entity Resolver들이 "이게 해소 대상 엔티티다"라고 판단하는 기준**이다(§3에서 전부 `MATCH (entity:__Entity__)`로 시작하는 이유).
- `_upsert_nodes()`/`_upsert_relationships()`(L236-276): `batch_size`(기본 1000) 단위로 나눠(`batched()`, L80-87) Cypher `UNWIND $rows ...MERGE...` 형태 쿼리(`neo4j_queries.upsert_node_query`/`upsert_relationship_query`)를 실행. Neo4j 5.23+ 여부에 따라 지원 문법(`WITH` variable scope clause 등)이 갈려서 버전 체크(`is_version_5_23_or_above` 등, L213-215)를 생성자에서 미리 해둔다.
- `_db_cleaning()`(L278-284): `clean_db=True`(기본값)면 쓰기 후 정리 쿼리(`db_cleaning_query`) 실행 — 임시 인덱스/내부 프로퍼티(`__tmp_internal_id`) 제거 목적으로 추정.
- 실패 시 `neo4j.exceptions.ClientError`만 캐치해서 `KGWriterModel(status="FAILURE", ...)`로 반환(예외를 삼킴) — 파이프라인 자체는 죽지 않고 결과 상태로 실패를 알린다.

`ParquetWriter`(L322-539)는 Neo4j 대신 Parquet 파일로 내보내는 대안 구현체로, LoreKeeper PoC 범위에서는 직접 관련 없어 상세 생략(대량 배치 임포트/분석용).

---

## 3. Entity Resolution (엔티티 중복 해소) — `components/resolver.py`

### 3.1 왜 필요한가

§2.3에서 봤듯 LLM은 청크마다 독립적으로 추출하므로, "카엘"이 3화 청크와 7화 청크에서 각각 별개의 `Character` 노드로 만들어질 수 있다(호칭이 "카엘"/"그 남자"/"단장님"처럼 달라지면 문제가 더 심해짐). Resolver는 **`Neo4jWriter`가 DB에 다 쓴 뒤**, DB에 이미 존재하는 `__Entity__` 레이블 노드들을 대상으로 중복을 찾아 **APOC `apoc.refactor.mergeNodes`로 실제로 병합**한다. 즉 이건 그래프 객체를 다루는 인메모리 로직이 아니라 **Cypher 기반 후처리**다.

`EntityResolver` 베이스 클래스(L54-71)는 `driver`와 `filter_query`(해소 대상 범위를 좁히는 WHERE 절)만 갖고, `run() -> ResolutionStats`를 서브클래스가 구현한다.

### 3.2 SinglePropertyExactMatchResolver — 완전 일치 (L74-167)

`SimpleKGPipeline`의 **기본 resolver**(`perform_entity_resolution=True`일 때 자동으로 이걸 씀, `simple_kg_builder.py:317-323`). 로직:

1. 먼저 카운트 쿼리로 해소 대상 노드 수 확인 (0개면 조기 종료).
2. 핵심 병합 쿼리(L134-159)를 라인별로 풀어보면:
   ```cypher
   MATCH (entity:__Entity__)                      -- (filter_query 있으면 여기 삽입)
   WITH entity, entity.name as prop               -- resolve_property 기본값 "name"
   WITH entity, prop WHERE prop IS NOT NULL        -- name 없는 노드는 대상에서 제외
   UNWIND labels(entity) as lab
   WITH lab, prop, entity WHERE NOT lab IN ['__Entity__', '__KGBuilder__']  -- 내부 예약 레이블 skip
   WITH prop, lab, collect(entity) AS entities     -- (레이블, name값)별로 그룹핑
   CALL apoc.refactor.mergeNodes(entities, {properties:'discard', mergeRels:true})
   YIELD node
   RETURN count(node) as c
   ```
   즉 **"같은 레이블 + 같은 `name` 속성값을 가진 노드는 전부 하나로 합친다"**는 완전탐색적(exact) 규칙이다. 그룹핑 단위가 `(label, name)`이므로 다른 레이블끼리는 절대 합쳐지지 않는다(`Character` "낙양"과 `Location` "낙양"은 안전).
3. `apoc.refactor.mergeNodes` 옵션(`resolver.py:153-156`이 주석으로 설명):
   - `properties:'discard'` — 병합되는 여러 노드가 같은 속성 키에 다른 값을 가지면 하나만(먼저 세팅된 값 우선) 남기고 나머지는 버림. **정보 손실이 발생할 수 있는 지점** — 예를 들어 `Character.description`이 청크마다 다르게 요약됐다면 그중 하나만 살아남는다. (LoreKeeper 스키마에서 `description`을 "덮어써도 되는 참고 정보"로 설계한 것과 맞물려 이 손실을 감내 가능한 필드로 한정한 게 중요 — `phase1-kg-schema.md` 참고. 반대로 `CharacterState`처럼 값이 갈리면 안 되는 정보는 애초에 `Character`가 아니라 별도 노드로 분리해 이 discard 위험을 피한 설계.)
   - `mergeRels:true` — 병합 대상 노드들이 같은 타입의 관계로 같은 대상 노드를 가리키면 관계도 하나로 합침. 다르면 전부 새 노드에 재연결.
4. 반환값 `ResolutionStats(number_of_nodes_to_resolve, number_of_created_nodes)` — 해소 대상 노드 수 대비 실제 병합 후 남은 노드 수를 보여줘서 "몇 개가 몇 개로 줄었는지" 파악 가능.

**한계**: 정확히 문자열이 같아야 하므로 "카엘" vs "카엘님" vs "그 남자"는 하나도 못 합친다. 이걸 완화하는 게 아래 유사도 기반 resolver들이다.

### 3.3 BasePropertySimilarityResolver — 유사도 기반 공통 로직 (L178-312)

`SpaCySemanticMatchResolver`와 `FuzzyMatchResolver`가 공유하는 추상 베이스. `compute_similarity(text_a, text_b) -> float`(0~1)만 서브클래스가 구현하면 된다(`abc.abstractmethod`, L215-220).

`run()`(L222-296) 처리 흐름:

1. **Cypher로 후보 수집**(L231-245): `resolve_properties`(기본 `["name"]`, 여러 속성 지정 가능)를 레이블별로 모은다.
   ```cypher
   MATCH (entity:__Entity__)
   UNWIND labels(entity) AS lab
   WITH lab, entity WHERE NOT lab IN ['__Entity__', '__KGBuilder__']
   WITH lab, collect({ id: elementId(entity), name: entity.name, ... }) AS labelCluster
   RETURN lab, labelCluster
   ```
2. **Python 쪽 페어와이즈 비교**(L250-270): 레이블별로 `resolve_properties` 값들을 공백으로 이어붙여 하나의 문자열로 만들고(`node_texts`), `itertools.combinations`로 **모든 쌍**에 대해 `compute_similarity()`를 호출. `similarity_threshold`(기본 0.8) 이상이면 `{id1, id2}` 쌍을 후보로 기록. → **O(n²)** 비교이므로 노드 수가 크면 느려질 수 있음(PoC 규모에선 문제 없음, Phase 4 규모 확대 시 고려사항).
3. **겹치는 쌍을 그룹으로 합치기** `_consolidate_sets()`(L298-312): Union-Find와 유사하게, 이미 만든 그룹(`consolidated`)과 새 쌍이 교집합이 있으면 그 그룹에 합치고, 없으면 새 그룹을 만든다. 예: `{A,B}`, `{B,C}` 두 쌍이 나오면 최종적으로 `{A,B,C}` 하나의 그룹으로 합쳐짐(전이적 병합).
4. 그룹 크기가 2 이상인 것만 실제로 `apoc.refactor.mergeNodes` 호출(§3.2와 동일 옵션: `properties:'discard', mergeRels:true`) — 이번엔 `elementId` 목록으로 `MATCH (n) WHERE elementId(n) IN $ids`.

### 3.4 SpaCySemanticMatchResolver (L315-433)

`compute_similarity()` = spaCy 정적 단어벡터(`en_core_web_lg` 기본)의 **코사인 유사도**(L371-395, `numpy.dot`/`numpy.linalg.norm` 직접 계산). `embedding_cache` 딕셔너리로 같은 텍스트 재계산 방지(L379-383). 모델이 로컬에 없으면 자동 다운로드 시도(`_load_or_download_spacy_model`, L397-432, `auto_download_spacy_model=False`면 수동 설치 안내만 하고 에러).

**주의**: 이 방식은 영어 특화 정적 임베딩이라 한국어 텍스트("진소천" vs "그 청년")에는 의미상 유사도를 거의 잡아내지 못할 가능성이 높다 — LoreKeeper처럼 한국어 웹소설이 대상이면 이 resolver를 그대로 쓰기보다, 문맥 임베딩 기반 커스텀 resolver(예: OpenAI/Cohere 임베딩 + 코사인 유사도)를 직접 구현하는 편이 나을 수 있다(같은 `BasePropertySimilarityResolver`를 상속해서 `compute_similarity`만 교체하면 됨).

### 3.5 FuzzyMatchResolver (L435-472)

`compute_similarity()` = RapidFuzz의 `fuzz.WRatio(text_a, text_b, processor=utils.default_process)` (0~100 점수) `/ 100.0`으로 정규화(L466-471). `processor=utils.default_process`가 비교 전에 소문자화·양끝 공백 제거·구두점 제거 등 정규화를 해준다. **문자열 형태 유사도**(edit distance 계열)라서 "카엘" vs "카엘님"처럼 substring/약간의 변형엔 강하지만, "그 남자"처럼 의미는 같아도 표기가 완전히 다른 경우는 못 잡는다.

### 3.6 세 Resolver 선택 기준 요약

| Resolver | 판단 근거 | 강점 | 약점 | 의존성 |
|---|---|---|---|---|
| `SinglePropertyExactMatchResolver` | 속성값 완전 일치 | 빠르고 예측 가능, 오탐 거의 없음 | 표기 변형 전혀 못 잡음 | APOC만 필요 (기본 포함) |
| `FuzzyMatchResolver` | 문자열 형태 유사도(edit distance) | 오탈자/약간의 표기 변형에 강함 | 의미가 같아도 형태가 다르면 실패, 한국어 조사 변형에 취약 가능 | `rapidfuzz` |
| `SpaCySemanticMatchResolver` | 단어 임베딩 코사인 유사도 | 의미적으로 유사한 다른 표현도 잡을 잠재력 | 영어 특화 모델이라 한국어엔 부적합할 가능성 큼 | `spacy` + 모델 다운로드 |

LoreKeeper Phase 4(Indexing 품질 검증)에서 "카엘/단장님/그" 같은 다중 호칭 통합을 검증하려면, 위 표의 한계상 **기본 제공 3종 중 어느 것도 한국어 대명사·별칭 해소에 그대로 맞지 않을 가능성이 높다** — 실험 설계 시 (a) `SinglePropertyExactMatchResolver`로 우선 베이스라인을 잡고, (b) 필요하면 `BasePropertySimilarityResolver`를 상속해 한국어 임베딩 모델(OpenAI `text-embedding-3-*` 등)로 `compute_similarity`를 직접 구현하는 방향을 검토할 것.

---

## 4. 커스텀 파이프라인으로 직접 조립하기 (참고 스니펫)

`SimpleKGPipeline` 없이 컴포넌트를 직접 이어붙이면 각 단계에 세밀하게 개입할 수 있다(예: 청크별 커스텀 로깅, pruning 통계 수집, resolver 교체).

아래는 **스키마를 정적으로 확정해 둔** LoreKeeper 방식에 맞춘 최소 조립 예시다. `pruner`가 스키마를 필요로 하므로 스키마를 어디선가 흘려보내야 하는데, 두 가지 방법이 있다.

**방법 A — `SchemaBuilder`를 컴포넌트로 넣어 DAG로 흘리기** (스키마도 파이프라인 산출물로 추적하고 싶을 때):

```python
from neo4j_graphrag.experimental.pipeline import Pipeline
from neo4j_graphrag.experimental.components.schema import SchemaBuilder
from neo4j_graphrag.experimental.components.text_splitters.fixed_size_splitter import FixedSizeSplitter
from neo4j_graphrag.experimental.components.embedder import TextChunkEmbedder
from neo4j_graphrag.experimental.components.entity_relation_extractor import LLMEntityRelationExtractor, OnError
from neo4j_graphrag.experimental.components.graph_pruning import GraphPruning
from neo4j_graphrag.experimental.components.kg_writer import Neo4jWriter
from neo4j_graphrag.experimental.components.resolver import SinglePropertyExactMatchResolver

pipe = Pipeline()
pipe.add_component(SchemaBuilder(), "schema")                               # ← 스키마 컴포넌트 추가
pipe.add_component(FixedSizeSplitter(chunk_size=1500, chunk_overlap=100), "splitter")
pipe.add_component(TextChunkEmbedder(embedder=my_embedder), "chunk_embedder")
pipe.add_component(LLMEntityRelationExtractor(llm=my_llm, on_error=OnError.IGNORE), "extractor")
pipe.add_component(GraphPruning(), "pruner")
pipe.add_component(Neo4jWriter(driver=driver), "writer")
pipe.add_component(SinglePropertyExactMatchResolver(driver=driver), "resolver")

pipe.connect("schema", "extractor", {"schema": "schema"})                   # schema → extractor
pipe.connect("splitter", "chunk_embedder", {"text_chunks": "splitter"})
pipe.connect("chunk_embedder", "extractor", {"chunks": "chunk_embedder"})
pipe.connect("extractor", "pruner", {"graph": "extractor", "schema": "schema"})  # schema → pruner
pipe.connect("pruner", "writer", {"graph": "pruner.graph"})
pipe.connect("writer", "resolver", {})

result = await pipe.run({
    "splitter": {"text": manuscript_text},
    # SchemaBuilder.run()의 인자들을 여기서 채운다 (정적 GraphSchema를 dict로 풀거나 node_types/... 직접 지정)
    "schema": {"node_types": NODE_TYPES, "relationship_types": REL_TYPES, "patterns": PATTERNS},
})
```

**방법 B — 컴포넌트 없이 정적 `GraphSchema`를 `run()` 파라미터로 직접 주입** (가장 단순, PoC `poc/src/schema.py` 방식):

```python
# schema 컴포넌트를 아예 안 만들고, 이미 만들어 둔 GraphSchema 인스턴스를 각 run 파라미터에 꽂는다.
pipe.connect("chunk_embedder", "extractor", {"chunks": "chunk_embedder"})
pipe.connect("extractor", "pruner", {"graph": "extractor"})
pipe.connect("pruner", "writer", {"graph": "pruner.graph"})
pipe.connect("writer", "resolver", {})

result = await pipe.run({
    "splitter": {"text": manuscript_text},
    "extractor": {"schema": SCHEMA},   # SCHEMA = 정적 GraphSchema 인스턴스
    "pruner":    {"schema": SCHEMA},   # pruner에도 같은 스키마를 넘겨야 정제가 동작
})
```

> ⚠️ 방법 B에서 `pruner`에 `schema`를 안 넘기면 `GraphPruning.run()`은 `schema=None`으로 보고 **아무 정제도 하지 않고 그대로 통과**시킨다(`graph_pruning.py:138-156`). 스키마 기반 필터링을 원하면 반드시 `pruner`에도 스키마를 전달할 것.

---

## 4b. 재구현 관점: 내가 작성해야 할 코드 vs 라이브러리에 맡길 코드

"라이브러리 + 코드"로 LoreKeeper 인덱싱을 구현할 때, 각 단계에서 **그대로 쓸 것 / 파라미터만 바꿀 것 / 직접 서브클래싱해 새로 짤 것**을 구분하면 다음과 같다. 이 표가 곧 작성해야 할 코드의 지도다.

| 단계 | 라이브러리 기본 | LoreKeeper에서 내가 할 일 | 작성 형태 |
|---|---|---|---|
| 분할 | `FixedSizeSplitter` (글자 수) | 회차/문단 경계 존중이 필요하면 `TextSplitter` 서브클래스 또는 LangChain 어댑터로 교체. 최소한 `chunk_size`/`overlap` 튜닝. | `TextSplitter.run()` 구현 or 파라미터 |
| 임베딩 | `TextChunkEmbedder` + `Embedder` | 한국어 임베딩 모델(`Embedder` 인터페이스) 주입만 하면 됨. 직접 짤 것 거의 없음. | `Embedder` 어댑터 |
| 스키마 | 자동/수동 분기 | **Phase 1에서 확정한 `GraphSchema`를 정적 상수로 정의**(PoC `schema.py`). 자동 추출은 온보딩·검증 도구로만. | 정적 `GraphSchema` |
| 추출 | `LLMEntityRelationExtractor` | 그대로 사용. 단 **프롬프트(`ERExtractionTemplate`)를 한국어·무협 도메인용으로 커스터마이즈**하고 `examples`(few-shot)를 채우는 게 품질 핵심. Claude 사용 시 `use_structured_output` 가능 여부 확인(현 코드 주석상 OpenAI/VertexAI 명시 — Claude면 V1 경로). | `prompt_template` 문자열 + `examples` |
| Lexical Graph | `LexicalGraphBuilder` | 그대로 사용(원문 근거 추적을 공짜로 얻음). `LexicalGraphConfig`로 라벨명만 조정 가능. | 설정만 |
| 정제 | `GraphPruning` | 그대로 사용. `PruningStats`를 **Phase 2 인덱싱 품질 지표로 수집**하는 코드만 추가. | 통계 수집 로직 |
| 적재 | `Neo4jWriter` | 그대로 사용. `__Entity__` 라벨 규약에 의존. | 그대로 |
| **해소** | `SinglePropertyExactMatchResolver` | **여기가 직접 작성 비중이 가장 큼.** 아래 별도 설명. | `BasePropertySimilarityResolver` 서브클래스 |

### 핵심으로 직접 짜야 할 코드: 한국어 Entity Resolver

§3.4/3.5에서 봤듯 기본 3종 resolver는 한국어 다중 호칭("카엘"/"단장님"/"그 남자")에 부적합하다. `BasePropertySimilarityResolver`를 상속해 `compute_similarity`만 교체하면 나머지(후보 수집 Cypher, O(n²) 페어 비교, `_consolidate_sets` 전이 병합, APOC merge)는 전부 재사용된다. 내가 새로 짜야 하는 건 **유사도 함수 하나**뿐이다:

```python
from neo4j_graphrag.experimental.components.resolver import BasePropertySimilarityResolver

class KoreanEmbeddingResolver(BasePropertySimilarityResolver):
    """한국어 문맥 임베딩(예: OpenAI text-embedding-3-large) 코사인 유사도로 병합."""

    def __init__(self, driver, embedder, *, similarity_threshold=0.85, **kw):
        super().__init__(driver, similarity_threshold=similarity_threshold, **kw)
        self._embedder = embedder          # 한국어 지원 임베딩 클라이언트 주입
        self._cache: dict[str, list[float]] = {}

    def compute_similarity(self, text_a: str, text_b: str) -> float:
        a, b = self._embed(text_a), self._embed(text_b)   # 캐시 활용
        # 코사인 유사도 (resolver.py의 _cosine_similarity와 동일 공식)
        import numpy as np
        va, vb = np.asarray(a), np.asarray(b)
        n = np.linalg.norm(va) * np.linalg.norm(vb)
        return float(np.dot(va, vb) / n) if n else 0.0
```

주의할 설계 포인트(전부 §3에서 근거):
1. **`resolve_properties`를 `["name", "description"]` 등으로 넓히면** 후보 텍스트가 풍부해져 문맥 유사도가 더 잘 잡힌다(`BasePropertySimilarityResolver`가 공백으로 이어붙임, `resolver.py:256-262`).
2. **`filter_query`로 해소 범위를 좁혀** 오병합(false merge)을 줄인다 — 예: 같은 작품(`WHERE entity.work_id = $wid`) 안에서만 병합.
3. **`properties:'discard'` 병합 정책 때문에 병합 시 속성값 손실**이 생기므로(§3.2), 값이 갈리면 안 되는 정보(`CharacterState` 등)는 애초에 병합 대상 노드에 두지 말고 별도 노드로 분리(Phase 1 설계와 일치).
4. **O(n²) 비교 비용**: 대규모(수천 노드+)에서는 blocking(라벨/초성 등으로 후보군을 먼저 좁힌 뒤 페어 비교) 없이는 느리다. `run()`을 오버라이드해 label 클러스터 안에서 다시 blocking key로 나누는 최적화가 필요할 수 있다.
5. **오탐 방어**: 임베딩 유사도만으론 "형"·"동생"처럼 가까운 다른 인물을 잘못 합칠 수 있다. `similarity_threshold`를 보수적으로(0.85+) 잡고, Phase 4에서 병합 결과를 사람이 라벨링해 threshold를 튜닝하는 루프를 둘 것.

---

## 4c. Few-shot 예시 (무협 도메인, `poc/src/schema.py` 스키마 기준)

`ERExtractionTemplate`의 `{examples}` 자리에 넣는 few-shot 문자열이다. LoreKeeper 스키마의 **비자명한 규칙**을 예시로 가르치는 게 목적이다:

1. **신원과 상태의 분리** — "오른팔을 잃었다" 같은 가변 사실은 `Character` 속성이 아니라 별도 `CharacterState` 노드로 뽑아야 한다.
2. **`HAS_STATE` + `ESTABLISHED_IN` 이중 연결** — 상태 노드는 인물(`HAS_STATE`)과 성립 사건(`ESTABLISHED_IN`) 양쪽에 붙는다.
3. **`HOSTS` 방향** — `Location → Event` (사건이 장소를 가리키는 게 아님).
4. **`LOCATED_IN` 공간 계층** — 장소가 상위 장소를 가리킨다.
5. **`story_order` 채우기** — 명시적 시간 묘사가 없으면 `chapter`와 같은 값(FLOAT).
6. **청크-로컬 임시 ID** — `"0"`, `"1"`… 문자열로 노드를 매기고 관계에서 재사용.

### 전제: chapter 주입 규약

`Event.chapter`(INTEGER)와 `story_order`는 **LLM이 원문만 봐선 알 수 없다**(청크는 순수 텍스트다). 따라서 인덱싱 시 각 청크 앞에 `【3화】` 같은 회차 마커를 프리픽스로 붙여 넣는 규약을 둔다. few-shot 예시도 이 규약을 전제로 작성한다 — 이렇게 해야 LLM이 마커를 읽어 `chapter=3`, `story_order=3.0`을 채운다.

### few-shot 문자열 (그대로 `examples`로 전달 가능)

```python
# poc/src/extraction_examples.py 로 분리하거나 schema.py 옆에 두고 import 해서 쓴다.
EXTRACTION_FEW_SHOT = r"""
### 예시 1
입력 텍스트:
【3화】 화산파 대전에 정체불명의 무리가 들이닥쳤다. 젊은 검객 진소천은 이들에 맞서 싸웠으나,
적의 칼에 오른팔이 잘려나갔다. 화산파 대전은 영산 화산의 중턱에 자리 잡고 있었다.

출력 JSON:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "진소천", "description": "화산파의 젊은 검객"}},
  {"id": "1", "label": "Location", "properties": {"name": "화산파 대전", "description": "영산 화산 중턱의 화산파 본거지"}},
  {"id": "2", "label": "Location", "properties": {"name": "화산", "description": "화산파가 자리 잡은 영산"}},
  {"id": "3", "label": "Event", "properties": {"title": "화산파 혈사", "description": "정체불명의 무리가 화산파 대전을 습격함", "chapter": 3, "story_order": 3.0}},
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "right_arm", "value": "lost", "evidence": "적의 칼에 오른팔이 잘려나갔다"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "HOSTS", "start_node_id": "1", "end_node_id": "3", "properties": {}},
  {"type": "LOCATED_IN", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "4", "end_node_id": "3", "properties": {}}
]}

### 예시 2
입력 텍스트:
【7화】 부상에서 회복한 진소천이 낙양성에 도착했다. 잘려나갔던 그의 오른팔에는 정교한 기관 의수가 달려 있었다.
낙양성은 중원 한복판의 번화한 성이다. 그곳에서 정파의 회합이 열렸다.

출력 JSON:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "진소천"}},
  {"id": "1", "label": "Location", "properties": {"name": "낙양성", "description": "중원 한복판의 번화한 성"}},
  {"id": "2", "label": "Location", "properties": {"name": "중원", "description": "무림 전체를 아우르는 대륙"}},
  {"id": "3", "label": "Event", "properties": {"title": "낙양성 회합", "description": "회복한 진소천이 낙양성 정파 회합에 참석함", "chapter": 7, "story_order": 7.0}},
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "right_arm", "value": "prosthetic", "evidence": "잘려나갔던 그의 오른팔에는 정교한 기관 의수가 달려 있었다"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "HOSTS", "start_node_id": "1", "end_node_id": "3", "properties": {}},
  {"type": "LOCATED_IN", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "4", "end_node_id": "3", "properties": {}}
]}
"""
```

**예시 2가 추가로 가르치는 것**: 같은 `attribute="right_arm"`에 대해 3화의 `lost`와 별개로 7화의 `prosthetic` 상태를 **새 `CharacterState` 노드로** 만든다(덮어쓰지 않음). 조회 시 `ESTABLISHED_IN → Event.chapter`가 질의 회차 이하 중 가장 큰 값이 "현재 유효 상태"가 되는 스키마 설계(`schema.py:82-97`)와 정확히 맞물린다.

### `story_order` 변환 규칙 예시 (명시적 시간 묘사가 있을 때)

예시 1·2는 명시적 시간 묘사가 없어 `story_order == chapter`인 단순 케이스다. 원문에 시간 단서가 있으면 아래처럼 **비교 스케일 값으로 변환**해서 넣는다(`schema.py:63-77`). 이 표를 few-shot에 한 줄 규칙으로 덧붙이거나 예시 3으로 확장하면 된다.

| 원문 단서 (예: 12화 본문) | chapter | story_order | 이유 |
|---|---|---|---|
| 시간 언급 없음 | 12 | `12.0` | 기본값 = chapter |
| "3년 전 그날을 떠올렸다" (회상 사건) | 12 | 예: `4.5` | 현재보다 과거 → 기존 값들 사이에 끼워 넣는 실수값 |
| "그로부터 반년 뒤" (직전 사건이 story_order 8.0였다면) | 12 | `8.0`과 다음 값 사이, 예: `8.5` | 인접 두 값 사이 실수값(fractional indexing) |

> ⚠️ `story_order`는 원문의 절대 연도("환력 1023년")나 상대 표현("3년 전")을 **그대로 옮겨 적는 필드가 아니다.** 다른 Event와 상대 비교만 가능하면 되는 정규화된 순서값이므로, 회상·플래시백은 chapter보다 작은 값, 미래 예언·복선은 큰 값으로 변환한다.

### 배선 방법

`examples`는 `LLMEntityRelationExtractor.run(..., examples=...)`의 인자다. **주의: `SimpleKGPipeline`은 `examples`를 노출하지 않으므로**(`simple_kg_builder.py`의 `_get_extractor`/`get_run_params`가 넘기지 않음), few-shot을 쓰려면 §4의 **커스텀 파이프라인**에서 run 파라미터로 직접 전달해야 한다:

```python
from extraction_examples import EXTRACTION_FEW_SHOT

result = await pipe.run({
    "splitter": {"text": manuscript_text},
    "extractor": {"schema": SCHEMA, "examples": EXTRACTION_FEW_SHOT},  # ← 여기로 주입
    "pruner":    {"schema": SCHEMA},
})
```

### (선택) 자동 스키마 추출용 few-shot

새 IP 온보딩 시 `SchemaFromTextExtractor`를 쓸 거라면 `SchemaExtractionTemplate`의 `{examples}`에도 별도 few-shot이 필요하다(형식이 다름: 인스턴스가 아니라 `node_types`/`relationship_types`/`patterns`/`constraints` **타입 정의** JSON을 출력해야 함, `prompts.py:249-308`). 런타임 인덱싱 경로에선 쓰지 않으므로 여기선 배선 지점만 명시하고 상세 예시는 생략한다 — 필요 시 위 무협 스키마(`schema.py`)를 그대로 정답 출력으로 삼는 1-shot을 만들면 된다.

---

## 5. 요약: 텍스트 한 편이 그래프가 되기까지

1. **분할**: `FixedSizeSplitter`가 원문을 글자 수 기준 겹치는 청크로 자름 (문장 경계 보장 없음).
2. **임베딩**: 각 청크 텍스트를 벡터화해 나중에 `Chunk` 노드 속성으로 심음.
3. **추출 (청크별 독립, 병렬)**: `LLMEntityRelationExtractor`가 스키마를 프롬프트에 주입해 LLM에게 JSON(or structured output)으로 노드/관계를 뽑게 함. 청크마다 임시 ID → `chunk_id:` prefix로 전역 유일화. **이 단계에서 청크 간 중복/모순 판단은 전혀 일어나지 않음.**
4. **레티컬 그래프 부착**: `Document`/`Chunk`/`NEXT_CHUNK`/`FROM_CHUNK`를 자동으로 얹어 원문 추적 가능하게 함.
5. **가지치기**: `GraphPruning`이 스키마에 없는 타입/속성/패턴을 제거하고, 방향이 뒤집힌 관계는 자동 보정하며, 필수(EXISTENCE/KEY) 속성 누락 항목은 통째로 버림. 사유별 통계(`PruningStats`) 확보 가능.
6. **적재**: `Neo4jWriter`가 배치 단위로 MERGE, 모든 추출 엔티티에 `__Entity__` 공통 레이블 부여.
7. **엔티티 해소**: DB에 적재된 `__Entity__` 노드들을 대상으로 (기본) 완전 일치 또는 (옵션) 유사도 기반 매칭으로 `apoc.refactor.mergeNodes` — 여러 청크에서 따로 생성된 동일 인물/장소 노드를 하나로 합침. `properties:'discard'` 정책 때문에 병합 시 속성값 일부가 유실될 수 있다는 점이 스키마 설계(가변 정보는 별도 노드로 분리)에 영향을 줌.
