"""
그래프 추출용 few-shot 예시.

`EXTRACTION_FEW_SHOT`은 LLMEntityRelationExtractor의 프롬프트 템플릿 `{examples}` 자리에
주입된다(ERExtractionTemplate). 무협에 한정하지 않고 웹소설 전반(현대 회귀 판타지, 로맨스
판타지 등)을 커버해 장르 편향 없이 추출 규칙을 가르친다.

가르치는 핵심 규칙:
1. 챕터 마커 `[N화]`를 읽어 Event.chapter/story_order를 채운다.
2. 시간에 따라 바뀌고 검증 대상이 되는 사실(부상, 생사, 소속)은 Character 속성이 아니라
   별도 CharacterState 노드로 만들고, 상태가 바뀌면 기존 노드를 고치지 않고 새 노드를 만든다.
3. CharacterState는 HAS_STATE로 인물과, ESTABLISHED_IN으로 성립 Event와 잇는다.
4. 장소 계층은 LOCATED_IN으로 한 단계씩 잇는다.
5. attribute/value는 영문 snake_case로 정규화해 일관되게 쓴다(status/alive/dead 등).
6. 노드 id는 청크 안에서만 유효한 임시 문자열이며, 관계에서 그 id를 재사용한다.
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
  {"id": "3", "label": "CharacterState", "properties": {"attribute": "left_leg", "value": "lost", "evidence": "강도현은 왼쪽 다리를 잃었다"}}
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
  {"id": "4", "label": "CharacterState", "properties": {"attribute": "left_leg", "value": "prosthetic", "evidence": "의족을 맞춘 강도현은 다시 걷게 되었다"}},
  {"id": "5", "label": "CharacterState", "properties": {"attribute": "allegiance", "value": "rebels", "evidence": "그날 황실을 배신하고 반란군에 가담했다"}}
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
"""
