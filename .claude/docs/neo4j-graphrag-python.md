# neo4j-graphrag-python 레포지토리 정리

> GitHub: https://github.com/neo4j/neo4j-graphrag-python  
> 공식 문서: https://neo4j.com/docs/neo4j-graphrag-python/current/  
> 버전: 1.18.0 | 라이선스: Apache 2.0 | Python: 3.10~3.14 | Neo4j 최소: 5.18.1

---

## 1. 프로젝트 개요

Neo4j 공식 Python 라이브러리. **Graph RAG (Retrieval-Augmented Generation)** 애플리케이션 구축에 특화되어 있으며, 기존 `neo4j-genai` 패키지의 후속작이다.

핵심 목적:
1. **Knowledge Graph 구축**: 비정형 텍스트(PDF, Markdown 등)에서 LLM으로 지식 그래프 자동 생성
2. **GraphRAG 질의응답**: 다양한 검색 전략(Vector, Hybrid, Text2Cypher 등)과 LLM을 결합한 Q&A

---

## 2. 주요 기능

| 카테고리 | 기능 |
|---|---|
| KG 구축 | `SimpleKGPipeline`: PDF/Markdown → 지식 그래프 자동 생성 |
| KG 구축 | `Pipeline`: 컴포넌트 직접 조립하는 커스텀 파이프라인 |
| KG 구축 | `SchemaFromTextExtractor`: LLM으로 스키마 자동 추출 |
| KG 구축 | `SchemaFromExistingGraphExtractor`: 기존 그래프에서 스키마 역추출 |
| KG 구축 | 엔티티 중복 해소 (Exact, Fuzzy, SpaCy 기반 Resolver) |
| 벡터 인덱스 | `create_vector_index`, `create_fulltext_index`, `upsert_vectors` |
| Retriever | `VectorRetriever`, `VectorCypherRetriever` |
| Retriever | `HybridRetriever`, `HybridCypherRetriever` |
| Retriever | `Text2CypherRetriever` (자연어 → Cypher 자동 변환) |
| Retriever | 외부 Vector DB 연동: Weaviate, Pinecone, Qdrant |
| GraphRAG | `GraphRAG`: 검색 + 증강 + 생성 일체형 |
| 대화 | `Neo4jMessageHistory`, `InMemoryMessageHistory` |
| 도구 호출 | `Tool`, `ToolsRetriever` (LLM Function Calling) |
| 파이프라인 | 시각화 (`pipeline.draw()`), 이벤트 스트리밍 (`pipeline.stream()`) |
| 출력 | `ParquetWriter`: Parquet 형식으로 그래프 내보내기 |
| 통합 | LangChain, LlamaIndex 텍스트 스플리터 어댑터 |

---

## 3. 모듈 구조

```
src/neo4j_graphrag/
├── indexes.py                 # 벡터/전문 인덱스 CRUD
├── message_history.py         # 대화 히스토리
├── tool.py                    # Tool / ToolParameter 추상화
├── types.py                   # 공통 Pydantic 모델
├── exceptions.py              # 예외 클래스 계층
│
├── embeddings/                # 임베딩 프로바이더 (8종)
├── generation/                # GraphRAG 생성 레이어
│   ├── graphrag.py            # GraphRAG 메인 클래스
│   └── prompts.py             # 프롬프트 템플릿
├── llm/                       # LLM 프로바이더 (8종)
├── retrievers/                # Retriever 구현체
│   └── external/              # Weaviate, Pinecone, Qdrant
│
└── experimental/              # KG 구축 파이프라인 (실험적)
    ├── components/
    │   ├── data_loader.py     # PdfLoader, MarkdownLoader
    │   ├── embedder.py        # TextChunkEmbedder
    │   ├── entity_relation_extractor.py
    │   ├── kg_writer.py       # Neo4jWriter, ParquetWriter
    │   ├── lexical_graph.py   # LexicalGraphBuilder
    │   ├── resolver.py        # 엔티티 중복 해소
    │   ├── schema.py          # GraphSchema, SchemaBuilder
    │   └── text_splitters/
    └── pipeline/
        ├── kg_builder.py      # SimpleKGPipeline
        ├── pipeline.py        # Pipeline (핵심 실행 엔진)
        └── config/            # YAML/JSON 기반 설정
```

---

## 4. 핵심 컴포넌트

### GraphRAG (`generation/graphrag.py`)

검색 → 증강 → 생성 3단계 실행:
```
query_text → _build_query() → retriever.search() → prompt.format() → llm.invoke() → RagResultModel
```

```python
rag = GraphRAG(retriever=retriever, llm=llm)
result = rag.search(query_text="Who directed The Matrix?", retriever_config={"top_k": 5})
print(result.answer)
```

### Retriever 계층

| 클래스 | 전략 | 특징 |
|---|---|---|
| `VectorRetriever` | 벡터 유사도 | Neo4j ANN 인덱스, 필터 지원 |
| `VectorCypherRetriever` | 벡터 + Cypher | 벡터 검색 후 그래프 추가 탐색 |
| `HybridRetriever` | 벡터 + 전문검색 | `alpha` 가중치, naive/linear 랭커 |
| `HybridCypherRetriever` | 벡터 + 전문검색 + Cypher | 하이브리드 후 그래프 탐색 |
| `Text2CypherRetriever` | LLM → Cypher | 읽기 전용 보장 (`EXPLAIN`), few-shot 예제 지원 |
| `ToolsRetriever` | Function Calling | LLM이 적합한 검색 도구 선택 |

### LLM 인터페이스 (`llm/base.py`)

- `LLMInterfaceV2` (권장, LangChain 호환): 메시지 리스트 입력
- `LLMInterface` (deprecated): 문자열 입력
- 동기 `invoke()` / 비동기 `ainvoke()` 모두 지원
- Context Manager 지원 (`__enter__` / `__aenter__`)

### KG 파이프라인 흐름 (`experimental/`)

```
문서 (PDF/Markdown)
  ↓ DataLoader (PdfLoader / MarkdownLoader)
  ↓ TextSplitter (FixedSize / LangChain / LlamaIndex)
  ↓ TextChunkEmbedder
  ↓ LLMEntityRelationExtractor  ← 스키마 (GraphSchema)
  ↓ EntityResolver (선택: Exact / Fuzzy / SpaCy)
  ↓ KGWriter (Neo4jWriter / ParquetWriter)
  ↓ Neo4j 저장
```

**LexicalGraph 구조** (자동 생성):
```
Document → CONTAINS → Chunk → NEXT_CHUNK → Chunk
                               ↑ FROM_CHUNK
                          추출 엔티티 노드
```

### 프롬프트 템플릿 (`generation/prompts.py`)

| 클래스 | 용도 |
|---|---|
| `RagTemplate` | GraphRAG 질의응답용 |
| `Text2CypherTemplate` | 자연어 → Cypher 변환용 |
| `ERExtractionTemplate` | 엔티티-관계 추출용 |
| `SchemaExtractionTemplate` | 스키마 자동 추출용 |

---

## 5. 설치

```bash
# 기본 설치
pip install neo4j-graphrag

# LLM 프로바이더 선택 설치
pip install neo4j-graphrag[openai]
pip install neo4j-graphrag[anthropic]
pip install neo4j-graphrag[google]
pip install neo4j-graphrag[cohere]
pip install neo4j-graphrag[bedrock]
pip install neo4j-graphrag[ollama]
pip install neo4j-graphrag[mistralai]

# 외부 Vector DB
pip install neo4j-graphrag[weaviate]
pip install neo4j-graphrag[pinecone]
pip install neo4j-graphrag[qdrant]

# NLP 도구
pip install neo4j-graphrag[sentence-transformers]
pip install neo4j-graphrag[nlp]             # spaCy (Python ≤3.13)
pip install neo4j-graphrag[fuzzy-matching]  # RapidFuzz
pip install neo4j-graphrag[pyarrow]         # Parquet 출력

# 프레임워크 통합 (실험적)
pip install neo4j-graphrag[langchain]
pip install neo4j-graphrag[llama-index]
```

### 핵심 의존성

| 패키지 | 버전 | 용도 |
|---|---|---|
| `neo4j` | ≥5.17.0, <7.0.0 | Neo4j 드라이버 |
| `pydantic` | ≥2.6.3, <3.0.0 | 데이터 검증 |
| `pypdf` | - | PDF 파싱 |
| `tenacity` | - | 재시도 로직 |
| `json-repair` | - | LLM 출력 JSON 자동 복구 |

---

## 6. 사용 예시

### 벡터 인덱스 생성 및 데이터 삽입

```python
import neo4j
from neo4j_graphrag.indexes import create_vector_index, upsert_vectors

with neo4j.GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "password")) as driver:
    create_vector_index(
        driver,
        name="moviePlotsEmbedding",
        label="Movie",
        embedding_property="plotEmbedding",
        dimensions=1536,
        similarity_fn="cosine",
    )
    upsert_vectors(
        driver,
        ids=[1, 2, 3],
        embedding_property="plotEmbedding",
        embeddings=[[0.1, 0.2, ...], ...],
    )
```

### GraphRAG 질의응답 (VectorRetriever)

```python
from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.generation import GraphRAG
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.retrievers import VectorRetriever

retriever = VectorRetriever(
    driver,
    index_name="moviePlotsEmbedding",
    embedder=OpenAIEmbeddings(model="text-embedding-ada-002"),
    return_properties=["title", "plot"],
)
rag = GraphRAG(retriever=retriever, llm=OpenAILLM(model_name="gpt-4o"))
result = rag.search(query_text="Who directed The Matrix?", retriever_config={"top_k": 5})
print(result.answer)
```

### VectorCypherRetriever (그래프 탐색 결합)

```python
from neo4j_graphrag.retrievers import VectorCypherRetriever

retriever = VectorCypherRetriever(
    driver,
    index_name="actorEmbedding",
    retrieval_query="""
        MATCH (node)-[:ACTED_IN]->(m:Movie)
        RETURN node.name AS actor, m.title AS movie, score
        ORDER BY score DESC
    """,
    embedder=embedder,
)
```

### SimpleKGPipeline (PDF → 지식 그래프)

```python
import asyncio
from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline

async def build_kg():
    async with OpenAILLM(model_name="gpt-4o") as llm:
        pipeline = SimpleKGPipeline(
            llm=llm,
            driver=driver,
            embedder=OpenAIEmbeddings(),
            schema={
                "node_types": ["Person", "Organization", "Location"],
                "relationship_types": ["SITUATED_AT", "INTERACTS", "LED_BY"],
                "patterns": [
                    ("Person", "SITUATED_AT", "Location"),
                    ("Person", "INTERACTS", "Person"),
                    ("Organization", "LED_BY", "Person"),
                ],
            },
        )
        result = await pipeline.run_async(file_path="doc.pdf")

asyncio.run(build_kg())
```

---

## 7. 지원 LLM / Embedding 프로바이더

### LLM (8종)

| 클래스 | 비고 |
|---|---|
| `OpenAILLM`, `AzureOpenAILLM` | 구조화 출력 지원 |
| `AnthropicLLM` | Claude 계열 |
| `BedrockLLM` | AWS Bedrock |
| `CohereLLM` | Cohere Command |
| `GoogleGenAILLM` | Gemini |
| `MistralAILLM` | Mistral |
| `OllamaLLM` | 로컬 Ollama |
| `VertexAILLM` | Google Vertex AI, 구조화 출력 지원 |

### Embedding (8종)

`OpenAIEmbeddings`, `AzureOpenAIEmbeddings`, `BedrockEmbeddings`, `CohereEmbeddings`, `GoogleGenAIEmbeddings`, `MistralAIEmbeddings`, `OllamaEmbeddings`, `SentenceTransformerEmbeddings`, `VertexAIEmbeddings`

---

## 8. Entity Resolver (중복 해소)

| 클래스 | 방법 | 특징 |
|---|---|---|
| `SinglePropertyExactMatchResolver` | 정확한 속성값 일치 | APOC `mergeNodes` 활용 |
| `SpaCySemanticMatchResolver` | spaCy 코사인 유사도 | `en_core_web_lg` 등 |
| `FuzzyMatchResolver` | RapidFuzz WRatio | 0~100 → 0~1 정규화 |

공통 파라미터: `similarity_threshold` (기본 0.8), `resolve_properties`, `filter_query`

---

## 9. 주요 예외 계층

```
Neo4jGraphRagError
├── RetrieverInitializationError / RagInitializationError
├── LLMGenerationError → RetryableError → RateLimitError
├── EmbeddingsGenerationError
├── SearchValidationError / FilterValidationError
├── Neo4jIndexError / Neo4jInsertionError / Neo4jVersionError
├── Text2CypherRetrievalError
├── SchemaFetchError / SchemaValidationError / SchemaExtractionError
├── PdfLoaderError / MarkdownLoadError / UnsupportedDocumentFormatError
└── PromptMissingInputError / PromptMissingPlaceholderError
```

---

## 10. 테스트 및 개발

```bash
# 개발 환경 설정
uv sync --group dev
uv run pre-commit install

# 단위 테스트
uv run pytest tests/unit

# E2E 테스트 (Docker 필요)
docker compose up -d
uv run pytest tests/e2e
```

코드 품질 도구: `ruff` (Lint/Format), `mypy` (타입 검사), `pre-commit`, `coverage`

---

## 11. 기여

1. GitHub 저장소에 이슈 제출 (버전, OS, 재현 코드 포함)
2. Fork → 브랜치 생성 → 변경 → 단위 테스트 추가
3. CLA 서명: https://neo4j.com/developer/cla
4. PR 제출 (Rebase 방식 권장)
5. 커뮤니티: https://community.neo4j.com/
