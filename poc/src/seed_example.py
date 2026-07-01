"""
더미 예시 그래프 시드 스크립트 (무협 장르).
schema.py의 스키마(Character/Location/Event/CharacterState)와 일치하는 예시 데이터를 Neo4j에 삽입한다.
여러 사건·인물·장소로 상태 조회(CharacterState)와 위치 타임라인(APPEARS_IN+HOSTS) 둘 다 검증한다.
Neo4j Browser(http://localhost:7474)에서 그래프를 시각화할 수 있다.

사용법:
    python src/seed_example.py           # 기존 데이터 유지하고 삽입
    python src/seed_example.py --reset   # 전체 초기화 후 삽입
"""

import sys
from client import get_driver

# 예시 데이터: 장소 계층(중원 > 화산/낙양성 > 화산파 대전/낙양 객잔) + 인물 2명 + 사건 3개
CYPHER_SEED = """
// 장소 생성 (공간 계층: LOCATED_IN)
MERGE (jungwon:Location {name: '중원'})
  SET jungwon.description = '무림 전체를 아우르는 중원 대륙'
MERGE (hwasan:Location {name: '화산'})
  SET hwasan.description = '화산파가 자리 잡은 영산'
MERGE (hwasan_hall:Location {name: '화산파 대전'})
  SET hwasan_hall.description = '화산파의 본거지이자 문파 회합 장소'
MERGE (nakyang:Location {name: '낙양성'})
  SET nakyang.description = '중원 한복판의 번화한 성'
MERGE (nakyang_inn:Location {name: '낙양 객잔'})
  SET nakyang_inn.description = '낙양성 저잣거리의 정보가 모이는 객잔'

MERGE (hwasan)-[:LOCATED_IN]->(jungwon)
MERGE (nakyang)-[:LOCATED_IN]->(jungwon)
MERGE (hwasan_hall)-[:LOCATED_IN]->(hwasan)
MERGE (nakyang_inn)-[:LOCATED_IN]->(nakyang)

// 인물 생성
MERGE (jinsocheon:Character {name: '진소천'})
  SET jinsocheon.description = '화산파의 젊은 검객'
MERGE (baekriyeon:Character {name: '백리연'})
  SET baekriyeon.description = '정보를 다루는 강호의 여협'

// 사건 생성 (chapter는 연재 순서, story_order는 명시적 시간 묘사가 없어 chapter와 동일값)
MERGE (e1:Event {title: '화산파 혈사', chapter: 3})
  SET e1.description = '정체불명의 무리가 화산파 대전을 습격함',
      e1.story_order = 3.0
MERGE (e2:Event {title: '낙양 객잔 잠입', chapter: 5})
  SET e2.description = '백리연이 낙양 객잔에 잠입해 배후 세력의 정보를 수집함',
      e2.story_order = 5.0
MERGE (e3:Event {title: '낙양성 회합', chapter: 7})
  SET e3.description = '부상에서 회복한 진소천이 낙양성으로 이동해 정파 회합에 참석함',
      e3.story_order = 7.0

// 사건-장소 연결 (HOSTS: Location -> Event)
MERGE (hwasan_hall)-[:HOSTS]->(e1)
MERGE (nakyang_inn)-[:HOSTS]->(e2)
MERGE (nakyang)-[:HOSTS]->(e3)

// 인물-사건 연결 (APPEARS_IN: Character -> Event)
MERGE (jinsocheon)-[:APPEARS_IN]->(e1)
MERGE (baekriyeon)-[:APPEARS_IN]->(e1)
MERGE (baekriyeon)-[:APPEARS_IN]->(e2)
MERGE (jinsocheon)-[:APPEARS_IN]->(e3)

// 상태 변화 기록 (CharacterState: 진소천이 화산파 혈사에서 오른팔을 잃음)
CREATE (s1:CharacterState {attribute: 'right_arm', value: 'lost', evidence: '진소천의 오른팔이 적의 칼에 잘려나갔다'})
CREATE (jinsocheon)-[:HAS_STATE]->(s1)
CREATE (s1)-[:ESTABLISHED_IN]->(e1)
"""

CYPHER_RESET = "MATCH (n) DETACH DELETE n"

# 검증 쿼리 1: 진소천의 right_arm 상태 조회 (기대값: lost, 3화 근거)
CYPHER_CHECK_STATE = """
MATCH (c:Character {name:'진소천'})-[:HAS_STATE]->(s:CharacterState {attribute:'right_arm'})
      -[:ESTABLISHED_IN]->(e:Event)
RETURN s.value AS value, e.chapter AS as_of_chapter, s.evidence AS evidence
ORDER BY e.chapter DESC
LIMIT 1
"""

# 검증 쿼리 2: 진소천의 위치 타임라인 (기대값: (3, 화산파 대전), (7, 낙양성))
CYPHER_CHECK_LOCATION_TIMELINE = """
MATCH (c:Character {name:'진소천'})-[:APPEARS_IN]->(e:Event)<-[:HOSTS]-(l:Location)
RETURN e.chapter AS chapter, l.name AS location
ORDER BY e.chapter
"""


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

        # 검증 1: 상태 조회
        print("\n--- 진소천의 right_arm 상태 (기대값: lost, 3화 근거) ---")
        for record in session.run(CYPHER_CHECK_STATE):
            print(f"  value={record['value']}, as_of_chapter={record['as_of_chapter']}, evidence={record['evidence']}")

        # 검증 2: 위치 타임라인
        print("\n--- 진소천의 위치 타임라인 (기대값: (3, 화산파 대전), (7, 낙양성)) ---")
        for record in session.run(CYPHER_CHECK_LOCATION_TIMELINE):
            print(f"  chapter={record['chapter']}, location={record['location']}")

    driver.close()
    print("\nNeo4j Browser에서 확인: http://localhost:7474")
    print("쿼리: MATCH (n)-[r]->(m) RETURN n, r, m")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    seed(reset=reset)
