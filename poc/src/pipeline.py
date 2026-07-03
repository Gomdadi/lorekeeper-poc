"""
커스텀 인덱싱 파이프라인 조립 (방법 A — SchemaBuilder를 DAG 컴포넌트로 포함).

SimpleKGPipeline 대신 Pipeline을 직접 조립하는 이유:
- SimpleKGPipeline은 extractor에 few-shot `examples`를 넘길 경로가 없다.
- resolver가 SinglePropertyExactMatchResolver로 하드코딩돼 있어 커스텀 resolver로 스왑 불가.

DAG:
    splitter → embedder → extractor → pruner → writer → (순서만) → resolver
    schema(SchemaBuilder) → extractor, schema → pruner
resolver는 데이터 입력이 없으므로 writer→resolver를 빈 input_config로 연결해 실행 순서만 강제한다.
"""

from __future__ import annotations

import neo4j

from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.experimental.components.embedder import TextChunkEmbedder
from neo4j_graphrag.experimental.components.entity_relation_extractor import (
    LLMEntityRelationExtractor,
    OnError,
)
from neo4j_graphrag.experimental.components.graph_pruning import GraphPruning
from neo4j_graphrag.experimental.components.kg_writer import Neo4jWriter
from neo4j_graphrag.experimental.components.resolver import EntityResolver
from neo4j_graphrag.experimental.components.schema import SchemaBuilder
from neo4j_graphrag.experimental.components.text_splitters.base import TextSplitter
from neo4j_graphrag.experimental.pipeline import Pipeline
from neo4j_graphrag.llm import OpenAILLM

# 추출(스키마·그래프)에 쓸 OpenAI 모델. GPT-5 계열이므로 temperature/top_p 등 샘플링
# 파라미터는 전달하지 않고 모델 기본값을 쓴다(비기본 temperature는 400으로 거부됨).
EXTRACTION_MODEL = "gpt-5.4-mini"
# 청크 임베딩 모델. retrieval을 아직 안 쓰므로 저비용 모델로 통일.
EMBEDDING_MODEL = "text-embedding-3-small"
# 반복 전달되는 프리픽스(스키마+few-shot)의 프롬프트 캐시 라우팅 안정화용 키.
PROMPT_CACHE_KEY = "lorekeeper-extract"


def build_llm() -> OpenAILLM:
    """추출용 OpenAI LLM 인스턴스. prompt_cache_key를 model_params로 넣어 모든 호출 경로에 전달한다."""
    return OpenAILLM(
        model_name=EXTRACTION_MODEL,
        model_params={"prompt_cache_key": PROMPT_CACHE_KEY},
    )


def build_embedder() -> TextChunkEmbedder:
    """청크 임베딩 컴포넌트."""
    return TextChunkEmbedder(embedder=OpenAIEmbeddings(model=EMBEDDING_MODEL))


def build_pipeline(
    splitter: TextSplitter,
    resolver: EntityResolver,
    driver: neo4j.Driver,
    database: str,
) -> Pipeline:
    """
    변형별 splitter/resolver를 받아 인덱싱 DAG를 조립해 반환한다.

    schema/extractor는 매 파이프라인마다 새로 만든다(상태 오염 방지).
    스키마 자체(node_types/relationship_types/patterns)는 run 데이터로 주입하므로
    여기서는 SchemaBuilder 컴포넌트만 등록한다.
    """
    llm = build_llm()

    pipe = Pipeline()
    pipe.add_component(splitter, "splitter")
    pipe.add_component(build_embedder(), "embedder")
    pipe.add_component(SchemaBuilder(), "schema")
    pipe.add_component(
        # V2 structured output 사용(OpenAILLM은 supports_structured_output=True).
        # on_error=RAISE: 한 청크의 추출 실패를 조용히 빈 그래프로 삼키지 않고 드러낸다.
        LLMEntityRelationExtractor(llm=llm, use_structured_output=True, on_error=OnError.RAISE),
        "extractor",
    )
    pipe.add_component(GraphPruning(), "pruner")
    # clean_db=True로 쓰기 전에 DB를 비운다(harness에서 별도 리셋도 하지만 안전장치).
    pipe.add_component(
        Neo4jWriter(driver=driver, neo4j_database=database, clean_db=True), "writer"
    )
    pipe.add_component(resolver, "resolver")

    # 데이터 흐름 배선 (하위 입력 파라미터 ← 상위 출력 필드)
    pipe.connect("splitter", "embedder", {"text_chunks": "splitter"})
    pipe.connect("embedder", "extractor", {"chunks": "embedder"})
    pipe.connect("schema", "extractor", {"schema": "schema"})
    pipe.connect("schema", "pruner", {"schema": "schema"})
    pipe.connect("extractor", "pruner", {"graph": "extractor"})
    pipe.connect("pruner", "writer", {"graph": "pruner.graph"})
    # resolver는 입력이 없어 데이터 매핑 없이 순서만 강제(writer 완료 후 실행).
    pipe.connect("writer", "resolver", {})

    return pipe
