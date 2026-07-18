# 스키마·프롬프트·few-shot 보완: Item 노드 + reified 소유/소속(ABOUT) + P0~P3 정정

## Context (왜 이 작업을 하나)

한국어 웹소설 → Neo4j KG 추출 PoC의 인덱싱 품질을 진단한 결과, 원문의 의미가 여러 층위에서 그래프로 안 넘어간다. 최종 목표는 "새 회차가 기존 설정과 충돌하는지 탐지"이며 핵심은 시간에 따라 변하는 사실의 정확한 추적이다. ch1~2 실제 DB를 원문과 대조해 4범주를 확인했다.

- **P0 (구조적 공백)**: '멸살법'(작중 웹소설), 첨부파일 선물 등 **이름을 갖고 소유·저작되는 '사물'**을 담을 타입이 없어 `Character.description`·`RELATED_TO`로 왜곡·소실.
- **P1 (스키마엔 있으나 추출 실패)**: Location 0개(지하철이 두 Event 무대인데 HOSTS 없음), Organization 0개.
- **P2 (누락 CharacterState)**: 유상아 자전거 상실, 정직원 승진, 김독자 계약해지 임박 등 시점 변화 누락.
- **P3 (오분류)**: `소속=회사원`(직업유형을 소속 자리에), `왼팔=부상`(일시적 압박통 과추출), `RELATED_TO[작가와 독자]`(작품 매개 역할을 사람-사람으로 평탄화).
- **P4 (과추출/노이즈)**: 서사적으로 중요치 않은 엑스트라·단역(지나가는 할머니·아이·행인·승객 등 이름 없는 배경 인물)을 Character로, '팔이 아픔' 같은 일시적 통증·피로를 CharacterState로 잘못 뽑아 그래프에 노이즈가 쌓임.
- **공통 근본원인(구조 불완전성)**: 소속·저작 역할 등이 `Character.description` 자유텍스트에만 있고 노드/관계로는 안 만들어져 그래프 조회 불가. (description 존재 자체가 문제가 아니라 **구조가 그것을 커버하지 못하는 것**이 문제 — 아래 방향 7 참고.)

**확정 방향** (장르 무관 general 스키마만 확장):
1. 일반 **`Item`** 노드 추가(무기·성물·문서·작품 등 대상화되는 사물).
2. **소유·소속을 reified CharacterState + `ABOUT` edge로 모델링** — 소유 상태는 `(CharacterState{attribute:'소유'})-[:ABOUT]->(Item)`, 소속 상태는 `(CharacterState{attribute:'소속'})-[:ABOUT]->(Organization)`. 시점 이력은 append-only CharacterState 체인으로 쌓아 충돌 탐지·provenance(ESTABLISHED_IN/EVIDENCED_BY) 보존. 대상은 문자열이 아니라 그래프 노드(ABOUT)로 식별.
3. **ABOUT은 LLM이 KG 출력에 직접 emit**(후처리·문자열 조인 없음). 회차 통째 단일 청크라 Item/Org/CharacterState가 한 출력에 공존 → 노드 id로 바로 연결.
4. **`MEMBER_OF` 제거** — 소속이 CharacterState+ABOUT로 완전 표현되어 중복. "현재 소속"은 최신 상태에서 파생.
5. 저작·소비(저자/독자/제작자)는 비시간적 역할이므로 **`INVOLVED_WITH(role)`**(Character→Item)로. LitRPG 시스템 계층은 **제외**.
6. P1~P3는 스키마 변경 없이 프롬프트/few-shot로 정정.
7. **구조 완전성(completeness)**: description은 추론·RAG용 자연어 보조 속성으로 노드/관계와 **중복 허용**. 목표는 **구조(노드·관계)만으로 모든 정보가 완전히 추출**되는 것 — description에만 있고 구조엔 빠진 사실이 없게 한다("description을 지워도 정보 손실 없음"이 기준). 앞선 "중복 금지" 방향은 폐기.

**결과물**: `schema.py` / `extractor.py` / `extraction_examples.py` 수정 → ch1~2 재인덱싱 검증.

---

## 변경 1 — `poc/src/schema.py`

### 1-A. `ITEM` NodeType 신규 (ORGANIZATION 뒤, line 246 다음)
Item = 사물의 **정체성**(무엇인지·종류·유래 같은 비시간적 사실)만. "누가 언제부터 소유/상실"은 Item이 아니라 `CharacterState{attribute:'소유'}-[:ABOUT]->(Item)`로.
```python
ITEM = NodeType(
    label="Item",
    additional_properties=False,
    description=(
        "서사 안에서 고유한 이름을 갖고 소유·이동·저작·수호되는 '사물' 하나. "
        "소지품(자전거·검), 작중 창작물(소설·문서·편지), 무기·성물·아이템 등 '대상화되는 사물'을 "
        "장르에 치우치지 않게 담는다 — 인물도 장소도 조직도 아닌, 사람이 다루는 물건/작품이면 여기다. "
        "이 노드는 사물의 '정체성'(무엇인지·종류·유래 같은 비시간적 사실)만 표현한다. "
        "'지금 누가 이걸 가지고 있는가'는 이 노드가 아니라 CharacterState(attribute='소유', value='보유'/'상실')를 "
        "만들어 그 상태를 이 Item에 ABOUT 관계로 잇는다 — 소유·이동되는 사물은 Item(정체성)과 "
        "소유 CharacterState(시점)가 '공존'하며 중복이 아니다. Item은 정체성 노드이므로 소유자 변동으로 값을 고치지 않는다."
    ),
    properties=[
        PropertyType(name="name", type="STRING", description=(
            "사물의 정식 명칭. 여러 표현이 등장해도 항상 같은 대표 이름 하나로 통일한다 — 달라지면 같은 사물이 "
            "여러 노드로 나뉜다."
        )),
        PropertyType(name="description", type="STRING", description=(
            "이 사물의 비시간적 특징(종류·유래·용도·저작 배경 등) 요약. 대시보드·RAG 컨텍스트용. "
            "소유자는 CharacterState가 담당한다(여기 적어도 참고용일 뿐 — 소유자는 회차에 따라 바뀐다)."
        )),
    ],
)
```

### 1-B. `ABOUT` RelationshipType 신규 (RELATED_TO 뒤)
소유/소속 CharacterState가 **어떤 대상에 관한** 것인지 그 노드를 직접 가리킨다. `'<이름>_소유'` 문자열 규칙을 그래프 edge로 대체.
```python
ABOUT = RelationshipType(
    label="ABOUT",
    description=(
        "상태 사실(CharacterState)이 '어떤 외부 대상에 관한' 것인지 그 대상 노드로 직접 가리킨다. "
        "소유 상태는 그 사물(Item)을, 소속 상태는 그 조직(Organization)을 ABOUT으로 잇는다 "
        "— (CharacterState{attribute:'소유'})-[:ABOUT]->(Item), (CharacterState{attribute:'소속'})-[:ABOUT]->(Organization). "
        "덕분에 소유/소속 대상을 문자열이 아니라 그래프 노드로 식별해 '이 사물의 현재 소유자', "
        "'이 조직의 구성원' 같은 대상 기준 조회가 된다. "
        "부상·생사·능력처럼 인물 자신에 대한 상태(외부 대상 없음)에는 ABOUT을 만들지 않는다."
    ),
)
```

### 1-C. `INVOLVED_WITH` RelationshipType 신규 (ABOUT 뒤) — 저작/소비 역할
비시간적 역할. 저자·독자를 사람-사람 RELATED_TO로 평탄화하지 말고 각자를 작품(Item)에 연결.
```python
INVOLVED_WITH = RelationshipType(
    label="INVOLVED_WITH",
    additional_properties=False,
    description=(
        "인물이 어떤 사물(Item)에 대해 '소유' 이외의 역할로 관여함 — 저작한 저자, 읽는 독자, 만든 제작자 등. "
        "역할은 role 속성에. 방향은 항상 인물(주체)→사물. 저작·소비는 사람-사람 관계가 아니라 "
        "'사람이 작품에 대해 갖는 역할'이므로 RELATED_TO로 평탄화하지 말고 각자를 이 관계로 그 작품에 연결한다. "
        "'누가 지금 가지고 있는가'(소유)는 이 관계가 아니라 CharacterState(attribute='소유')+ABOUT으로 표현한다."
    ),
    properties=[
        PropertyType(name="role", type="STRING", description=(
            "인물이 이 사물에 대해 갖는 역할을 한국어로 짧게. 같은 역할은 항상 같은 표현으로 통일"
            "(저작=항상 '저자', 읽는 쪽='독자', 만든 쪽='제작자')."
        )),
    ],
)
```
> 코드 주석 트레이드오프: 저작을 별도 CREATED 타입으로 두면 소비용 타입이 또 필요 → role 일반화로 시작.

### 1-D. `MEMBER_OF` 제거
`MEMBER_OF` 정의(line 298-305)와 PATTERNS/RELATIONSHIP_TYPES에서의 참조를 **삭제**. 소속은 `CharacterState{attribute:'소속'}-[:ABOUT]->(Organization)`가 대체. Organization NodeType 자체는 유지하되 description의 "MEMBER_OF로 연결" 문구(line 225)를 "소속 상태(CharacterState attribute='소속')를 ABOUT으로 이 조직에 연결"로 수정.

### 1-E. `CharacterState.description` 정정 (line 155-169)
- **소유/소속 모델 명시**: "소유·소속처럼 대상이 있는 상태는 attribute를 '소유'/'소속'으로 쓰고, value에는 **상태(소유→'보유'/'상실', 소속→'소속'/'이탈')**를 넣으며, **그 대상 노드(Item/Organization)를 ABOUT 관계로 직접 잇는다**. 대상 이름을 value에 문자열로 적지 않는다."
- **과추출 금지**: "일시적·순간적 감각(잠깐의 압박통·어지럼)처럼 이후 회차와 대조할 지속 상태가 아닌 것은 CharacterState로 만들지 않는다."
- **소속 vs 신분 분리**: "'회사원'·'계약직'·'정직원' 같은 직업유형·고용형태·신분은 소속이 아니다 — attribute='신분'으로 분리하고, 실제 조직은 Organization+ABOUT으로."
- 기존 "능력·소지품을 별도 노드로 안 만든다"(line 161-162)는 **소지품 부분 수정**: 이름 있는 소지품은 Item 노드로도 만들고 소유 상태를 ABOUT으로 연결(능력·무공은 종전대로 CharacterState attribute만).

### 1-F. 구조 완전성(completeness) — 노드/관계'만'으로 모든 정보 추출
**정책(사용자 결정)**: description은 LLM이 이후 추론·RAG에서 참고할 **자연어 보조 속성**이며 노드/관계와 **중복돼도 무방**하다. 목표는 "description에서 정보를 빼는 것"이 아니라, **구조(노드·관계·상태)만으로 모든 정보가 완전히 추출되는 것** — description에만 존재하고 구조엔 빠진 사실이 없게 한다. (앞 버전의 "중복 금지/이중 기재 금지" 방향은 폐기.)

**진단 재해석**: tls123 "멸살법의 작가", 유상아 "인사팀 직원"이 description에 **있는 것 자체는 문제가 아니다**(그건 추론 보조로 유용). 문제는 그 사실이 **구조로는 안 만들어져 그래프 조회가 불가능**한 것. → P0/P1(Item·Location·Organization + ABOUT/INVOLVED_WITH)이 구조의 '집'을 만들고, 아래 completeness 규칙이 "빠짐없이 구조화"를 강제한다.

**completeness 체크리스트 — 아래 사실은 description에 적혀 있든 없든 반드시 구조로도 만든다**:
| 사실 | 반드시 만들 구조 |
|---|---|
| 소속(회사·문파·팀) | Organization + `CharacterState{attribute:'소속'}`-[:ABOUT]->Org |
| 신분·고용형태(계약직·정직원) | `CharacterState{attribute:'신분'}` |
| 소지품 소유 | Item + `CharacterState{attribute:'소유'}`-[:ABOUT]->Item |
| 저작·독자 등 작품 역할 | Item + `INVOLVED_WITH{role}` |
| 인물 간 서사 관계 | `RELATED_TO{type}` |
| 사건 무대 장소 | Location + `HOSTS` |

**필드별 조치**:
- `Character.description`(line 44-51): 문구를 크게 바꾸지 않는다(소속 등 예시 유지, 중복 허용). 단 기존의 **중복 금지 취지 문구**("두 필드가 같은 내용을 담으면 … 일관성이 없어진다", line 48-49)를 **삭제**하고, 대신 한 줄 추가 — "여기 배경으로 적더라도 소속·상태·소지품·역할 등 구조로 표현되는 사실은 해당 노드/관계로도 **반드시** 만든다(description만으로 끝내지 않는다)."
- `Item.description`(신규 1-A): 소유자를 굳이 안 적어도 되나(소유는 CharacterState가 source of truth), 적어도 무방. "미기재" 강제는 하지 않음.
- `Organization.description` / `Location.description`: 변경 없음(completeness 원칙과 상충 없음).
- `Event.description`(line 108-115): 한 줄 추가 — "인물의 소속 변경·소지품 획득 같은 상태 변화를 서술해도 좋으나, 그 상태 자체는 반드시 CharacterState로도 만든다(Event.description 서술만으로 끝내지 않는다)." (중복은 허용, 구조 누락만 금지.)
- `RELATED_TO.description`: 변경 없음(작품 매개 역할은 INVOLVED_WITH로 만들되, 관계 부연은 자유).

### 1-G. 목록 변수 갱신 (line 346-367)
```python
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
]  # ("Character","MEMBER_OF","Organization") 삭제
NODE_TYPES = [CHARACTER, LOCATION, EVENT, CHARACTER_STATE, ORGANIZATION, ITEM]
RELATIONSHIP_TYPES = [
    APPEARS_IN, HOSTS, HAS_STATE, ESTABLISHED_IN, LOCATED_IN,
    RELATED_TO, INVOLVED_WITH, ABOUT,               # MEMBER_OF 삭제
]
```

### 1-H. 과추출 방지 — 중요 인물·지속 상태만 (schema.description 보강)
- **Character NodeType description(line 23-28)에 추가**: "서사적으로 의미 있는 인물만 노드로 만든다 — 이름이 있거나, 반복 등장하거나, 사건·관계·상태에 관여하는 인물. 지나가는 할머니·아이·행인·승객처럼 배경에 잠깐 스치는 이름 없는 엑스트라·단역은 만들지 않는다(그들과 엮인 APPEARS_IN·관계도 만들지 않는다)."
- **CharacterState는 1-E의 '일시적 감각 배제'가 담당(재확인·예시 보강)**: "'팔이 아픔' 같은 일시적 통증, 잠깐의 피로·긴장·감정처럼 그 회차에서 소모되고 이후 회차와 대조할 대상이 아닌 상태는 만들지 않는다 — 지속되는 부상/불구·생사·소속·능력·소지품 변화만 CharacterState로."

---

## 변경 2 — `poc/src/extractor.py` 도메인 규칙 (`DEFAULT_TEMPLATE`, line 79-106)

- **(수정) 소지품 불릿(line 90-92)**: "이름 있는 소지품·물건은 **Item 노드로 만들고**, 소유는 `CharacterState(attribute='소유', value='보유'/'상실')`를 만들어 그 상태를 `ABOUT`으로 그 Item에 잇는다. 소지품 이동 시: Item 1개 + 넘긴 인물 '상실'·받은 인물 '보유' CharacterState 각각 + 각 상태의 ABOUT→Item. 능력·무공은 종전대로 CharacterState attribute만."
- **(신규) 사물·저작·소비 규칙**: 고유 이름 사물(작중 소설·문서·무기·성물·선물)은 description/RELATED_TO에 녹이지 말고 **Item**으로. 저작·제작 → `INVOLVED_WITH(role='저자'/'제작자')`, 독자 → `INVOLVED_WITH(role='독자')`. **작가·독자는 사람-사람 관계 아님** — RELATED_TO 금지, 각자를 Item에 연결.
- **(수정) 조직 불릿(line 94)** → Location/Organization 적극 추출 + 소속 모델:
  - "사건의 구체적 공간이 있으면 **반드시 Location + HOSTS**(지하철·사무실 등 일상 공간 포함)."
  - "회사·부서·문파·단체는 **Organization 노드**로. 인물 소속은 `CharacterState(attribute='소속', value='소속'/'이탈')`를 만들어 `ABOUT`으로 그 Organization에 잇는다(소속을 문자열로만 두지 않음). **MEMBER_OF는 쓰지 않는다.**"
  - "'회사원'·'계약직'·'정직원'은 소속 아님 — attribute='신분'으로 분리."
- **(수정) RELATED_TO 불릿(line 95-99)**: 예시에서 **'작가-독자' 삭제**. 추가: "작품·사물 매개 역할(저자·독자·소유자)은 RELATED_TO로 평탄화 말고 Item+INVOLVED_WITH/CharacterState+ABOUT으로."
- **(신규) 인물 선별(엑스트라 배제)**: "서사적으로 의미 있는 인물만 Character로 만든다 — 이름이 있거나, 반복 등장하거나, 사건·관계·상태에 관여하는 인물. 지나가는 할머니·아이·행인·승객처럼 배경에 잠깐 스치는 이름 없는 엑스트라·단역은 만들지 않는다(그들에 대한 APPEARS_IN·관계·상태도 만들지 않는다)."
- **(신규) 과추출 금지(일시적 상태)**: "CharacterState는 이후 회차와 대조할 **지속 상태**만. '팔이 아픔' 같은 일시적 통증, 급정거로 팔 잡힌 순간적 압박통, 잠깐의 어지럼·피로·긴장·감정처럼 그 회차에서 소모되는 상태는 만들지 않는다 — 지속 부상/불구·생사·소속·능력·소지품 변화만."
- **(신규) 구조 완전성(일반 원칙)**: "description은 추론·참고용 자연어 요약이며 노드/관계와 중복돼도 된다. 그러나 구조로 표현 가능한 사실(소속·신분·소지품 소유·저작/독자 역할·인물 관계·사건 장소)은 description에 적혀 있든 없든 **반드시 그 구조(Organization/CharacterState/Item/INVOLVED_WITH/RELATED_TO/Location + ABOUT/HOSTS)로도 만든다** — description에만 있고 노드/관계엔 없는 사실을 남기지 않는다."
- **(신규) ABOUT 직접 출력 지침**: "소유/소속 CharacterState를 만들 때 그 대상(Item/Organization)을 **같은 출력에서 ABOUT 관계로 함께 낸다**(노드 id 재사용). 후처리에 의존하지 않는다."

---

## 변경 3 — `poc/src/extraction_examples.py` few-shot

기존 JSON 형식(id 재사용·evidence_chunk) 유지. docstring 규칙(line 8-23) 갱신 + 아래 예시 조정.

- **예시 2 (반란군 가담)**: 현재 `CharacterState 소속=반란군`을 → `Organization(반란군)` 노드 + `CharacterState{attribute:'소속', value:'소속'}` + `HAS_STATE` + `ESTABLISHED_IN` + **`ABOUT`→반란군**으로 교체(소속 reified 시연).
- **예시 3 (무협 화산파)**: `MEMBER_OF` 제거 → `Organization(화산파)` + `CharacterState{attribute:'소속',value:'소속'}`+`ABOUT`→화산파. 무공 CharacterState는 유지.
- **예시 4 (로판 성배)**: **Item('빛의 성배') 노드 추가** + 두 소유 CharacterState(`attribute:'소유'`, value `상실`/`보유`) 각각 `HAS_STATE`·`ESTABLISHED_IN`·**`ABOUT`→성배**. → Item(정체성)+소유 상태(시점) 공존 시연.
- **예시 5 (현대 드라마, 반례 종합)**: 입력을 (a)조직 소속 (b)계약직→정직원 신분 (c)일시적 접촉 vs 실제 골절 (d)**지나가는 엑스트라**(옆자리 할머니·아이가 놀라 웅성거림)로 확장. 출력: Location(지하철)+HOSTS, Organization(대한물산)+`CharacterState{attribute:'소속'}`+ABOUT(**MEMBER_OF 없음**), `신분=계약직/정직원`, 실제 지속 부상만 CharacterState, **일시적 팔 아픔은 CharacterState 없음**, **할머니·아이는 Character 노드 없음** — P1·P3(a)·P3(b)·**P4**(엑스트라·일시적 상태 배제) 동시 시연. (Character.description은 "인사팀 사무직" 등 자연어로 자유롭게 쓰되 소속은 구조로도 반드시 만든다 — completeness 시연.)
- **예시 6 신규 (작중 창작물, 장르 중립)**: 무명 작가 '해무'의 웹소설 `<탑의 문>`·유일 독자 준호. Character 2 + **Item `<탑의 문>`** + `INVOLVED_WITH`(해무 role='저자', 준호 role='독자') + **해무-준호 RELATED_TO 없음**(평탄화 금지 대조) — P0·저작·소비·P3(c) 시연.

---

## 변경 불필요 (호환 확인됨)
- `context.py`: `_node_display`(line 32-40)가 `name` 우선이라 `Item:멸살법` 정상 직렬화. ABOUT/INVOLVED_WITH 관계도 일반 rel 직렬화 경로 사용.
- `resolver.py`: `CombiningFuzzyResolver`가 라벨별 `name` 그룹핑 → Item 병합 자동, 타 라벨과 안 섞임.
- `evidence.py` / `indexing.py`: 변경 없음. ABOUT은 **LLM이 직접 emit**하므로 evidence.py식 후처리 불필요. `schema.NODE_TYPES/RELATIONSHIP_TYPES/PATTERNS` 주입(indexing.py:142-144)만으로 반영. indexing.py는 재인덱싱 진입점.

---

## 주의점
- **이중화(Item + 소유 CharacterState) 혼동**: LLM이 하나만 낼 위험. 방지 — "소유·이동 사물은 Item + 소유 CharacterState + ABOUT 셋 다" 규칙을 Item.description·CharacterState.description·프롬프트·few-shot 4곳 반복, 예시 4에서 직접 시연.
- **ABOUT emit 누락**: CharacterState는 냈는데 ABOUT을 빠뜨릴 수 있음 → 대상 식별 불가. 방지: 프롬프트 "함께 낸다" 명시 + 예시 2/3/4/5에 ABOUT 포함. 검증 쿼리로 ABOUT 없는 소유/소속 CharacterState 적발.
- **MEMBER_OF 제거 파급**: 기존 novel_context 덤프·조회가 MEMBER_OF에 의존하지 않는지 확인(현재 인스턴스 0건이라 안전). "현재 소속" 조회는 최신 CharacterState 파생으로 대체.
- **role 값 파편화**: description 정규값 열거 + few-shot 통일.
- **소급 안 됨**: 깨끗한 DB에 ch1~2 재인덱싱 필요.
- **범위 밖**: evidence_chunk +1 off-by-one은 별개 이슈(chunk_size=100/overlap=0 이미 반영으로 완화, 근본 원인은 후속).

---

## 검증 (ch1~2 재인덱싱 후 Cypher)
```
cd poc && LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py  # 이어서 ch2
```
- **P0 Item**: `MATCH (i:Item) RETURN i.name` — 멸살법/자전거/선물 존재.
- **소유 reified**: `MATCH (c:Character)-[:HAS_STATE]->(cs:CharacterState {attribute:'소유'})-[:ABOUT]->(i:Item) RETURN c.name, cs.value, i.name`.
- **소속 reified**: `MATCH (c:Character)-[:HAS_STATE]->(cs:CharacterState {attribute:'소속'})-[:ABOUT]->(o:Organization) RETURN c.name, cs.value, o.name` — 유상아→(조직).
- **저작·소비**: `MATCH (c:Character)-[r:INVOLVED_WITH]->(i:Item) RETURN c.name, r.role, i.name` — tls123 저자, 김독자 독자.
- **P1 Location/HOSTS**: `MATCH (l:Location)-[:HOSTS]->(e:Event) RETURN l.name, collect(e.title)` — 지하철.
- **MEMBER_OF 제거 확인**: `MATCH ()-[r:MEMBER_OF]->() RETURN count(r)` — 0.
- **ABOUT 누락 적발**: `MATCH (cs:CharacterState) WHERE cs.attribute IN ['소유','소속'] AND NOT (cs)-[:ABOUT]->() RETURN cs` — 0 기대.
- **P3(a)**: `MATCH (cs:CharacterState {attribute:'소속'}) RETURN cs.value` — '회사원' 없음(상태값만). `attribute:'신분'`에 계약직/정직원.
- **P3(b)**: 김독자 팔 관련 CharacterState — **0건 기대**.
- **P3(c)**: 김독자↔tls123 RELATED_TO — **0건 기대**.
- **회귀**: 라벨 카운트에 Item 등장.
- **P4 과추출 방지(육안)**: `MATCH (c:Character) RETURN c.name` — 주요 인물만(지나가는 할머니·아이·행인·승객 등 엑스트라 없음). `MATCH (cs:CharacterState) RETURN cs.attribute, cs.value` — '팔 아픔'류 일시적 상태 없음(지속 부상/소속/소지품/능력만).
- **구조 완전성(육안, 필수)**: `MATCH (c:Character) RETURN c.name, c.description` — description에 적힌 사실 중 소속('인사팀 직원')·저작 역할('멸살법의 작가')·소지품·관계가 각각 구조(Organization+ABOUT / INVOLVED_WITH / Item+ABOUT / RELATED_TO)로도 **빠짐없이 만들어졌는지** 5건 스팟체크(description에 남아 있는지는 무관 — 중복 허용, 구조 누락만 실패). 즉 "description을 지워도 정보 손실이 없는가"가 판정 기준.
