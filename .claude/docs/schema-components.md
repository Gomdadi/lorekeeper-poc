# GraphSchema 컴포넌트 정리

> 소스: `neo4j_graphrag.experimental.components.schema`  
> 버전: neo4j-graphrag 1.18.0

---

## 컴포넌트 관계 요약

```
PropertyType          → NodeType.properties / RelationshipType.properties
NodeType              → GraphSchema.node_types
RelationshipType      → GraphSchema.relationship_types
Pattern (또는 tuple)   → GraphSchema.patterns
ConstraintType        → GraphSchema.constraints

SchemaBuilder.create_schema_model(
    node_types, relationship_types, patterns, constraints
) → GraphSchema
```

`GraphSchema`는 Python 메모리 객체. Neo4j DB에 저장되지 않으며 `SimpleKGPipeline`에 넘겨 LLM 프롬프트 구성에 사용된다.

---

## PropertyType

노드 또는 관계 위의 속성 하나를 정의한다.

```python
PropertyType(
    name="name",           # 속성 키 이름
    type="STRING",         # Neo4j 타입 (아래 목록 참고)
    description="",        # LLM 프롬프트용 설명. DB에 저장되지 않음
)
```

### 허용 type 값 (`Neo4jPropertyTypeName`)

```
BOOLEAN, DATE, DURATION, FLOAT, INTEGER, LIST,
LOCAL_DATETIME, LOCAL_TIME, POINT, STRING, ZONED_DATETIME, ZONED_TIME
```

> **주의**: `required` 필드는 deprecated. DB 레벨 필수 제약은 `ConstraintType(EXISTENCE)`를 사용한다.

---

## NodeType

그래프에서 가능한 노드 타입을 정의한다.

```python
NodeType(
    label="Character",             # Neo4j 노드 레이블
    description="소설 등장인물",    # LLM 프롬프트용 타입 설명. DB에 저장되지 않음
    properties=[                   # 최소 1개 필수 (min_length=1)
        PropertyType(name="name", type="STRING"),
        PropertyType(name="status", type="STRING"),
    ],
    additional_properties=False,   # True면 LLM이 정의되지 않은 속성도 추출 가능
                                   # properties가 비어있으면 자동으로 True
)
```

### 문자열 단축 표기

```python
# 아래 두 표현은 동일하다
NodeType("Character")
NodeType(label="Character", properties=[PropertyType(name="name", type="STRING")], additional_properties=True)
```

> **주의**: `__`로 시작하거나 끝나는 label은 라이브러리 내부 예약어라 사용 불가.

---

## RelationshipType

그래프에서 가능한 관계 타입을 정의한다. 시작/끝 노드는 여기서 정하지 않고 `patterns`에서 지정한다.

```python
RelationshipType(
    label="APPEARS_IN",             # 관계 레이블 (대문자 관행)
    description="인물이 사건에 등장함", # LLM 프롬프트용 설명. DB에 저장되지 않음
    properties=[],                  # 관계 속성 (없어도 됨)
    additional_properties=True,     # properties=[] 이면 자동으로 True로 보정됨
)
```

---

## Pattern

어떤 노드 타입 사이에 어떤 관계가 허용되는지를 정의한다.

```python
# tuple 형식 (간편)
("Character", "APPEARS_IN", "Event")

# Pattern 객체 형식 (동일한 의미)
Pattern(source="Character", relationship="APPEARS_IN", target="Event")
```

`GraphSchema` 생성 시 패턴에 사용된 label이 `node_types`와 `relationship_types`에 없으면 `SchemaValidationError` 발생.

---

## ConstraintType

DB 레벨 제약을 정의한다. `GraphSchema.constraints`에 포함시키면 Neo4j에 실제 제약이 적용된다.

```python
ConstraintType(
    type=GraphConstraintType.UNIQUENESS,  # 제약 종류
    node_type="Character",                # 노드 타입 (node_type or relationship_type 둘 중 하나만)
    property_names=("name",),             # 대상 속성 이름 (tuple)
)
```

### GraphConstraintType 종류

| 값 | 의미 | 복합(다중 속성) | 비고 |
|---|---|---|---|
| `UNIQUENESS` | 속성 값 중복 불허 | 가능 | |
| `EXISTENCE` | 속성 null 불허 | 불가 (1개만) | |
| `KEY` | 중복 불허 + null 불허 | 가능 | KEY는 EXISTENCE를 포함 — 동일 속성에 KEY + EXISTENCE 동시 설정 불가 |

> `relationship_type`은 `node_type` 대신 사용. 둘 중 하나만 지정해야 함.

---

## GraphSchema

위 컴포넌트를 조합한 최종 스키마 객체. **불변(immutable)**.

```python
GraphSchema(
    node_types=(CHARACTER, LOCATION, EVENT),
    relationship_types=(APPEARS_IN, LOCATED_AT, INVOLVES),
    patterns=(
        Pattern("Character", "APPEARS_IN", "Event"),
        ...
    ),
    constraints=(
        ConstraintType(type=GraphConstraintType.UNIQUENESS, node_type="Character", property_names=("name",)),
    ),
    # LLM이 스키마 밖의 항목을 추출하는 것을 허용할지 여부
    additional_node_types=False,         # False: 정의된 노드 타입 외 추출 금지
    additional_relationship_types=False, # False: 정의된 관계 타입 외 추출 금지
    additional_patterns=False,           # False: 정의되지 않은 패턴 추출 금지
)
```

### 파일로 저장/불러오기

Python 메모리 객체이므로 재사용하려면 파일로 직렬화할 수 있다.

```python
# 저장
schema.save("schema.json")   # JSON
schema.save("schema.yaml")   # YAML

# 불러오기
schema = GraphSchema.from_file("schema.json")
```

---

## SchemaBuilder

`GraphSchema`를 생성하는 팩토리. 직접 `GraphSchema()`를 쓰는 것과 기능적으로 동일하지만, Pipeline 컴포넌트로도 사용 가능하다.

```python
# 정적 메서드 (동기, 일반 코드에서 사용)
SCHEMA = SchemaBuilder.create_schema_model(
    node_types=[CHARACTER, LOCATION, EVENT],
    relationship_types=[APPEARS_IN, LOCATED_AT, INVOLVES],
    patterns=[
        ("Character", "APPEARS_IN", "Event"),
        ("Character", "LOCATED_AT", "Location"),
        ("Event", "INVOLVES", "Character"),
    ],
    constraints=[],  # 생략 가능
)

# 비동기 메서드 (Pipeline 내부에서 사용)
await schema_builder.run(node_types=..., relationship_types=..., patterns=...)
```

유효성 검사 실패 시 `SchemaValidationError` 발생.

---

## 자동 스키마 추출 (참고)

직접 정의 대신 LLM이나 기존 그래프에서 스키마를 자동 생성하는 방법도 있다.

| 클래스 | 방법 |
|---|---|
| `SchemaFromTextExtractor` | 텍스트에서 LLM으로 스키마 자동 추출 |
| `SchemaFromExistingGraphExtractor` | 기존 Neo4j 그래프에서 스키마 역추출 |

---

## 요약: `description` 저장 위치

| 위치 | 저장 대상 | DB 저장 여부 |
|---|---|---|
| `NodeType.description` | 해당 노드 **타입** 전체 설명 | X (LLM 프롬프트용) |
| `RelationshipType.description` | 해당 관계 **타입** 전체 설명 | X (LLM 프롬프트용) |
| `PropertyType.description` | 해당 속성의 의미 설명 | X (LLM 프롬프트용) |
| `PropertyType(name="description", type="STRING")` | 인스턴스별 부연 설명 | O (실제 노드/관계 속성으로 저장) |
