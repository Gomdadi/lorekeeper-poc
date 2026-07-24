"""LoreKeeper 공개 API.

이 패키지는 다른 프로젝트에서 `pip install -e ./poc` 후
`from lorekeeper import indexing, build_retrieval_tools, ...` 형태로 소비된다.
소비 쪽이 내부 모듈 경로(.indexing, .retrieval 등)를 몰라도 되도록
자주 쓰는 진입점을 여기서 re-export 한다.
"""

# 인덱싱 진입점: async def indexing(chapter, text) -> dict
from .indexing import indexing

# 검색(retrieval) 관련 공개 함수/클래스.
# retrieval 모듈은 별도로 관리되며, 아래 이름들이 그 공개 API다.
from .retrieval import (
    build_retrieval_tools,
    build_retrievers,
    build_vector_cypher_retriever,
    build_hybrid_cypher_retriever,
    build_entity_state_history_retriever,
    build_text2cypher_retriever,
)

__all__ = [
    "indexing",
    "build_retrieval_tools",
    "build_retrievers",
    "build_vector_cypher_retriever",
    "build_hybrid_cypher_retriever",
    "build_entity_state_history_retriever",
    "build_text2cypher_retriever",
]
