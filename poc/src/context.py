"""
배경 컨텍스트(novel_context) 조립 및 회차 요약.

indexing.py가 각 회차 추출 프롬프트에 주입할 novel_context를 만든다. 두 소스를 결합한다.
  (a) 그래프 덤프  — 현재 DB의 도메인 노드/관계를 사람이 읽는 텍스트로 직렬화(엔티티 식별·별칭 정합용).
  (b) 회차 요약    — 이전 회차까지 누적된 Chapter.summary(서사 흐름 보강).

이 회차의 요약을 생성하는 summarize_episode도 여기 둔다(생성된 요약은 Chapter.summary에 저장돼
다음 회차의 (b)로 재사용된다).
"""

from __future__ import annotations

from pipeline import build_llm

# 그래프 덤프에서 제외할 라벨. 메타 라벨(writer 부여)과 lexical/provenance 레이어(Chunk/Chapter)는
# 배경 컨텍스트로 주지 않는다 — 추출 대상은 도메인 노드다.
_EXCLUDED_LABELS = {"__Entity__", "__KGBuilder__", "Chunk", "Chapter"}


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


def dump_graph_text(driver, database: str) -> str:
    """
    현재 DB의 도메인 노드/관계를 사람이 읽는 텍스트로 직렬화한다.
    메타/lexical/provenance 라벨(__Entity__/__KGBuilder__/Chunk/Chapter)은 제외한다.
    DB가 비어 있으면(첫 회차) 빈 문자열을 반환한다.
    """
    node_records, _, _ = driver.execute_query(
        """
        MATCH (n)
        WHERE NOT n:Chunk AND NOT n:Chapter
        RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props
        """,
        database_=database,
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

    # 도메인 노드 사이의 관계만 조회(양끝이 Chunk/Chapter가 아닌 것).
    rel_records, _, _ = driver.execute_query(
        """
        MATCH (a)-[rel]->(b)
        WHERE NOT a:Chunk AND NOT b:Chunk AND NOT a:Chapter AND NOT b:Chapter
        RETURN elementId(a) AS s, type(rel) AS t, elementId(b) AS e,
               properties(rel) AS props
        """,
        database_=database,
    )
    rel_lines: list[str] = []
    for r in rel_records:
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


def load_chapter_summaries(driver, database: str) -> str:
    """이전 회차까지의 Chapter.summary를 number 순으로 이어붙여 줄거리 요약 텍스트를 만든다."""
    records, _, _ = driver.execute_query(
        """
        MATCH (c:Chapter)
        WHERE c.summary IS NOT NULL
        RETURN c.number AS number, c.summary AS summary
        ORDER BY c.number
        """,
        database_=database,
    )
    if not records:
        return ""
    return "\n".join(f"[{r['number']}화] {r['summary']}" for r in records)


def build_context(graph_dump: str, summaries: str) -> str:
    """그래프 덤프와 회차 요약을 섹션 구분해 novel_context로 결합한다."""
    parts: list[str] = []
    if graph_dump:
        parts.append("# 지금까지의 그래프\n" + graph_dump)
    if summaries:
        parts.append("# 지금까지의 줄거리 요약\n" + summaries)
    return "\n\n".join(parts)


async def summarize_episode(text: str) -> str:
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
