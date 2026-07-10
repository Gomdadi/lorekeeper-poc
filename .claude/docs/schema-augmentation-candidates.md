# 스키마 추가 검토 목록 (목표 2 + 2-B)

원고 자동추출(`schema_suggest.py`, input.txt)과 형제 프로젝트(kgdb·soma-poc) 스키마 차용 분석을 합쳐,
현행 `poc/src/schema.py`(Character / Location / Event / CharacterState)에 **추가를 검토할 후보**를 정리한다.
**이 문서는 발견/판단용이며 schema.py를 자동 반영하지 않는다.**

---

## 1. 방법과 핵심 한계

- 자동추출: `SchemaFromTextExtractor`가 input.txt(31375자)에서 스키마를 제안 → `output/schema_suggested.json` 저장.
- 비교 기준은 **label 텍스트 완전일치**다. 따라서 의미가 같아도 이름이 다르면 "추가 후보"로 잡힌다
  (예: 자동추출 `Person` = 우리 `Character`). raw 차집합을 그대로 후보로 보면 안 되고 **의미 기준으로 재분류**해야 한다.
- 자동추출은 **원고 표면에 드러난 것만** 제안한다 → 시간축·상태변화 같은 설계 결정은 못 잡고(아래 3절),
  장면 속 구체 사물이나 장르 특정 개념에 과적합하는 경향이 있다.

---

## 2. 자동추출 결과의 의미 기준 재분류

이번 원고는 "전지적 독자 시점"류(시나리오·코인·스킬·시스템 메시지·지하철 장면)라 그 색이 강하게 묻어났다.

| 자동추출 라벨 (속성) | 판정 | 사유 |
|---|---|---|
| `Person`(name, age) | **중복** | 우리 `Character`와 동일. 이름만 다름 |
| `Skill`(name, level) | **후보 ○** | 인물의 능력. 우리에 없음. kgdb `skill`과 교차 |
| `Trait`(name, category) | 후보 △ | 특성. 단 우리 `Character.description`과 경계 정리 필요 |
| `Scenario`(number, difficulty, reward_coins, ...) | 후보 △ | 게이트/던전. 장르 편향 강함(헌터물 한정) |
| `Message`(content) | 후보 △ | 게임 시스템 메시지. kgdb `system`/`status`와 교차 |
| `Coin`(amount) | 후보 △ | 재화. Item류로 일반화 대상 |
| `Entity`(species) | 기각 | 너무 일반적/모호 |
| `Novel`, `Episode`, `Author` | 기각 | 작품·회차·작가 = 메타데이터. KG 추출 대상 아님 |
| `Platform`,`Train`,`Cell`,`Door` | 기각 | 특정 장면의 구체 사물. 과적합 → Location/Item으로 추상화할 대상이지 라벨 아님 |

관계 후보 중 유의미: `HAS_SKILL`, `HAS_TRAIT`, `FACES`(시나리오 대면), `EARNS`(재화 획득). 나머지(RIDES/HAS_DOOR 등)는 과적합.

**요약**: 자동추출 노드 14개 중 실제 검토가치는 `Skill`·`Trait`·`Scenario`·`Message`(시스템)·`Coin`(재화) 정도. 나머지는 메타 혼입 또는 장면 과적합 노이즈.

---

## 3. 자동추출·형제 프로젝트 모두 못 가진 우리 고유 설계 (현행 유지 권장)

- `Event.chapter` / `Event.story_order`(FLOAT fractional indexing) — 연대기 순서.
- `CharacterState` + `ESTABLISHED_IN` — 상태변화의 시점별 유효값 조회/모순 탐지.
- `LOCATED_IN` 엄격 1단계 계층 — 장소 계층 복원.

자동추출은 이 4개(Character/CharacterState/Event/Location)와 5개 관계를 **전부 놓쳤다**. 두 형제 저장소에도 시간축이 없다.
→ 우리 핵심 강점이며 **유지가 정답**.

---

## 4. 형제 프로젝트 차용 후보 (2-B)

### kgdb (`docs/schema/schema.cypher`, `docs/guides/kgdb-knowledge-layer-guide.md`, `docs/lorekeeper/data-and-workflows/auto-extraction-json-contract.md`)
- **SourceSpan 증거 노드 + EVIDENCED_BY** — `CharacterState.evidence`(문자열 복사)를 증거 노드로 승격. 충돌탐지 "어느 회차/문단이 근거냐"에 직결.
- **Character.aliases[]** + 후보→노드 2단계 병합 — 단일 name 강제로 버려지는 별칭 보존.
- **인물↔인물 관계**(`knows`/`opposes`/`alliedWith`) — 우리 PATTERNS에 전무. 소설 관계망의 핵심.
- **reviewStatus / lifecycleStatus** — 주석 처리된 retcon 필드를 "작가 검토/확정" 워크플로로 대체.
- 웹소설 엔티티(`item`/`organization`/`skill`/`title`/`status`/`system`/`rule`) + `causes`(인과) 술어.

### soma-poc (`mvp/src/llm_indexer.py`, `mvp/indexing.md`)
- **~~관계 `since`/`until` 유효구간~~ → 차용하지 않음 (우리 설계와 충돌)**. `until`은 ⓐ추출 시점에 종료 시점을 알 수 없고 ⓑ종료 시 과거 노드를 소급 수정해야 해서, 우리 `CharacterState`의 **append-only + 순서 기반 무효화**(`schema.py:151-154`: 새 노드만 추가, `ESTABLISHED_IN`→`chapter`의 "시점 이하 최댓값"이 현재값) 원칙과 정면 충돌한다. `since`는 우리 `ESTABLISHED_IN`과 중복. → **관계의 시간 추적이 필요하면 `until`이 아니라, CharacterState 패턴(상태 노드 + ESTABLISHED_IN + 순서 무효화)을 인물↔인물 관계에도 확장**하는 것이 일관된 해법.
- **Item / Organization 노드** — 소속 배신·아이템 소유 이동 등 관계형 충돌의 무대.

---

## 5. 교차 검증 → 최종 추가 검토 목록 (우선순위)

자동추출과 형제 프로젝트가 **독립적으로 같은 방향을 가리키면** 신뢰도가 높다.

| 우선 | 후보 | 자동추출 | 형제 | 비고 |
|---|---|---|---|---|
| 1 | **인물↔인물 관계**(knows/opposes/alliedWith) | ✗ | kgdb ○ | 자동추출은 놓쳤으나 소설 KG 핵심. 강력 추천 |
| 2 | **Item / Organization 노드** | Coin △ | kgdb·soma ○ | 관계형 충돌(소유 이동·소속 변경)의 무대 |
| 3 | **Skill**(능력) | ○ | kgdb ○ | 두 소스 교차. 능력 성장물에 유의미 |
| 4 | **SourceSpan 증거 노드** | ✗ | kgdb ○ | 충돌탐지 근거 추적 인프라. evidence 승격 |
| 5 | **시스템 메시지/상태**(Message→system/status) | Message △ | kgdb ○ | 게임/헌터물 한정. 장르 편향 주의 |
| 6 | **reviewStatus/lifecycleStatus** | ✗ | kgdb ○ | 작가 검토/확정 워크플로 |
| — | ~~관계 since/until~~ | — | — | **차용 제외** — until은 append-only·순서 무효화 원칙과 충돌(4절 참고). 관계 시간추적은 CharacterState 패턴 확장으로 |
| — | Trait / Scenario | ○ | kgdb 일부 | 장르 편향 강함. Character.description·Event와 경계 정리 후 판단 |

### 장르 편향 주의
`Scenario`·`Coin`·`Skill`·시스템 메시지는 **게임/헌터물에 강하게 특화**돼 있다. 스키마는 웹소설 전반(로맨스·무협 포함) 범용성을 지향하므로, 이들을 상시 스키마에 넣을지 vs 장르별 확장으로 둘지는 **타겟 장르 범위 결정**에 달렸다.

---

## 6. 산출물
- `poc/output/schema_suggested.json` — 자동추출 원본 스키마(속성 포함, 상세 검토용).
- 실행: `cd poc && uv run python src/schema_suggest.py` (원고 `LOREKEEPER_INPUT`, 강도 `LOREKEEPER_REASONING`으로 조절).
