import argparse
from pathlib import Path

from naive_indexing.graphrag_runner import run_graphrag
from naive_indexing.neo4j_loader import get_graph_structure, load_to_neo4j
from naive_indexing.validation_generator import generate_validation_set
from naive_indexing.validator import run_validation


def _print_graph_structure(structure: dict):
    print("\n[Nodes]")
    for node in structure["nodes"]:
        desc = (node["description"] or "")[:60]
        print(f"  - {node['name']} ({node['type']}): {desc}")

    print("\n[Edges]")
    for edge in structure["edges"]:
        desc = (edge["description"] or "")[:60]
        print(f"  - {edge['source']} → {edge['target']}: {desc}")


def main():
    parser = argparse.ArgumentParser(description="LoreKeeper Naive Indexing POC")
    parser.add_argument("--input", required=True, help="원고 텍스트 파일 경로")
    parser.add_argument(
        "--skip-indexing", action="store_true", help="GraphRAG indexing 건너뜀"
    )
    args = parser.parse_args()

    manuscript_path = Path(args.input)
    manuscript_text = manuscript_path.read_text(encoding="utf-8")

    # Step 1a: MS GraphRAG indexing
    if not args.skip_indexing:
        print("\n[Step 1a] MS GraphRAG indexing 시작...")
        parquet_paths = run_graphrag(manuscript_path)
        print(f"  → entities      : {parquet_paths['entities']}")
        print(f"  → relationships : {parquet_paths['relationships']}")
    else:
        parquet_paths = {
            "entities": "graphrag_workspace/output/create_final_entities.parquet",
            "relationships": "graphrag_workspace/output/create_final_relationships.parquet",
        }
        print("\n[Step 1a] GraphRAG indexing 건너뜀 (--skip-indexing)")

    # Step 1b: Claude API로 검증 set 생성
    print("\n[Step 1b] Claude API로 검증 set 생성 중...")
    validation_set = generate_validation_set(manuscript_text)
    print(f"  → {len(validation_set)}개 Q&A 생성 완료")

    # Step 2a: Neo4j 로드
    print("\n[Step 2a] Neo4j 로드 중...")
    stats = load_to_neo4j(parquet_paths)
    print(f"  → 노드 {stats['nodes']}개, 엣지 {stats['edges']}개 로드 완료")

    # 그래프 노드/엣지 구조 출력
    structure = get_graph_structure()
    _print_graph_structure(structure)

    # Step 2b: Validation (LLM-as-judge)
    print("\n[Step 2b] LLM-as-judge 검증 중...")
    print("\n  검증 Q&A 목록:")
    for i, qa in enumerate(validation_set):
        print(f"  [{i+1:2}] Q: {qa['query']}")
        print(f"        A: {qa['label']}")

    results = run_validation(validation_set)

    # 최종 결과
    passed = [r for r in results if r["pass"]]
    failed = [r for r in results if not r["pass"]]

    print(f"\n{'='*60}")
    total = len(results)
    print(f"검증 결과: {len(passed)}/{total} 통과 ({len(passed)/total*100:.1f}%)")

    if failed:
        print(f"\n실패 케이스 ({len(failed)}개):")
        for f in failed:
            print(f"  Q : {f['query']}")
            print(f"  예상: {f['label']}")
            print(f"  이유: {f['reason']}")
            print()


if __name__ == "__main__":
    main()
