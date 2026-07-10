"""
그래프 추출용 few-shot 예시.

`EXTRACTION_FEW_SHOT`은 LLMEntityRelationExtractor의 프롬프트 템플릿 `{examples}` 자리에
주입된다(ERExtractionTemplate). 무협에 한정하지 않고 웹소설 전반(현대 회귀 판타지, 로맨스
판타지 등)을 커버해 장르 편향 없이 추출 규칙을 가르친다.

가르치는 핵심 규칙:
1. 챕터 마커 `[N화]`를 읽어 Event.chapter/story_order를 채운다.
2. 시간에 따라 바뀌고 검증 대상이 되는 사실(부상, 생사, 소속, 능력·무공, 소지품)은 Character 속성이
   아니라 별도 CharacterState 노드로 만들고, 상태가 바뀌면 기존 노드를 고치지 않고 새 노드를 만든다.
3. CharacterState는 HAS_STATE로 인물과, ESTABLISHED_IN으로 성립 Event와 잇는다.
4. 장소 계층은 LOCATED_IN으로 한 단계씩 잇는다.
5. attribute/value는 한국어로 짧게 쓰되, 같은 축은 매번 같은 표현·같은 입도로 통일한다(생사/생존/사망 등).
6. 노드 id는 청크 안에서만 유효한 임시 문자열이며, 관계에서 그 id를 재사용한다.
7. 조직·세력·단체는 Organization 노드로 만들고 인물의 현재 소속은 MEMBER_OF로 잇는다. 소속이 바뀌는
   시점 변화는 CharacterState(attribute='소속')로 별도 기록한다 — 현재 연결은 MEMBER_OF, 변화 이력은 상태.
8. 인물↔인물 관계는 RELATED_TO로 잇고 종류는 type 속성에 담는다 — 사제/동맹/적대/혈연/연인뿐 아니라
   작가-독자·동료·사수처럼 직업적·일상적 관계도 포함한다. 두 인물이 상호작용하거나 서로를 특정한
   관계로 대하면 적극적으로 추출한다(단순히 함께 등장한 것만으로는 만들지 않는다).
9. 능력·무공은 CharacterState(attribute='무공', value=성취 수준)로, 소지품은 CharacterState
   (attribute='OO_소유', value='보유'/'상실')로 표현한다. 소지품이 인물 간 이동하면 넘긴 인물에 '상실',
   받은 인물에 '보유'로 각각 별도 CharacterState를 만든다. 능력·소지품은 별도 노드로 만들지 않는다.
"""

EXTRACTION_FEW_SHOT = r"""
예시 1 (현대 회귀 판타지)

입력 텍스트:
[3화]
게이트 붕괴 사고에서 강도현은 왼쪽 다리를 잃었다. 서울 각성자 협회는 그를 즉시 후송했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "강도현", "description": "각성자"}},
  {"id": "1", "label": "Location", "properties": {"name": "서울 각성자 협회", "description": "각성자를 관리하는 기관"}},
  {"id": "2", "label": "Event", "properties": {"title": "게이트 붕괴 사고", "description": "게이트가 붕괴하며 강도현이 부상당함", "chapter": 3, "story_order": 3.0}},
  {"id": "3", "label": "CharacterState", "properties": {"attribute": "왼다리", "value": "상실", "evidence": "강도현은 왼쪽 다리를 잃었다"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "2", "properties": {}},
  {"type": "HOSTS", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "3", "end_node_id": "2", "properties": {}}
]}

예시 2 (로맨스 판타지 — 상태 변화 시 새 CharacterState 노드 생성, 장소 계층)

입력 텍스트:
[8화]
의족을 맞춘 강도현은 다시 걷게 되었다. 그는 왕성의 대연회장에서 열린 연회에 참석했고, 그날 황실을 배신하고 반란군에 가담했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "강도현", "description": "각성자"}},
  {"id": "1", "label": "Location", "properties": {"name": "대연회장", "description": "왕성 내부의 연회 공간"}},
  {"id": "2", "label": "Location", "properties": {"name": "왕성", "description": "왕국의 중심 성"}},
  {"id": "3", "label": "Event", "properties": {"title": "대연회장 연회와 반란 가담", "description": "강도현이 연회에 참석한 뒤 반란군에 가담함", "chapter": 8, "story_order": 8.0}},
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "왼다리", "value": "의족", "evidence": "의족을 맞춘 강도현은 다시 걷게 되었다"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "소속", "value": "반란군", "evidence": "그날 황실을 배신하고 반란군에 가담했다"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "HOSTS", "start_node_id": "1", "end_node_id": "3", "properties": {}},
  {"type": "LOCATED_IN", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "4", "end_node_id": "3", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "5", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "5", "end_node_id": "3", "properties": {}}
]}

예시 3 (무협 — 조직 소속(Organization/MEMBER_OF), 사제 관계(RELATED_TO), 무공을 CharacterState로)

입력 텍스트:
[12화]
청운은 화산파에 정식 입문했다. 그의 사부 검선 진자강이 매화검법을 전수했고, 청운은 마침내 매화검법을 대성했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "청운", "description": "화산파 후기지수"}},
  {"id": "1", "label": "Character", "properties": {"name": "진자강", "description": "검선이라 불리는 검객"}},
  {"id": "2", "label": "Organization", "properties": {"name": "화산파", "description": "정파 검문"}},
  {"id": "3", "label": "Event", "properties": {"title": "청운의 화산파 입문과 매화검법 대성", "description": "청운이 화산파에 입문해 진자강에게 매화검법을 전수받고 대성함", "chapter": 12, "story_order": 12.0}},
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "소속", "value": "화산파", "evidence": "청운은 화산파에 정식 입문했다"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "무공", "value": "매화검법 대성", "evidence": "청운은 마침내 매화검법을 대성했다"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "3", "properties": {}},
  {"type": "MEMBER_OF", "start_node_id": "0", "end_node_id": "2", "properties": {}},
  {"type": "RELATED_TO", "start_node_id": "0", "end_node_id": "1", "properties": {"type": "사제", "description": "청운이 진자강의 제자로 매화검법을 전수받음"}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "4", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "4", "end_node_id": "3", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "5", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "5", "end_node_id": "3", "properties": {}}
]}

예시 4 (로맨스 판타지 — 소지품 소유 이동을 두 CharacterState로: 넘긴 인물 '상실' + 받은 인물 '보유')

입력 텍스트:
[15화]
황녀 레아나는 자신의 성물인 빛의 성배를 기사 카일에게 건넸다. 성배를 받은 카일은 그것을 지키기로 맹세했다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "레아나", "description": "제국의 황녀"}},
  {"id": "1", "label": "Character", "properties": {"name": "카일", "description": "황녀를 호위하는 기사"}},
  {"id": "2", "label": "Event", "properties": {"title": "빛의 성배 양도", "description": "레아나가 빛의 성배를 카일에게 넘기고 카일이 수호를 맹세함", "chapter": 15, "story_order": 15.0}},
  {"id": "3", "label": "CharacterState", "properties": {"attribute": "빛의 성배_소유", "value": "상실", "evidence": "레아나는 자신의 성물인 빛의 성배를 기사 카일에게 건넸다"}},
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "빛의 성배_소유", "value": "보유", "evidence": "성배를 받은 카일은 그것을 지키기로 맹세했다"}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "2", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "0", "end_node_id": "3", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "3", "end_node_id": "2", "properties": {}},
  {"type": "HAS_STATE", "start_node_id": "1", "end_node_id": "4", "properties": {}},
  {"type": "ESTABLISHED_IN", "start_node_id": "4", "end_node_id": "2", "properties": {}}
]}

예시 5 (현대 드라마 — 직업적 인물 관계를 RELATED_TO로 적극 추출)

입력 텍스트:
[7화]
편집자 한소희는 신인 작가 도윤을 처음 만났다. 도윤은 자신의 첫 원고를 한소희에게 맡겼고, 두 사람은 담당 편집자와 작가로 함께 일하게 되었다.

출력:
{"nodes": [
  {"id": "0", "label": "Character", "properties": {"name": "한소희", "description": "출판사 편집자"}},
  {"id": "1", "label": "Character", "properties": {"name": "도윤", "description": "신인 작가"}},
  {"id": "2", "label": "Event", "properties": {"title": "한소희와 도윤의 첫 만남", "description": "편집자 한소희가 신인 작가 도윤의 원고를 맡아 담당 편집자가 됨", "chapter": 7, "story_order": 7.0}}
],
"relationships": [
  {"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "2", "properties": {}},
  {"type": "APPEARS_IN", "start_node_id": "1", "end_node_id": "2", "properties": {}},
  {"type": "RELATED_TO", "start_node_id": "0", "end_node_id": "1", "properties": {"type": "담당 편집자", "description": "한소희가 도윤의 원고를 담당하는 편집자"}}
]}
"""
