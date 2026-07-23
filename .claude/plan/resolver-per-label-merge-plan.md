# Resolver 라벨별 병합 전략 재설계

## Context

직전 KG 재설계(커밋 `01f6425`)로 Event/CharacterState가 `name`을 갖게 되면서, 단일
`CombiningFuzzyResolver`(WRatio 0.85, 전 도메인 라벨 대상)가 이들을 병합 후보로 끌어들였다. 이는
`.claude/plan/kg-name-description-redesign-plan.md`의 **"감수하는 것 1"** 로 명시된 위험이다:
(a) 긴 서술구의 WRatio 오병합("어깨를 베임" vs "옆구리를 베임"), (b) 병합 시 `evidence_chunk`가
catch-all `discard`(resolver.py:38)로 소실돼 근거 추적 불가, (c) resolver가 `link_evidence`보다
먼저 돌아 소실 확정.

**핵심 통찰 — name의 성격이 라벨마다 다르다:**
- **Character / Item / Location / Organization**의 name = **정준 유일 식별자**("김독자", "탑의 문").
- **CharacterState / Event**의 name = **서술형**("스물여덟 살", "지하철 급정거 사고"). 전역 유일이
  아니라 인물·회차를 가로질러 충돌한다. 게다가 유사도(fuzzy/embedding)로 가르려는 차이가 하필
  숫자·부위 같은 작은 결정적 토큰이라, 어떤 매칭도 신뢰할 수 없다.

그래서 **라벨(node type)마다 병합 전략을 다르게** 건다:

| 라벨 | 전략 | 이유 |
| --- | --- | --- |
| Character | **fuzzy** (WRatio 0.85) | 짧은 이름 표기변형(김독자/독자 씨) coref. 유사도가 값을 하는 유일한 자리 |
| Item / Location / Organization | **정규화 exact-match** | 정준 이름. 표기 흔들림 흡수 + 숫자 오병합 0("지하철 3807칸"≠"3907칸") |
| CharacterState / Event | **무병합** | 서술형 name의 충돌·이력파괴·근거소실 원천 차단 |
| (병합 시) description | **LLM collapse** | combine된 배열을 luna-high로 한 문자열로 합침 |

**부수 효과 (설계의 핵심 이점):** `evidence_chunk`/`EVIDENCED_BY`를 가진 라벨은 Event·CharacterState
**둘뿐**이다. 이 둘을 무병합으로 두면 **어떤 병합도 근거를 건드리지 않는다** — "감수하는 것 1"이
단순 완화가 아니라 **완전 해소**된다. resolver↔link_evidence 순서 문제도 무의미해진다.

의도한 결과: 라벨 특성에 맞는 병합, 오병합·근거소실 제거, 그리고 병합 description의 유실 없는 단일화.

---

## 변경

수정 대상은 **2개 파일**(`resolver.py`, `indexing.py`). `pipeline.py`·`schema.py`는 무변경
(`build_pipeline`은 여전히 resolver 하나를 컴포넌트로 받는다).

### 1. `poc/src/resolver.py`

기존 3 resolver(`CombiningFuzzyResolver`/`CombiningExactMatchResolver`/`OpenAIEmbeddingResolver`)는
**building block으로 유지**. 셋을 신설한다.

**(a) `NormalizedExactMatchResolver(CombiningExactMatchResolver)`** — 정규화 후 완전일치. 그룹핑
키를 `entity.name`(resolver.py:198) 대신 **공백·꺾쇠·따옴표·괄호류를 제거한 문자열**로 바꾼다
(예: `apoc.text.replace(entity.name, '[\\s<>《》「」『』()\\[\\]]', '')`). **한글엔 대소문자가 없어
`toLower`는 쓰지 않는다.** 숫자·문자는 보존되므로 `탑의 문`↔`탑의문`↔`<탑의 문>`은 병합하되
`3807칸`≠`3907칸`은 분리한다. 병합 후 남는 `name`은 첫 노드의 **원본 표기**(정규화 문자열은 그룹핑에만
쓰고 저장하지 않음). run() 본문은 `CombiningExactMatchResolver.run()`을 복사하고 그룹핑 키 한 줄만
정규화로 교체(코드베이스의 run() 복사 패턴과 일관). 문자셋은 실측하며 조정.

**(b) `PerLabelResolver(EntityResolver)`** — 라벨별 스코프 resolver를 순차 실행하는 조립 resolver.
기존 resolver들이 이미 지원하는 `filter_query`(Cypher WHERE, `_run_combining_similarity`의
resolver.py:48-50 및 exact의 resolver.py:184-186에서 MATCH에 append됨)로 라벨을 좁힌다.
```
CombiningFuzzyResolver(filter_query="WHERE entity:Character")
NormalizedExactMatchResolver(filter_query="WHERE entity:Item OR entity:Location OR entity:Organization")
```
- CharacterState/Event는 어느 스코프에도 없음 = **무병합**(자연 배제).
- `run(self) -> ResolutionStats`: 각 sub-resolver.run()을 순차 await하고 카운트를 합산 반환.
  기존 resolver들과 동일하게 클래스에 run()을 직접 정의(ComponentMeta 요구).
- `EntityResolver`(라이브러리 `resolver.py`의 Component 베이스)를 상속하고 `super().__init__(driver=driver)`.

**(c) `collapse_merged_descriptions(driver, database)`** — 병합 후처리. `_MERGE_PROPS`의
`description:'combine'`(resolver.py:38)로 병합 시 description이 **배열**이 되는데, 이를 LLM으로
한 문자열로 합친다.
- 대상 탐지: `MATCH (n) WHERE n.description IS NOT NULL AND NOT n.description IS :: STRING`
  (배열 타입 = 병합 흔적. kg-redesign 검증(5)의 그 신호 재사용).
- 각 노드의 `name` + 서술 배열을 `build_llm("high")`(pipeline.py:71, lazy import)에 넣어 병합.
- **제약 프롬프트(필수)**: ① 입력 서술에 실제로 있는 내용만 — 새 사실·인과·감정·배경 추가 금지
  ② 모순되면 하나를 고르거나 화해시키지 말고 **둘 다 병기** ③ 중복은 한 번만.
- 결과를 `SET n.description = $merged`(문자열)로 되돌림 → 다음 회차엔 재-collapse 대상 아님(멱등).

모듈 docstring도 "3종 비교"에서 "라벨별 조립(PerLabelResolver) + description collapse" 체제로 갱신.

### 2. `poc/src/indexing.py`

- import: `from resolver import PerLabelResolver, collapse_merged_descriptions` (기존
  `CombiningFuzzyResolver` import 교체).
- indexing.py:132: `CombiningFuzzyResolver(...)` → `PerLabelResolver(driver=driver, neo4j_database=database)`.
  `build_pipeline` 시그니처·배선 불변.
- indexing.py:151(`link_evidence` 호출) **뒤에** `await collapse_merged_descriptions(driver, database)` 추가.
  (collapse는 병합 후여야 하고 link_evidence와 순서 무관 — evidence 라벨은 병합 안 되므로.)

---

## 열린 하위 결정 (기본값 채택, 실측 후 조정)

1. **CharacterState 중복의 novel_context 토큰 비용.** 무병합이라 중복 노드가 쌓일 수 있으나 correctness엔
   무해. "배경 컨텍스트 길이" 로그상 과하면, 노드 병합이 아니라 `context.dump_graph_text`에서 같은
   `(Character, name)` 상태를 한 줄로만 렌더링하는 **덤프-레이어 dedup**으로 대응(근거·이력 완전 보존).
   **이번 스코프 밖**, 계측 후 판단.

---

## 검증

재인덱싱(`docker exec ... "MATCH (n) DETACH DELETE n;"` 후 1~N화 순차) 뒤 Cypher로 확인.
저비용 경로: **1~2화만 먼저 인덱싱**해 라벨 스코핑·collapse 동작을 눈으로 확인한 뒤 전체.

1. **근거 보존(회귀, 최우선)** — 병합으로 근거 잃은 Event/CharacterState 0:
   ```cypher
   MATCH (f) WHERE (f:Event OR f:CharacterState) AND NOT (f)-[:EVIDENCED_BY]->()
   RETURN labels(f)[0] AS lab, count(*) AS n;
   ```
2. **CharacterState/Event 무병합 확인** — 이 두 라벨엔 배열 description이 없어야(무병합):
   ```cypher
   MATCH (n) WHERE (n:Event OR n:CharacterState) AND NOT n.description IS :: STRING RETURN count(*);  -- 0
   ```
3. **Character 병합** — 표기변형 동일인물이 한 노드로. 동명·별칭 중복 Character 수 감소.
4. **Item/Loc/Org** — 완전동일 name 중복은 병합, 숫자 다른 것은 미병합(직접 확인).
5. **collapse 완료** — 모든 배열 description이 문자열로 collapse됨:
   ```cypher
   MATCH (n) WHERE n.description IS NOT NULL AND NOT n.description IS :: STRING RETURN count(*);  -- 0
   ```
6. **ResolutionStats 로그** — 라벨별 병합 건수가 재인덱싱 로그에 찍히는지, 변경 전(전 라벨 fuzzy) 대비
   Event/CharacterState 병합이 사라졌는지 대조.

> 재인덱싱은 비용이 든다(직전 실측 6화 누적 약 $0.43). 사용자 승인 후 실행하며, 이 계획의 코드 변경
> 자체는 재인덱싱 없이 완료할 수 있다(검증만 재인덱싱 필요).
