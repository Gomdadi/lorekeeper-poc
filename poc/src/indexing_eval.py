"""
Indexing 검증 harness.

동일 입력 텍스트(poc/data/input.txt)에 대해 컴포넌트 후보를 하나씩 바꿔가며(OFAT)
인덱싱 파이프라인을 돌리고, 구조지표를 자동 집계한다. 각 변형의 그래프는 실행 직후
poc/output/<variant>.cypher로 덤프해 보존하고(human judge용), 비교표를 report.md로 쓴다.

정답 라벨 채점은 하지 않는다 — 지표 + 덤프 재적재 육안 비교로 판정한다.

실행(예시): API 크레딧이 있을 때
    cd poc && uv run python src/indexing_eval.py
Neo4j(Community)는 단일 DB만 쓰므로 변형마다 DETACH DELETE로 리셋 후 실행한다.
"""

from __future__ import annotations

import asyncio
import os

from neo4j_graphrag.experimental.components.resolver import (
    SinglePropertyExactMatchResolver,
)
from neo4j_graphrag.experimental.components.text_splitters.fixed_size_splitter import (
    FixedSizeSplitter,
)

from client import get_driver
from extraction_examples import EXTRACTION_FEW_SHOT
from pipeline import build_pipeline
from resolver import OpenAIEmbeddingResolver
from schema import NODE_TYPES, PATTERNS, RELATIONSHIP_TYPES
from splitters import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    ChapterTaggingSplitter,
    KiwiSentenceSplitter,
    KSSSentenceSplitter,
    make_recursive_splitter,
)

# 단일 DB 이름(Community). NEO4J_DATABASE가 없으면 기본 'neo4j'.
DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# 경로: 이 파일 기준 상대 경로로 입력/출력 위치를 잡는다.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(_SRC_DIR, "..", "data", "input.txt")
OUTPUT_DIR = os.path.join(_SRC_DIR, "..", "output")
REPORT_PATH = os.path.join(OUTPUT_DIR, "report.md")

# 라벨/관계 집계에서 제외할 내부 메타 라벨(writer가 모든 엔티티에 부여).
_META_LABELS = {"__Entity__", "__KGBuilder__"}


def _make_baseline_splitter() -> ChapterTaggingSplitter:
    """FixedSizeSplitter를 챕터 태깅으로 감싼 베이스라인 splitter."""
    return ChapterTaggingSplitter(
        FixedSizeSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    )


# 변형 레지스트리 (OFAT). splitter/resolver는 매 변형마다 새로 만들도록 팩토리로 둔다.
# resolver 팩토리는 (driver, database)를 받는다.
VARIANTS = [
    {
        "name": "v_baseline",
        "splitter": _make_baseline_splitter,
        "resolver": lambda driver, db: SinglePropertyExactMatchResolver(
            driver=driver, neo4j_database=db
        ),
    },
    {
        "name": "v_recursive",
        "splitter": lambda: ChapterTaggingSplitter(make_recursive_splitter()),
        "resolver": lambda driver, db: SinglePropertyExactMatchResolver(
            driver=driver, neo4j_database=db
        ),
    },
    {
        "name": "v_kiwi",
        "splitter": lambda: ChapterTaggingSplitter(KiwiSentenceSplitter()),
        "resolver": lambda driver, db: SinglePropertyExactMatchResolver(
            driver=driver, neo4j_database=db
        ),
    },
    {
        "name": "v_kss",
        "splitter": lambda: ChapterTaggingSplitter(KSSSentenceSplitter()),
        "resolver": lambda driver, db: SinglePropertyExactMatchResolver(
            driver=driver, neo4j_database=db
        ),
    },
    {
        "name": "v_resolver_embed",
        "splitter": _make_baseline_splitter,
        "resolver": lambda driver, db: OpenAIEmbeddingResolver(
            driver=driver, neo4j_database=db
        ),
    },
]


def _reset_db(driver) -> None:
    """단일 DB를 완전히 비운다(변형 간 격리)."""
    driver.execute_query("MATCH (n) DETACH DELETE n", database_=DATABASE)


def _label_counts(driver) -> dict[str, int]:
    """메타 라벨을 제외한 라벨별 노드 수."""
    records, _, _ = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS lab RETURN lab AS lab, count(*) AS cnt",
        database_=DATABASE,
    )
    return {
        r["lab"]: r["cnt"] for r in records if r["lab"] not in _META_LABELS
    }


def _rel_counts(driver) -> dict[str, int]:
    """관계 타입별 수."""
    records, _, _ = driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt",
        database_=DATABASE,
    )
    return {r["t"]: r["cnt"] for r in records}


def _dump_graph(driver, name: str) -> None:
    """
    현재 DB 전체 그래프를 <name>.cypher로 덤프한다.
    apoc.export.cypher.all은 컨테이너 import 디렉토리(/var/lib/neo4j/import)에 쓰는데,
    docker-compose에서 이 경로를 host ./poc/output에 매핑해 두어 host에서 바로 보인다.
    """
    driver.execute_query(
        "CALL apoc.export.cypher.all($file, {format:'plain'})",
        {"file": f"{name}.cypher"},
        database_=DATABASE,
    )


async def run_variant(driver, variant: dict) -> dict:
    """한 변형을 실행하고 지표 dict를 반환한다."""
    name = variant["name"]
    print(f"\n=== {name} 실행 ===")

    _reset_db(driver)

    splitter = variant["splitter"]()
    resolver = variant["resolver"](driver, DATABASE)
    pipe = build_pipeline(splitter, resolver, driver, DATABASE)

    # SchemaBuilder 컴포넌트에 스키마 목록을, extractor에 few-shot을 run 데이터로 주입한다.
    with open(INPUT_PATH, encoding="utf-8") as f:
        text = f.read()
    data = {
        "splitter": {"text": text},
        "schema": {
            "node_types": NODE_TYPES,
            "relationship_types": RELATIONSHIP_TYPES,
            "patterns": PATTERNS,
        },
        "extractor": {"examples": EXTRACTION_FEW_SHOT},
    }

    result = await pipe.run(data)
    run_id = result.run_id

    # pruner 산출물은 leaf가 아니라 final_results에 없으므로 store에서 직접 조회한다.
    # store에는 model_dump()된 dict가 저장되며, pruned_* 개수는 computed property라
    # dict에는 없으므로 리스트 길이로 센다.
    pruner_res = await pipe.store.get_result_for_component(run_id, "pruner") or {}
    pstats = pruner_res.get("pruning_stats", {})

    # resolver는 leaf라 final_results(= result.result)에 dict로 담긴다.
    resolver_stats = (result.result or {}).get("resolver", {})

    labels = _label_counts(driver)
    metrics = {
        "name": name,
        "labels": labels,
        "rels": _rel_counts(driver),
        "chunks": labels.get("Chunk", 0),
        "pruned_nodes": len(pstats.get("pruned_nodes", [])),
        "pruned_rels": len(pstats.get("pruned_relationships", [])),
        "pruned_props": len(pstats.get("pruned_properties", [])),
        "resolve_target": resolver_stats.get("number_of_nodes_to_resolve"),
        "resolve_merged": resolver_stats.get("number_of_created_nodes"),
    }

    _dump_graph(driver, name)
    print(f"    라벨: {metrics['labels']}")
    print(f"    관계: {metrics['rels']}")
    print(
        f"    pruned(n/r/p): {metrics['pruned_nodes']}/"
        f"{metrics['pruned_rels']}/{metrics['pruned_props']}, "
        f"resolve(대상/병합): {metrics['resolve_target']}/{metrics['resolve_merged']}"
    )
    print(f"    덤프: output/{name}.cypher")
    return metrics


def _write_report(rows: list[dict]) -> None:
    """변형별 지표를 마크다운 비교표로 저장한다."""
    lines = [
        "# Indexing 변형 비교 리포트",
        "",
        "| variant | chunks | 라벨별 노드 | 관계 타입별 | pruned(n/r/p) | resolve(대상/병합) |",
        "|---|---|---|---|---|---|",
    ]
    for m in rows:
        # 라벨/관계는 메타 제외 후 사람이 읽기 좋게 문자열로 편다.
        labels = ", ".join(f"{k}:{v}" for k, v in sorted(m["labels"].items()))
        rels = ", ".join(f"{k}:{v}" for k, v in sorted(m["rels"].items()))
        pruned = f"{m['pruned_nodes']}/{m['pruned_rels']}/{m['pruned_props']}"
        resolve = f"{m['resolve_target']}/{m['resolve_merged']}"
        lines.append(
            f"| {m['name']} | {m['chunks']} | {labels} | {rels} | {pruned} | {resolve} |"
        )
    lines.append("")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n리포트 저장: {REPORT_PATH}")


async def main() -> None:
    if not os.path.exists(INPUT_PATH):
        raise FileNotFoundError(
            f"입력 텍스트가 없습니다: {INPUT_PATH} — 【N화】 마커를 포함한 원문을 준비하세요."
        )
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    driver = get_driver()
    rows = []
    try:
        for variant in VARIANTS:
            rows.append(await run_variant(driver, variant))
    finally:
        driver.close()

    _write_report(rows)


if __name__ == "__main__":
    asyncio.run(main())
