"""
회차 누적 인덱싱 진입점 함수 (컴포넌트).

`indexing(chapter, text)` 한 번 호출 = 한 회차 인덱싱. DB를 리셋하지 않고(clean_db=False)
이전 회차까지 누적된 결과 위에 새 회차를 얹는다. 두 개의 병렬 산출을 만든다.

  1. 추출(KG): 회차 원고 전체를 단일 청크로 넣어(WholeTextSplitter) Character/Event/CharacterState
     등 도메인 그래프를 추출한다. 회차 내 coreference를 한 컨텍스트에서 해소하기 위함.
  2. 근거·벡터(Chunk): 같은 원고를 KSS로 잘게 쪼개 Chunk 노드로 저장하고 text-embedding-3-small로
     임베딩한다. 추출된 Event/CharacterState는 근거 문장이 속한 Chunk를 evidence_chunk 번호로
     가리키며, 후처리(evidence.link_evidence)가 이를 EVIDENCED_BY 관계로 잇는다. 회차는 Chapter
     노드로 승격하고 각 Chunk를 IN_CHAPTER로 잇는다. rolling summary는 Chapter.summary 노드
     property로 누적한다.

배경 컨텍스트(novel_context) 조립과 회차 요약은 context.py, 근거 링크는 evidence.py로 분리했고,
이 모듈은 그 컴포넌트들을 순서대로 엮는 오케스트레이터다. '새 회차 우선' 지침으로 anchoring을
막는다(extractor.py).

실행(CLI):
    cd poc && LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py
프로그램적 호출:
    await indexing(1, text, driver)
"""

from __future__ import annotations

import asyncio
import os

from chunks import write_chunk_layer
from client import get_driver
from context import (
    build_context,
    dump_graph_text,
    load_chapter_summaries,
    summarize_episode,
)
from evidence import link_evidence
from extraction_examples import EXTRACTION_FEW_SHOT
from pipeline import build_pipeline
from resolver import CombiningFuzzyResolver
from schema import NODE_TYPES, PATTERNS, RELATIONSHIP_TYPES
from splitters import KSSSentenceSplitter, WholeTextSplitter

# 단일 DB 이름(Community). NEO4J_DATABASE가 없으면 기본 'neo4j'.
DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# 경로: 이 파일 기준 상대 경로로 입력 위치를 잡는다.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 추출 LLM의 reasoning effort. 기본값 'high'(luna 기본 세팅).
# LOREKEEPER_REASONING 환경변수로 덮어쓸 수 있다.
_EXTRACT_REASONING = os.environ.get("LOREKEEPER_REASONING", "high")

# 근거·벡터용 KSS 조각 크기(글자). 100자면 청크당 평균 약 3문장으로, 근거를 문장 단위로
# 정밀하게 짚기에 적합하다.
DEFAULT_KSS_CHUNK_SIZE = 100


def _label_counts(driver, database: str) -> dict[str, int]:
    """메타 라벨을 제외한 라벨별 노드 수(간단한 결과 출력용)."""
    records, _, _ = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS lab RETURN lab AS lab, count(*) AS cnt",
        database_=database,
    )
    return {
        r["lab"]: r["cnt"]
        for r in records
        if r["lab"] not in {"__Entity__", "__KGBuilder__"}
    }


def _rel_counts(driver, database: str) -> dict[str, int]:
    """관계 타입별 수."""
    records, _, _ = driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt",
        database_=database,
    )
    return {r["t"]: r["cnt"] for r in records}


async def indexing(
    chapter: int,
    text: str,
    driver=None,
    *,
    database: str = DATABASE,
    reasoning: str | None = _EXTRACT_REASONING,
    kss_chunk_size: int = DEFAULT_KSS_CHUNK_SIZE,
) -> dict:
    """
    한 회차를 누적 인덱싱한다.

    chapter: 회차 번호(정수). Event.chapter·Chapter.number·Chunk.chapter의 근거가 된다.
    text: 회차 원고 전체.
    driver: 재사용할 Neo4j 드라이버. None이면 이 함수가 열고 닫는다.
    반환: 라벨/관계 카운트·토큰·요약을 담은 dict.
    """
    own_driver = driver is None
    if own_driver:
        driver = get_driver()
    try:
        # 1. 배경 컨텍스트(현재 그래프 덤프 + 이전 회차 요약) 조합.
        graph_dump = dump_graph_text(driver, database)
        summaries = load_chapter_summaries(driver, database)
        context = build_context(graph_dump, summaries)
        print(
            f"배경 컨텍스트 길이: {len(context)}자 "
            f"(그래프 {len(graph_dump)}자 + 요약 {len(summaries)}자)"
        )

        # 2. 근거·벡터용 KSS 청킹. raw.chunks는 (a)Chunk 레이어 생성과 (b)추출 마커 조립에
        #    함께 쓰이므로 여기서 한 번만 쪼갠다. overlap=0: 겹침이 있으면 경계 문장이 인접
        #    두 [C{i}] 마커에 중복 노출돼 LLM의 evidence_chunk 번호 선택이 모호해지므로 끈다.
        raw = await KSSSentenceSplitter(
            chunk_size=kss_chunk_size, chunk_overlap=0
        ).run(text)

        # 3. Chunk/Chapter provenance 레이어 생성(Chunk 노드·임베딩·NEXT_CHUNK + Chapter +
        #    IN_CHAPTER + 벡터 인덱스). 상세는 chunks.write_chunk_layer 참고.
        await write_chunk_layer(driver, database, chapter, raw.chunks)

        # 4. 추출 텍스트 조립: [chapter:N] 헤더 + 각 KSS 조각 앞에 [C{index}] 마커.
        #    마커 번호는 Chunk.index와 일치 → LLM의 evidence_chunk 번호가 Chunk에 매핑된다.
        marked = f"[chapter:{chapter}]\n" + " ".join(
            f"[C{c.index}] {c.text}" for c in raw.chunks
        )

        # 5. 추출 파이프라인 실행(회차 통째 단일 청크, DB 누적). resolver는 best-fit인 fuzzy.
        pipe, llm = build_pipeline(
            WholeTextSplitter(),
            CombiningFuzzyResolver(driver=driver, neo4j_database=database),
            driver,
            database,
            reasoning_effort=reasoning,
            novel_context=context,
            clean_db=False,
        )
        data = {
            "splitter": {"text": marked},
            "schema": {
                "node_types": NODE_TYPES,
                "relationship_types": RELATIONSHIP_TYPES,
                "patterns": PATTERNS,
            },
            "extractor": {"examples": EXTRACTION_FEW_SHOT},
        }
        await pipe.run(data)

        # 6. evidence_chunk 번호 → EVIDENCED_BY 관계(resolver 뒤).
        link_evidence(driver, database, chapter)

        # 7. 이 회차 요약 생성 → Chapter.summary에 저장(다음 회차 컨텍스트로 재사용).
        summary = await summarize_episode(text)
        driver.execute_query(
            "MATCH (c:Chapter {number: $chapter}) SET c.summary = $summary",
            {"chapter": chapter, "summary": summary},
            database_=database,
        )

        # 8. 결과 요약(누적된 전체 DB 기준).
        labels = _label_counts(driver, database)
        rels = _rel_counts(driver, database)
        print(f"\n=== {chapter}화 인덱싱 완료 (누적) ===")
        print(f"    라벨: {labels}")
        print(f"    관계: {rels}")
        print(
            f"    추출 토큰(req/resp/total): {llm.total_request_tokens}/"
            f"{llm.total_response_tokens}/{llm.total_tokens} "
            f"(LLM 호출 {llm.call_count}회)"
        )
        print(f"    이 회차 요약:\n{summary}")
        return {
            "chapter": chapter,
            "labels": labels,
            "rels": rels,
            "tokens": {
                "request": llm.total_request_tokens,
                "response": llm.total_response_tokens,
                "total": llm.total_tokens,
            },
            "summary": summary,
        }
    finally:
        if own_driver:
            driver.close()


if __name__ == "__main__":
    # 얇은 CLI 래퍼: LOREKEEPER_CHAPTER로 회차, LOREKEEPER_INPUT으로 원고 파일을 받아 진입점 호출.
    chapter_env = os.environ.get("LOREKEEPER_CHAPTER")
    if not chapter_env:
        raise SystemExit("LOREKEEPER_CHAPTER 환경변수로 회차 번호를 지정하세요(예: LOREKEEPER_CHAPTER=1).")
    input_path = os.environ.get("LOREKEEPER_INPUT") or os.path.join(
        _SRC_DIR, "..", "data", "input.txt"
    )
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"입력 텍스트가 없습니다: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        episode_text = f.read()
    asyncio.run(indexing(int(chapter_env), episode_text))
