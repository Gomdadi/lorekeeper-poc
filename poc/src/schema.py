"""
KG 스키마 정의 모듈.
현재는 e2e 동작 검증용 더미 스키마를 포함한다.
Phase 1 스키마 설계 완료 후 이 파일의 노드/관계 정의를 교체한다.
"""

from neo4j_graphrag.experimental.components.schema import (
    SchemaBuilder,
    NodeType,
    RelationshipType,
    PropertyType,
    GraphSchema,
)

# --- 더미 스키마 (Phase 1 설계 전 임시) ---

# 노드 타입 정의
CHARACTER = NodeType(
    label="Character",
    description="소설 등장인물",
    properties=[
        PropertyType(name="name", type="STRING"),
        PropertyType(name="status", type="STRING"),  # alive / dead
    ],
)

LOCATION = NodeType(
    label="Location",
    description="소설 내 장소",
    properties=[
        PropertyType(name="name", type="STRING"),
    ],
)

EVENT = NodeType(
    label="Event",
    description="소설 내 사건",
    properties=[
        PropertyType(name="title", type="STRING"),
        PropertyType(name="chapter", type="INTEGER"),
    ],
)

# 관계 타입 정의 (start/end는 patterns에서 지정)
APPEARS_IN = RelationshipType(
    label="APPEARS_IN",
    description="인물이 사건에 등장함",
)

LOCATED_AT = RelationshipType(
    label="LOCATED_AT",
    description="인물이 특정 장소에 있음",
)

INVOLVES = RelationshipType(
    label="INVOLVES",
    description="사건이 인물을 포함함",
)

# 허용 관계 패턴: (시작 노드 label, 관계 label, 끝 노드 label)
PATTERNS = [
    ("Character", "APPEARS_IN", "Event"),
    ("Character", "LOCATED_AT", "Location"),
    ("Event", "INVOLVES", "Character"),
]

# GraphSchema 객체 생성
SCHEMA: GraphSchema = SchemaBuilder.create_schema_model(
    node_types=[CHARACTER, LOCATION, EVENT],
    relationship_types=[APPEARS_IN, LOCATED_AT, INVOLVES],
    patterns=PATTERNS,
)


if __name__ == "__main__":
    print("스키마 로드 성공")
    print(f"노드 타입: {[n.label for n in SCHEMA.node_types]}")
    print(f"관계 타입: {[r.label for r in SCHEMA.relationship_types]}")
    print(f"패턴: {SCHEMA.patterns}")
