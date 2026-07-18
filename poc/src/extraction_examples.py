"""
그래프 추출용 few-shot 예시.

`EXTRACTION_FEW_SHOT`은 LLMEntityRelationExtractor의 프롬프트 템플릿 `{examples}` 자리에
주입된다(ERExtractionTemplate). 무협에 한정하지 않고 웹소설 전반(현대 회귀 판타지, 로맨스
판타지 등)을 커버해 장르 편향 없이 추출 규칙을 가르친다.

각 예시가 시연하는 규칙은 예시 헤더에 표기했다. 규칙의 원문(권위)은 extractor.py의 도메인 추출
규칙과 schema.py의 노드/관계 description에 있으므로, 여기서는 중복 나열하지 않는다.
"""

EXTRACTION_FEW_SHOT = r"""
예시 1 (로맨스 판타지 — 소속을 Organization + CharacterState(소속) + ABOUT로, 장소 계층)

입력 텍스트:
[chapter:8]
[C0] 의족을 맞춘 강도현은 다시 걷게 되었다. [C1] 그는 왕성의 대연회장에서 열린 연회에 참석했고, 그날 황실을 배신하고 반란군에 가담했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "강도현", "description": "각성자"}},
  {"id": "1", "label": "Location", "properties": {"name": "대연회장", "description": "왕성 내부의 연회 공간"}},
  {"id": "2", "label": "Location", "properties": {"name": "왕성", "description": "왕국의 중심 성"}},
  {"id": "3", "label": "Organization", "properties": {"name": "반란군", "description": "황실에 맞서는 세력"}},
  {"id": "4", "label": "Event", "properties": {"title": "대연회장 연회와 반란 가담", "description": "강도현이 연회에 참석한 뒤 반란군에 가담함", "chapter": 8, "story_order": 8.0, "evidence_chunk": "C1"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "왼다리", "value": "의족", "evidence": "의족을 맞춘 강도현은 다시 걷게 되었다", "evidence_chunk": "C0"}},
  {"id": "6", "label": "CharacterState", "properties": {"attribute": "소속", "value": "소속", "evidence": "그날 황실을 배신하고 반란군에 가담했다", "evidence_chunk": "C1"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "HOSTS", "start_node_id": "1", "end_node_id": "4", "properties": {}},
  {"type": "LOCATED_IN", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "5", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "5", "end_node_id": "4", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "6", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "6", "end_node_id": "4", "properties": {}},
  {"type": "ABOUT", "start_node_id": "6", "end_node_id": "3", "properties": {}}
]}

예시 2 (무협 — 같은 회차 두 사건을 story_order 12.0/12.1로 순서 부여, 소속(Organization+ABOUT), 사제 관계, 무공)

입력 텍스트:
[chapter:12]
[C0] 청운은 화산파에 정식 입문했다. [C1] 이후 그의 사부 검선 진자강이 매화검법을 전수했고, 청운은 마침내 매화검법을 대성했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "청운", "description": "화산파 후기지수"}},
  {"id": "1", "label": "Character", "properties": {"name": "진자강", "description": "검선이라 불리는 검객"}},
  {"id": "2", "label": "Organization", "properties": {"name": "화산파", "description": "정파 검문"}},
  {"id": "3", "label": "Event", "properties": {"title": "청운의 화산파 입문", "description": "청운이 화산파에 정식 입문함", "chapter": 12, "story_order": 12.0, "evidence_chunk": "C0"}},
  {"id": "4", "label": "Event", "properties": {"title": "매화검법 전수와 대성", "description": "진자강이 청운에게 매화검법을 전수하고 청운이 대성함", "chapter": 12, "story_order": 12.1, "evidence_chunk": "C1"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "소속", "value": "소속", "evidence": "청운은 화산파에 정식 입문했다", "evidence_chunk": "C0"}},
  {"id": "6", "label": "CharacterState", "properties": {"attribute": "무공", "value": "매화검법 대성", "evidence": "청운은 마침내 매화검법을 대성했다", "evidence_chunk": "C1"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "4", "properties": {}},
  {"type": "RELATED_TO", "start_node_id": "0", "end_node_id": "1", "properties": {"type": "사제", "description": "청운이 진자강의 제자로 매화검법을 전수받음"}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "5", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "5", "end_node_id": "3", "properties": {}},
  {"type": "ABOUT", "start_node_id": "5", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "6", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "6", "end_node_id": "4", "properties": {}}
]}

예시 3 (로맨스 판타지 — Item(정체성) + 소유 이동을 두 CharacterState(소유) + ABOUT로: 넘긴 인물 '상실' + 받은 인물 '보유')

입력 텍스트:
[chapter:15]
[C0] 황녀 레아나는 자신의 성물인 빛의 성배를 기사 카일에게 건넸다. [C1] 성배를 받은 카일은 그것을 지키기로 맹세했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "레아나", "description": "제국의 황녀"}},
  {"id": "1", "label": "Character", "properties": {"name": "카일", "description": "황녀를 호위하는 기사"}},
  {"id": "2", "label": "Item", "properties": {"name": "빛의 성배", "description": "황녀 레아나의 성물"}},
  {"id": "3", "label": "Event", "properties": {"title": "빛의 성배 양도", "description": "레아나가 빛의 성배를 카일에게 넘기고 카일이 수호를 맹세함", "chapter": 15, "story_order": 15.0, "evidence_chunk": "C0,C1"}},
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "소유", "value": "상실", "evidence": "레아나는 자신의 성물인 빛의 성배를 기사 카일에게 건넸다", "evidence_chunk": "C0"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "소유", "value": "보유", "evidence": "성배를 받은 카일은 그것을 지키기로 맹세했다", "evidence_chunk": "C1"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "3", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "4", "end_node_id": "3", "properties": {}},
  {"type": "ABOUT", "start_node_id": "4", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "1", "end_node_id": "5", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "5", "end_node_id": "3", "properties": {}},
  {"type": "ABOUT", "start_node_id": "5", "end_node_id": "2", "properties": {}}
]}

예시 4 (현대 드라마 — 반례 종합: Location/Organization+ABOUT, 소속 vs 신분 분리, 엑스트라·일시적 상태 배제)

입력 텍스트:
[chapter:7]
[C0] 대한물산 인사팀에서 계약직으로 일하던 유나는 지훈과 함께 퇴근길 지하철을 탔다. [C1] 지하철이 급정거하자 유나가 지훈의 팔을 세게 붙잡았고, 옆자리 할머니와 아이가 놀라 웅성거렸다. [C2] 그 사고로 지훈은 갈비뼈가 부러졌고, 유나는 그달 정직원으로 전환되었다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "유나", "description": "대한물산 인사팀 사무직"}},
  {"id": "1", "label": "Character", "properties": {"name": "지훈", "description": "유나의 동료"}},
  {"id": "2", "label": "Location", "properties": {"name": "지하철", "description": "퇴근길 지하철"}},
  {"id": "3", "label": "Organization", "properties": {"name": "대한물산", "description": "유나가 다니는 회사"}},
  {"id": "4", "label": "Event", "properties": {"title": "지하철 급정거 사고", "description": "지하철이 급정거하며 지훈이 갈비뼈를 다치고 유나가 정직원으로 전환된 계기", "chapter": 7, "story_order": 7.0, "evidence_chunk": "C1,C2"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "신분", "value": "계약직", "evidence": "대한물산 인사팀에서 계약직으로 일하던 유나", "evidence_chunk": "C0"}},
  {"id": "6", "label": "CharacterState", "properties": {"attribute": "소속", "value": "소속", "evidence": "대한물산 인사팀에서 계약직으로 일하던 유나", "evidence_chunk": "C0"}},
  {"id": "7", "label": "CharacterState", "properties": {"attribute": "신분", "value": "정직원", "evidence": "유나는 그달 정직원으로 전환되었다", "evidence_chunk": "C2"}},
  {"id": "8", "label": "CharacterState", "properties": {"attribute": "갈비뼈", "value": "골절", "evidence": "그 사고로 지훈은 갈비뼈가 부러졌고", "evidence_chunk": "C2"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "4", "properties": {}},
  {"type": "HOSTS", "start_node_id": "2", "end_node_id": "4", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "5", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "5", "end_node_id": "4", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "6", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "6", "end_node_id": "4", "properties": {}},
  {"type": "ABOUT", "start_node_id": "6", "end_node_id": "3", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "7", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "7", "end_node_id": "4", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "1", "end_node_id": "8", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "8", "end_node_id": "4", "properties": {}}
]}
(주의: 옆자리 할머니·아이는 지나가는 엑스트라이므로 Character로 만들지 않는다. 유나가 지훈의 팔을 붙잡은 것은 일시적 접촉이므로 CharacterState로 만들지 않는다. 지속되는 부상인 지훈의 '갈비뼈=골절'만 만든다.)

예시 5 (작중 창작물 — Item + INVOLVED_WITH(저자/독자), 작가-독자를 사람-사람 관계로 평탄화하지 않음)

입력 텍스트:
[chapter:2]
[C0] 무명 작가 해무가 쓴 웹소설 <탑의 문>은 10년째 연재 중이었다. [C1] 준호는 그 소설의 유일한 독자로, 매 회차를 빠짐없이 읽었다. [C2] 준호는 가끔 지하철에서 심심풀이로 유행하는 아무 소설이나 제목만 흘려보기도 했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "해무", "description": "무명 웹소설 작가"}},
  {"id": "1", "label": "Character", "properties": {"name": "준호", "description": "탑의 문의 애독자"}},
  {"id": "2", "label": "Item", "properties": {"name": "탑의 문", "description": "해무가 10년째 연재 중인 웹소설"}},
  {"id": "3", "label": "Event", "properties": {"title": "탑의 문 연재와 준호의 독서", "description": "해무가 탑의 문을 연재하고 준호가 유일한 독자로 매 회차를 읽음", "chapter": 2, "story_order": 2.0, "evidence_chunk": "C0,C1"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "3", "properties": {}},
  {"type": "INVOLVED_WITH", "start_node_id": "0", "end_node_id": "2", "properties": {"role": "저자"}},
  {"type": "INVOLVED_WITH", "start_node_id": "1", "end_node_id": "2", "properties": {"role": "독자"}}
]}
(주의 1: 해무와 준호는 '작가-독자'이지만 작품(탑의 문)에 대한 각자의 역할이므로 두 인물 사이에 RELATED_TO를 만들지 않는다.
주의 2: C2의 '유행하는 아무 소설'은 심심풀이로 제목만 흘려본 소품·농담성 언급이라 Item으로 만들지 않는다 — 준호가 실제로 몰입해 읽는 <탑의 문>만 만든다.)
"""
