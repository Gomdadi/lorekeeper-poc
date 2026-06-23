import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
MODEL = "claude-haiku-4-5-20251001"

CLIENT = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

JUDGE_PROMPT = """다음 그래프 컨텍스트를 바탕으로 질문에 대한 예상 답변이 올바른지 판단하세요.

[그래프 컨텍스트]
{context}

[질문] {query}
[예상 답변] {label}

JSON으로만 응답하세요: {{"pass": true 또는 false, "reason": "한 줄 이유"}}"""


def _get_graph_context(session, query: str) -> str:
    rows = session.run(
        """
        CALL db.index.fulltext.queryNodes('entity_name_idx', $query)
        YIELD node, score
        WITH node ORDER BY score DESC LIMIT 5
        OPTIONAL MATCH (node)-[r:RELATES_TO]-(neighbor)
        RETURN node.name AS entity, node.type AS type, node.description AS desc,
               r.description AS rel, neighbor.name AS neighbor
        """,
        query=query,
    ).data()

    if not rows:
        return "관련 엔티티를 찾을 수 없습니다."

    lines = []
    seen = set()
    for row in rows:
        entity = row["entity"]
        if entity not in seen:
            seen.add(entity)
            lines.append(f"[{row['type']}] {entity}: {row['desc']}")
        if row.get("rel") and row.get("neighbor"):
            lines.append(f"  → {entity} -[{row['rel']}]→ {row['neighbor']}")

    return "\n".join(lines)


def run_validation(validation_set: list[dict]) -> list[dict]:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    results = []

    with driver.session() as session:
        for qa in validation_set:
            query = qa["query"]
            label = qa["label"]

            context = _get_graph_context(session, query)

            response = CLIENT.messages.create(
                model=MODEL,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": JUDGE_PROMPT.format(
                        context=context, query=query, label=label
                    ),
                }],
            )

            raw = response.content[0].text.strip()
            try:
                verdict = json.loads(raw)
            except json.JSONDecodeError:
                verdict = {"pass": False, "reason": "응답 파싱 실패"}

            results.append({
                "query": query,
                "label": label,
                "pass": verdict.get("pass", False),
                "reason": verdict.get("reason", ""),
            })

    driver.close()
    return results
