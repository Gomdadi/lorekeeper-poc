# luna high — run4 (중복방지만 유지)
- gpt-5.6-luna, reasoning=high
- 토큰: 입력 25,333 / 출력 15,684 / 합 41,017
- 비용: $0.1194

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 9
  CharacterState: 4
  Character: 3
  Chapter: 2
  Item: 2
  Location: 1
  Organization: 1

### 관계 카운트
  EVIDENCED_BY: 57
  APPEARS_IN: 15
  HOSTS: 4
  HAS_STATE: 4
  ESTABLISHED_IN: 4
  INVOLVED_WITH: 4
  ABOUT: 2
  RELATED_TO: 1

### Event (chapter, story_order)
  1화 so=1.0  멸살법 완결 확인
  1화 so=1.1  멸살법 추천글과 악성 댓글
  1화 so=1.2  tls123의 감사 메시지와 선물 약속
  1화 so=1.3  멸살법 유료화 통보
  2화 so=1.9  유상아의 정직원 승진
  2화 so=2.0  퇴근길 지하철에서의 김독자와 유상아의 대화
  2화 so=2.1  멸살법 유료화와 게시판 소실
  2화 so=2.2  지하철 급정거와 혼란
  2화 so=2.3  메인 시나리오의 시작

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Item: 작가의 특별한 선물
  Location: 지하철
  Organization: 대기업 계열사

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 소속=소속 -ABOUT-> 대기업 계열사 (성립 1화)
  김독자: 신분=계약직 (성립 1화)
  유상아: 소속=소속 -ABOUT-> 대기업 계열사 (성립 2화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  tls123 -[발송자]-> 작가의 특별한 선물
  김독자 -[수령인]-> 작가의 특별한 선물
  김독자 -[동료]-> 유상아
  지하철 HOSTS 퇴근길 지하철에서의 김독자와 유상아의 대화
  지하철 HOSTS 멸살법 유료화와 게시판 소실
  지하철 HOSTS 지하철 급정거와 혼란
  지하철 HOSTS 메인 시나리오의 시작
