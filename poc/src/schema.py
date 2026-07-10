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
    # 스키마에 정의되지 않은 속성은 GraphPruning이 제거하고 pruning_stats.pruned_properties에 기록한다.
    # (RelationshipType들은 properties가 비어 있어 False를 줘도 라이브러리가 True로 강제하므로 노드에만 적용)
    additional_properties=False,
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
    additional_properties=False,  # 스키마 미정의 속성 제거 → pruning_stats에 기록
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
    additional_properties=False,  # 스키마 미정의 속성 제거 → pruning_stats에 기록
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
    additional_properties=False,  # 스키마 미정의 속성 제거 → pruning_stats에 기록
    description=(
        "인물의 한 속성(attribute)이 특정 회차부터 갖는 값(value)을 나타내는 사실 노드. "
        "이 노드는 나중에 다른 회차의 내용과 대조해서 모순 여부를 판정해야 할 사실만 담는다 — "
        "신체 손상, 생사 여부, 소속 변경, 능력·무공 습득/성장, 소지품 획득/상실처럼 시간에 따라 "
        "바뀌고 그 변화 자체가 검증 대상이 되는 사실만 대상이며, 한 번 정해지면 바뀌지 않는 "
        "배경 설정(외형, 성격 등)은 여기 넣지 않고 Character.description에 넣는다. "
        "인물의 능력(스킬·무공)과 소지품은 별도 노드로 만들지 않고 이 CharacterState의 attribute로 표현한다 "
        "— 능력/소지품은 인물에 종속된 상태이기 때문이다. "
        "소지품 소유가 인물 간 이동하면 넘겨준 인물에는 value '상실', 받은 인물에는 value '보유'로 "
        "각각 별도 CharacterState를 만들어 이동을 표현한다. "
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
                "상태 속성명. 한국어로 짧게 쓴다(예: 오른팔, 생사, 소속, 무공, 소지품). 같은 종류의 상태는 "
                "항상 같은 표현으로 쓴다 — 생사 여부는 매번 '생사'로만 쓰고 '생존여부'나 '사망여부' "
                "같은 표현을 새로 만들지 않는다. 또한 입도를 통일한다 — '오른팔_부상'/'오른팔_상태'처럼 "
                "같은 축을 잘게 나누지 말고 '오른팔'처럼 신체 부위·속성 단위로만 쓴다. attribute 표현이나 "
                "입도가 회차마다 달라지면 같은 속성의 과거/현재 CharacterState들을 시간순으로 비교할 수 "
                "없어 이 노드 타입의 존재 목적 자체가 깨진다."
            ),
        ),
        PropertyType(
            name="value",
            type="STRING",
            description=(
                "속성의 현재 값을 한국어로 짧게 적는다(예: 상실, 온전함, 생존, 사망). "
                "서술 문장이 아니라 상태를 나타내는 짧은 표현으로 쓴다 — 자세한 정황은 evidence에 담고, "
                "여기에는 값만 간결하게 넣는다."
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

ORGANIZATION = NodeType(
    label="Organization",
    additional_properties=False,  # 스키마 미정의 속성 제거 → pruning_stats에 기록
    description=(
        "소설에 등장하는 조직·세력·단체 하나(문파, 길드, 가문, 국가 세력 등). "
        "여러 인물이 소속을 공유하는 엔티티이므로 CharacterState의 문자열 값이 아니라 독립 노드로 둔다 "
        "— 그래야 '이 조직의 구성원은 누구인가' 같은 조직 단위 조회가 된다. "
        "인물의 현재 소속은 MEMBER_OF로 연결하고, 소속이 바뀌는 시점 변화는 "
        "CharacterState(attribute='소속')로 별도 기록한다."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description=(
                "조직의 정식 명칭. 같은 조직을 가리키는 여러 표현이 등장해도 항상 같은 "
                "대표 이름 하나로 통일한다 — 값이 달라지면 같은 조직이 여러 노드로 나뉜다."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "이 조직의 사건과 무관한 일반적 특징(성격/목적/규모 등) 요약. "
                "시각화 대시보드 표시·RAG 컨텍스트용 참고 정보."
            ),
        ),
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

MEMBER_OF = RelationshipType(
    label="MEMBER_OF",
    description=(
        "인물이 현재 어느 조직(Organization)에 소속돼 있는지를 나타낸다. "
        "소속이 언제부터·어떻게 바뀌었는지 같은 시점 변화는 이 관계로 표현하지 않고 "
        "CharacterState(attribute='소속')로 기록한다 — 이 관계는 현재 확립된 소속 연결만 담는다."
    ),
)
RELATED_TO = RelationshipType(
    label="RELATED_TO",
    additional_properties=False,  # 스키마 미정의 속성 제거 → pruning_stats에 기록
    description=(
        "인물과 인물 사이의 서사적 관계(동맹, 적대, 사제, 혈연, 연인 등)를 나타낸다. "
        "관계의 종류는 type 속성에 담는다. 방향은 관계의 주체→대상으로 잡되, "
        "상호적 관계(동맹 등)는 한 방향만 연결해도 된다. "
        "관계가 시간에 따라 변하는 것(예: 동맹→적대)까지 시점 추적하려면 이 관계 대신 "
        "CharacterState로 기록해야 하나, 여기서는 현재 확립된 관계만 담는다."
    ),
    properties=[
        PropertyType(
            name="type",
            type="STRING",
            description=(
                "관계의 종류를 한국어로 짧게 쓴다(예: 동맹, 적대, 사제, 혈연, 연인). "
                "같은 종류는 항상 같은 표현으로 통일한다."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description="이 관계에 대한 짧은 부연(예: '어린 시절 같은 스승 밑에서 수학'). 없으면 생략 가능.",
        ),
    ],
)

# --- 허용 관계 패턴 ---

PATTERNS = [
    ("Character", "APPEARS_IN", "Event"),
    ("Location", "HOSTS", "Event"),
    ("Character", "HAS_STATE", "CharacterState"),
    ("CharacterState", "ESTABLISHED_IN", "Event"),
    # ("CharacterState", "REFERENCED_IN", "Event"),  # 레트콘 제외 범위 — 위 REFERENCED_IN 주석과 함께 배제
    ("Location", "LOCATED_IN", "Location"),
    ("Character", "MEMBER_OF", "Organization"),
    ("Character", "RELATED_TO", "Character"),
]

# --- 스키마 조립 ---

# 커스텀 파이프라인의 SchemaBuilder 컴포넌트(run 데이터)에서 재사용할 수 있도록
# 노드/관계 타입 목록을 모듈 변수로 노출한다. SchemaBuilder.run()은 node_types/
# relationship_types/patterns를 인자로 받으므로 조립된 SCHEMA가 아니라 이 목록들을 넘긴다.
NODE_TYPES = [CHARACTER, LOCATION, EVENT, CHARACTER_STATE, ORGANIZATION]
RELATIONSHIP_TYPES = [
    APPEARS_IN, HOSTS,
    HAS_STATE, ESTABLISHED_IN, LOCATED_IN,  # REFERENCED_IN은 레트콘 제외 범위라 배제
    MEMBER_OF, RELATED_TO,
]

SCHEMA: GraphSchema = SchemaBuilder.create_schema_model(
    node_types=NODE_TYPES,
    relationship_types=RELATIONSHIP_TYPES,
    patterns=PATTERNS,
)


if __name__ == "__main__":
    print("스키마 로드 성공")
    print(f"노드 타입: {[n.label for n in SCHEMA.node_types]}")
    print(f"관계 타입: {[r.label for r in SCHEMA.relationship_types]}")
    print(f"패턴: {SCHEMA.patterns}")
