# neo4j-graphrag-python 디렉토리 구조 정리

> 소스: `neo4j-graphrag-python/src/neo4j_graphrag/`
> 버전: 1.18.0 (레포에 소스 직접 포함)
> 목적: 라이브러리가 어떤 축으로 나뉘고 각 폴더/파일이 무슨 역할인지 한눈에 파악한다. KG 추출 파이프라인 상세는 `kg-extraction-pipeline.md` 참고.

---

## 큰 그림: 두 개의 축

`neo4j_graphrag`는 크게 **두 축**으로 나뉜다.

1. **검색·생성(RAG) 축** — *이미 만들어진* 그래프에서 데이터를 꺼내(retrieval) LLM으로 답을 생성(generation)하는, 안정화된 부분.
2. **KG 구축 축(`experimental/`)** — *텍스트에서* 그래프를 만들어내는(indexing) 실험적 부분.

`llm/`과 `embeddings/`(외부 모델 어댑터)는 두 축이 공유한다.

```
neo4j_graphrag/
├─ llm/, embeddings/     ← 외부 모델 어댑터 (양쪽 축이 공유)
│
├─ [검색·생성 축, 안정화됨]
│   retrievers/          ← 그래프에서 데이터 꺼내기 (R)
│   generation/          ← 꺼낸 걸로 답변 생성 (G) + 프롬프트
│   indexes.py, filters.py, schema.py(읽기), neo4j_queries.py
│
└─ experimental/         ← [KG 구축 축, 실험적] 텍스트 → 그래프
    ├─ components/       ← 부품(splitter/extractor/resolver/...)
    └─ pipeline/         ← 부품을 잇는 DAG 엔진 + SimpleKGPipeline
```

LoreKeeper 기준: **인덱싱**은 `experimental/`(+`llm`/`embeddings`)를, 이후 **질의응답**은 `retrievers/`·`generation/`을 사용한다.

---

## 1. 최상위 폴더 (`src/neo4j_graphrag/`)

| 폴더 | 의미 | 핵심 내용 |
|---|---|---|
| **`llm/`** | LLM 제공자 어댑터 | `LLMInterface` 공통 인터페이스(`base.py`) + 벤더별 구현: `anthropic_llm.py`(Claude), `openai_llm.py`, `vertexai_llm.py`, `cohere_llm.py`, `google_genai_llm.py`, `mistralai_llm.py`, `bedrock_llm.py`, `ollama_llm.py`. `rate_limit.py`(요청 제한), `types.py`·`utils.py`. **추출·검색에서 LLM을 부르는 모든 지점이 여기를 거침** |
| **`embeddings/`** | 임베딩 제공자 어댑터 | `Embedder` 공통 인터페이스(`base.py`) + 벤더별: `openai.py`, `cohere.py`, `sentence_transformers.py`(로컬), `vertexai.py`, `google_genai.py`, `mistral.py`, `bedrock.py`, `ollama.py`. 청크·쿼리를 벡터로 변환 |
| **`retrievers/`** | **검색기** (RAG의 "R") | Neo4j에서 관련 데이터를 꺼내오는 전략들: `vector.py`(벡터 유사도), `hybrid.py`(벡터+풀텍스트), `text2cypher.py`(자연어→Cypher 생성), `tools_retriever.py`. `base.py`가 공통 인터페이스 |
| **`retrievers/external/`** | 외부 벡터DB 검색기 | Neo4j가 아닌 `pinecone/`, `qdrant/`, `weaviate/`에 벡터를 두고 검색할 때. `utils.py` 공용 |
| **`generation/`** | **생성** (RAG의 "G") | `graphrag.py`(GraphRAG 파이프라인: retriever로 context를 뽑아 LLM에 넣고 답변 생성), `prompts.py`(`ERExtractionTemplate`·`RagTemplate`·`Text2CypherTemplate`·`SchemaExtractionTemplate` 등 모든 프롬프트 템플릿), `types.py` |
| **`experimental/`** | **텍스트 → KG 구축** (별도 축) | 인덱싱 파이프라인 전체. §3에서 상술 |
| **`utils/`** | 내부 공용 유틸 | `driver_config.py`(드라이버 설정/user-agent 오버라이드), `version_utils.py`(Neo4j 버전 체크), `logging.py`, `validation.py`, `file_handler.py`, `json_schema_structured_output.py`(structured output 스키마 변환), `rate_limit.py` |

---

## 2. 최상위 단일 `.py` 파일

| 파일 | 의미 |
|---|---|
| `indexes.py` | Neo4j 벡터/풀텍스트 **인덱스 생성·관리** 헬퍼 (`create_vector_index` 등) |
| `neo4j_queries.py` | 라이브러리가 쓰는 **Cypher 쿼리 문자열 생성** 함수 모음 (KGWriter의 `upsert_node_query`/`upsert_relationship_query` 등) |
| `schema.py` | (최상위) **기존 그래프의 스키마를 읽어오는** 유틸. ⚠️ `experimental/components/schema.py`(추출 가이드용 스키마 정의)와는 **다른 파일**이니 혼동 주의 |
| `filters.py` | 검색 시 메타데이터 필터를 Cypher WHERE 절로 변환 |
| `message_history.py` | 대화형 RAG의 대화 이력 저장 (인메모리/Neo4j) |
| `tool.py` | LLM tool-calling용 도구 정의 |
| `types.py` | 전역 공용 타입 (`LLMMessage` 등) |
| `exceptions.py` | 전역 예외 클래스 (`LLMGenerationError`, `SchemaValidationError` 등) |
| `py.typed` | 타입 힌트 제공 패키지 표식(PEP 561) |

---

## 3. `experimental/` 내부 (KG 구축 축)

| 폴더/파일 | 의미 |
|---|---|
| **`experimental/components/`** | 파이프라인의 **개별 부품**들. `entity_relation_extractor.py`(LLM 추출), `resolver.py`(엔티티 해소), `schema.py`(추출 가이드 스키마 정의), `graph_pruning.py`(스키마 기반 정제), `kg_writer.py`(Neo4j 적재), `lexical_graph.py`(문서 골격 그래프), `embedder.py`(청크 임베딩), `text_splitters/`(청킹: fixed_size/langchain/llamaindex), `data_loader.py`·`pdf_loader.py`(문서 로딩), `neo4j_reader.py`, `graph_schema_extraction.py`, `parquet_*`(대안 출력) |
| **`experimental/pipeline/`** | 부품을 이어 실행하는 **DAG 엔진**. `pipeline.py`(위상정렬 + asyncio 실행), `component.py`(모든 컴포넌트의 베이스 클래스), `orchestrator.py`(실행 조율), `pipeline_graph.py`(DAG 자료구조), `kg_builder.py`(`SimpleKGPipeline` 진입점), `stores.py`(중간 결과 저장), `notification.py`(진행 이벤트), `exceptions.py` |
| **`experimental/pipeline/config/`** | 파이프라인을 **설정으로 조립**하는 계층. `template_pipeline/simple_kg_builder.py`(8개 컴포넌트 배선 정의), `object_config.py`·`param_resolver.py`(객체/파라미터 파싱), `runner.py`(설정→실행) |
| **`experimental/pipeline/types/`** | 파이프라인 타입 정의 (`definitions.py`의 `ConnectionDefinition` 등, `orchestration.py`, `context.py`, `schema.py`) |
| **`experimental/utils/`** | experimental 전용 유틸 (`schema.py` 등) |

### 인덱싱 파이프라인 8단계 (참고)

`SimpleKGPipeline`이 `components/`의 부품을 `pipeline/`로 이어 만든 것:

```
file_loader → splitter → chunk_embedder → extractor → pruner → writer → resolver
                                    (schema 컴포넌트가 extractor·pruner에 스키마 공급)
```

각 단계의 상세 로직과 Entity Resolution은 `kg-extraction-pipeline.md`에 정리되어 있다.

---

## 4. 관련 문서

- `kg-extraction-pipeline.md` — 텍스트→KG 추출 파이프라인 & Entity Resolution 코드 라인 단위 분석
- `schema-components.md` — `GraphSchema`/`NodeType`/`RelationshipType`/`ConstraintType` 구조
- `neo4j-graphrag-python.md` — 라이브러리 개요
