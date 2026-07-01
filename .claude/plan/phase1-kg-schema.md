# Phase 1: LoreKeeper KG 스키마 설계 (초안)

## Context

LoreKeeper는 웹소설 작가를 위한 설정 충돌 감지 서비스다. 현재 `poc/src/schema.py`의 더미 스키마(`Character(name, status)`, `Location(name)`, `Event(title, chapter)` + `APPEARS_IN/LOCATED_AT/INVOLVES`)는 "지금 상태"만 표현할 뿐, 시간에 따른 상태 변화를 담지 못한다. `Character.status`처럼 단일 속성을 덮어쓰면 "5화 시점엔 오른팔이 있었다"는 과거 사실이 사라져서, 신규 회차와 과거 설정을 대조하는 충돌 탐지 자체가 성립하지 않는다.

`verification-phases.md`의 Phase 1 성공 기준은 세 가지다: (1) 상태 변화·시간 구조·공간 계층을 표현할 수 있는가, (2) 특정 시점 기준 상태를 Cypher로 정확히 조회할 수 있는가, (3) 레트콘 시 과거 노드/엣지를 소급 수정할 수 있는 구조인가. 이 계획은 더미 스키마를 이 세 조건을 만족하는 스키마로 교체하는 초안이다.

**중요**: `GraphSchema`(neo4j-graphrag-python)는 Python 메모리 객체로, `SimpleKGPipeline`이 LLM에게 "무엇을 추출할지" 가이드하는 용도다(`.claude/docs/schema-components.md`). 시간 축/버저닝을 라이브러리가 자동 처리해주지 않으므로, NodeType/RelationshipType 설계로 직접 구현해야 한다.

이 계획은 **초안**이며, 사용자가 검토 후 피드백을 주면 반영해서 확정한다.

---

## 설계 검토: 상태 변화 모델링 방식 비교

세 가지 방식을 검토했다.

| 기준 | (a) 버저닝된 State 노드 | (b) 관계에 시간 속성(`since/until_chapter`) | (c) 관계에 효과 직접 기록 |
|---|---|---|---|
| 특정 시점 상태 조회 | 정확, 단순 (`chapter` 정렬 후 최신 값) | 가능하나 매번 직전 관계의 `until_chapter`를 갱신해야 함 | 스키마 없는 자유 속성이라 LLM 추출 신뢰도 낮음 |
| 레트콘 역추적(그래프 네이티브) | 가능 — 상태가 1급 노드라 참조 관계를 붙일 수 있음 | 어려움 — Neo4j 관계는 다른 노드가 가리킬 대상이 못 됨(APOC 없이) | 사실상 불가 |
| 새 타입 비용 | +1 노드, +4 관계 | +0~1 | +0 |
| 감사(evidence/retcon 플래그) | 자연스럽게 노드 속성으로 부착 | 확장성 낮음 | 어려움 |

**채택: (a) 버저닝된 State 노드 패턴.** PoC 규모에서 노드 타입 1개 추가 비용은 감내 가능하고, 요구사항 (2)·(3)을 그래프 네이티브하게 만족하는 유일한 방식이다.

기타 검토 사항:
- **공간 계층**: 새 노드 타입 없이 `Location`에 자기참조 관계 `LOCATED_IN`만 추가 (요새→도시→왕국).
- **description 속성 추가**: `Character`/`Location`/`Event`에 `PropertyType(name="description", type="STRING")`(DB에 실제 저장되는 속성, NodeType/PropertyType 자체의 `description` 인자와는 다름)을 추가해 시각화 대시보드용 사람이 읽는 요약을 담는다. `CharacterState`엔 이미 `evidence`가 같은 역할을 하므로 추가하지 않는다. 이 필드는 충돌 탐지 판정의 근거가 아니며(그건 `CharacterState`의 역할), 최신 내용으로 덮어써도 되고 레트콘 이력 추적 대상이 아니다.
- **APPEARS_IN/INVOLVES 중복 제거**: 더미 스키마는 `Character -[:APPEARS_IN]-> Event`와 `Event -[:INVOLVES]-> Character`를 둘 다 정의했는데, 이는 같은 사실을 방향만 바꿔 중복 표현한 것이다. Cypher는 관계 방향과 무관하게 양쪽에서 조회 가능하므로(`(c)-[:APPEARS_IN]->(e)` ≡ `(e)<-[:APPEARS_IN]-(c)`), `INVOLVES`는 제거하고 `APPEARS_IN` 하나로 통합한다.
- **사건의 발생 장소 + 인물의 시점별 위치**: 더미 스키마의 `Character -[:LOCATED_AT]-> Location`은 시간 정보가 없어 "언제 그 위치에 있었는지"에 답할 수 없고, 인물이 이동할 때마다 덮어쓰면 과거 위치 기록이 사라지는 문제(= `CharacterState`를 만든 이유와 동일한 문제)가 있다. 그래서 `LOCATED_AT`은 제거하고, 대신 `Location -[:HOSTS]-> Event`(장소가 사건의 무대가 됨) 하나만 추가한다 — `APPEARS_IN`/`ESTABLISHED_IN`/`REFERENCED_IN`처럼 Event를 목적어로 두는 방향과 일관성을 맞춰, `MATCH (e:Event)<-[r]-(n)` 한 패턴으로 사건과 관련된 모든 정보를 모을 수 있게 한다. 인물의 시점별 위치는 새 노드 없이 `Character -[:APPEARS_IN]-> Event <-[:HOSTS]- Location`을 이어서, `Event.chapter`로 정렬해 유도한다 — `CharacterState` 조회와 동일한 패턴("시점 이하 중 최신값")이라 별도 버저닝 구조가 필요 없다. 단, 두 사건 사이의 정확한 이동 시점(예: 3화와 7화 사이 정확히 언제 이동했는지)은 원문에 명시되지 않으면 알 수 없다 — 이는 스키마의 한계가 아니라 원천 데이터의 한계다.
- **시간/챕터 구조**: `Chapter`/타임라인 노드나 `NEXT_CHAPTER` 관계는 과잉 설계로 판단해 기각 — `Event.chapter`(정수) 비교로 충분. 다만 플래시백(연재 순서 ≠ 작중 시간)을 위해 `Event.story_order`(FLOAT) 하나만 추가.
  - **story_order는 생략 가능한 선택 필드가 아니라 항상 채우는 필수 컨벤션으로 둔다.** 규칙: 원문에 작중 시간대를 알 수 있는 명시적 묘사가 없으면 `chapter`와 동일한 값을 넣고, 명시적 묘사(예: "3년 전", "그날 밤")가 있으면 그 작중 시간대에 해당하는 값을 넣는다. "생략"이라는 상태 자체를 없애서, 매 회차마다 최소 chapter값이라도 반드시 채워지도록 강제한다.
  - **암묵적 플래시백(명시적 시간 표현 없이 과거 시점이 드러나는 경우) 대응**: 완벽한 사전 차단은 불가능하다 — LLM이 맥락만으로 매번 정확히 잡아내긴 어렵다. 놓친 케이스는 이미 알려진 `CharacterState`와 충돌하는 것으로 자연스럽게 탐지되므로(기존 충돌 탐지 메커니즘이 안전망 역할), Phase 7의 작가 판단 분기(오류/레트콘)에 **세 번째 선택지 "암묵적 플래시백(과거 시점 묘사)"**를 추가해야 한다 — 이 경우 `CharacterState`는 그대로 두고 해당 `Event.story_order`만 정정한다. (Phase 7 설계 시 forward-note로 반영 필요, 지금 스키마 구조 변경은 없음)
  - **story_order 값 충돌 문제**: 기본값을 `chapter`와 동일하게 채우면 챕터 번호는 빈틈없는 연속 정수라서, 나중에 "3화와 4화 사이" 같은 시점이 밝혀져도 끼워 넣을 정수가 없다(기존 값들을 밀어야 하는 문제 — (b) 방식을 기각한 이유와 동일). 그래서 `story_order` 타입을 `INTEGER`가 아니라 **`FLOAT`**로 정의한다. 기본값은 여전히 `chapter`와 같은 값(예: `5.0`)이고, 나중에 두 챕터 사이로 밝혀지면 그 사이의 실수(예: `3.5`)를, 1화 이전이면 더 작은 값(예: `0.5`)을 끼워 넣으면 된다 — 두 값 사이엔 항상 중간값이 존재하므로 삽입 공간이 이론상 무한하다("fractional indexing" 패턴).
  - **원문 시간 표현을 그대로 저장하지 않는다**: 원문은 회차마다 시간을 다르게 표현할 수 있다(절대 작중 달력 "환력 1023년", 상대 표현 "3년 전", 또는 아무 언급 없음). 이 이질적인 표현들을 그대로 옮겨 적으면 서로 비교가 불가능하므로, LLM은 원문 표현이 무엇이든 해석해서 **다른 Event들과 상대적으로 비교 가능한 story_order 스케일 값으로 변환**해 채워야 한다. 원문 근거 자체는 `neo4j_graphrag`가 자동 구성하는 lexical graph(`Chunk`/`FROM_CHUNK`)로 이미 추적되므로, 원문 표현을 보존하기 위한 별도 속성은 필요 없다.
  - **주의**: Neo4j 공식 문서 확인 결과, Property Existence Constraint(NOT NULL)는 Enterprise Edition 전용이며 `docker-compose.yml`의 `neo4j:5.26-community`(Community Edition)에서는 지원되지 않는다. `PropertyType.required`도 deprecated. 따라서 이 "필수"는 DB가 강제하는 게 아니라 스키마 description(LLM 프롬프트 가이드)과 시드/인덱싱 로직이 지키는 컨벤션이다. 실제로 모든 Event에 story_order가 채워졌는지의 완전성 검증은 Phase 1 범위가 아니라, `verification-phases.md`의 Phase 2(Indexing 검증 인프라)가 인덱싱 후 `story_order IS NULL`인 Event를 찾아내는 방식으로 다뤄야 한다.

---

## 최종 스키마 초안

새 노드 타입은 `CharacterState` 1개만 추가한다. **Item 등은 추가하지 않음** — 이번 요구사항(상태 변화/시간/공간 계층)에 불필요한 확장이며, 필요해지면 `CharacterState`의 attribute/value 패턴으로 커버 가능.

`poc/src/schema.py` 전체 교체안:

```python
"""
KG 스키마 정의 모듈.
Phase 1: 상태 변화·시간 구조·공간 계층·레트콘 대응을 지원하는 스키마.
"""

from neo4j_graphrag.experimental.components.schema import (
    SchemaBuilder,
    NodeType,
    RelationshipType,
    PropertyType,
    ConstraintType,
    GraphConstraintType,
    GraphSchema,
)

# --- 노드 타입 ---

CHARACTER = NodeType(
    label="Character",
    description="소설 등장인물. 신원(identity)만 표현하고, 가변 상태는 CharacterState로 분리한다.",
    properties=[
        PropertyType(name="name", type="STRING", description="인물 이름 또는 대표 호칭"),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "인물에 대한 간단한 요약(외형/성격/소속 등). 시각화 대시보드 표시용 참고 정보일 뿐, "
                "충돌 탐지 판정의 근거로 쓰지 않는다(그건 CharacterState의 역할). "
                "최신 내용으로 그냥 덮어쓰면 되고 레트콘 이력 추적 대상이 아니다."
            ),
        ),
    ],
)

LOCATION = NodeType(
    label="Location",
    description="소설 내 장소. LOCATED_IN으로 상위 장소를 가리켜 공간 계층(왕국>도시>요새)을 표현한다.",
    properties=[
        PropertyType(name="name", type="STRING"),
        PropertyType(
            name="description",
            type="STRING",
            description="장소에 대한 간단한 요약(지형/분위기/역할 등). 시각화 대시보드 표시용 참고 정보.",
        ),
    ],
)

EVENT = NodeType(
    label="Event",
    description=(
        "소설 내 사건. chapter는 연재 회차 순서(충돌 탐지 기준), "
        "story_order는 작중 연대기 순서(정규화된 상대 비교값)를 담는다. 자세한 규칙은 story_order 속성 설명 참고."
    ),
    properties=[
        PropertyType(name="title", type="STRING"),
        PropertyType(
            name="description",
            type="STRING",
            description="사건에 대한 간단한 요약(무슨 일이 있었는지). 시각화 대시보드 표시용 참고 정보.",
        ),
        PropertyType(name="chapter", type="INTEGER"),
        PropertyType(
            name="story_order",
            type="FLOAT",  # INTEGER가 아닌 FLOAT: chapter 기반 기본값은 빈틈없는 정수라서,
                            # 나중에 두 챕터 사이/이전 시점이 밝혀졌을 때 끼워 넣을 정수가 없음.
                            # FLOAT면 두 값 사이(예: 3과 4 사이 3.5)에 항상 삽입 가능(fractional indexing).
            description=(
                # 핵심: story_order는 원문의 실제 연도/날짜를 옮겨 적는 필드가 아니라,
                # Event끼리 상대적으로 비교하기 위해 정규화한 순서값이다.
                "다른 Event들과 상대적으로 비교 가능하도록 정규화한 작중 연대기 순서값. "
                "원문에 등장하는 절대 연도/작중 달력('환력 1023년')이나 상대 표현('3년 전')을 "
                "그대로 옮겨 적지 않고, 이 비교 스케일 위의 값으로 변환해서 넣는다.\n"
                "채우는 규칙: 모든 Event에 생략 없이 채운다 — "
                "명시적 시간 묘사가 없으면 chapter와 동일값을 쓰고, "
                "명시적 묘사(예: '환력 1023년', '3년 전')가 있으면 그 시간대에 맞는 값으로 변환한다. "
                "두 기존 값 사이의 시점이면 그 사이의 실수값을 사용한다(예: 3과 4 사이는 3.5)."
            ),
        ),
    ],
)

CHARACTER_STATE = NodeType(
    label="CharacterState",
    description=(
        "인물의 한 속성(attribute)이 특정 회차부터 갖는 값(value)을 나타내는 사실 노드. "
        "같은 attribute에 여러 CharacterState가 쌓일 수 있으며, "
        "ESTABLISHED_IN으로 연결된 Event.chapter가 조회 시점 이하 중 가장 큰 것이 '현재 유효한 값'이다."
    ),
    properties=[
        PropertyType(name="attribute", type="STRING", description="상태 속성명. 예: right_arm, left_arm, status(생사), allegiance"),
        PropertyType(name="value", type="STRING", description="속성 값. 예: lost, intact, alive, dead"),
        PropertyType(name="evidence", type="STRING", description="이 상태를 뒷받침하는 원문 근거 문장"),
        # 레트콘 처리는 일단 범위에서 제외 — 우선 상태 조회 구조부터 테스트한 뒤 필요 시 다시 추가
        # PropertyType(name="retconned", type="BOOLEAN", description="레트콘으로 더 이상 유효하지 않게 된 사실이면 true"),
        # PropertyType(name="retcon_note", type="STRING", description="레트콘 사유/정정 내용 메모"),
    ],
)

# --- 관계 타입 ---

APPEARS_IN = RelationshipType(label="APPEARS_IN", description="인물이 사건에 등장함")
HOSTS = RelationshipType(label="HOSTS", description="장소가 사건의 무대가 됨 (이 장소에서 해당 사건이 발생함)")

HAS_STATE = RelationshipType(label="HAS_STATE", description="인물이 특정 상태 사실을 가짐")
ESTABLISHED_IN = RelationshipType(label="ESTABLISHED_IN", description="상태 사실이 최초로 성립된 사건(회차)")
# 레트콘 처리는 일단 범위에서 제외 — REFERENCED_IN은 레트콘 역추적 전용이라 CharacterState.retconned/retcon_note와 함께 배제
# REFERENCED_IN = RelationshipType(
#     label="REFERENCED_IN",
#     description="이후 회차가 동일 상태 사실을 다시 언급/전제로 사용함 (레트콘 역추적용). "
#                  "Phase 1에서는 스키마 자리만 마련하고, 실제로 채우는 로직은 이후 인덱싱 Phase에서 구현한다.",
# )
LOCATED_IN = RelationshipType(
    label="LOCATED_IN",
    description="장소가 상위 장소에 포함됨 (예: 요새 LOCATED_IN 도시, 도시 LOCATED_IN 왕국)",
)

# --- 허용 관계 패턴 ---

PATTERNS = [
    ("Character", "APPEARS_IN", "Event"),
    ("Location", "HOSTS", "Event"),
    ("Character", "HAS_STATE", "CharacterState"),
    ("CharacterState", "ESTABLISHED_IN", "Event"),
    # ("CharacterState", "REFERENCED_IN", "Event"),  # 레트콘 제외 범위 — 위 REFERENCED_IN 주석과 함께 배제
    ("Location", "LOCATED_IN", "Location"),
]

# --- 스키마 조립 ---

SCHEMA: GraphSchema = SchemaBuilder.create_schema_model(
    node_types=[CHARACTER, LOCATION, EVENT, CHARACTER_STATE],
    relationship_types=[
        APPEARS_IN, HOSTS,
        HAS_STATE, ESTABLISHED_IN, LOCATED_IN,  # REFERENCED_IN은 레트콘 제외 범위라 배제
    ],
    patterns=PATTERNS,
)


if __name__ == "__main__":
    print("스키마 로드 성공")
    print(f"노드 타입: {[n.label for n in SCHEMA.node_types]}")
    print(f"관계 타입: {[r.label for r in SCHEMA.relationship_types]}")
    print(f"패턴: {SCHEMA.patterns}")
```

`chapter`를 `CharacterState`에 중복 저장하지 않고 `ESTABLISHED_IN → Event.chapter`로만 참조한다 (단일 진실 소스 유지, 조인 한 홉 비용은 PoC 규모에서 무시 가능).

---

## 과잉 설계 방지 체크

- 새 노드 타입: `CharacterState` 1개만 추가.
- 새 관계 타입: `HAS_STATE`, `ESTABLISHED_IN`, `LOCATED_IN`, `HOSTS` 4개. `REFERENCED_IN`은 레트콘 전용이라 `CharacterState.retconned`/`retcon_note`와 함께 이번 테스트 범위에서 주석 처리(필요 시 재활성화).
- 더미 스키마의 `LOCATED_AT`(인물→장소, 시간 정보 없음)은 제거. 시간 정보 없이 위치를 고정 저장하면 이동 시 과거 위치가 사라지는 문제가 있어, `Location -[:HOSTS]-> Event`를 통해 시점별로 유도하는 방식으로 대체. 다른 Event 관계들(`APPEARS_IN`/`ESTABLISHED_IN`/`REFERENCED_IN`)과 마찬가지로 Event가 목적어가 되도록 방향을 맞췄다.
- 더미 스키마의 `INVOLVES`(Event→Character)는 `APPEARS_IN`(Character→Event)과 방향만 다른 중복이라 제거.
- `Chapter`/타임라인 노드, `NEXT_CHAPTER` 관계는 정수 `chapter` 비교로 대체 가능해 기각.
- 관계 시간 속성(`until_chapter`) 버저닝 방식은 과거 엣지 갱신 부작용 + 레트콘 역추적 불가로 기각.

---

## 적용 범위

- `poc/src/schema.py`: 위 초안으로 전체 교체.
- `poc/src/seed_example.py`: 아래 시드 데이터 설계로 전체 교체.

## 시드 데이터 설계 (poc/src/seed_example.py) — 무협 장르

여러 사건/인물/위치로 상태 조회 + 위치 타임라인 둘 다 검증 가능하게 구성한다.

**장소 (계층 포함, 5개)**
- 중원 (Region)
  - 화산 (Mountain) `-[:LOCATED_IN]->` 중원
    - 화산파 대전 (Sect Hall) `-[:LOCATED_IN]->` 화산
  - 낙양성 (City) `-[:LOCATED_IN]->` 중원
    - 낙양 객잔 (Inn) `-[:LOCATED_IN]->` 낙양성

**인물 (2명)**: 진소천, 백리연

**사건 (3개, 서로 다른 장소/챕터)**
1. `화산파 혈사` (chapter 3) — `HOSTS` 화산파 대전 / `APPEARS_IN` 진소천·백리연 / `CharacterState`: 진소천 `right_arm=lost` `ESTABLISHED_IN` 이 사건
2. `낙양 객잔 잠입` (chapter 5) — `HOSTS` 낙양 객잔 / `APPEARS_IN` 백리연만
3. `낙양성 회합` (chapter 7) — `HOSTS` 낙양성 / `APPEARS_IN` 진소천만 (진소천이 3화 화산파 대전 → 7화 낙양성으로 이동했음을 보여줌)

**시드 후 자동 실행할 검증 쿼리** (콘솔에 출력)
- 진소천의 `right_arm` 상태 조회 (기대값: `lost`, 3화 근거)
- 진소천의 위치 타임라인 조회 (기대값: `(3, 화산파 대전), (7, 낙양성)`)

## 검증 방법

1. `cd poc && uv run python src/schema.py` → import 에러 없이 노드/관계/패턴 출력 확인.
2. `cd poc && uv run python src/seed_example.py --reset` → 시드 삽입 및 검증 쿼리 결과 콘솔 출력 확인.
3. Neo4j Browser(`http://localhost:7474`)에서 `MATCH (n)-[r]->(m) RETURN n, r, m`으로 그래프 시각화 확인.
