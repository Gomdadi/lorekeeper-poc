# luna high — run3 (프롬프트 보강 후)
- gpt-5.6-luna, reasoning=high
- 토큰: 입력 26,410 / 출력 15,936 / 합 42,346
- 비용: $0.1220

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 6
  Item: 4
  CharacterState: 4
  Character: 3
  Chapter: 2
  Location: 1
  Organization: 1

### 관계 카운트
  EVIDENCED_BY: 38
  APPEARS_IN: 10
  HAS_STATE: 4
  ESTABLISHED_IN: 4
  INVOLVED_WITH: 4
  HOSTS: 2
  ABOUT: 2
  RELATED_TO: 1

### Event (chapter, story_order)
  1화 so=1.0  멸살법 본편 완결과 김독자의 10년 독서
  1화 so=1.1  김독자의 멸살법 추천글 논란
  1화 so=1.2  tls123의 감사 연락과 유료화 예고
  2화 so=1.9  유상아의 정직원 승진
  2화 so=2.0  김독자와 유상아의 퇴근길 대화
  2화 so=2.1  지하철 정전과 메인 시나리오 시작

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망 이후의 세카이
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Item: 멸살법 추천글
  Item: 유상아의 자전거
  Location: 지하철
  Organization: 인사팀

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 신분=계약직 (성립 1화)
  유상아: 소속=소속 -ABOUT-> 인사팀 (성립 2화)
  유상아: 소유=상실 -ABOUT-> 유상아의 자전거 (성립 2화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  김독자 -[None]-> 멸망 이후의 세카이
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[저자]-> 멸살법 추천글
  김독자 -[None]-> 유상아
  지하철 HOSTS 김독자와 유상아의 퇴근길 대화
  지하철 HOSTS 지하철 정전과 메인 시나리오 시작
