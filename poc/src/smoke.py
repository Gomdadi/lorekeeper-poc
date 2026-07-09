"""
gpt-5.4-mini 스모크 테스트 (DB·드라이버 없이 API만 검증).

harness(indexing_eval.py)를 5변형 전부 돌리기 전에, 모델이 우리 사용 방식과
호환되는지 저비용으로 1회 확인한다. 크레딧이 생기면 이 스크립트를 가장 먼저 돌린다.

    cd poc && uv run python src/smoke.py

두 부분:
[A] extractor 경로 검증 — 실제 코드 경로(OpenAILLM + LLMEntityRelationExtractor,
    use_structured_output=True)로 예시 청크 1~2개를 추출. V2 structured output이
    파싱되는지, 파라미터(prompt_cache_key 등)를 모델이 거부하지 않는지, 스키마·few-shot·
    [N화] 마커가 그럴듯하게 작동하는지 눈으로 확인. DB 없음(create_lexical_graph=False).
[B] 프롬프트 캐싱 관찰 — 래퍼 LLMResponse는 cached_tokens를 노출하지 않으므로,
    openai 클라이언트를 직접 호출해 동일 프리픽스를 두 번 보내고
    usage.prompt_tokens_details.cached_tokens를 출력한다(best-effort).
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

# OPENAI_API_KEY를 .env(레포 루트)에서 로드. openai 클라이언트가 이 환경변수를 읽는다.
load_dotenv()

from neo4j_graphrag.experimental.components.entity_relation_extractor import (  # noqa: E402
    LLMEntityRelationExtractor,
    OnError,
)
from neo4j_graphrag.experimental.components.types import (  # noqa: E402
    TextChunk,
    TextChunks,
)

from extraction_examples import EXTRACTION_FEW_SHOT  # noqa: E402
from pipeline import EXTRACTION_MODEL, PROMPT_CACHE_KEY, build_llm  # noqa: E402
from schema import SCHEMA  # noqa: E402

# 대충 만든 예시 청크(테스트용). [N화] 마커 + 상태 변화(부상→의수)로
# Character/Location/Event/CharacterState와 chapter 채우기를 한 번에 자극한다.
SAMPLE_CHUNKS = [
    (
        "[1화]\n"
        "검객 서리안이 북방 요새 흑암성에 도착했다. 흑암성은 변경 왕국의 최전방 거점이다. "
        "그날 밤 마수의 습격에서 서리안은 왼팔을 잃었다."
    ),
    (
        "[2화]\n"
        "세 달 뒤, 서리안은 의수를 달고 다시 검을 잡았다. "
        "그는 흑암성 성주와 함께 왕도로 향했다."
    ),
]


async def smoke_extractor() -> None:
    """[A] 실제 extractor 경로로 V2 structured output 동작을 검증한다."""
    print("=== [A] extractor 스모크 (V2 structured output) ===")
    llm = build_llm()  # harness와 동일한 OpenAILLM 설정(gpt-5.4-mini + prompt_cache_key)
    extractor = LLMEntityRelationExtractor(
        llm=llm,
        use_structured_output=True,
        create_lexical_graph=False,  # Chunk/Document 노드 없이 추출 엔티티만 보기 위함
        on_error=OnError.RAISE,      # 실패를 삼키지 않고 그대로 드러냄
    )
    chunks = TextChunks(
        chunks=[TextChunk(text=t, index=i) for i, t in enumerate(SAMPLE_CHUNKS)]
    )

    graph = await extractor.run(
        chunks=chunks, schema=SCHEMA, examples=EXTRACTION_FEW_SHOT
    )

    print(f"노드 {len(graph.nodes)}개:")
    for n in graph.nodes:
        print(f"  [{n.label}] {n.properties}")
    print(f"관계 {len(graph.relationships)}개:")
    for r in graph.relationships:
        print(f"  {r.start_node_id} -[{r.type}]-> {r.end_node_id}")
    print("→ [A] 성공: V2 structured output/파라미터 호환 OK\n")


def smoke_caching() -> None:
    """[B] openai 클라이언트를 직접 호출해 cached_tokens를 관찰한다(best-effort)."""
    print("=== [B] 프롬프트 캐싱 관찰 (openai 직접 호출) ===")
    from openai import OpenAI

    client = OpenAI()

    # 안정적인 큰 프리픽스(스키마 + few-shot)를 system 메시지로 둔다. 캐싱은 1024토큰 이상
    # 프리픽스에 자동 적용되므로, 스키마 설명이 긴 우리 SCHEMA를 그대로 활용한다.
    prefix = (
        "다음은 지식그래프 추출 스키마와 예시다. 이 지시는 매 요청에서 동일하다.\n\n"
        f"[SCHEMA]\n{SCHEMA.model_dump(exclude_none=True)}\n\n"
        f"[EXAMPLES]\n{EXTRACTION_FEW_SHOT}"
    )

    def one_call(tail: str) -> None:
        resp = client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": prefix},   # 동일 프리픽스(캐시 대상)
                {"role": "user", "content": tail},        # 가변 꼬리
            ],
            prompt_cache_key=PROMPT_CACHE_KEY,
        )
        usage = resp.usage
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        print(
            f"  prompt_tokens={usage.prompt_tokens}, cached_tokens={cached}"
        )

    try:
        print("1차 호출(캐시 채우기):")
        one_call("한 단어로 'ok'라고만 답해.")
        print("2차 호출(동일 프리픽스 → cached_tokens 증가 기대):")
        one_call("한 단어로 'ok2'라고만 답해.")
        print("→ [B] 완료: 2차의 cached_tokens가 0보다 크면 캐싱 동작\n")
    except Exception as e:  # noqa: BLE001
        # 캐싱 관찰은 부가 검증이므로 실패해도 [A] 결과를 살린다. 원인만 출력.
        print(f"→ [B] 스킵(오류): {type(e).__name__}: {e}\n")


async def main() -> None:
    await smoke_extractor()
    smoke_caching()


if __name__ == "__main__":
    asyncio.run(main())
