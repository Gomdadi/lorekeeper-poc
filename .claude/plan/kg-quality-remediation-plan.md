# KG 추출 품질 보완 플랜 (kg-verification-ch1-6.md 후속)

## Context

`.claude/docs/kg-verification-ch1-6.md`에서 1~6화 원문과 그래프를 전수 대조한 결과, 심각도 S1 2건·S2 4건·S3 6건이 확인됐다. 이 플랜은 그중 **resolver를 제외한** 추출 품질 문제를 프롬프트·스키마 수정으로 해결한다.

해결하려는 근본 문제는 세 가지다.

1. **LLM에게 선택 분기가 있으면 틀린 쪽을 고른다** — 소유는 `CharacterState`+`ABOUT`, 역할은 `INVOLVED_WITH`로 갈라져 있어, LLM이 소유(곤충 채집망·맥가이버 칼)에 `INVOLVED_WITH role='사용자'`를 붙였다. 그 결과 5화 채집망의 획득→상실 궤적이 통째로 사라졌다.
2. **description이 구조화의 도피처가 된다** — 코인 100·업적·능력치가 `Event.description` 서술로 "때워지고" `CharacterState`로 승격되지 않았다. 6화 체력 강화와의 인과가 그래프상 끊긴다.
3. **요약 환각이 다음 회차로 전파된다** — `Chapter.summary`는 `novel_context`로 주입되는데, 1화(없는 관계 창작)·2화(다른 작품 『멸망 이후의 세카이』와 혼동) 오류가 이후 회차 추출에 오염원으로 들어간다.

**의도적 제외**: resolver의 `3807칸`/`3907칸` 오병합(S1-1)은 이번 범위 밖이다. 따라서 재인덱싱해도 Location 병합과 `LOCATED_IN` 자기참조는 **재현된다** — 검증 시 Location 관련 항목은 판정에서 제외한다.

---

## 변경 1 — `INVOLVED_WITH` 제거, 역할을 `CharacterState`+`ABOUT`으로 통합

인물→Item 관계를 **한 가지 메커니즘으로 통일**해 LLM의 선택 분기 자체를 없앤다.

```
변경 전: tls123 -[INVOLVED_WITH role='저자']-> Item:멸살법
변경 후: tls123 -[HAS_STATE]-> CharacterState{attribute:'역할', value:'저자'}
                              -[ABOUT]->          Item:멸살법
                              -[ESTABLISHED_IN]-> Event(역할이 드러난 사건)
```

`ESTABLISHED_IN`은 다른 상태와 동일하게 **역할이 원문에서 드러난 Event**에 앵커한다(모순 탐지 기준이 회차이므로 "언제 밝혀졌나"가 실용적으로 맞다).

### `poc/src/schema.py`
- `INVOLVED_WITH` RelationshipType 정의 삭제 (268~283행)
- `RELATIONSHIP_TYPES` 목록에서 `INVOLVED_WITH` 제거 (311행)
- `PATTERNS`에서 `("Character", "INVOLVED_WITH", "Item")` 제거 (299행)
- `ABOUT` description(259~266행)에 역할 추가 — 현재 "소유 상태는 Item, 소속 상태는 Organization"만 언급. `attribute='역할'`도 Item을 가리킨다는 설명 추가
- `CHARACTER_STATE` description(120~130행)에 역할 규칙 추가 — `attribute='역할'`, `value`는 `저자/독자/제작자`로 통일. 기존 소유·소속 설명과 같은 문단에 둔다
- `ITEM` description(191~192행) 갱신 — "'지금 누가 가졌는가'는 CharacterState(소유)+ABOUT" 문구에 역할도 같은 방식임을 덧붙임

### `poc/src/extractor.py`
- 도메인 규칙 89~91행 교체: "작품·사물을 저작/제작/열독한 인물은 INVOLVED_WITH(role=…)로 잇는다" → `CharacterState(attribute='역할', value='저자'/'독자'/'제작자')` + `ABOUT`→Item
- **"사람-사람 RELATED_TO로 묶지 말 것" 지침은 그대로 유지** (이 규칙은 잘 지켜지고 있었다)

### `poc/src/extraction_examples.py`
- **예시 5**(128~148행)를 새 형태로 재작성 — 이 예시가 역할 표현의 유일한 교보재다. 현재 CharacterState가 하나도 없으므로, 기존 Event(`탑의 문 연재와 준호의 독서`)를 앵커로 `역할=저자`/`역할=독자` 두 상태 + `HAS_STATE`/`ESTABLISHED_IN`/`ABOUT`을 추가
- 예시 5의 "주의 1"(작가-독자를 RELATED_TO로 묶지 않음) 문구는 유지

---

## 변경 2 — 서술·중복 속성 제거 (`Event.description`, `CharacterState.evidence`)

두 속성 모두 "구조 대신 텍스트로 때우는" 경로다. 제거 이유는 서로 다르다.

| 속성 | 제거 이유 |
|---|---|
| `Event.description` | 사실이 서술로 때워져 `CharacterState` 승격이 안 된다(코인·업적·능력치 누락의 직접 원인) |
| `CharacterState.evidence` | `EVIDENCED_BY`→`Chunk`에 원문이 이미 있어 **중복**. 자유 인용문은 그래프 순회도 안 된다 |

`CharacterState.evidence_chunk`는 **유지**한다 — 이것이 `EVIDENCED_BY` 28건(상태 수준 근거 링크)을 만드는 메커니즘이다.

### `poc/src/schema.py`
- `EVENT`의 `description` PropertyType 삭제 (82~89행)
- `EVENT.title` description 보강 (78~80행) — description이 사라져 **사건 식별이 title에만 의존**하므로, "무엇이 일어났는지 알 수 있을 만큼 구체적으로" 요구 조건을 추가
- `CHARACTER_STATE`의 `evidence` PropertyType 삭제 (145~149행)
- **dangling 참조 2곳 수정** — 제거되는 속성을 가리키는 문구가 남는다
  - `LOCATION.description`(64행): "특정 사건 전개는 여기 말고 **Event.description에**" → "사건 전개는 Event로 분리한다"
  - `CHARACTER_STATE.value`(143행): "서술 문장 말고 상태어만 — 자세한 정황은 **evidence에**" → 근거는 `evidence_chunk`(→Chunk)가 담당한다는 문구로 교체
- `CHARACTER`/`ORGANIZATION`/`ITEM`/`LOCATION`의 description은 **유지**(novel_context의 별칭 정합 신호)

> `GraphPruning`이 스키마 밖 속성을 제거하므로, LLM이 description·evidence를 내보내도 저장 단계에서 걸러진다.

### `poc/src/extraction_examples.py`
- 예시 1~5의 **모든 Event 노드에서 `description` 키 제거** (25, 51~52, 79, 106, 139행)
- **모든 CharacterState 노드에서 `evidence` 키 제거** (총 10곳 — 예시 1·2·3·4). `evidence_chunk`는 남긴다

### `poc/src/extractor.py`
- 도메인 규칙 108~109행("구조 완전성: description은 참고용이라…") 갱신 — Event에 description이 없어졌으므로 문구를 조정하고, **사건의 결과로 확정된 사실은 반드시 `CharacterState`로 만든다**는 요구로 바꾼다
- 111~112행의 `evidence_chunk` 규칙은 **그대로 유지**(인용문 `evidence`는 애초에 프롬프트에서 언급하지 않으므로 추가 수정 불필요)

> **부수 효과**: `context.py`의 `_node_display`는 name/title 외 모든 속성을 그래프 덤프에 넣는다. `evidence` 인용문이 빠지면 **`novel_context`가 눈에 띄게 줄어** 회차 누적 시 입력 토큰이 절감된다.

---

## 변경 3 — 요약 프롬프트 환각 방지 (`poc/src/context.py`)

`summarize_episode`(129~142행)의 system/user 프롬프트를 보강한다. 실패 사례가 두 유형이었다.

| 유형 | 사례 | 대응 지시 |
|---|---|---|
| 관계 창작 | 1화 "독자-작가 이상의 특별한 관계"(원문은 정반대인 질투) | 원문에 서술되지 않은 관계·감정·평가를 쓰지 않는다 |
| 고유명 혼동 | 2화 『멸망 이후의 세카이』를 멸살법으로 오인 | 고유명(인물·작품·조직·장소)은 원문 표기 그대로. 여러 작품·인물이 언급되면 각각을 구분하고, 확실하지 않으면 언급하지 않는다 |

추가할 지시 요지:
- 원문에 있는 사실만 쓰고 **해석·추정·평가를 덧붙이지 않는다**
- 요약이 **다음 회차 추출의 배경 컨텍스트로 쓰인다**는 용도를 명시(정확성 우선순위를 높임)

---

## 변경 4 — `CharacterState` 추출 규칙 보강

누락 패턴이 명확했다: **`[대괄호]` 시스템 메시지로 확정되는 수치·획득**이 상태로 승격되지 않았다(코인 100/6200/3500, 업적 '최초의 살해'·'대량 학살자', 스킬 '등장인물 일람'). 김남운은 **같은 정보창의 스킬·특성은 추출됐는데 능력치 4종만 통째로 빠졌다**.

### `poc/src/extractor.py` 도메인 규칙에 추가
- `[대괄호]` 시스템 메시지로 확정되는 획득·변동(코인·업적·레벨·능력치·스킬)은 `CharacterState`로 만든다
- 인물 **정보창·상태창이 통째로 제시되면 그 안의 항목을 빠짐없이** 상태로 만든다(일부만 뽑지 않는다)
- `attribute` 정규화 — 같은 종류는 같은 이름으로(코인·업적·능력치). `특성창` 같은 일회성 attribute를 만들지 않는다

### `poc/src/schema.py`
- `CHARACTER_STATE` description에 위 규칙 요약 반영(프롬프트와 스키마 양쪽에 있어야 GraphPruning·LLM 모두 일관)

---

## 변경 5 — `story_order` 프롬프트 규칙 보강 (`poc/src/extractor.py`)

6화에서 순서가 역전됐다(칼 상처 254행 → 체력 강화 318행인데 그래프는 6.1 체력강화 / 6.2 칼상처).

- 도메인 규칙 80~82행에 추가: **story_order는 원문에 등장하는 순서를 따른다. 회상·과거 사건만 예외로 더 작은 값을 준다.**

> **알려진 한계**: 이번 결정은 프롬프트 규칙만 두고 자동 검증은 도입하지 않는다. 기존에도 순서 규칙은 있었으나 틀렸으므로, **재발해도 자동으로 탐지되지 않는다.** 아래 검증 단계의 수동 확인이 유일한 안전망이다.

---

## 변경 6 — `schema.py` description 전면 정리

변경 1·2·4·5를 적용하면 각 타입의 `description`에 **없어진 개념을 가리키는 문구**와 이전 설계 단계의 잔재가 섞인다. 개별 수정으로 흩뿌리지 말고 한 번에 정리한다.

### 6-1. 사라지는 규칙 되살리기 (가장 중요)

`INVOLVED_WITH`를 삭제하면 **그 description에만 있던 규칙이 스키마에서 통째로 사라진다.**

> 현재 `INVOLVED_WITH` description(273행): *"저작·소비는 사람-사람 관계가 아니므로 RELATED_TO로 묶지 말고 각자를 그 작품에 잇는다."*

이 규칙은 **검증에서 잘 지켜지고 있던 것**(작가-독자를 RELATED_TO로 평탄화하지 않음)이므로 유실되면 회귀한다. → `RELATED_TO`의 description(241~244행)으로 **이전**한다.

### 6-2. dangling 참조 정리 (제거되는 대상을 가리키는 문구)

| 위치 | 현재 문구 | 조치 |
|---|---|---|
| `LOCATION.description` prop (64행) | "특정 사건 전개는 여기 말고 **Event.description**에" | Event로 분리한다는 표현으로 교체 |
| `CHARACTER_STATE.value` (143행) | "자세한 정황은 **evidence**에" | 근거는 `evidence_chunk`(→Chunk)가 담당한다로 교체 |

### 6-3. 새 설계(역할 통합) 반영

`INVOLVED_WITH` → `CharacterState(attribute='역할')`+`ABOUT` 전환을 관련 description 전체에 일관 반영한다.

| 위치 | 조치 |
|---|---|
| `CHARACTER_STATE.description` (120~130행) | 소유·소속과 나란히 **역할**(`attribute='역할'`, value=저자/독자/제작자) 규칙 추가 |
| `ABOUT.description` (261~265행) | "소유 상태는 Item, 소속 상태는 Organization"에 **역할 상태는 Item** 추가 |
| `ITEM.description` (186~193행) | "'지금 누가 가졌는가'는 CharacterState(소유)+ABOUT" 문구에 **역할도 같은 방식** 추가 |
| `ITEM.description` prop (206행) | "(소유자는 CharacterState가 담당)" → 소유·역할 모두 CharacterState가 담당으로 |
| `CHARACTER.description` prop (39~42행) | "소속·상태·소유·역할처럼 구조로 표현되는 사실은…"의 **역할**이 이제 CharacterState를 의미하도록 표현 정렬 |

### 6-4. 변경 4·5 규칙을 스키마에도 반영

프롬프트(`extractor.py`)와 스키마 양쪽이 모두 LLM에 전달되므로 **어긋나면 안 된다**.

- `CHARACTER_STATE.description`: `[대괄호]` 시스템 메시지로 확정되는 획득·변동(코인·업적·레벨·능력치·스킬)은 상태로 만든다 + 정보창이 통째로 제시되면 빠짐없이
- `EVENT.story_order` (98~104행): **원문 등장 순서를 따른다**(회상·과거만 예외)를 명시적으로 추가

### 6-5. 모듈 docstring 갱신 (1~4행)

현재 *"Phase 1: 상태 변화·시간 구조·공간 계층을 지원하는 스키마"* — 이후 추가된 `Item`·`Organization`·reified 소유/소속·근거 레이어가 반영돼 있지 않다. 현재 스키마의 실제 범위로 갱신한다.

> **정리 원칙**: 같은 규칙을 `schema.py`와 `extractor.py`에 중복 서술하면 드리프트가 생긴다(이번 정리의 원인이기도 하다). **schema.py는 노드·속성의 정의와 불변 규칙**, **extractor.py는 회차 마커·판단 기준 같은 추출 절차** 위주로 두고, 불가피하게 겹치는 항목은 표현을 일치시킨다.

---

## 검증

### 1. 재인덱싱
```bash
# DB 초기화
docker exec lorekeeper-neo4j cypher-shell -u neo4j -p lorekeeper "MATCH (n) DETACH DELETE n;"
# 1~6화 순차 인덱싱
cd poc && LOREKEEPER_CHAPTER=N LOREKEEPER_INPUT=data/input_chN.txt uv run python src/indexing.py
```
비용 참고: 직전 실측 6화 누적 **$0.43**(입력 100,572 / 출력 54,774 토큰).

### 2. 구조 검증 (Cypher)
```cypher
-- 변경 1: INVOLVED_WITH 소멸, 역할 상태 생성
MATCH ()-[r:INVOLVED_WITH]->() RETURN count(r);                        -- 0 이어야 함
MATCH (c:Character)-[:HAS_STATE]->(s:CharacterState {attribute:'역할'})
      -[:ABOUT]->(i:Item) RETURN c.name, s.value, i.name;              -- tls123/저자, 김독자/독자

-- 변경 2: Event.description / CharacterState.evidence 제거
MATCH (e:Event) WHERE e.description IS NOT NULL RETURN count(e);       -- 0 이어야 함
MATCH (s:CharacterState) WHERE s.evidence IS NOT NULL RETURN count(s); -- 0 이어야 함

-- 변경 2: 단, 상태 수준 근거 링크는 살아 있어야 함 (evidence_chunk 유지)
MATCH (s:CharacterState)-[:EVIDENCED_BY]->(:Chunk) RETURN count(*);    -- 0이 아니어야 함(이전 28건)

-- 변경 4: 누락됐던 상태들이 생성됐는지
MATCH (s:CharacterState) WHERE s.attribute IN ['코인','업적','능력치']
  RETURN s.attribute, s.value, count(*);                               -- 이전엔 0건

-- 변경 1 부수효과: 소유 상태가 제대로 생겼는지 (5화 채집망 획득→상실)
MATCH (c:Character {name:'김독자'})-[:HAS_STATE]->(s:CharacterState {attribute:'소유'})
      -[:ABOUT]->(i:Item {name:'곤충 채집망'})
  RETURN s.value, s.evidence;                                          -- 보유/상실 2건 기대
```

### 3. 수동 확인 (자동 검증이 없는 항목)
- **요약 환각**: `MATCH (c:Chapter) RETURN c.number, c.summary` — 1화에 "특별한 관계", 2화에 "멸망 이후의 세카이"가 **없어야** 한다
- **story_order**: 6화 Event를 story_order 순으로 뽑아 칼 상처가 체력 강화보다 **앞**인지 확인

### 4. 회귀 확인 (기존에 맞던 것이 깨지지 않았는지)
검증 리포트의 "✅ 정확했던 것" 항목을 재확인한다 — 특히 **유상아·이현성의 6화 미등장**(`APPEARS_IN`에 6 없음), **폐허 서울을 Location으로 만들지 않음**, 엑스트라·온라인 공간 미생성. 변경 4(상태 적극 추출)가 **과추출 쪽으로 진자를 밀 수 있으므로** 이 확인이 중요하다.

변경 6-1(사라지는 규칙 이전)의 회귀도 반드시 본다 — `INVOLVED_WITH` 삭제로 "작가-독자를 사람-사람 관계로 묶지 말 것" 규칙이 유실되면 아래가 생긴다.
```cypher
-- tls123 ↔ 김독자를 RELATED_TO로 평탄화하지 않았는지 (이전엔 올바르게 없었음)
MATCH (a:Character {name:'tls123'})-[r:RELATED_TO]-(b:Character {name:'김독자'})
  RETURN count(r);                                                     -- 0 이어야 함
```

### 5. 판정에서 제외할 항목
resolver 미수정으로 **재현이 예상되는** 것들 — Location `3807/3907` 병합, `LOCATED_IN` 자기참조 2건, 태풍여고/교실 병합, 그에 딸린 `HOSTS` 오귀속. 이번 변경의 성패와 무관하다.

---

## 후속 (이번 범위 밖)

- **resolver 숫자 가드** (S1-1) — 고유명에 든 숫자가 다르면 병합 금지. 파급이 가장 크므로 다음 우선순위. `poc/src/resolver.py`의 `compute_similarity` 또는 병합 직전 필터에 적용
- **story_order 자동 검증** — `evidence_chunk`의 `C{i}`가 원문 순서이므로, story_order 순위와 대조해 역전을 탐지 가능. 변경 5의 프롬프트 규칙이 실패할 경우 도입 검토
