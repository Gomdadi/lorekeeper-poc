"""
회차 누적 인덱싱 실행 스크립트 (변형 비교 harness와 별개 모드).

한 번 실행 = 한 회차 인덱싱. DB를 리셋하지 않고(clean_db=False, _reset_db 미호출) 이전 회차까지
누적된 결과 위에 새 회차를 얹는다. 각 실행은 이전 회차까지의 결과를 배경 컨텍스트(novel_context)로
추출 프롬프트에 주입한다.

novel_context 두 소스:
  (a) 그래프 덤프  — 현재 DB의 도메인 노드/관계를 사람이 읽는 텍스트로 직렬화(엔티티 식별·별칭 정합용).
  (b) rolling summary — 회차마다 3~5문장 서사 요약을 파일(output/rolling_summary.md)에 누적(서사 흐름 보강).

실행 흐름:
  1. 현재 DB 덤프 + rolling summary 로드 → novel_context 조합
  2. 이 회차 파이프라인 run (DB 리셋/clean_db 안 함, 누적)
  3. 이 회차 3~5문장 요약을 build_llm("high")로 생성해 rolling_summary.md에 append

실행(예시):
    cd poc && LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/index_episode.py
"""

from __future__ import annotations

import asyncio
import os
import re

from neo4j_graphrag.experimental.components.text_splitters.fixed_size_splitter import (
    FixedSizeSplitter,
)

from client import get_driver
from extraction_examples import EXTRACTION_FEW_SHOT
from pipeline import build_llm, build_pipeline
from resolver import CombiningFuzzyResolver
from schema import NODE_TYPES, PATTERNS, RELATIONSHIP_TYPES
from splitters import ChapterTaggingSplitter

# 단일 DB 이름(Community). NEO4J_DATABASE가 없으면 기본 'neo4j'.
DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# 경로: 이 파일 기준 상대 경로로 입력/출력 위치를 잡는다.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
# 입력 텍스트 경로. LOREKEEPER_INPUT 환경변수로 덮어쓸 수 있다(회차 파일을 순서대로 지정).
INPUT_PATH = os.environ.get("LOREKEEPER_INPUT") or os.path.join(
    _SRC_DIR, "..", "data", "input.txt"
)
OUTPUT_DIR = os.path.join(_SRC_DIR, "..", "output")
# 회차마다 누적되는 rolling summary 파일.
ROLLING_SUMMARY_PATH = os.path.join(OUTPUT_DIR, "rolling_summary.md")

# 추출 LLM의 reasoning effort. LOREKEEPER_REASONING 환경변수로 지정하며, 없으면 None →
# build_llm이 reasoning_effort 파라미터를 아예 넘기지 않는다(모델 기본 동작). 모델 A/B 비교용.
_EXTRACT_REASONING = os.environ.get("LOREKEEPER_REASONING")

# 그래프 덤프에서 제외할 라벨.
# __Entity__/__KGBuilder__는 writer가 붙이는 내부 메타 라벨. Chunk는 lexical graph 노드로
# 원고 원문을 그대로 담고 있어 배경 컨텍스트로 덤프하면 노이즈만 커진다(추출 대상은 도메인
# 노드다) → 제외한다. (Document 라벨은 이 파이프라인이 생성하지 않으므로 조건에 넣지 않는다 —
# 넣으면 Neo4j가 "존재하지 않는 라벨" 경고를 매 쿼리마다 띄운다.)
_EXCLUDED_LABELS = {"__Entity__", "__KGBuilder__", "Chunk"}
# 회차 마커 정규식([3화] 등). 요약 헤더에 회차를 표기하는 데 쓴다.
_CHAPTER_MARKER = re.compile(r"\[\s*\d+\s*화\s*\]")


def _node_display(labels: list[str], props: dict) -> tuple[str, str]:
    """
    (참조용 짧은 이름, 상세 한 줄)을 만든다.

    참조용 이름: 관계 직렬화에서 노드를 가리키는 짧은 표현(예: 'Character:홍길동').
    상세 한 줄: 노드 라벨과 주요 속성을 사람이 읽기 좋게 편 문자열.
    """
    # 메타/lexical을 뺀 도메인 라벨(보통 1개).
    domain_labels = [lab for lab in labels if lab not in _EXCLUDED_LABELS]
    label = domain_labels[0] if domain_labels else "Node"
    # 대표 이름: name → title → attribute=value 순으로 고른다.
    if props.get("name"):
        key = str(props["name"])
    elif props.get("title"):
        key = str(props["title"])
    elif props.get("attribute"):
        key = f"{props['attribute']}={props.get('value', '')}"
    else:
        key = "?"
    ref = f"{label}:{key}"
    # 상세: 대표 이름 외 나머지 속성을 짧게 덧붙인다.
    extras = {
        k: v
        for k, v in props.items()
        if k not in ("name", "title") and v not in (None, "")
    }
    extra_str = ", ".join(f"{k}={v}" for k, v in extras.items())
    detail = f"- ({label}) {key}" + (f" — {extra_str}" if extra_str else "")
    return ref, detail


def dump_graph_text(driver) -> str:
    """
    현재 DB의 도메인 노드/관계를 사람이 읽는 텍스트로 직렬화한다.
    메타/lexical 라벨(__Entity__/__KGBuilder__/Chunk/Document)은 제외한다.
    DB가 비어 있으면(첫 회차) 빈 문자열을 반환한다.
    """
    # 도메인 노드 조회. elementId를 관계 매핑 키로 쓴다.
    node_records, _, _ = driver.execute_query(
        """
        MATCH (n)
        WHERE NOT n:Chunk
        RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props
        """,
        database_=DATABASE,
    )
    if not node_records:
        return ""

    # elementId → (참조 이름, 상세 줄) 매핑을 만든다.
    ref_by_id: dict[str, str] = {}
    node_lines: list[str] = []
    for r in node_records:
        ref, detail = _node_display(r["labels"], r["props"])
        ref_by_id[r["id"]] = ref
        node_lines.append(detail)

    # 도메인 노드 사이의 관계만 조회(양끝이 Chunk/Document가 아닌 것).
    rel_records, _, _ = driver.execute_query(
        """
        MATCH (a)-[rel]->(b)
        WHERE NOT a:Chunk AND NOT b:Chunk
        RETURN elementId(a) AS s, type(rel) AS t, elementId(b) AS e,
               properties(rel) AS props
        """,
        database_=DATABASE,
    )
    rel_lines: list[str] = []
    for r in rel_records:
        # 양끝 노드가 도메인 노드 매핑에 있을 때만(방어적).
        s_ref = ref_by_id.get(r["s"])
        e_ref = ref_by_id.get(r["e"])
        if not s_ref or not e_ref:
            continue
        prop_str = ", ".join(f"{k}={v}" for k, v in (r["props"] or {}).items() if v)
        suffix = f" ({prop_str})" if prop_str else ""
        rel_lines.append(f"- {s_ref} -[{r['t']}]-> {e_ref}{suffix}")

    parts = ["## 노드", *sorted(node_lines)]
    if rel_lines:
        parts += ["", "## 관계", *sorted(rel_lines)]
    return "\n".join(parts)


def _load_rolling_summary() -> str:
    """rolling summary 파일을 로드한다. 없으면 빈 문자열."""
    if os.path.exists(ROLLING_SUMMARY_PATH):
        with open(ROLLING_SUMMARY_PATH, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _build_context(graph_dump: str, rolling_summary: str) -> str:
    """그래프 덤프와 rolling summary를 섹션 구분해 novel_context로 결합한다."""
    parts: list[str] = []
    if graph_dump:
        parts.append("# 지금까지의 그래프\n" + graph_dump)
    if rolling_summary:
        parts.append("# 지금까지의 줄거리 요약\n" + rolling_summary)
    return "\n\n".join(parts)


def _label_counts(driver) -> dict[str, int]:
    """메타 라벨을 제외한 라벨별 노드 수(간단한 결과 출력용)."""
    records, _, _ = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS lab RETURN lab AS lab, count(*) AS cnt",
        database_=DATABASE,
    )
    return {
        r["lab"]: r["cnt"]
        for r in records
        if r["lab"] not in {"__Entity__", "__KGBuilder__"}
    }


def _rel_counts(driver) -> dict[str, int]:
    """관계 타입별 수."""
    records, _, _ = driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt",
        database_=DATABASE,
    )
    return {r["t"]: r["cnt"] for r in records}


def _episode_header(text: str) -> str:
    """원고에 등장하는 회차 마커들을 모아 요약 헤더 문자열을 만든다.

    마커가 없으면 입력 파일명을 헤더로 쓴다.
    """
    markers = [m.group().strip() for m in _CHAPTER_MARKER.finditer(text)]
    if markers:
        return " ".join(dict.fromkeys(markers))  # 중복 제거, 등장 순서 유지
    return os.path.basename(INPUT_PATH)


async def _summarize_episode(text: str) -> str:
    """이 회차 원고를 3~5문장으로 요약한다. build_llm('high')로 추론 강도를 높여 인과·복선을 반영."""
    llm = build_llm("high")
    system = (
        "당신은 웹소설 편집자다. 회차 원고를 읽고 이후 회차와 대조할 때 도움이 되도록 "
        "핵심 서사를 간결히 요약한다."
    )
    user = (
        "다음 회차 원고를 3~5문장의 한국어로 요약하라. 등장인물, 주요 사건, 인물의 상태 변화"
        "(부상·생사·소속·능력·소지품)와 새로 드러난 관계를 중심으로 쓴다.\n\n"
        f"{text}"
    )
    resp = await llm.ainvoke(user, system_instruction=system)
    return resp.content.strip()


def _append_summary(header: str, summary: str) -> None:
    """rolling summary 파일에 이 회차 요약을 헤더와 함께 append한다."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(ROLLING_SUMMARY_PATH, "a", encoding="utf-8") as f:
        f.write(f"## {header}\n{summary}\n\n")


async def main() -> None:
    if not os.path.exists(INPUT_PATH):
        raise FileNotFoundError(
            f"입력 텍스트가 없습니다: {INPUT_PATH} — [N화] 마커를 포함한 회차 원문을 준비하세요."
        )
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(INPUT_PATH, encoding="utf-8") as f:
        text = f.read()

    driver = get_driver()
    try:
        # 1. 현재 DB 덤프 + rolling summary 로드 → novel_context 조합.
        graph_dump = dump_graph_text(driver)
        rolling_summary = _load_rolling_summary()
        context = _build_context(graph_dump, rolling_summary)
        print(f"배경 컨텍스트 길이: {len(context)}자 "
              f"(그래프 {len(graph_dump)}자 + 요약 {len(rolling_summary)}자)")

        # 2. 이 회차 파이프라인 run (DB 리셋/clean_db 안 함, 누적).
        # 회차 통째 = 한 청크(chunk_size=12000)로 회차 내 coreference를 한 컨텍스트에서 해소.
        splitter = ChapterTaggingSplitter(
            FixedSizeSplitter(chunk_size=12000, chunk_overlap=0)
        )
        resolver = CombiningFuzzyResolver(driver=driver, neo4j_database=DATABASE)
        pipe, llm = build_pipeline(
            splitter,
            resolver,
            driver,
            DATABASE,
            reasoning_effort=_EXTRACT_REASONING,
            novel_context=context,
            clean_db=False,
        )
        data = {
            "splitter": {"text": text},
            "schema": {
                "node_types": NODE_TYPES,
                "relationship_types": RELATIONSHIP_TYPES,
                "patterns": PATTERNS,
            },
            "extractor": {"examples": EXTRACTION_FEW_SHOT},
        }
        await pipe.run(data)

        # 3. 이 회차 요약을 생성해 rolling summary에 append.
        header = _episode_header(text)
        summary = await _summarize_episode(text)
        _append_summary(header, summary)

        # 실행 후 간단한 결과 출력(누적된 전체 DB 기준).
        print(f"\n=== {header} 인덱싱 완료 (누적) ===")
        print(f"    라벨: {_label_counts(driver)}")
        print(f"    관계: {_rel_counts(driver)}")
        print(
            f"    추출 토큰(req/resp/total): {llm.total_request_tokens}/"
            f"{llm.total_response_tokens}/{llm.total_tokens} "
            f"(LLM 호출 {llm.call_count}회)"
        )
        print(f"    이 회차 요약:\n{summary}")
        print(f"    rolling summary 저장: {ROLLING_SUMMARY_PATH}")
    finally:
        driver.close()


if __name__ == "__main__":
    asyncio.run(main())
