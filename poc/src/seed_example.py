"""
더미 예시 그래프 시드 스크립트.
schema.py의 더미 스키마와 일치하는 예시 데이터를 Neo4j에 삽입한다.
Neo4j Browser(http://localhost:7474)에서 그래프를 시각화할 수 있다.

사용법:
    python src/seed_example.py           # 기존 데이터 유지하고 삽입
    python src/seed_example.py --reset   # 전체 초기화 후 삽입
"""

import sys
from client import get_driver

# 예시 데이터: 웹소설 3화 — 북부 요새 전투
CYPHER_SEED = """
// 인물 생성
MERGE (kael:Character {name: '카엘'})
  SET kael.status = 'alive'
MERGE (riona:Character {name: '리오나'})
  SET riona.status = 'alive'

// 장소 생성
MERGE (fortress:Location {name: '북부 요새'})

// 사건 생성
MERGE (battle:Event {title: '북부 요새 전투', chapter: 3})

// 관계 연결
MERGE (kael)-[:APPEARS_IN]->(battle)
MERGE (riona)-[:APPEARS_IN]->(battle)
MERGE (kael)-[:LOCATED_AT]->(fortress)
MERGE (battle)-[:INVOLVES]->(kael)
MERGE (battle)-[:INVOLVES]->(riona)
"""

CYPHER_RESET = "MATCH (n) DETACH DELETE n"


def seed(reset: bool = False) -> None:
    driver = get_driver()
    with driver.session() as session:
        if reset:
            session.run(CYPHER_RESET)
            print("기존 데이터 초기화 완료")

        session.run(CYPHER_SEED)
        print("예시 그래프 삽입 완료")

        # 삽입 결과 확인
        result = session.run("MATCH (n) RETURN labels(n) AS label, count(*) AS cnt")
        print("\n--- 노드 현황 ---")
        for record in result:
            print(f"  {record['label'][0]}: {record['cnt']}개")

    driver.close()
    print("\nNeo4j Browser에서 확인: http://localhost:7474")
    print("쿼리: MATCH (n)-[r]->(m) RETURN n, r, m")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    seed(reset=reset)
