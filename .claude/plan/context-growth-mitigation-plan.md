# 배경 컨텍스트 선형 증가 억제 — 요약 계층화 + 그래프 덤프 압축

## Context

회차가 진행될수록 novel_context가 선형 증가해 원고 프롬프트를 희석한다. 6화 실측:

| 구성 | 크기(6화 완료 시점) | 증가율 | 비중 |
| --- | --- | --- | --- |
| 그래프 덤프 | 34,060자 (노드 15,292 + **관계 18,763**) | ~5,000자/화 | 93% |
| 회차 요약 누적 | ~2,200자 | ~350자/화 | 7% |

- 덤프 관계 섹션(55%)의 대부분은 HAS_STATE(73)·ESTABLISHED_IN(72)·APPEARS_IN(85) = 230줄 ≈ 15,000자 —
  노드 줄과 정보가 중복되는 배선 나열.
- 덤프 노드 섹션의 88%는 Event·CharacterState description — 오래된 회차 것까지 전문 유지 중.
- 요약은 회차별 3~5문장이 무한 누적(100화 시점 ~3.5만자 전망).

**사용자 결정**: 스코프 = 요약 + 그래프 덤프 둘 다. 요약 방식 = 전역 요약(길이 상한) + 최근 K화 원문.

의도한 결과: 컨텍스트가 회차당 ~5,300자 → 수백 자 수준 증가로 꺾이고(6화 기준 전체 ~34K → ~1만 이하),
직전 회차 연속성(클리프행어·미해결 긴장)과 엔티티 식별 정보는 보존된다.

수정 대상 **2개 파일**: `context.py`(핵심), `indexing.py`(배선). schema.py·extractor.py 무변경
(추출 대상·규칙 불변 — 컨텍스트 *공급* 레이어만 바꿔 회귀 기준선 유지).

---

## A. 요약 계층화 (`context.py` + `indexing.py`)

**저장**: 전역 요약은 `Story` 싱글턴 노드(`MERGE (s:Story {id:'main'})`)의 `summary` 속성.
`__Entity__` 라벨이 없어 resolver 대상 밖. **Story는 모든 Chapter의 부모 노드** — 매 회차
`(Chapter {number})-[:IN_STORY]->(Story)` 를 MERGE (Chunk-[:IN_CHAPTER]->Chapter와 같은
자식→부모 방향 관례). `Chapter.summary`(회차별)는 **현행 유지** — drift 시 재구축 가능한
원천이자 전역 요약의 입력.

1. **`update_global_summary(driver, database, chapter, chapter_summary)` 신설** — 매 회차 끝에 호출.
   `build_llm("high")`로 `기존 전역 요약 + 이번 회차 요약 → 새 전역 요약` 갱신. **첫 회차도 특례 없이
   같은 경로** — 기존 전역 요약이 없으면 빈 입력으로 같은 LLM 생성을 태워 형식·문체를 일관되게 만든다.
   프롬프트 제약:
   - 입력에 있는 내용만(새 사실·해석 금지), 고유명 원문 표기 유지 — `summarize_episode`(context.py:134)의
     기존 제약 문구 재사용
   - **결과 크기는 회차 누적과 무관하게 일정 유지** — 항상 15문장/1,200자 이내로 쓰고, 넘칠 것 같으면
     오래된 사건을 더 굵게 압축해 상한 안에서 최근 전개에 비중을 둔다(누적 증가 금지)
   - 결과를 Story.summary에 SET + IN_STORY MERGE.
2. **`load_chapter_summaries` → 두 소스 로드로 개편**: Story.summary(전역) + 최근 K화
   `Chapter.summary`(`ORDER BY number DESC LIMIT K` 후 오름차순 정렬). `_RECENT_WINDOW = 3` 모듈 상수.
3. **`build_context` 섹션 3개로**: `# 지금까지의 전체 줄거리(압축)` / `# 최근 회차 요약`(회차 번호 표기) /
   `# 지금까지의 그래프`. 빈 소스는 섹션 생략(현행 패턴 유지).
4. `indexing.py` step 8: `summarize_episode` → Chapter.summary 저장(유지) 뒤
   `await update_global_summary(...)` 추가. step 1의 길이 로그에 전역/최근 구성 표시.

크기: 전역 ~1,200자(O(1)) + 최근 3화 ~1,100자 = 요약부 **상수화**.

## B. 그래프 덤프 압축 (`context.py` `dump_graph_text` 재구조화)

관계 나열을 **엔티티 중심 중첩 렌더링**으로 전부 흡수하고(`## 관계` 섹션 폐지), 오래된 서사
노드는 이름만 남긴다. `dump_graph_text(driver, database, current_chapter)`로 시그니처 변경
(최근성 판정 기준).

1. **모든 관계를 인라인으로** — Character·Event를 허브로 조직 (관계 줄 281 → 0줄):
   - **Character 중심**: `HAS_STATE`는 상태 줄 중첩(그 안에 `ESTABLISHED_IN`·`ABOUT` 흡수),
     `RELATED_TO`·`OWNS`·`MEMBER_OF` 등 나머지 인물 관계도 Character 줄 하위에 인라인
     ```
     - (Character) 김독자 — description...
       · 상태: 스물여덟 살 (3화 성립) — description...
       · 상태: 두 성좌의 관심 (6화 성립, 대상: 성좌) — ...
       · 관계: 이현성과 RELATED_TO — ...
     ```
   - **Event 중심**: `HOSTS`(장소)·`APPEARS_IN`(참여 인물) 인라인:
     `— 장소: 지하철 3807칸, 참여: 김독자, 이현성`
   - Location/Organization 줄에 `LOCATED_IN`/`PART_OF` 인라인: `— 상위: 대기업 계열사`
   - 어떤 인라인 규칙에도 안 걸리는 관계 타입이 나타나면 해당 출발 노드 줄에 일반형
     (`· 관계: TYPE → 대상`)으로 인라인 — 별도 섹션은 두지 않는다.
2. **최근성 축약**: `chapter < current_chapter - _RECENT_WINDOW`인 Event·CharacterState는
   **name + chapter(+story_order)만** 렌더 — description과 인라인(장소·참여·대상 등)도 생략하며,
   다른 노드 줄에 인라인으로 등장할 때도 이름만 쓴다. 서술형 name이라 이름만으로도 재추출 방지
   신호는 유지. Character/Item/Location/Organization은 항상 전체 렌더(총 1,172자로 작고 정준
   식별자라 배경의 핵심).
3. **노이즈 제거**: extras에서 `evidence_chunk` 제외(내부 배선용, 노드당 ~18자 × 113노드),
   덤프 쿼리에서 `NOT n:Story` 제외(그래프 덤프 대상 아님 — `_EXCLUDED_LABELS`에도 추가).

증가 모델(변경 후): 오래된 회차는 노드당 ~30자 이름 줄만 남아 **회차당 증가 ~600자**(현행 대비 1/8).
선형 성분이 완전히 사라지진 않음 — 수백 화 스케일에선 검색 기반 선별이 필요하며, 이는
`.claude/docs/chunking-embedding-production-decisions.md`에 프로덕션 결정으로 추가만 해 둔다(구현 안 함).

---

## 실행 순서

0. 이 플랜을 `.claude/plan/context-growth-mitigation-plan.md`로 저장
1. `context.py` A(요약 계층화) → 검증: import + 기존 6개 Chapter.summary로 1→6화 순차 시뮬레이션
   실행해 전역 요약 생성·길이 확인(LLM 6회, 저비용) — Story.summary 실제 저장은 시뮬레이션 마지막
   상태로 1회만
2. `context.py` B(덤프 재구조화) → 검증: 현재 DB로 신구 덤프 비교 (아래)
3. `indexing.py` 배선 → 검증: import + dry 컨텍스트 조립 출력 확인
4. production-decisions 문서에 "컨텍스트 선별(검색 기반)" 결정 항목 추가

## 검증 (재인덱싱 불필요)

1. **크기**: 새 `dump_graph_text(driver, 'neo4j', 7)` 실행 — 34,060자 → 목표 1만 자 이하
2. **무손실 확인**: 모든 도메인 노드가 정확히 1회 등장(라벨별 노드 수와 렌더 줄 수 대조),
   인라인으로 표현된 관계 총수 = 281(현재 관계 총수, 최근성 축약으로 생략된 것은 생략 수로 설명) —
   `## 관계` 섹션은 폐지되므로 잔류 줄 0
3. **최근성 경계**: chapter 4~6 Event/State는 description 포함, 1~3은 이름만인지 육안 확인
4. **전역 요약 품질**: 시뮬레이션 산출물을 1~6화 회차 요약과 대조 — 없는 사실·고유명 왜곡 없는지,
   1,200자 이내인지
5. **회귀(다음 재인덱싱 때)**: 배경 컨텍스트 길이 로그 추이 + 라벨별 노드 수가 기준선
   (Character 9 / Event 39 / State 74 / Item 4 / Org 5 / Loc 5) 대비 급변 없는지 — 컨텍스트 형식
   변화가 추출 품질에 주는 영향은 이 시점에 실측

> 주의: 덤프 형식 변경은 추출 LLM이 보는 배경의 모양을 바꾸므로 경미한 추출 변동 가능성이 있다
> (extractor.py 규칙은 형식 중립이라 문구 수정 불요). 다음 재인덱싱에서 기준선 대조로 감지한다.
