# luna medium — 기존 DB 결과 (재실행 안 함)
- 모델: gpt-5.6-luna, reasoning_effort: medium
- 토큰: 이 run은 미기록. 직전 동일 설정 medium run 실측 참고 → 입력≈24,797 / 출력≈4,275 / 합≈29,072
- 추정 비용(참고): $0.0504 (입력 $1.00 + 출력 $6.00 /1M)

## 그래프 덤프
### 라벨 카운트
  Chunk: 89
  Event: 10
  CharacterState: 6
  Character: 3
  Chapter: 2
  Item: 2
  Organization: 2
  Location: 1

### 관계 카운트
  EVIDENCED_BY: 75
  APPEARS_IN: 19
  HOSTS: 6
  HAS_STATE: 6
  ESTABLISHED_IN: 6
  ABOUT: 3
  INVOLVED_WITH: 2
  RELATED_TO: 1

### Event (chapter, story_order)
  1화 so=1.0  멸살법 본편 완결 확인
  1화 so=1.1  멸살법 추천글 논란
  1화 so=1.2  tls123의 감사 연락과 선물 약속
  1화 so=1.3  멸살법 유료화 통보
  2화 so=2.0  지하철에서 김독자와 유상아의 만남
  2화 so=2.1  지하철에서의 회사 대화와 유상아의 승진
  2화 so=2.2  김독자의 독자 정체성 수용
  2화 so=2.3  멸살법 유료화 메일과 작품 소실
  2화 so=2.4  지하철 정전과 급정거
  2화 so=2.5  메인 시나리오 개시

### Character / Item / Location / Organization
  Character: tls123
  Character: 김독자
  Character: 유상아
  Item: 멸망한 세계에서 살아남는 세 가지 방법
  Item: 작가의 특별한 선물
  Location: 지하철
  Organization: 대기업 계열사
  Organization: 인사팀

### CharacterState (attribute=value, ABOUT, 성립 회차)
  김독자: 소속=소속 -ABOUT-> 대기업 계열사 (성립 2화)
  김독자: 소속=소속 -ABOUT-> 대기업 계열사 (성립 1화)
  김독자: 신분=계약직 (성립 2화)
  김독자: 신분=계약직 (성립 1화)
  유상아: 소속=소속 -ABOUT-> 인사팀 (성립 2화)
  유상아: 신분=정직원 (성립 2화)

### INVOLVED_WITH / RELATED_TO / HOSTS
  tls123 -[저자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[독자]-> 멸망한 세계에서 살아남는 세 가지 방법
  김독자 -[동료]-> 유상아
  지하철 HOSTS 지하철에서 김독자와 유상아의 만남
  지하철 HOSTS 지하철에서의 회사 대화와 유상아의 승진
  지하철 HOSTS 김독자의 독자 정체성 수용
  지하철 HOSTS 멸살법 유료화 메일과 작품 소실
  지하철 HOSTS 지하철 정전과 급정거
  지하철 HOSTS 메인 시나리오 개시
