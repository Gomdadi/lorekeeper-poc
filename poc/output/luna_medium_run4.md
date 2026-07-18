# luna medium — run4 (중복방지만 유지)
- gpt-5.6-luna, reasoning=medium
- 토큰: 입력 24,959 / 출력 4,292 / 합 29,251
- 비용: $0.0507

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 6
  CharacterState: 4
  Character: 3
  Chapter: 2
  Organization: 2
  Item: 1
  Location: 1

### 관계 카운트
  EVIDENCED_BY: 33
  APPEARS_IN: 10
  HAS_STATE: 4
  ESTABLISHED_IN: 4
  HOSTS: 2
  ABOUT: 2
  INVOLVED_WITH: 2

### Event (chapter, story_order)
  1화 so=1.0  멸살법 완결과 김독자의 독서
  1화 so=1.1  작가와의 연락 및 선물 약속
  2화 so=1.8  유상아의 정직원 승진
  2화 so=2.0  퇴근길 지하철에서의 김독자와 유상아의 대화
  2화 so=2.1  멸살법 작가의 유료화 안내 메일
  2화 so=2.2  지하철 정차와 메인 시나리오의 시작

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Location: 지하철
  Organization: 대기업 계열사
  Organization: 인사팀

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 소속=소속 -ABOUT-> 대기업 계열사 (성립 1화)
  김독자: 신분=계약직 (성립 1화)
  유상아: 소속=소속 -ABOUT-> 인사팀 (성립 2화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  지하철 HOSTS 퇴근길 지하철에서의 김독자와 유상아의 대화
  지하철 HOSTS 지하철 정차와 메인 시나리오의 시작
