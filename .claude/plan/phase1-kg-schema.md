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
    description=(
        "소설에 등장하는 인물. 이 노드는 인물의 신원(누구인지 식별하는 고정된 사실)만 표현한다. "
        "시간이 지나며 바뀔 수 있는 사실(신체 상태, 생사 여부, 소속 등)은 이 노드의 속성으로 넣지 않고 "
        "CharacterState로 별도 기록한다 — Character는 한 번 만들어지면 값이 갱신되지 않는 "
        "정적인 식별자 노드로 취급한다."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description=(
                "인물의 정식 이름 또는 원문에서 대표적으로 쓰이는 호칭 하나. "
                "동일 인물을 가리키는 여러 표현(별명, 직함, 존칭 등)이 원문에 등장해도 "
                "항상 같은 대표 이름 하나로 통일해서 넣는다 — 값이 회차마다 달라지면 "
                "같은 인물이 서로 다른 노드로 나뉘어 저장된다. 외형/성격 등 부가 설명은 "
                "여기 넣지 않고 description 속성에 적는다."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "이 인물의 사건과 무관한 일반적 특징(외형/성격/소속 등) 요약. "
                "시각화 대시보드 표시·RAG 컨텍스트용 참고 정보일 뿐, 충돌 탐지 판정의 근거로 쓰지 않는다"
                "(그건 CharacterState의 역할). 특정 사건에서 이 인물이 무엇을 했는지는 여기 적지 않고 "
                "그 사건의 Event.description에 적는다 — 두 필드가 같은 내용을 담으면 "
                "추출할 때마다 어느 필드에 들어갈지 일관성이 없어진다. "
                "최신 내용으로 그냥 덮어쓰면 되고 레트콘 이력 추적 대상이 아니다."
            ),
        ),
    ],
)

LOCATION = NodeType(
    label="Location",
    description=(
        "소설 내 장소 하나. LOCATED_IN 관계로 자신을 포함하는 바로 위 단계의 장소를 가리켜 "
        "공간 계층(예: 요새→도시→왕국)을 표현한다. 단계를 건너뛰어 연결하지 않는다 "
        "(요새는 도시를 가리키고 도시가 왕국을 가리키게 하며, 요새가 왕국을 직접 가리키면 안 된다). "
        "원문에 상위 장소가 명시되지 않으면 LOCATED_IN 없이 장소 노드만 생성해도 된다."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description=(
                "장소의 정식 명칭. 같은 장소를 가리키는 여러 표현이 원문에 등장해도 "
                "항상 같은 대표 이름 하나로 통일해서 넣는다 — 값이 달라지면 같은 장소가 "
                "서로 다른 노드로 나뉘어 저장되고 LOCATED_IN 계층도 끊어진다."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "이 장소의 사건과 무관한 일반적 특징(지형/분위기/역할 등) 요약. "
                "시각화 대시보드 표시·RAG 컨텍스트용 참고 정보. "
                "특정 사건에서 이 장소에 무슨 일이 있었는지는 여기 적지 않고 "
                "그 사건의 Event.description에 적는다."
            ),
        ),
    ],
)

EVENT = NodeType(
    label="Event",
    description=(
        "소설 내에서 일어난 하나의 사건. 인물의 상태 변화, 갈등, 만남처럼 이후 시점에서 "
        "다시 참조되거나 대조될 만한 사건 단위로 추출한다 — 문장 하나하나를 별도 사건으로 "
        "쪼개지 않고, 배경 묘사나 사소한 행동까지 사건으로 만들지 않는다. "
        "chapter는 연재 회차 순서(충돌 탐지 기준), "
        "story_order는 작중 연대기 순서(정규화된 상대 비교값)를 담는다. 자세한 규칙은 story_order 속성 설명 참고."
    ),
    properties=[
        PropertyType(
            name="title",
            type="STRING",
            description=(
                "사건을 식별하는 짧은 제목. 같은 사건이 여러 문단/청크에 걸쳐 언급돼도 "
                "항상 같은 title을 써서 하나의 Event 노드로 병합되게 한다."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "이 사건에서 실제로 무슨 일이 있었는지 서술하는 요약. "
                "참여 인물의 일반적 특징(Character.description)이나 장소의 일반적 특징"
                "(Location.description)과 겹치는 내용은 넣지 않고, 이 사건 자체의 전개만 적는다 — "
                "같은 정보가 여러 필드에 들어갈 수 있으면 추출할 때마다 어느 필드에 담길지 달라진다."
            ),
        ),
        PropertyType(
            name="chapter",
            type="INTEGER",
            description="이 사건이 실린 연재 회차 번호. 원문에 회차 표시가 있으면 그 값을 그대로 쓴다.",
        ),
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
        "이 노드는 나중에 다른 회차의 내용과 대조해서 모순 여부를 판정해야 할 사실만 담는다 — "
        "신체 손상, 생사 여부, 소속 변경처럼 시간에 따라 바뀌고 그 변화 자체가 검증 대상이 되는 "
        "사실만 대상이며, 한 번 정해지면 바뀌지 않는 배경 설정(외형, 성격 등)은 여기 넣지 않고 "
        "Character.description에 넣는다. "
        "같은 attribute에 여러 CharacterState가 쌓일 수 있으며, "
        "ESTABLISHED_IN으로 연결된 Event.chapter가 조회 시점 이하 중 가장 큰 것이 '현재 유효한 값'이다. "
        "상태가 바뀔 때마다 기존 노드를 수정하지 않고 항상 새 CharacterState 노드를 만든다 — "
        "과거 값을 덮어쓰면 시점별 조회와 모순 탐지 자체가 불가능해진다."
    ),
    properties=[
        PropertyType(
            name="attribute",
            type="STRING",
            description=(
                "상태 속성명. 영문 snake_case 식별자로 정규화해서 쓴다(예: right_arm, left_arm, "
                "status(생사), allegiance). 같은 종류의 상태는 항상 동일한 attribute 값을 써야 한다 — "
                "예를 들어 생사 여부는 매번 'status'로만 쓰고 '생사여부'나 'alive_or_dead' 같은 "
                "다른 표현을 새로 만들지 않는다. attribute 값이 회차마다 달라지면 같은 속성의 과거/현재 "
                "CharacterState들을 시간순으로 비교할 수 없어 이 노드 타입의 존재 목적 자체가 깨진다."
            ),
        ),
        PropertyType(
            name="value",
            type="STRING",
            description=(
                "속성의 현재 값을 짧고 일관된 표현으로 적는다(예: lost, intact, alive, dead). "
                "attribute와 마찬가지로 같은 의미면 항상 같은 표현을 쓴다 — 사망은 매번 'dead'로만 쓰고 "
                "'사망함', '죽음', '숨을 거둠' 같은 표현을 섞어 쓰지 않는다. 표현이 섞이면 동일한 상태를 "
                "비교할 때 서로 다른 값으로 인식되어 조회가 실패한다."
            ),
        ),
        PropertyType(
            name="evidence",
            type="STRING",
            description=(
                "이 상태 변화를 직접 뒷받침하는 원문 문장을 그대로 인용하거나 가깝게 옮긴다. "
                "해석이나 추가 설명은 덧붙이지 않는다 — 이 필드는 서사 요약(description)이 아니라 "
                "판정 근거이므로, attribute/value만으로 왜 그렇게 판단했는지 확인할 수 있는 "
                "최소한의 원문 근거만 담는다."
            ),
        ),
        # 레트콘 처리는 일단 범위에서 제외 — 우선 상태 조회 구조부터 테스트한 뒤 필요 시 다시 추가
        # PropertyType(name="retconned", type="BOOLEAN", description="레트콘으로 더 이상 유효하지 않게 된 사실이면 true"),
        # PropertyType(name="retcon_note", type="STRING", description="레트콘 사유/정정 내용 메모"),
    ],
)

# --- 관계 타입 ---

APPEARS_IN = RelationshipType(
    label="APPEARS_IN",
    description=(
        "인물이 사건에 실제로 참여하거나 그 사건의 현장에 있었음을 나타낸다. "
        "다른 인물의 대화나 회상 속에서 이름만 언급된 경우는 포함하지 않는다."
    ),
)
HOSTS = RelationshipType(
    label="HOSTS",
    description=(
        "장소가 사건의 무대가 됨 (이 장소에서 해당 사건이 발생함). "
        "사건이 일어난 가장 구체적인 장소 하나에만 연결한다 — 예를 들어 화산파 대전에서 벌어진 "
        "사건이면 화산파 대전에만 HOSTS를 연결하고, 그 상위 장소인 화산이나 중원에는 "
        "중복으로 연결하지 않는다(상위 장소는 LOCATED_IN을 따라가면 알 수 있다)."
    ),
)

HAS_STATE = RelationshipType(
    label="HAS_STATE",
    description=(
        "인물이 특정 상태 사실(CharacterState)을 가짐을 나타내는 연결. "
        "한 인물은 같은 attribute에 대해 여러 CharacterState를 가질 수 있으며(회차마다 새 사실이 "
        "추가되는 구조), 그 사실들 각각을 모두 이 관계로 인물에 연결한다."
    ),
)
ESTABLISHED_IN = RelationshipType(
    label="ESTABLISHED_IN",
    description=(
        "상태 사실(CharacterState)이 최초로 성립된 사건(회차)을 가리킨다. "
        "상태가 바뀔 때마다 새로 생성되는 CharacterState 각각이 자신이 성립된 Event를 가리켜야 하며, "
        "이 Event의 chapter 값이 그 사실의 시간 순서 기준이 된다."
    ),
)
# 레트콘 처리는 일단 범위에서 제외 — REFERENCED_IN은 레트콘 역추적 전용이라 CharacterState.retconned/retcon_note와 함께 배제
# REFERENCED_IN = RelationshipType(
#     label="REFERENCED_IN",
#     description="이후 회차가 동일 상태 사실을 다시 언급/전제로 사용함 (레트콘 역추적용). "
#                  "Phase 1에서는 스키마 자리만 마련하고, 실제로 채우는 로직은 이후 인덱싱 Phase에서 구현한다.",
# )
LOCATED_IN = RelationshipType(
    label="LOCATED_IN",
    description=(
        "장소가 자신을 포함하는 바로 위 단계의 장소를 가리킴 (예: 요새 LOCATED_IN 도시, "
        "도시 LOCATED_IN 왕국). 반드시 한 단계 위의 장소만 가리키고 단계를 건너뛰어 연결하지 않는다 "
        "— 그래야 계층 전체를 그래프 순회로 복원할 수 있다."
    ),
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

## 추후 필요 시 반영 (지금은 범위 밖)

- **`Item` 노드 + `ItemState`**: 아이템이 특정 인물 소유가 아니라 자기 고유의 정체성/이력을 가질 때(예: 여러 인물 손을 거치는 전설의 검)는 `CharacterState`의 attribute/value로 못 담는다. 이 경우 `Character`/`CharacterState`와 동일한 패턴(`Item` + `ItemState` + 소유권 변화 추적 관계)이 필요하지만, 지금은 (1) Phase 1 검증 목표(상태/시간/공간)에 필수가 아니고 (2) "어떤 아이템이 1급 노드로 승격할 만큼 중요한지"를 가릴 기준 자체가 없어 스키마부터 만들면 나중에 기준과 안 맞아 갈아엎을 위험이 크다. 실제 아이템 충돌 시나리오를 마주쳤을 때, 그 판단(무엇이 "중요한 아이템"인지)은 Phase 4 인덱싱 단계의 LLM 추출 판단으로 미루고, 그때 `Character`/`CharacterState`를 본떠 저비용으로 확장한다.
- **`additional_properties=True`**: 스키마에 정의 안 된 속성을 LLM이 자유롭게 추가하게 허용하는 옵션. RAG 검색 시 "노드를 통째로 가져와 컨텍스트로 사용"하는 방식이면 키 이름이 인스턴스마다 달라져도 검색 자체가 깨지진 않지만(LLM이 알아서 읽음), 그 정보량은 이미 `Character`/`Location`/`Event`에 있는 `description`(자유 텍스트 요약, 임베딩/컨텍스트 투입에 적합) 필드가 전부 감당 가능하다. 즉 새로운 표현력을 주지 못하는 중복 기능이라 지금은 켜지 않는다(기본값 `False` 유지). `description`만으로 커버 안 되는 구체적 필요(예: 특정 속성을 Cypher `WHERE`로 직접 필터링해야 하는 경우)가 생기면, 그때 정식 `PropertyType`으로 스키마에 편입한다.

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
