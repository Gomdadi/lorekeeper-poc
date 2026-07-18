# luna medium — run3 (프롬프트 보강 후)
- gpt-5.6-luna, reasoning=medium
- 토큰: 입력 26,323 / 출력 3,952 / 합 30,275
- 비용: $0.0500

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 8
  Character: 3
  Chapter: 2
  CharacterState: 2
  Item: 1
  Location: 1

### 관계 카운트
  EVIDENCED_BY: 39
  APPEARS_IN: 12
  HOSTS: 3
  INVOLVED_WITH: 2
  ESTABLISHED_IN: 2
  HAS_STATE: 2
  RELATED_TO: 1

### Event (chapter, story_order)
  1화 so=1.0  멸살법 완결 확인과 10년 독서
  1화 so=1.1  멸살법 추천글과 비난
  1화 so=1.2  작가의 감사 연락과 유료화 통보
  2화 so=1.8  유상아의 정직원 승진
  2화 so=2.0  퇴근길 지하철에서의 김독자와 유상아의 대화
  2화 so=2.1  작가의 메일과 멸살법 게시판 소멸
  2화 so=2.2  지하철 급정거와 정전
  2화 so=2.3  메인 시나리오의 시작

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Location: 지하철

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 신분=계약직 (성립 1화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[동료]-> 김독자
  지하철 HOSTS 퇴근길 지하철에서의 김독자와 유상아의 대화
  지하철 HOSTS 지하철 급정거와 정전
  지하철 HOSTS 메인 시나리오의 시작
