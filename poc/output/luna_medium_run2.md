# luna medium — 신규 run2
- gpt-5.6-luna, reasoning=medium
- 토큰: 입력 25,009 / 출력 5,673 / 합 30,682
- 비용: $0.0590

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 6
  CharacterState: 4
  Character: 3
  Chapter: 2
  Location: 1
  Organization: 1
  Item: 1

### 관계 카운트
  EVIDENCED_BY: 41
  APPEARS_IN: 10
  ESTABLISHED_IN: 4
  HAS_STATE: 4
  HOSTS: 3
  ABOUT: 2
  INVOLVED_WITH: 2
  RELATED_TO: 1

### Event (chapter, story_order)
  1화 so=1.0  멸살법 완결과 김독자의 독서
  1화 so=1.1  멸살법 추천글 논란
  1화 so=1.2  작가와의 쪽지와 멸살법 유료화
  2화 so=2.0  퇴근길 지하철에서 김독자와 유상아의 대화
  2화 so=2.1  멸살법 유료화와 게시판 소멸
  2화 so=2.2  지하철 정전과 메인 시나리오 시작

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Location: 지하철
  Organization: 대기업 계열사

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 소속=소속 -ABOUT-> 대기업 계열사 (성립 1화)
  김독자: 신분=계약직 (성립 1화)
  유상아: 소속=소속 -ABOUT-> 대기업 계열사 (성립 2화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[동료]-> 유상아
  지하철 HOSTS 퇴근길 지하철에서 김독자와 유상아의 대화
  지하철 HOSTS 멸살법 유료화와 게시판 소멸
  지하철 HOSTS 지하철 정전과 메인 시나리오 시작
