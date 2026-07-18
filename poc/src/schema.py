"""
KG 스키마 정의 모듈.
Phase 1: 상태 변화·시간 구조·공간 계층을 지원하는 스키마.
"""

from neo4j_graphrag.experimental.components.schema import (
    SchemaBuilder,
    NodeType,
    RelationshipType,
    PropertyType,
    GraphSchema,
)

# --- 노드 타입 ---
# additional_properties=False: 스키마 미정의 속성을 GraphPruning이 제거(pruning_stats에 기록).
# RelationshipType은 properties가 비면 라이브러리가 True로 강제하므로 노드에만 실효.

CHARACTER = NodeType(
    label="Character",
    additional_properties=False,
    description=(
        "소설에 등장하는 인물. 신원(고정된 식별 사실)만 표현하고, 시간에 따라 바뀌는 사실(상태·소속 등)은 "
        "CharacterState로 둔다 — 한 번 만든 뒤 값을 갱신하지 않는 정적 식별자 노드. "
        "서사적으로 의미 있는 인물만 만든다(지나가는 행인·단역 같은 이름 없는 엑스트라는 제외)."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description=(
                "인물의 정식 이름 또는 대표 호칭 하나. 별명·직함·존칭 등으로 다르게 불려도 항상 같은 "
                "대표 이름으로 통일한다(달라지면 같은 인물이 여러 노드로 분열). 외형/성격 등은 description에."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "인물의 일반 특징(외형/성격/소속·이력 등) 자연어 요약. 참고·RAG용이며 노드/관계와 겹쳐도 무방. "
                "단 소속·상태·소유·역할처럼 구조로 표현되는 사실은 반드시 해당 노드/관계로도 만든다"
                "(description만으로 끝내지 않음). 이력 추적 대상이 아니며 덮어쓰기 가능."
            ),
        ),
    ],
)

LOCATION = NodeType(
    label="Location",
    additional_properties=False,
    description=(
        "소설 내 장소 하나. 인물이 물리적으로 존재하는 실제 공간만 만든다 — 댓글창·게시판·앱 화면 같은 "
        "온라인·가상 공간은 제외. 상위 장소는 LOCATED_IN으로 한 단계씩만 잇는다(요새→도시→왕국, 건너뛰기 "
        "금지). 상위 장소가 원문에 없으면 노드만 만들어도 된다."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description="장소의 정식 명칭. 여러 표현이 나와도 같은 대표 이름으로 통일한다(달라지면 노드 분열·계층 단절).",
        ),
        PropertyType(
            name="description",
            type="STRING",
            description="장소의 사건과 무관한 일반 특징(지형/분위기/역할). 특정 사건 전개는 여기 말고 Event.description에.",
        ),
    ],
)

EVENT = NodeType(
    label="Event",
    additional_properties=False,
    description=(
        "소설 내 하나의 사건. 이후 참조·대조될 만한 단위로 추출하고, 문장 단위로 쪼개거나 배경 묘사·사소한 "
        "행동까지 사건화하지 않는다. chapter=연재 회차(충돌 탐지 기준), story_order=작중 시간순(속성 설명 참고)."
    ),
    properties=[
        PropertyType(
            name="title",
            type="STRING",
            description="사건 식별용 짧은 제목. 여러 청크에 걸쳐 언급돼도 같은 title로 한 Event에 병합되게 한다.",
        ),
        PropertyType(
            name="description",
            type="STRING",
            description=(
                "이 사건의 전개 요약. 인물의 상태 변화를 서술해도 좋으나, 그 상태 자체(누가 무엇을 소유/소속)는 "
                "반드시 CharacterState로도 만든다(서술만으로 끝내지 않음)."
            ),
        ),
        PropertyType(
            name="chapter",
            type="INTEGER",
            description="이 사건이 실린 연재 회차 번호. 원문에 회차 표시가 있으면 그 값을 그대로 쓴다.",
        ),
        PropertyType(
            name="story_order",
            type="FLOAT",  # 두 값 사이(예: 3.0/3.1 사이 3.05)에 삽입 가능하도록 FLOAT.
            description=(
                "Event들을 작중 시간순으로 정렬하기 위한 순서값(실제 연도/날짜를 그대로 적지 않고 이 스케일로 "
                "변환). 모든 Event에 채운다. 같은 chapter에 여러 사건이 있으면 발생 순서대로 chapter.0, "
                "chapter.1, chapter.2 …로 0.1씩 증가(예: 3화 세 사건이면 3.0, 3.1, 3.2); 하나면 chapter.0. "
                "회상·과거 사건은 더 작은 값(3화의 '지난달' 사건이면 2.8), 1화보다 이전(프리퀄)이면 0이나 음수"
                "(예: 0.5, -1.0). 두 값 사이 시점이면 그 사이 실수(3.0과 3.1 사이 3.05)."
            ),
        ),
        PropertyType(
            name="evidence_chunk",
            type="STRING",
            description=(
                "이 사건의 근거 원문 문장이 있는 청크 번호(예: 'C3', 여럿이면 'C3,C4'). 실제 그 문장이 있는 "
                "청크만 쓰고 추측하지 않는다. 후처리에서 EVIDENCED_BY 관계로 바뀌고 노드에서 제거된다."
            ),
        ),
    ],
)

CHARACTER_STATE = NodeType(
    label="CharacterState",
    additional_properties=False,
    description=(
        "인물의 한 속성(attribute)이 특정 회차부터 갖는 값(value). 시간에 따라 바뀌어 이후 대조·모순 판정 "
        "대상이 되는 사실만 담는다 — 부상·생사·소속·능력·소지품 등(불변 배경은 Character.description). "
        "능력·무공은 attribute로만; 이름 있는 소지품은 Item + attribute='소유'(value='보유'/'상실') + ABOUT→Item, "
        "소속은 attribute='소속'(value='소속'/'이탈') + ABOUT→Organization으로 표현한다(대상은 value 문자열이 "
        "아니라 ABOUT 노드로 식별). 소지품이 인물 간 이동하면 넘긴 인물 '상실'·받은 인물 '보유'를 각각 만든다. "
        "'회사원'·'계약직' 같은 신분·고용형태는 소속이 아니라 attribute='신분'으로 분리한다. 일시적 통증·피로처럼 "
        "그 회차에서 소모되는 상태는 만들지 않는다(지속 상태만). 같은 attribute에 여러 개가 쌓이며, "
        "ESTABLISHED_IN Event.chapter가 조회 시점 이하 중 가장 큰 것이 '현재 유효한 값'이다. 상태가 바뀌면 "
        "기존 노드를 고치지 말고 항상 새 노드를 만든다."
    ),
    properties=[
        PropertyType(
            name="attribute",
            type="STRING",
            description=(
                "상태 속성명, 짧게(예: 오른팔, 생사, 소속, 무공). 같은 종류는 항상 같은 표현·같은 입도로 통일한다"
                "('생사'로 통일, '오른팔_부상'처럼 잘게 쪼개지 않음) — 흔들리면 시간순 비교가 깨진다."
            ),
        ),
        PropertyType(
            name="value",
            type="STRING",
            description="속성 값을 짧게(예: 상실, 온전함, 생존, 사망). 서술 문장 말고 상태어만 — 자세한 정황은 evidence에.",
        ),
        PropertyType(
            name="evidence",
            type="STRING",
            description="이 상태를 뒷받침하는 원문 문장을 그대로/가깝게 인용한다(해석·설명을 덧붙이지 않음). 판정 근거용.",
        ),
        PropertyType(
            name="evidence_chunk",
            type="STRING",
            description=(
                "이 상태의 근거 원문 문장이 있는 청크 번호(예: 'C3', 여럿이면 'C3,C4'). 실제 그 문장이 있는 "
                "청크만 쓰고 추측하지 않는다. 후처리에서 EVIDENCED_BY 관계로 바뀌고 노드에서 제거된다."
            ),
        ),
    ],
)

ORGANIZATION = NodeType(
    label="Organization",
    additional_properties=False,
    description=(
        "조직·세력·단체(문파·길드·가문·회사·부서 등). 여러 인물이 공유하는 엔티티라 문자열이 아닌 독립 노드로 "
        "둔다('이 조직의 구성원은 누구인가' 조회를 위해). 인물 소속은 CharacterState(attribute='소속')를 만들어 "
        "ABOUT으로 이 조직에 잇고, '현재 소속'은 최신 상태에서 파생한다."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description="조직의 정식 명칭. 여러 표현이 나와도 같은 대표 이름으로 통일한다(달라지면 노드 분열).",
        ),
        PropertyType(
            name="description",
            type="STRING",
            description="조직의 사건과 무관한 일반 특징(성격/목적/규모 등) 요약. 참고·RAG용.",
        ),
    ],
)

ITEM = NodeType(
    label="Item",
    additional_properties=False,
    description=(
        "소유·이동·저작되거나 인물이 주고받는 '사물'(소지품·작중 창작물·무기·성물·선물·첨부물 등). 인물도 장소도 "
        "조직도 아닌, 사람이 다루는 물건/작품이면 여기다. 고유명이 없어도 서사적으로 중요하면 만들되 내용을 "
        "요약한 지시적 이름을 붙인다(예: '작가가 보낸 선물'). 단, 농담·소품으로 스치듯 언급되고 서사에 영향이 "
        "없는 사물(예: 제목만 흘려본 소설)은 만들지 않는다 — 실제로 소유·저작·사용되거나 이후 비중 있게 다뤄지는 "
        "것만. 이 노드는 사물의 정체성(무엇인지·종류·유래)만 담고, '지금 누가 가졌는가'는 CharacterState"
        "(attribute='소유')+ABOUT→Item으로 별도 표현한다(정체성과 소유 시점이 공존)."
    ),
    properties=[
        PropertyType(
            name="name",
            type="STRING",
            description=(
                "사물의 정식 명칭. 여러 표현이 나와도 같은 대표 이름으로 통일한다. 고유명이 없으면 내용을 요약한 "
                "지시적 이름을 만든다(예: '작가가 보낸 선물', '노인의 상자')."
            ),
        ),
        PropertyType(
            name="description",
            type="STRING",
            description="사물의 비시간적 특징(종류/유래/용도/저작 배경 등) 요약. 참고·RAG용(소유자는 CharacterState가 담당).",
        ),
    ],
)

# --- 관계 타입 ---

APPEARS_IN = RelationshipType(
    label="APPEARS_IN",
    description=(
        "인물이 사건에 실제로 참여하거나 현장에 있었음. 다른 인물의 대화·회상 속에서 이름만 언급된 경우는 제외."
    ),
)
HOSTS = RelationshipType(
    label="HOSTS",
    description=(
        "장소가 사건의 무대가 됨. 가장 구체적인 장소 하나에만 연결한다(상위 장소는 LOCATED_IN을 따라가면 알 수 있음)."
    ),
)
HAS_STATE = RelationshipType(
    label="HAS_STATE",
    description="인물이 CharacterState를 가짐. 같은 attribute에 여러 상태가 쌓이면 각각을 모두 이 관계로 연결한다.",
)
ESTABLISHED_IN = RelationshipType(
    label="ESTABLISHED_IN",
    description="CharacterState가 성립된 Event를 가리킨다. 그 Event.chapter가 이 상태의 시간 순서 기준이 된다.",
)
LOCATED_IN = RelationshipType(
    label="LOCATED_IN",
    description="장소가 한 단계 위 장소를 가리킴(요새→도시→왕국). 단계를 건너뛰지 않는다(계층을 순회로 복원하기 위해).",
)

RELATED_TO = RelationshipType(
    label="RELATED_TO",
    additional_properties=False,
    description=(
        "인물↔인물의 서사적 관계(동맹·적대·사제·혈연·연인 등). 종류는 type 속성에. 방향은 주체→대상(상호적 관계는 "
        "한 방향만). 현재 확립된 관계만 담는다(시점 추적이 필요하면 CharacterState로)."
    ),
    properties=[
        PropertyType(
            name="type",
            type="STRING",
            description="관계 종류를 짧게(예: 동맹, 적대, 사제, 혈연, 연인). 같은 종류는 항상 같은 표현으로 통일한다.",
        ),
        PropertyType(
            name="description",
            type="STRING",
            description="이 관계에 대한 짧은 부연(예: '어린 시절 같은 스승 밑에서 수학'). 없으면 생략 가능.",
        ),
    ],
)

ABOUT = RelationshipType(
    label="ABOUT",
    description=(
        "CharacterState가 어떤 외부 대상에 관한 것인지 그 노드로 직접 가리킨다 — 소유 상태는 Item, 소속 상태는 "
        "Organization. 덕분에 대상을 문자열이 아닌 그래프 노드로 식별한다('이 사물의 현재 소유자', '이 조직의 "
        "구성원' 조회). 부상·생사·능력처럼 외부 대상이 없는 상태에는 만들지 않는다."
    ),
)

INVOLVED_WITH = RelationshipType(
    label="INVOLVED_WITH",
    additional_properties=False,
    description=(
        "인물이 사물(Item)에 대해 '소유' 이외의 역할로 관여함 — 저자·독자·제작자 등(role에). 방향은 인물→사물. "
        "저작·소비는 사람-사람 관계가 아니므로 RELATED_TO로 묶지 말고 각자를 그 작품에 잇는다. 소유는 이 관계가 "
        "아니라 CharacterState(attribute='소유')+ABOUT으로."
    ),
    properties=[
        PropertyType(
            name="role",
            type="STRING",
            description="인물이 사물에 대해 갖는 역할을 짧게. 같은 역할은 통일한다(저자/독자/제작자).",
        ),
    ],
)

# --- 근거(provenance) 레이어 ---
# 아래 노드/관계는 LLM이 아니라 indexing.py가 직접 만든다. 그래서 NODE_TYPES/RELATIONSHIP_TYPES/PATTERNS에는
# 넣지 않는다(넣으면 LLM이 생성하려 든다): Chunk{chapter,index,text,embedding}, Chapter{number,summary},
# (Event|CharacterState)-[:EVIDENCED_BY]->Chunk, (Chunk)-[:IN_CHAPTER]->Chapter, (Chunk)-[:NEXT_CHUNK]->Chunk.

# --- 허용 관계 패턴 ---

PATTERNS = [
    ("Character", "APPEARS_IN", "Event"),
    ("Location", "HOSTS", "Event"),
    ("Character", "HAS_STATE", "CharacterState"),
    ("CharacterState", "ESTABLISHED_IN", "Event"),
    ("Location", "LOCATED_IN", "Location"),
    ("Character", "RELATED_TO", "Character"),
    ("Character", "INVOLVED_WITH", "Item"),          # 저자/독자/제작자
    ("CharacterState", "ABOUT", "Item"),             # 소유 대상
    ("CharacterState", "ABOUT", "Organization"),     # 소속 대상
]

# --- 스키마 조립 ---
# SchemaBuilder.run()은 node_types/relationship_types/patterns를 인자로 받으므로,
# 커스텀 파이프라인에서 재사용할 수 있게 이 목록들을 모듈 변수로 노출한다.

NODE_TYPES = [CHARACTER, LOCATION, EVENT, CHARACTER_STATE, ORGANIZATION, ITEM]
RELATIONSHIP_TYPES = [
    APPEARS_IN, HOSTS, HAS_STATE, ESTABLISHED_IN, LOCATED_IN,
    RELATED_TO, INVOLVED_WITH, ABOUT,
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
