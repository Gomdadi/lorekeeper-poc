# luna high — 신규 실행
- 모델: gpt-5.6-luna, reasoning_effort: high
- 토큰(2화 누적 합산): 입력 25,087 / 출력 15,703 / 합 40,790
- 비용: $0.1193 (입력 $1.00 + 출력 $6.00 /1M)

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 9
  CharacterState: 6
  Item: 3
  Character: 3
  Chapter: 2
  Location: 1
  Organization: 1

### 관계 카운트
  EVIDENCED_BY: 50
  APPEARS_IN: 16
  ESTABLISHED_IN: 6
  HAS_STATE: 6
  HOSTS: 4
  ABOUT: 4
  INVOLVED_WITH: 2

### Event (chapter, story_order)
  1화 so=1.0  멸살법 본편 완결과 김독자의 10년 독서
  1화 so=1.1  멸살법 추천글 논란
  1화 so=1.2  작가 tls123의 감사 메시지와 선물 제안
  1화 so=1.3  멸살법 유료화 결정
  2화 so=1.9  유상아의 정직원 승진
  2화 so=2.0  김독자와 유상아의 퇴근길 지하철 대화
  2화 so=2.1  멸살법 유료화와 작가의 메일
  2화 so=2.2  지하철 정전과 급정거
  2화 so=2.3  무료 서비스 종료와 메인 시나리오 시작

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Item: 멸살법 작가의 첨부 파일
  Item: 유상아의 자전거
  Location: 지하철
  Organization: 대기업 계열사

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 소속=소속 -ABOUT-> 대기업 계열사 (성립 1화)
  김독자: 소유=보유 -ABOUT-> 멸살법 작가의 첨부 파일 (성립 2화)
  김독자: 신분=계약직 (성립 1화)
  유상아: 소속=소속 -ABOUT-> 대기업 계열사 (성립 2화)
  유상아: 소유=상실 -ABOUT-> 유상아의 자전거 (성립 2화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  지하철 HOSTS 김독자와 유상아의 퇴근길 지하철 대화
  지하철 HOSTS 멸살법 유료화와 작가의 메일
  지하철 HOSTS 지하철 정전과 급정거
  지하철 HOSTS 무료 서비스 종료와 메인 시나리오 시작
