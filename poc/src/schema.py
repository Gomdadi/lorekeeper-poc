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
