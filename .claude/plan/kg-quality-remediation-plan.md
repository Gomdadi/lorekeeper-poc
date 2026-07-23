# KG 추출 품질 보완 플랜

> 근거: `.claude/docs/kg-verification-ch1-6.md` (1~6화 원문↔그래프 전수 대조, S1 2건·S2 4건·S3 6건)
> 대상: `poc/src/` — `schema.py` · `extractor.py` · `extraction_examples.py` · `context.py` · `resolver.py`

---

## Context

검증 리포트가 드러낸 추출 품질 문제를 프롬프트·스키마 수정으로 해결한다. 근본 원인은 셋이다.

1. **LLM에게 선택 분기가 있으면 틀린 쪽을 고른다** — 소유는 `CharacterState`+`ABOUT`, 역할은 `INVOLVED_WITH`로 갈라져 있어 LLM이 소유(곤충 채집망·맥가이버 칼)에 `INVOLVED_WITH role='사용자'`를 붙였다. 그 결과 5화 채집망의 획득→상실 궤적이 통째로 사라졌다.
2. **자유 서술이 구조화의 도피처가 된다** — 코인 100·업적·능력치가 `Event.description` 서술로 "때워지고" `CharacterState`로 승격되지 않았다. 6화 체력 강화와의 인과가 그래프상 끊긴다.
3. **요약 환각이 다음 회차로 전파된다** — `Chapter.summary`는 `novel_context`로 주입되는데, 1화(없는 관계 창작)·2화(다른 작품 『멸망 이후의 세카이』와 혼동) 오류가 이후 회차 추출에 오염원으로 들어간다.

여기에 더해 **검색 구조가 확정되면서 스키마 설계 기준 자체가 바뀌었다.**

> vector RAG → top-k 노드 → n-hop 그래프 확장으로 컨텍스트를 모아 **LLM as judge**에게 넘긴다. 판단은 LLM이 하고, 파이프라인의 목적은 **관련 노드를 잘 끌어모으는 것**이다.

`attribute`를 축으로 한 기계적 대조는 포기한다 — `attribute`는 LLM이 생성하므로 일관성을 보장할 수 없고(리포트에서 이미 `특성창` 같은 일회성 attribute가 나왔고 김남운 능력치는 축 자체가 생성되지 않았다), 신뢰할 수 없는 축 위에 모순 탐지를 올릴 수 없다. 대신 **노드의 자기서술성**이 유일한 요구사항이 된다.

---

## 설계 원칙

이 플랜의 모든 변경은 아래 넷에서 파생된다.

| 원칙 | 내용 |
|---|---|
| **장르 중립** | 프롬프트·스키마에 특정 작품/장르의 형식(`[대괄호]`·코인·정보창·시나리오)을 넣지 않는다. `extraction_examples.py:5-6`이 이미 명시한 설계 원칙이다 |
| **요약이 아니라 인용** | LLM이 생성한 요약은 사실 오류를 낳고 구조화 도피처가 된다. 원문 인용은 검증 가능하다 |
| **description은 부가 속성** | 구조로 표현 가능한 사실이 description에만 남고 실제 노드·관계로 나타나서는 안 된다 |
| **노드는 자기서술적** | n-hop으로 딸려왔을 때 그 노드만 읽고 무슨 상태인지 알 수 있어야 한다 |

**의도적 제외**: resolver의 `3807칸`/`3907칸` 오병합(S1-1)은 이번 범위 밖이다. 따라서 재인덱싱해도 Location 병합과 `LOCATED_IN` 자기참조는 **재현된다** — 검증 시 Location 관련 항목은 판정에서 제외한다.

---

## 변경 1 — `CharacterState`를 `state` 단일 속성으로 재설계

가장 근본적인 구조 변경이며 변경 2가 여기 의존한다.

```
변경 전: CharacterState { attribute, value, evidence, evidence_chunk }
변경 후: CharacterState { state, evidence, evidence_chunk }

  state:          "어깨를 칼날에 깊게 베임"
  evidence:       "가까스로 심장을 빗나간 칼날이 어깨를 깊이 긋고 지나갔다"
  evidence_chunk: "C41"
```

축을 버리면 설명용 두 번째 필드는 `evidence`의 열화 복사본이 되므로 하나로 통합한다. 대상은 `ABOUT`, 시점은 `ESTABLISHED_IN`, 근거는 `evidence`가 이미 담당하므로 중복이 사라진다.

현재 `{attribute:'소속', value:'소속'}`은 덤프에 `- (CharacterState) 소속=소속`으로 찍혀 아무것도 전달하지 않는다. 실제 대상은 `ABOUT`이 들고 있어 `value`가 할 말이 없는 퇴화 사례다.

### `poc/src/context.py` — 먼저 고쳐야 할 파손 지점

```python
# 31, 36~37행 현재
# 대표 이름: name → title → attribute=value 순으로 고른다.
elif props.get("attribute"):
    key = f"{props['attribute']}={props.get('value', '')}"
```

`attribute`가 사라지면 이 분기가 걸리지 않아 `key = "?"`로 떨어진다. 그러면 **모든 CharacterState의 참조 이름이 `CharacterState:?`로 동일**해져 관계 직렬화(`ref_by_id`)에서 서로 구분되지 않는다. 그래프 덤프가 통째로 무의미해지므로 반드시 함께 고친다.

→ `props.get("state")`를 쓰도록 교체하고 31행 주석도 `name → title → state`로 갱신한다.

### `poc/src/schema.py`

**`CHARACTER_STATE` 노드 description(120~130행) 전면 재작성.**

| 현재 문구 | 조치 |
|---|---|
| "인물의 한 속성(attribute)이 특정 회차부터 갖는 값(value)" | `state` 단일 정의로 재작성 |
| "능력·무공은 attribute로만 / 소유는 attribute='소유'(value='보유'/'상실') / 소속은 attribute='소속'" | 전부 삭제 — `state`에 자연어로 서술하고 대상만 `ABOUT`으로 잇는다 |
| "'회사원'·'계약직' 같은 신분·고용형태는 소속이 아니라 attribute='신분'으로 분리" | **의미는 유지** — "신분과 소속은 별개의 state 노드로 만든다"로 재작성 |
| "같은 attribute에 여러 개가 쌓이며, ESTABLISHED_IN Event.chapter가 조회 시점 이하 중 가장 큰 것이 '현재 유효한 값'이다"(127~129행) | **삭제** — 축 대조를 포기했으므로 무효 |
| "상태가 바뀌면 기존 노드를 고치지 말고 항상 새 노드를 만든다" | **유지·강화** — 이력이 노드 나열로만 남으므로 더 중요해진다 |
| "일시적 통증·피로처럼 그 회차에서 소모되는 상태는 만들지 않는다(지속 상태만)" | 유지 |

**PropertyType 교체**
- `attribute`(132~139행)·`value`(140~144행) 삭제 → `state` 신설
- `state` description 요지: *"이 인물이 이 시점부터 갖는 상태를 그 자체로 읽히게 서술한다(예: '어깨를 칼날에 깊게 베임', '대한물산 인사팀에 계약직으로 소속'). 원문을 그대로 옮기지 말고 상태로 압축하되, 무엇에 관한 상태인지 알 수 있을 만큼은 구체적으로. 대상이 있는 상태(소유·소속·역할)는 대상 노드를 `ABOUT`으로 함께 잇는다."*
- `evidence`·`evidence_chunk`(145~157행)는 변경 3에 따라 유지

**다른 타입에서 attribute를 참조하는 곳**

| 위치 | 조치 |
|---|---|
| `ORGANIZATION.description`(166행) | "CharacterState(attribute='소속')" → attribute 표기 제거 |
| `ITEM.description`(192행) | "CharacterState(attribute='소유')+ABOUT" → attribute 표기 제거 |
| `HAS_STATE.description`(227행) | "같은 attribute에 여러 상태가 쌓이면" → "한 인물에 여러 상태가 쌓이면" |
| `ABOUT.description`(262~264행) | attribute 전제 없이 재작성(변경 2의 역할 추가와 함께) |

`ESTABLISHED_IN.description`(231행)의 "그 Event.chapter가 이 상태의 시간 순서 기준"은 **유지**한다 — 시점 정보 자체는 여전히 유효하고, n-hop 확장 시 회차 필터로 쓸 수 있다.

### `poc/src/extractor.py`

| 행 | 조치 |
|---|---|
| 83~85 | "시간에 따라 변해 나중에 **대조·모순 판정 대상**이 되는 사실" → 판정 전제가 바뀌었으므로 "시간에 따라 변하는 사실"로 재작성 |
| 86~88 | "능력·무공은 attribute로만 / 소유는 CharacterState(attribute='소유', value='보유'/'상실')" → state 서술 + ABOUT으로 재작성 |
| 94~97 | 소속/신분 규칙 → state 서술로 재작성. "신분과 소속을 별개 상태로 분리" 취지는 유지 |
| 100~101 | "attribute/value는 짧고 일관되게(생사는 항상 '생사'…)" → **삭제**. 축 일관성 요구 자체가 사라졌다 |
| 110 | "소유·소속 CharacterState는 대상을 같은 출력에서 ABOUT으로 함께 낸다" → 유지(표현만 조정) |

### `poc/src/extraction_examples.py` — CharacterState 10곳 재작성

| 예시 | 현재 | 변경 후 `state` |
|---|---|---|
| 1 (26행) | `attribute:왼다리, value:의족` | `왼다리에 의족을 착용해 다시 걷게 됨` |
| 1 (27행) | `attribute:소속, value:소속` | `반란군에 가담` (ABOUT→반란군) |
| 2 (53행) | `attribute:소속, value:소속` | `화산파에 정식 입문` (ABOUT→화산파) |
| 2 (54행) | `attribute:무공, value:매화검법 대성` | `매화검법 대성` |
| 3 (80행) | `attribute:소유, value:상실` | `빛의 성배를 카일에게 넘겨 상실` (ABOUT→빛의 성배) |
| 3 (81행) | `attribute:소유, value:보유` | `빛의 성배를 넘겨받아 보유` (ABOUT→빛의 성배) |
| 4 (107행) | `attribute:신분, value:계약직` | `계약직 신분` |
| 4 (108행) | `attribute:소속, value:소속` | `대한물산 인사팀 소속` (ABOUT→대한물산) |
| 4 (109행) | `attribute:신분, value:정직원` | `정직원으로 전환` |
| 4 (110행) | `attribute:갈비뼈, value:골절` | `갈비뼈 골절` |

`evidence` 값은 전부 그대로 둔다. 예시 4의 주의 문구 중 `'갈비뼈=골절'` 표기만 새 형태로 고친다.

> 108행이 중요한 검증 사례다 — 현재 `소속=소속`은 evidence(`"대한물산 인사팀에서 계약직으로 일하던 유나"`)와 `ABOUT`(대한물산) 어느 쪽도 반영하지 못한다. `대한물산 인사팀 소속`으로 바뀌면 리포트 S3-12가 지적한 **유상아 소속의 evidence/ABOUT 불일치**와 같은 유형의 문제가 예시 수준에서 먼저 해소된다.

---

## 변경 2 — `INVOLVED_WITH` 제거, 역할을 `CharacterState`로 통합

인물→Item 관계를 **한 가지 메커니즘으로 통일**해 LLM의 선택 분기 자체를 없앤다(S2-5).

```
변경 전: tls123 -[INVOLVED_WITH role='저자']-> Item:멸살법
변경 후: tls123 -[HAS_STATE]-> CharacterState{state:'멸살법의 저자'}
                              -[ABOUT]->          Item:멸살법
                              -[ESTABLISHED_IN]-> Event(역할이 드러난 사건)
```

`ESTABLISHED_IN`은 다른 상태와 동일하게 **역할이 원문에서 드러난 Event**에 앵커한다.

### `poc/src/schema.py`
- `INVOLVED_WITH` RelationshipType 정의 삭제(268~283행)
- `RELATIONSHIP_TYPES`에서 제거(311행), `PATTERNS`에서 `("Character", "INVOLVED_WITH", "Item")` 제거(299행)
- `ABOUT` description에 역할 상태도 Item을 가리킨다는 설명 추가
- `ITEM.description`에 역할도 CharacterState 방식임을 덧붙임

### 사라지는 규칙 되살리기 (가장 중요)

`INVOLVED_WITH`를 삭제하면 **그 description에만 있던 규칙이 스키마에서 통째로 사라진다.**

> 현재 `INVOLVED_WITH` description(273행): *"저작·소비는 사람-사람 관계가 아니므로 RELATED_TO로 묶지 말고 각자를 그 작품에 잇는다."*

이 규칙은 **검증에서 잘 지켜지고 있던 것**(작가-독자를 RELATED_TO로 평탄화하지 않음)이므로 유실되면 회귀한다. → `RELATED_TO`의 description(241~244행)으로 **이전**한다.

> **순서 의존성**: 이 이전 작업은 `INVOLVED_WITH` 삭제와 **같은 커밋에서** 해야 한다. 삭제만 먼저 적용하고 재인덱싱하면 그 사이 규칙이 유실된다.

### `poc/src/extractor.py`
- 89~91행 교체: "작품·사물을 저작/제작/열독한 인물은 INVOLVED_WITH(role=…)로 잇는다" → `CharacterState(state='탑의 문의 저자')` + `ABOUT`→Item
- "사람-사람 RELATED_TO로 묶지 말 것" 지침은 그대로 유지(잘 지켜지고 있었다)

### `poc/src/extraction_examples.py`
- 예시 5(128~148행)를 새 형태로 재작성 — 이 예시가 역할 표현의 유일한 교보재다. 기존 Event(`탑의 문 연재와 준호의 독서`)를 앵커로 상태 2개를 추가한다
  - `{state: '탑의 문의 저자'}` + `HAS_STATE`/`ESTABLISHED_IN`/`ABOUT`→탑의 문
  - `{state: '탑의 문의 유일한 독자'}` + 동일
- 예시 5의 "주의 1"(작가-독자를 RELATED_TO로 묶지 않음) 문구는 유지

---

## 변경 3 — `Event.description` 제거, `Event.evidence` 신설

`Event.description`은 LLM이 생성한 **요약**이라 사실 오류를 낳고 구조화 도피처가 된다. 제거하되, 정보량 공백은 **원문 인용**으로 메운다.

| 속성 | 성질 | 조치 |
|---|---|---|
| `Event.description` | LLM 요약 — 사실 오류 가능, 구조화 도피처 | **제거** |
| `Event.evidence` | 원문 인용 — 검증 가능 | **신설** |
| `CharacterState.evidence` | 원문 인용 | **유지** |
| `CharacterState.evidence_chunk` | 청크 포인터 | 유지 |

근거:

- 리포트 S3-12의 사실 오류 2건(Event 4.4 "여고생들이 차례로 살해된 끝에", 4.3 살인/봉쇄 혼합)은 **전부 요약이 만든 것**이다. 인용이었다면 발생할 수 없다. 4.3은 그 오류가 회차 요약까지 전파됐다.
- 인용은 **원문 substring 매칭으로 환각을 자동 검증**할 수 있다. 요약은 불가능하다. 이 프로젝트에 없던 검증 수단이 하나 생긴다.
- 리포트 S3-12는 김남운 배후성의 값 손실을 "evidence 필드엔 남아 있어 경미"로 평가했다 — evidence가 실제로 정보 손실을 막고 있었다.
- Chunk는 `indexing.py:57` 기준 100자·평균 3문장이라, 인용문이 근거를 더 정밀하게 짚는다. `EVIDENCED_BY`(순회)와 `evidence`(정밀 근거)는 중복이 아니라 **다른 해상도**다.

### `poc/src/schema.py`
- `EVENT`의 `description` PropertyType 삭제(82~89행)
- `EVENT`에 `evidence` PropertyType 신설 — `CHARACTER_STATE.evidence`와 같은 문구를 쓴다: *"이 사건의 근거가 되는 원문 문장을 그대로/가깝게 인용한다(해석·설명을 덧붙이지 않음)."* 새 개념 도입이 아니라 **기존 패턴을 Event로 확대**하는 것이다
- `LOCATION.description` prop(64행)의 dangling 참조 수정: "특정 사건 전개는 여기 말고 **Event.description에**" → "사건 전개는 Event로 분리한다"

### `poc/src/extraction_examples.py`
- 예시 1~5의 Event 노드에서 `description` 키를 `evidence`로 **교체**하고, 값을 요약이 아닌 **입력 텍스트의 원문 인용**으로 바꾼다(25, 51, 52, 79, 106, 139행)
  - 예: `"description": "강도현이 연회에 참석한 뒤 반란군에 가담함"` → `"evidence": "그는 왕성의 대연회장에서 열린 연회에 참석했고, 그날 황실을 배신하고 반란군에 가담했다"`
  - 예시가 인용 규칙의 유일한 교보재이므로, 인용문이 실제로 그 예시의 입력 텍스트에 **문자 그대로** 존재해야 한다

### `poc/src/extractor.py`
- 108~109행("구조 완전성: description은 참고용이라…") → 변경 4에서 함께 재작성
- `evidence` 인용 규칙을 프롬프트에 **명시 추가** — 현재 프롬프트는 `evidence_chunk`만 언급하고 `evidence`는 다루지 않는다(111~112행). Event까지 확대되므로 "원문을 그대로 인용하고 해석을 덧붙이지 않는다"를 명시해야 한다

### `poc/src/context.py`
- `_node_display`(41~48행)의 `extras`에서 `evidence`를 제외한다 — 그래프 덤프는 엔티티 식별·구조 신호용이고 근거 텍스트는 거기 필요 없다

> 이 한 줄로 **저장(판정 근거)과 주입(컨텍스트 토큰)이 분리**된다. 정보는 DB에 남으면서 `novel_context`는 오히려 줄어든다.

---

## 변경 4 — 모든 `description`의 위상을 명시

`Event.description` 제거만으로는 부족하다. **남는 모든 `description`이 같은 도피처가 될 수 있다.**

> **원칙**: `description`은 해당 노드·관계를 설명하는 **부가적 속성**일 뿐이다. 노드/관계와 내용이 겹치는 것은 무방하나, **구조로 표현 가능한 사실이 `description`에만 남고 실제 노드·관계로 나타나지 않아서는 안 된다.**

근거가 둘이다.

- 리포트 S3-8이 정확히 이 실패다 — 코인·업적·능력치가 `Event.description` 서술로 때워지고 상태로 승격되지 않았다. 같은 유인이 `Character`·`Item`·`Organization`·`Location`의 description에도 그대로 있다.
- `resolver.py:36`의 `_MERGE_PROPS`는 `{description:'combine', ...}`이라 병합 시 description이 **배열로 합쳐진다**(주석에 "스칼라 스키마와 어긋난다"고 명시). S1-1에서 3807칸·3907칸 설명이 한 노드에 섞인 게 그 결과다. **description에만 있는 사실은 오병합 시 어느 원본의 것인지 복구할 수 없다.**

### 정준 문구

드리프트를 막기 위해 **모든 위치에 같은 문장**을 쓴다.

```
구조로 표현 가능한 사실은 여기에만 두지 말고 반드시 해당 노드/관계로도 만든다.
```

### `poc/src/schema.py` — 남는 description 5곳

| 위치 | 현재 상태 | 조치 |
|---|---|---|
| `CHARACTER.description` prop (36~42행) | 이미 같은 취지 존재 | 정준 문구로 **표현 통일** |
| `LOCATION.description` prop (61~65행) | 없음 | 추가 (변경 3의 dangling 수정과 같은 줄에서 처리) |
| `ORGANIZATION.description` prop (175~179행) | 없음 | 추가 |
| `ITEM.description` prop (203~207행) | "(소유자는 CharacterState가 담당)"으로 부분적 | 정준 문구로 **일반화 승격** |
| `RELATED_TO.description` prop (251~255행) | 없음 | 추가 — 관계 부연에 등장하는 인물·조직·사건도 노드로 존재해야 한다 |

노드 타입 자체의 `description=`(생성자 인자)은 스키마 설명문이지 추출 대상 속성이 아니므로 건드리지 않는다.

### `poc/src/extractor.py`

108~109행의 "구조 완전성" 규칙을 **모든 description에 적용되는 일반 규칙**으로 승격한다(현재는 문장이 Event 중심으로 읽힌다).

```
- 구조 완전성: 모든 description은 해당 노드·관계를 설명하는 부가 속성일 뿐이다. 노드/관계와
  내용이 겹쳐도 되나, 구조로 표현 가능한 사실(소속·신분·소유·역할·관계·장소·상태)이
  description에만 남고 해당 노드/관계로 나타나지 않아서는 안 된다.
```

---

## 변경 5 — `Character.aliases` 신설

**새 정보를 요구하는 게 아니라, 이미 description으로 새고 있는 정보를 구조화한다.** 변경 4의 원칙이 적용되는 첫 사례다.

```python
# extraction_examples.py:49 — 입력 텍스트의 "그의 사부 검선 진자강"에서
{"name": "진자강", "description": "검선이라 불리는 검객"}
```

`schema.py:31-32`의 `name` 설명은 *"별명·직함·존칭 등으로 다르게 불려도 항상 같은 대표 이름으로 통일한다"*고만 하고 **그 별명을 어디 둘지는 말하지 않는다**. 그래서 description으로 흘러간다.

### 이름 통일을 실제로 개선하는 경로

회차 간 이름 통일의 주 메커니즘은 resolver가 아니라 **`novel_context` 그래프 덤프**다. `context.py:41-48`의 `_node_display`가 `name`/`title` 외 속성을 덤프에 넣으므로 `aliases`는 자동으로 다음 회차 프롬프트에 실린다.

```
- (Character) 김독자 — aliases=독자 씨,김 대리, description=...
```

다음 회차에서 변형 호칭을 만난 LLM이 대표 이름으로 매핑할 단서가 생긴다. `context.py` 추가 수정은 필요 없다(변경 3에서 제외하기로 한 것은 `evidence`뿐이다).

### `poc/src/schema.py`

`CHARACTER`에 `aliases` PropertyType 신설:

```
"이 인물이 원문에서 달리 불리는 호칭을 쉼표로 나열한다(예: '독자 씨, 김 대리').
원문에 실제로 등장한 호칭만 쓰고 서술·설명은 넣지 않는다. name은 대표 이름 하나로 유지하고
변형 표기는 전부 여기 모은다 — 다음 회차에서 같은 인물을 같은 노드로 잇는 단서다."
```

`CHARACTER.name`(31~32행)에도 "변형 표기는 aliases에" 한 구절을 덧붙여 별칭의 행선지를 명시한다.

### `poc/src/resolver.py`

`_MERGE_PROPS`(36행)에 `aliases:'combine'`을 추가한다.

```python
_MERGE_PROPS = "{description:'combine', aliases:'combine', `.*`:'discard'}"
```

현재 `` `.*`:'discard' `` catch-all에 걸려 노드 병합 시 한쪽 aliases가 조용히 유실된다. description과 같은 취급이 맞다.

### `poc/src/extraction_examples.py`

예시 2의 진자강을 고친다 — 별칭이 description에 묻힌 현 상태가 정확히 변경 4가 금지하는 패턴이다.

```
변경 전: {"name": "진자강", "description": "검선이라 불리는 검객"}
변경 후: {"name": "진자강", "aliases": "검선", "description": "화산파의 검객"}
```

`extractor.py`에는 규칙을 넣지 않는다 — 속성의 정의이므로 schema.py가 권위를 갖는다.

### 정직한 한계

**이번 재인덱싱으로는 효과를 측정할 수 없다.** 리포트 1~6화에 별칭 실패 사례가 없고(등장인물 8명), S1-1의 `3807/3907`은 별칭이 아니라 서로 다른 실체였다. 개선을 보여줄 baseline이 없다.

따라서 **회차가 늘었을 때 값을 하는 투자**로 위치시키고 이번 라운드 판정 항목에는 넣지 않는다. **적용 범위는 `Character`만** — 다른 타입도 같은 문제를 갖지만 표기 변형 실패 근거가 없다.

---

## 변경 6 — 상태 추출 규칙 (장르 중립)

누락 패턴이 명확했다(S3-8): 코인 100/6200/2700/3500, 업적 '최초의 살해'·'대량 학살자', 스킬 '등장인물 일람', 김남운 능력치 4종. 김남운은 **같은 정보창의 스킬·특성은 추출됐는데 능력치 4종만 통째로 빠졌다.**

이 실패들의 **성질**을 추출하면 다음 셋이다. 어느 것도 장르·형식을 언급하지 않는다.

`poc/src/extractor.py` 도메인 규칙에 추가할 문구:

```
- 인물의 상태가 원문에 명시적으로 제시되면(서술이든 목록·표·공지 형태든) CharacterState로
  만든다. 원문이 값을 직접 확정해 준 것은 해석 여지가 없으므로 우선 추출 대상이다.
- 한 인물의 여러 상태가 한자리에 열거되면 일부만 고르지 말고 열거된 항목을 빠짐없이 만든다.
- 값이 변하는 상태는 변할 때마다 새 CharacterState를 만든다(기존 노드를 고치지 않는다).
```

각 규칙이 잡는 실패:

| 규칙 | 잡는 실패 |
|---|---|
| 1 | 코인·업적·스킬 — 원문이 값을 명시했는데 상태로 승격 안 됨 |
| 2 | 김남운 능력치 4종 — 같은 정보창의 스킬·특성은 뽑고 능력치만 통째로 누락 |
| 3 | 코인 잔액 6200 → 2700 → 3500 추적 |

무협의 "내공 3갑자", 로판의 "신탁", 현대물의 "연봉·직급"에도 그대로 적용된다.

`poc/src/schema.py`의 `CHARACTER_STATE.description`에도 같은 취지를 한 문장으로 반영한다(프롬프트·스키마 양쪽이 LLM에 전달되므로 어긋나면 안 된다).

---

## 변경 7 — 요약 프롬프트 환각 방지 (`poc/src/context.py`)

`summarize_episode`(129~142행)의 system/user 프롬프트를 보강한다. 실패 사례가 두 유형이었다(S2-6).

| 유형 | 사례 | 대응 지시 |
|---|---|---|
| 관계 창작 | 1화 "독자-작가 이상의 특별한 관계"(원문은 정반대인 질투) | 원문에 서술되지 않은 관계·감정·평가를 쓰지 않는다 |
| 고유명 혼동 | 2화 『멸망 이후의 세카이』를 멸살법으로 오인 | 고유명(인물·작품·조직·장소)은 원문 표기 그대로. 여러 작품·인물이 언급되면 각각을 구분하고, 확실하지 않으면 언급하지 않는다 |

추가할 지시 요지:
- 원문에 있는 사실만 쓰고 **해석·추정·평가를 덧붙이지 않는다**
- 요약이 **다음 회차 추출의 배경 컨텍스트로 쓰인다**는 용도를 명시(정확성 우선순위를 높임)

---

## 변경 8 — `story_order` 규칙 보강 (`poc/src/extractor.py`)

6화에서 순서가 역전됐다(S1-2). 칼 상처 254행 → 체력 강화 318행인데 그래프는 6.1 체력강화 / 6.2 칼상처로, 인과가 뒤집혔다.

- 도메인 규칙 79~82행에 추가: **story_order는 원문에 등장하는 순서를 따른다. 회상·과거 사건만 예외로 더 작은 값을 준다.**

> **알려진 한계**: 프롬프트 규칙만 두고 자동 검증은 도입하지 않는다. 기존에도 순서 규칙은 있었으나 틀렸으므로 **재발해도 자동으로 탐지되지 않는다.** 검증 단계의 수동 확인이 유일한 안전망이다.

---

## 변경 9 — `schema.py` 잔재 정리

변경 1~8을 적용하면 각 타입의 description에 **없어진 개념을 가리키는 문구**와 이전 설계 단계의 잔재가 섞인다. 개별 수정으로 흩뿌리지 말고 한 번에 정리한다.

### `EVENT.title`의 부정확한 서술

현재 `schema.py:80`은 *"여러 청크에 걸쳐 언급돼도 같은 title로 한 Event에 병합되게 한다"*라고 하지만, `indexing.py:132`가 `CombiningFuzzyResolver`를 `resolve_properties` 기본값(`["name"]`)으로 부르므로 **`name`이 없는 Event는 resolver 병합 대상이 아니다**. 회차 전체를 단일 청크로 넣는 `WholeTextSplitter` 구성이라 실제로 문제가 안 됐을 뿐이다. 사실과 맞게 고친다.

`EVENT.description`이 사라져 **사건 식별이 title에만 의존**하므로 "무엇이 일어났는지 알 수 있을 만큼 구체적으로"를 요구 조건에 추가한다. title이 병합 키가 아니므로 구체화해도 병합이 깨질 위험은 없다.

### 모듈 docstring 갱신 (1~4행)

현재 *"Phase 1: 상태 변화·시간 구조·공간 계층을 지원하는 스키마"* — 이후 추가된 `Item`·`Organization`·reified 소유/소속·근거 레이어가 반영돼 있지 않다. 현재 스키마의 실제 범위로 갱신한다.

> **정리 원칙**: 같은 규칙을 `schema.py`와 `extractor.py`에 중복 서술하면 드리프트가 생긴다(이번 정리의 원인이기도 하다). **schema.py는 노드·속성의 정의와 불변 규칙**, **extractor.py는 회차 마커·판단 기준 같은 추출 절차** 위주로 두고, 불가피하게 겹치는 항목은 표현을 일치시킨다.

---

## 파일별 변경 요약

| 파일 | 변경 |
|---|---|
| `schema.py` | CharacterState를 `state` 단일로 재설계, `INVOLVED_WITH` 삭제, `EVENT.description` 삭제·`evidence` 신설, `CHARACTER.aliases` 신설, description 5곳에 정준 문구, attribute 참조 4곳 정리, dangling 참조·`EVENT.title`·docstring 정리 |
| `extractor.py` | 상태 규칙 재작성(state 기반), 역할 규칙 교체, 장르 중립 3규칙 추가, 구조 완전성 일반화, evidence 인용 규칙 명시, story_order 보강, attribute 일관성 규칙 삭제 |
| `extraction_examples.py` | CharacterState 10곳 재작성, Event의 `description`→`evidence` 교체(원문 인용), 예시 5에 역할 상태 2개 추가, 진자강 `aliases` 분리 |
| `context.py` | `_node_display`의 `attribute=value` 키 생성 → `state`(**파손 지점**), `extras`에서 `evidence` 제외, `summarize_episode` 프롬프트 보강 |
| `resolver.py` | `_MERGE_PROPS`에 `aliases:'combine'` 추가 |

---

## 실행 순서

1. `context.py`의 파손 지점(`attribute=value` 키 생성)을 먼저 고친다
2. `schema.py` → `extractor.py` → `extraction_examples.py` 순으로 적용. **변경 2의 규칙 이전은 `INVOLVED_WITH` 삭제와 같은 커밋**
3. 1~2화만 먼저 인덱싱해 그래프 덤프 형태를 확인한 뒤 3~6화 진행

---

## 검증

### 1. 재인덱싱
```bash
docker exec lorekeeper-neo4j cypher-shell -u neo4j -p lorekeeper "MATCH (n) DETACH DELETE n;"
cd poc && LOREKEEPER_CHAPTER=N LOREKEEPER_INPUT=data/input_chN.txt uv run python src/indexing.py
```
비용 참고: 직전 실측 6화 누적 **$0.43**(입력 100,572 / 출력 54,774 토큰).

### 2. 스키마 전환 (변경 1·2·3)
```cypher
-- CharacterState가 state 단일로 전환됐는지
MATCH (s:CharacterState) WHERE s.attribute IS NOT NULL OR s.value IS NOT NULL
  RETURN count(s);                                                     -- 0 이어야 함
MATCH (s:CharacterState) WHERE s.state IS NULL RETURN count(s);        -- 0 이어야 함

-- INVOLVED_WITH 소멸, 역할이 state로
MATCH ()-[r:INVOLVED_WITH]->() RETURN count(r);                        -- 0 이어야 함
MATCH (c:Character)-[:HAS_STATE]->(s:CharacterState)-[:ABOUT]->(i:Item)
  RETURN c.name, s.state, i.name;                                      -- tls123/저자, 김독자/독자

-- Event: 요약이 사라지고 인용이 남았는지
MATCH (e:Event) WHERE e.description IS NOT NULL RETURN count(e);       -- 0 이어야 함
MATCH (e:Event) WHERE e.evidence IS NULL RETURN count(e);              -- 0 이어야 함
MATCH (s:CharacterState)-[:EVIDENCED_BY]->(:Chunk) RETURN count(*);    -- 0이 아니어야 함(이전 28건)
```

### 3. 자기서술성 (변경 1) — 육안 확인
```cypher
MATCH (c:Character)-[:HAS_STATE]->(s:CharacterState)
  RETURN c.name, s.state, s.evidence ORDER BY c.name;
```
`소속=소속` 같은 무정보 상태가 없어야 하고, `state`만 읽고 무슨 상태인지 알 수 있어야 한다.

**그래프 덤프 육안 확인** — 2화 인덱싱 시 출력되는 배경 컨텍스트에서 CharacterState 줄이 `- (CharacterState) ?`가 아니라 상태 서술로 찍히는지 본다. 깨지면 이후 회차 추출 품질이 전부 영향받으므로 **1화 → 2화 시점에 먼저 확인**한다.

### 4. 인용 환각 자동 검증 (이번에 새로 얻는 수단)
```cypher
-- 연결된 청크 '어느 하나에도' 인용문이 없으면 환각 의심
MATCH (n) WHERE n.evidence IS NOT NULL
  AND NOT EXISTS {
    MATCH (n)-[:EVIDENCED_BY]->(c:Chunk) WHERE c.text CONTAINS n.evidence
  }
RETURN labels(n)[0], n.evidence LIMIT 20;
```
> **주의**: 청크별로 검사하면 안 된다. `evidence_chunk`가 'C0,C1'처럼 여러 청크를 가리킬 때 인용문은 그중 한 청크에만 있으므로, 나머지 청크가 전부 오탐으로 잡힌다(few-shot 5개 중 3개가 이 형태다). 위처럼 **연결된 청크 전체에 대해 EXISTS로** 판정해야 한다.
>
> 그래도 인용이 청크 경계를 걸치면 어느 단일 chunk에도 온전히 담기지 않으므로 **경고 수준**이다(0건이 이상적이나 위반이 곧 환각은 아니다). 청크를 이어붙여 원고 전문과 대조하는 스크립트는 후속으로 검토한다.

### 5. 누락됐던 상태 (변경 6) — 리포트 S3-8의 7건
프롬프트에는 넣지 않고 판정 기준으로만 쓴다.

| 대상 | 기대 상태 |
|---|---|
| 김독자 | 코인 100 (5화) |
| 김독자 | 코인 6200 → 2700 투자 → 3500 (6화) |
| 김독자 | 업적 '최초의 살해' (5화) |
| 김독자 | 업적 '대량 학살자' (6화) |
| 김독자 | 스킬 '등장인물 일람' (6화) |
| 김남운 | 능력치 4종 (6화) |
| 김독자 | 계약 만료 임박 (2화) |

```cypher
MATCH (s:CharacterState)
  WHERE s.state CONTAINS '코인' OR s.state CONTAINS '업적' OR s.state CONTAINS '체력'
  RETURN s.state;                                                      -- 이전엔 0건
```

### 6. description에만 남은 사실 (변경 4)
완전 자동 검증은 어렵다. 가장 흔한 실패형인 **소속**만 기계적으로 잡는다.
```cypher
MATCH (c:Character), (o:Organization)
WHERE c.description CONTAINS o.name
  AND NOT EXISTS { MATCH (c)-[:HAS_STATE]->(s:CharacterState)-[:ABOUT]->(o) }
RETURN c.name, o.name, c.description;                                  -- 0건이 이상적
```
> 리포트 S2-4 부수(이지혜의 태풍여고 소속 누락)는 태풍여고가 Location으로 잘못 만들어져 있어 이 쿼리로는 안 잡힌다 — resolver 수정 후에야 유효해진다.

### 7. aliases 위생 확인 (변경 5, 판정 항목 아님)
```cypher
MATCH (c:Character) RETURN c.name, c.aliases, c.description ORDER BY c.name;
```
나열된 호칭이 **원문에 실제로 등장하는 표기**인지(서술형 별칭 창작 금지), 별칭이 여전히 description에 중복으로 남아 있지 않은지 확인한다.

### 8. 수동 확인 (자동 검증이 없는 항목)
- **요약 환각**(변경 7): `MATCH (c:Chapter) RETURN c.number, c.summary` — 1화에 "특별한 관계", 2화에 "멸망 이후의 세카이"가 **없어야** 한다
- **story_order**(변경 8): 6화 Event를 story_order 순으로 뽑아 칼 상처가 체력 강화보다 **앞**인지 확인

### 9. 회귀 확인
리포트의 "✅ 정확했던 것"을 재확인한다 — 특히 **유상아·이현성의 6화 미등장**(`APPEARS_IN`에 6 없음), **폐허 서울을 Location으로 만들지 않음**, 엑스트라·온라인 공간 미생성. 변경 6(상태 적극 추출)이 **과추출 쪽으로 진자를 밀 수 있으므로** 중요하다.

변경 2의 규칙 이전이 제대로 됐는지도 반드시 본다.
```cypher
-- tls123 ↔ 김독자를 RELATED_TO로 평탄화하지 않았는지 (이전엔 올바르게 없었음)
MATCH (a:Character {name:'tls123'})-[r:RELATED_TO]-(b:Character {name:'김독자'})
  RETURN count(r);                                                     -- 0 이어야 함
```

### 10. 판정에서 제외할 항목
resolver 미수정으로 **재현이 예상되는** 것들 — Location `3807/3907` 병합, `LOCATED_IN` 자기참조 2건, 태풍여고/교실 병합, 그에 딸린 `HOSTS` 오귀속. 이번 변경의 성패와 무관하다.

---

## 이번 범위 밖

- **resolver 숫자 가드**(S1-1) — 고유명에 든 숫자가 다르면 병합 금지. 파급이 가장 크므로 다음 우선순위. `poc/src/resolver.py`의 `compute_similarity` 또는 병합 직전 필터
- **S3-7 시나리오 규칙 구조화** — `Scenario` 노드는 장르 특정 개념이라 general 스키마에 맞지 않아 **불채택**. 다만 변경 3의 `Event.evidence`가 원문(`난이도 F / 제한시간 30분 / 보상 300코인 / 실패시 사망`)을 인용으로 보존하므로 정보 손실은 없다. 구조적 조회는 포기하는 의도적 트레이드오프
- **`Organization` 계층 관계**(`PART_OF`) — `인사팀 ⊂ 대기업 계열사`를 표현할 수단이 없어 LLM이 둘 중 하나를 고르게 된다. S3-12(유상아 소속의 evidence는 "인사팀"인데 `ABOUT`은 "대기업 계열사")와 S3-11(재무팀은 있는데 인사팀은 없는 입도 비대칭)의 공통 원인. `Location`의 `LOCATED_IN`과 같은 규약이고 장르 중립적이다(문파-분타, 회사-부서, 군대-부대). 재인덱싱 변수를 늘리지 않기 위해 resolver 숫자 가드와 같은 라운드로 미룬다
- **`RELATED_TO`의 시점 추적** — S3-11의 김남운 팀 제안·거절(적대 전환의 직접 원인)이 누락됐다. `schema.py:243`이 의도적으로 시점을 배제하고 CharacterState를 대안으로 제시했으나 **실제로는 둘 다 만들어지지 않았다**. 이번 재인덱싱 후에도 관계 변화가 안 잡히면 재검토
- **`aliases`를 `Location`·`Organization`·`Item`으로 확대** — 표기 변형 실패 근거가 없어 보류. 변경 5의 효과 확인 후 판단
- **S3-9** 할머니·프롤로그 주인공 Character 누락 (비중 필터 관련)
- **S3-10** HOSTS 누락 5건 / **S3-11** 자전거·수표·인사팀 등
- **S2-4 부수** 이지혜의 태풍여고 소속 상태 누락 (병합이 아니라 추출 누락)
- **story_order 자동 검증** — `evidence_chunk`의 `C{i}`가 원문 순서이므로 story_order 순위와 대조해 역전을 탐지 가능. 변경 8의 프롬프트 규칙이 실패하면 도입 검토

---

## 알려진 한계

- **모순 탐지를 구조적으로 보장하지 않는다.** "지금 어깨 상태는?" 같은 시점 조회는 `state` 문자열 매칭이 아니라 n-hop 수집 + LLM 판단에 의존한다. 의도된 트레이드오프다
- 상태가 회차 누적으로 쌓이면(32화 기준 인물당 수십 개) n-hop 확장 시 컨텍스트가 커진다. 필터가 필요해지면 `attribute`가 아니라 `ESTABLISHED_IN`→`Event.chapter`(시점)나 evidence 유사도를 축으로 쓴다 — LLM 생성값에 의존하지 않는 축이다
- 이번 재인덱싱은 변경 1~9가 한꺼번에 들어가므로 **개별 효과 분리는 불가능**하다. 리포트 대비 총량 개선만 본다
