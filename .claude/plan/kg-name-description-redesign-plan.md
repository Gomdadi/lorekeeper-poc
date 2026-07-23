# KG 스키마 재설계 — 전 노드 `name` + `description` 통일

## Context

직전 라운드(커밋 `01f6425`)에서 `CharacterState`를 `state` 단일 속성으로, `Event.description`을 `evidence`(원문 인용)로 바꾸고 1~6화를 재인덱싱해 전수 검증했다. 결과는 갈렸다.

- **성공**: 인용 환각 0건(evidence 88건 전부 원문 실재 문장), `소속=소속` 같은 무정보 상태 소멸, story_order 역전 해소
- **실패**: 환각이 사라진 게 아니라 **`title`로 이동**했다 — 5화 `"할머니를 집단 폭행해 살해함"`(할머니는 생존, 6화 원문이 직접 반박), 4화 `"도깨비가 국무총리를 살해함"`(원문은 주체 미특정)

원인은 명확하다. `Event.description`을 없애면서 서술 부담이 `title`로 옮겨갔는데, `title`에는 근거 제약이 없었다. 이번 라운드의 **1차 대응**(`schema.py:106-112`에 title 인용 범위 제약 추가)은 이미 적용돼 있으나, 사용자 판단은 더 근본적인 재설계다.

**재설계의 논지**: `evidence`로 원문을 붙들어 두는 방식은 어차피 `evidence_chunk` → `EVIDENCED_BY` → `Chunk`로 근거를 따라갈 수 있으므로 중복이다. 근거 추적은 chunk 링크에 전적으로 맡기고, 각 노드는 **`name`(무엇인지)** 과 **`description`(어떤 상태·사건인지, 추론이 아니라 근거 청크의 원문에 기대어)** 두 속성으로 통일한다. 그러면 `title`/`state`/`evidence`라는 타입별 특수 속성이 사라지고 전 노드가 같은 모양이 된다.

**의도한 결과**: 노드 형태의 통일, 서술이 갈 자리의 명확화(`title`로 새던 환각이 근거 제약이 걸린 `description`으로), 그리고 특수 속성 제거로 인한 스키마 단순화.

---

## 설계 원칙 — `name`과 `description`의 경계

이 변경의 성패가 걸린 지점이다. 사용자 결정에 따라 **`name`은 기존 `state`/`title` 문구를 그대로 유지**한다. 즉 name은 짧은 라벨이 아니라 이미 자기서술적인 구다. 따라서 description을 "그 노드가 무엇인지 설명"이라고만 규정하면 **필연적으로 동어반복이 된다**.

| | 담는 것 | 담지 않는 것 |
|---|---|---|
| `name` | 사건·상태를 한 구절로 식별하는 자기서술적 구. n-hop으로 딸려왔을 때 그 줄만 읽어도 뜻이 통하는 최소 단위 | 정황·경위·정도·범위 |
| `description` | name이 압축하며 **버린 것**의 복원 — 누가 관여했는지, 어떤 계기로, 어느 정도로, 어떤 순서로, 원문이 결과를 어디까지 서술했는지 | name의 재진술. 원문에 없는 인과·동기·감정 |

```
name:        "어깨를 칼날에 깊이 베임"
description: "김남운이 팀 제안을 거절당한 뒤 맥가이버 칼로 공격했고,
              칼날이 심장을 빗나가 어깨를 긋고 지나갔다"
```

**긍정 서술만으로는 LLM이 재진술로 때운다.** 두 문장을 스키마·프롬프트 양쪽에 음성 규칙으로 못 박는다:

> **"name을 어미만 바꿔 되풀이하면 이 속성은 쓰지 않은 것과 같다."**
>
> **"덧붙일 정황이 원문에 없으면 근거 서술을 풀어 쓰는 데서 그친다 — 짧은 서술이 지어낸 서술보다 낫다."**

두 번째 문장이 특히 중요하다. 빈칸을 채울 도피처를 열어 주지 않으면 LLM이 지어낸다.

### 축자 인용 없이 환각을 억제하는 세 겹

`evidence` 제거로 substring 앵커가 사라진다. 대체 장치:

1. **범위 앵커** — "`evidence_chunk`가 가리키는 청크의 원문에 근거해 쓴다. **그 청크에 없는 내용은 쓰지 않는다.**" 근거 대상을 노드 자신이 선언한 청크로 한정한다(자동 검증의 접점)
2. **어휘 앵커** — "**고유명·수치·호칭은 원문 표기 그대로 쓴다.**" 축자 *문장*은 포기하되 축자 *토큰*은 유지시킨다. 이것이 아래 검증 (1)을 살려 두는 결정적 장치
3. **단정 금지** — 현행 `title` 규칙의 강점을 이식: "원문이 명시하지 않은 결과(사망·성공·실패)나 인과를 단정하지 않고, 원문이 진행 중이면 진행 중으로 쓴다"

---

## 파일별 변경

수정 대상은 **4개 파일**이다. 전수 grep 결과 `title`/`state` 속성명을 참조하는 코드는 `schema.py`·`extraction_examples.py`·`context.py` 세 곳뿐이며, `evidence.py`(`evidence_chunk`와 라벨만 참조)·`indexing.py`·`chunks.py`는 수정이 불필요하다. `Event.title`/`CharacterState.state`에 걸린 인덱스·제약도 없다.

### 1. `poc/src/schema.py` — 유일 권위

**`EVENT`(L95-148)**
- `title`(L103-113) → `name`으로 키 교체. 현행 title 문구의 "evidence가 담당" → "정황은 description이 담당", "evidence에 인용된 범위" → "evidence_chunk가 가리키는 원문 범위"로 조정. 단정 금지 규칙은 그대로 유지
- `evidence`(L130-138) → `description` 신설. 위 설계 원칙의 문구 적용
- `evidence_chunk`(L139-146) 유지. `"근거 원문 문장이 있는 청크"` → `"근거가 되는 원문이 있는 청크"`(축자 문장을 더는 뽑지 않으므로 "그 문장"이 겉돈다) + `"description은 이 청크의 원문에만 근거해야 하므로 실제 근거 청크를 빠짐없이 적는다"` 추가
- properties 선언 순서를 `name→description→chapter→story_order→evidence_chunk`로 정리(few-shot 키 순서와 일치시킴)

**`CHARACTER_STATE`(L150-195)**
- `state`(L169-180) → `name`. 기존 문구 대부분 유지하되 **L177의 `"(원문 인용은 evidence가 담당)"` → `"(성립 정황은 description이 담당)"`**. 이 파일에서 가장 놓치기 쉬운 한 곳
- `evidence`(L181-185) → `description`. 한 줄 축약형을 여러 줄로 확장. `"한 근거 문장에서 여러 상태를 뽑았다면 각 description은 그 상태에 해당하는 부분에만 초점을 맞춘다(같은 서술을 여러 노드에 복사하지 않는다)"` 포함
- 노드 자체의 description(L153-167)은 **무변경** — `state`/`evidence` 속성명을 참조하는 문구가 없다(L154-166의 "상태"는 전부 도메인 어휘)

**나머지 4개 노드**(`CHARACTER` L57-66 / `LOCATION` L84-91 / `ORGANIZATION` L211-218 / `ITEM` L242-250)
- 속성 구성은 그대로. description에 `"원문에 근거해 쓰고 추론·평가를 덧붙이지 않는다"` 한 문장씩 추가
- 기존 정준 문구(`"구조로 표현 가능한 사실은 여기에만 두지 말고…"`, L63/L89/L216/L247/L301)는 **유지** — 속성 개편과 독립적인 규칙이다
- 이 네 노드에는 `evidence_chunk`가 없으므로 "청크 원문에 근거" 앵커 문구는 쓰지 않는다. 존재하지 않는 속성을 요구하게 된다. **이 비대칭은 의도적**이며 근거 추적의 무게중심이 Event/CharacterState에 있다는 기존 설계와 일치

**모듈 docstring**(L1-14): L10의 `evidence(원문 인용) + evidence_chunk` → `evidence_chunk` 단독으로. 전 노드 `name`+`description` 통일 규약을 한 문장 추가

### 2. `poc/src/extractor.py` — 프롬프트

도메인 규칙(L79-123) 중 네 곳만 손댄다. **L83-87, L91-114, L118은 무변경** — 추출 *대상*을 바꾸는 변경이 아니므로 범위를 건드리면 안 된다(검증 (6)의 전제).

| 위치 | 처리 |
|---|---|
| L88-90 (`state` 서술 규칙) | `Event.name`/`CharacterState.name` 공통 규칙으로 교체. "정황은 description이 담당" 명시 |
| L119-120 (`evidence` 인용 규칙) | **`description` 규칙으로 전면 교체** — 이번 변경의 무게중심 |
| L121-122 (`evidence_chunk`) | `"description은 여기 적은 청크의 원문에만 근거해야 하므로 실제 근거 청크를 빠짐없이 적는다"` 추가 |
| L115-117 (구조 완전성) | 첫 문장 `"모든 description은 …부가 속성일 뿐이다"`가 이제 오해를 부른다(Event/CharacterState의 description은 근거 서술의 핵심이 됐다) → `"원문에 근거해 설명하는 자리다"`로 조정. 뒷문장(구조로 표현 가능한 사실이 description에만 남아선 안 됨)은 유지 |

### 3. `poc/src/extraction_examples.py` — few-shot (가장 손이 많이 감)

few-shot은 **스키마 문구보다 강한 신호**다. 동어반복 예시를 하나라도 보이면 위 설계 원칙이 무력화된다. Event 6개 + CharacterState 13개 = **19개 노드 전부** 재작성.

- 키 순서: Event `name→description→chapter→story_order→evidence_chunk`, CharacterState `name→description→evidence_chunk`
- `name`은 기존 `title`/`state` 값을 **그대로 유지**. `description`만 신규 작성
- 모듈 docstring L11-12의 불변식(`"evidence 값은 입력 텍스트에 문자 그대로 존재해야 한다"`)을 교체 — 축자는 아니지만 **입력 텍스트에서 확인 가능한 정황만** 담아야 하고, name을 되풀이하지 않아야 한다

**핵심 시연 두 가지** (이것이 없으면 재작성의 의미가 없다):

- **예시2 id3(L54) `"청운의 화산파 입문"`** — 원문이 한 문장뿐이라 정황을 지어낼 수 없다. description을 `"…입문 경위나 조건은 원문에 서술되지 않는다"`로 끝내 **서술의 한계 자체를 명시하는 패턴**을 가르친다. 6개 Event 중 가장 중요한 한 줄이며, 도피처 규칙의 few-shot 구현체다. 예시2 하단에 이를 설명하는 주의 문구 추가

- **예시4 id5/id6/id9(L111/L112/L115)** — 셋이 **동일한 evidence 문자열을 복사**하고 있었다(축자 인용 체제의 불가피한 산물). 새 체제에서는 최고의 교재가 된다: 같은 청크(C0)의 같은 문장을 근거로 삼되 description은 각자 자기 name이 다루는 부분에만 초점을 맞춘다 — **고용형태 / 소속 / 나이**. 셋 다 짧고, 그것이 의도다(원문이 그 이상을 주지 않았으므로 늘리면 곧 환각). 예시4 하단에 '주의 4'로 명시

- Character/Location/Organization/Item의 description은 무변경. 특히 예시4의 `"대한물산 인사팀 사무직"`은 주의 2를 시연하는 장치이므로 **반드시 유지**

### 4. `poc/src/context.py` + `pipeline.py` 주석

**`_node_display`(L21-56)**
- key 우선순위(L31-40): 모든 도메인 노드가 `name`을 가지므로 `name` 하나로 단순화(`else "?"` 폴백 유지). L31-32 주석 갱신
- extras 제외 목록(L47-53): `("name", "title", "state", "evidence")` → `("name",)`. L42-46 주석도 evidence 설명 → description 설명으로 갱신
- **`description`을 덤프에 싣는다**(제외하지 않음). 근거: `extractor.py:137-139`의 "배경에 이미 있는 상태가 값이 안 바뀌었으면 다시 만들지 마라" 규칙은 상태의 동일성 판단을 요구하는데 name만으로는 미세한 차이를 가릴 수 없다. 다만 evidence를 뺐던 이유가 토큰 비용이었으므로(L43-44 주석), `indexing.py:107-110`이 이미 출력하는 "배경 컨텍스트 길이" 로그를 회차별로 기록해 증가 추이를 계측한다. 임계를 넘으면 그때 Event/CharacterState의 description만 라벨 기준으로 제외한다(사후 전환 비용이 낮다)

**`pipeline.py:140-141`** — `"resolver는 name/elementId로만 매칭하고"` 뒤에 `"(이번 개편으로 Event/CharacterState도 name을 가져 resolver 매칭 대상에 들어온다)"` 한 구절 추가. 아래 위험을 코드 근처에 남긴다

---

## 감수하는 것 (명시)

**1. resolver 오병합 위험을 열어 둔 채 진행한다** — 사용자 결정에 따라 이번 라운드에서 고치지 않는다.

메커니즘: `CombiningFuzzyResolver`의 게이트는 라벨이 아니라 **`name` 속성의 유무**다(`resolver.py:74-79`, `resolve_properties=["name"]`). 지금까지 `name`이 없어 병합 후보에서 빠져 있던 Event/CharacterState가 **이번 변경으로 즉시 병합 대상이 된다**. 그리고 `indexing.py`에서 resolver(파이프라인 5단계)가 `link_evidence`(6단계)보다 먼저 돈다. `_MERGE_PROPS`(`resolver.py:38`)의 catch-all `` `.*`:'discard' ``에 `evidence_chunk`가 걸리므로, N개가 병합되면 **첫 노드의 청크에만 EVIDENCED_BY가 생기고 나머지 N-1개의 근거 링크가 소실**된다.

근거 추적을 chunk 링크 하나에 몰아준 이번 설계에서 근거 링크 소실은 곧 **검증 불가능한 노드의 발생**을 뜻한다. 추가로, threshold 0.85의 WRatio는 짧은 인물명 기준으로 튜닝됐는데(`resolver.py:228-230` 주석) `"어깨를 칼날에 깊이 베임"` vs `"옆구리를 칼날에 깊이 베임"`처럼 긴 서술구는 다른 사실인데도 문자열이 대부분 겹친다.

→ **계측만 한다**(검증 (5)). 실측 후 다음 라운드 선택지는 (i) `filter_query`로 resolver 대상에서 제외, (ii) `_MERGE_PROPS`에 `evidence_chunk:'combine'` 추가, (iii) `link_evidence`를 resolver 앞으로 이동.

**2. 환각 자동 검증의 커버리지가 낮아진다** — 직전 라운드에서 88건 전부를 통과시킨 substring 매칭 수단이 사라진다. 아래 (1)은 고유명·수치만 잡고 서술 전반은 못 잡으며, (2) LLM judge가 공백을 메우지만 judge 자신이 완전하지 않다. **"축자 인용을 요구하지 않는다"는 요구사항의 직접적 대가이며 회피할 방법이 없다.**

**3. 배경 컨텍스트 토큰이 evidence 시절 수준으로 회귀할 수 있다** — 계측 후 판단(위 4번 항목).

---

## 검증

재인덱싱: `docker exec lorekeeper-neo4j cypher-shell -u neo4j -p lorekeeper "MATCH (n) DETACH DELETE n;"` 후 `cd poc && LOREKEEPER_CHAPTER=N LOREKEEPER_INPUT=data/input_chN.txt uv run python src/indexing.py`을 1~6화 순차 실행. 직전 실측 비용 6화 누적 **$0.43**.

> 현재 DB에는 직전 라운드 검증을 마친 그래프가 들어 있고 덤프는 스크래치패드에만 있다. 지우기 전 보존이 필요하면 먼저 백업한다.

**(4)를 가장 먼저 돈다** — 속성 키 오타는 `additional_properties=False` 때문에 GraphPruning이 **조용히** 제거하므로, 다른 검증에 앞서 배선이 살아 있는지부터 확인해야 한다.

**(4) 배선·근거 링크 커버리지 (자동)** — 기대값 0
```cypher
MATCH (f) WHERE (f:Event OR f:CharacterState) AND NOT (f)-[:EVIDENCED_BY]->()
RETURN labels(f)[0] AS lab, count(*) AS n, collect(f.name)[..10] AS samples;
-- description/name이 빈 노드도 함께 센다 (키 이름 오타는 여기서만 드러난다)
MATCH (f) WHERE (f:Event OR f:CharacterState) AND (f.name IS NULL OR f.description IS NULL)
RETURN labels(f)[0] AS lab, count(*) AS n;
```

**(5) resolver 병합 계측 (자동)** — 위 "감수하는 것" 1번 대응
- 재인덱싱 로그의 `ResolutionStats`(`number_of_nodes_to_resolve` / `number_of_created_nodes`)를 변경 전후 비교. 병합 건수가 뛰었다면 Event/CharacterState가 새로 병합되기 시작한 것
- `description`이 배열 타입인 노드 = `_MERGE_PROPS`의 `description:'combine'`이 걸린 흔적이자 **병합이 실제로 일어났다는 직접 증거**
```cypher
MATCH (f) WHERE (f:Event OR f:CharacterState) AND f.description IS NOT NULL
  AND NOT f.description IS :: STRING
RETURN labels(f)[0] AS lab, f.name AS name, f.description AS desc;
```

**(3) 동어반복 검출 (자동)** — 이번 변경이 무의미해지지 않았음을 증명하는 핵심 지표
`rapidfuzz`가 이미 의존성에 있다(`resolver.py`). `fuzz.token_set_ratio(name, description)`가 **0.9 이상이면 동어반복 의심**으로 플래그. 목표 5% 미만. description 길이 분포(중앙값/최솟값)를 함께 보고, 극단적으로 짧은 것들이 전부 정당한 케이스(원문이 정황을 주지 않은)인지 확인.

**(1) 어휘 근거율 (자동)** — substring 검증의 계승자
스키마가 "고유명·수치·호칭은 원문 표기 그대로"를 요구하므로 이 부분만은 여전히 축자다. `description`에서 한글 고유명사 후보와 수치 토큰을 뽑아 EVIDENCED_BY로 연결된 Chunk `text` 합집합에 존재하는지 대조한다. 미매칭 토큰이 환각의 1차 후보.
```cypher
MATCH (f)-[:EVIDENCED_BY]->(ck:Chunk) WHERE f:Event OR f:CharacterState
RETURN labels(f)[0] AS lab, f.name AS name, f.description AS desc, collect(ck.text) AS chunks;
```
> 직전 라운드의 `check_evidence2.py`(스크래치패드)가 참고 가능하나, **문장 단위 substring이 아니라 토큰 단위 대조로 다시 짜야 한다.** 그때 겪은 함정 두 개를 반복하지 말 것: CSV 파싱 시 `skipinitialspace=True`(따옴표 혼입), 그리고 문장 분할 시 `?"`·`>` 같은 종결자 처리.

**(2) LLM judge — 의미 수준 (반자동, 전수)**
(1)의 쿼리 결과 `(name, description, chunks)` 삼중항을 판정 프롬프트에 넣어 세 축으로 라벨링: ① description이 청크 원문으로 뒷받침되는가 ② 원문에 없는 단정·인과·감정이 있는가 ③ 원문이 진행 중인 것을 완료로 서술했는가. 88건 규모면 샘플링 없이 전수 가능. `context.summarize_episode`의 `build_llm("high")` 패턴 차용.

**(6) 회귀 기준선**
이번 변경은 **속성 개편이지 추출 대상 변경이 아니므로 노드/관계 수가 유의미하게 달라지면 안 된다.** 달라졌다면 프롬프트 문구 교체가 의도치 않게 추출 범위를 건드린 것이다(`extractor.py` L83-87/L91-114를 무변경으로 두는 이유). 직전 라운드 실측치와 대조: Character 8 / Event 36 / CharacterState 52 / Location 4 / Chapter 6, ABOUT 15 / APPEARS_IN 80 / HAS_STATE 52 / ESTABLISHED_IN 52 / EVIDENCED_BY 138.

**(7) 원래 문제의 해소 확인 — 이번 재설계의 출발점**
- 5화 할머니 Event: `name`에 "살해"가 **없어야** 함(원문이 진행 중이므로)
- 4화 국무총리 Event: `name`·`description` 어느 쪽도 도깨비를 살해 주체로 **단정하지 않아야** 함

---

## 실행 순서

1. **`schema.py`** — 유일 권위. 여기부터 확정해야 나머지 문구가 파생된다
2. **`extractor.py`** — 스키마 문구를 프롬프트 어투로 압축 이식
3. **`extraction_examples.py`** — 1·2가 정한 규칙을 19개 노드로 시연. 규칙 위반이 가장 치명적인 파일
4. **`context.py`** + `pipeline.py` 주석 한 줄 — 소비 측 배선
5. **재인덱싱 1~6화** → 검증 (4)(5)(3)(1) 자동 → (2) LLM judge → (6)(7) 대조

> 1~2화만 먼저 인덱싱해 그래프 덤프 형태와 동어반복 여부를 눈으로 확인한 뒤 3~6화를 진행하면, few-shot이 잘못 가르쳤을 때 전체 비용을 날리지 않는다.
