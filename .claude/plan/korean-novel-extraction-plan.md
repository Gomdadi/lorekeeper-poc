# 한국어 웹소설 추출 파이프라인 개선 계획

## Context

현재 인덱싱 파이프라인은 neo4j-graphrag의 **라이브러리 기본 영어 프롬프트**(`ERExtractionTemplate`)로 한국어 웹소설 원고에서 KG를 추출한다. 세 가지 개선으로 추출 품질을 높인다.

1. **프롬프트 최적화** — 영어 기본 프롬프트를 한국어 웹소설 도메인(회차 마커 `[N화]`, `CharacterState` 시간축 규칙 등)에 맞게 재작성.
2. **스키마 자동 추출** — LLM에게 원고로부터 스키마를 제안받아, 우리가 수동 설계한 스키마(`schema.py`)에 **빠뜨린 노드/관계 타입 후보**가 있는지 발견.
3. **배경지식 prefix 주입** — 소설 요약·인물 사전을 각 청크 추출 프롬프트에 함께 실어, LLM이 개별 청크만 보지 않고 작품 전반을 인지한 채 추출하도록.

**구현 순서**: 목표 2(스키마 자동추출)를 먼저 돌려 추가할 노드/관계를 확정한 뒤, 그 스키마 위에서 목표 1(프롬프트)+3(배경지식)을 통합 구현한다. 스키마가 프롬프트·배경지식 설계에도 반영되므로 이 순서가 논리적이다.

---

## 조사로 확정된 라이브러리 접점 (neo4j-graphrag 1.18.0)

- 커스텀 프롬프트: `LLMEntityRelationExtractor(prompt_template=...)` 인자로 교체 가능. `ERExtractionTemplate` 서브클래스 권장. placeholder `{schema}`/`{examples}`/`{text}` 유지 필수, JSON 리터럴 중괄호는 `{{`/`}}` 이스케이프. V2 structured output 모드에서도 이 프롬프트가 그대로 LLM에 전달됨.
- 배경지식 주입: `run()`이 청크별 프롬프트로 넘기는 자유 텍스트는 `examples`뿐. 전용 `{novel_context}` placeholder를 쓰려면 **`extract_for_chunk` 오버라이드**가 필요(V1·V2 양쪽의 단일 진입점, `run`/`run_for_chunk`는 그대로 상속 가능).
- 스키마 자동추출: `SchemaFromTextExtractor(llm=...).run(text=원고)` → `GraphSchema` 반환. 우리 `schema.py`와 **동일한 타입**(NodeType/RelationshipType/Pattern)이라 label 집합 비교가 바로 됨. 단 자동 추출은 description·properties가 빈약하고 `CharacterState` 같은 시간축 설계는 못 잡음 → **"추가 후보 발견용"으로만** 쓰고 자동 반영하지 않음.

---

## 목표 2: 스키마 자동 추출 및 비교 (먼저 실행)

**신규 파일** `poc/src/schema_suggest.py` (오프라인 분석 스크립트, 파이프라인 미변경):

- `SchemaFromTextExtractor(llm=build_llm(...), use_structured_output=True)`를 만들어 원고(`LOREKEEPER_INPUT` 또는 기본 `input.txt`)로 `run(text=...)` 호출 → `GraphSchema` 획득.
- `build_llm`은 `pipeline.py:67`을 재사용(reasoning_effort 옵션 전달 가능).
- 획득한 `.node_types` / `.relationship_types` / `.patterns`의 label을 기존 `schema.py`의 `NODE_TYPES` / `RELATIONSHIP_TYPES` / `PATTERNS` label 집합과 비교.
- 콘솔에 리포트 출력: **기존에 없는 후보**(추가 검토 대상)와 겹치는 것, 그리고 자동추출이 놓친 우리 고유 설계(예: `CharacterState`)를 구분해 표시.
- 자동추출 원본 `GraphSchema`는 `output/schema_suggested.json`으로 `save()` 저장(사람이 상세 검토용).
- **schema.py는 이 단계에서 자동 수정하지 않음.** 리포트를 보고 사람이 추가 여부를 판단 → 필요 시 별도로 `schema.py`에 수동 반영.

### 목표 2-B: 형제 프로젝트 스키마 차용 분석 (LLM 자동추출과 병행)

`../kgdb`와 `../soma-poc`는 **모두 같은 LoreKeeper 프로젝트의 형제/자매 저장소**(kgdb=정식 MVP 워크스페이스, soma-poc=초기 프로토타입)다. LLM 자동추출은 원고에 표면적으로 드러난 것만 제안하므로, **이미 우리 도메인을 고민한 두 저장소의 스키마를 함께 검토**해 자동추출이 놓치는 설계 후보를 보완한다. 자동추출 리포트 + 아래 차용 후보를 합쳐 "schema.py 추가 검토 목록"을 만들고, 반영 여부는 사람이 판단(자동 반영 없음).

**kgdb 차용 후보** (`kgdb/docs/schema/schema.cypher`, `docs/guides/kgdb-knowledge-layer-guide.md`, `docs/lorekeeper/data-and-workflows/auto-extraction-json-contract.md`):
- `SourceSpan` 증거 노드 + `EVIDENCED_BY` — 현재 `CharacterState.evidence`(문자열 복사)를 증거 노드로 승격해 근거 공유·역추적. 충돌탐지의 "어느 회차/문단이 근거냐"에 직결.
- `Character.aliases[]` + 후보→WorldEntity 2단계 병합 — 단일 `name` 강제로 버려지는 별칭을 보존해 노드 분열 감소.
- 인물↔인물 관계 어휘(`knows`/`opposes`/`alliedWith`) — 우리 PATTERNS에 전무. 소설 관계망의 핵심.
- `reviewStatus`/`lifecycleStatus` — 주석 처리된 retcon 필드를 "작가 검토/확정" 워크플로로 대체.
- 웹소설 특화 엔티티(`item`/`organization`/`skill`/`title`/`status`/`system`/`rule`)와 `causes`(인과) 술어.

**soma-poc 차용 후보** (`soma-poc/mvp/src/llm_indexer.py`, `mvp/indexing.md`):
- 관계 `since`/`until` 유효구간 — 인물↔인물/조직 "관계의 시작·종료 시점" 모델링(우리 CharacterState는 인물 상태만 추적). 단 soma의 Event `name` 문자열 참조는 fragile → 우리 `Event.story_order`/`chapter` 숫자값으로 개선 차용.
- `Item`/`Organization` 노드 — 소속 배신·아이템 소유 이동 등 관계형 충돌의 무대.

**우리가 앞서 차용 불필요(현행 유지)**: 시간축(`Event.chapter`/`story_order` fractional indexing), 상태변화 시점별 유효값 조회(`ESTABLISHED_IN`), 엄격한 장소 1단계 계층(`LOCATED_IN`) — 두 저장소 모두 이 축이 없거나 약함.

---

## 목표 3: 증분 컨텍스트 주입 (그래프 덤프 + rolling summary)

정적 "작품 요약 + 인물 사전"(폐기)은 원고 전체를 미리 아는 걸 전제해 실제 운영(새 회차를 기존 세계관과 대조)과 괴리된다. 대신 soma-poc 방식의 **증분 컨텍스트**를 쓴다.

**입력 단위 = 회차**. 한 번 실행 = 한 회차 인덱싱. 자동 순차 루프는 만들지 않고(사용자가 회차 파일을 순서대로 수동 실행), 각 실행은 **이전 회차까지 누적된 결과**를 컨텍스트로 주입한다.

novel_context 소스 두 가지 조합:
- **(a) 그래프 전체 덤프** — 현재 DB의 노드/관계를 텍스트로 직렬화(soma-poc `mvp/src/neo4j_client.py`의 `dump_graph_text` 참고). 엔티티 식별·별칭 정합·맥락 제공.
- **(b) rolling summary** — 회차마다 3~5문장 서사 요약을 파일에 누적(soma-poc `summary_store.py` 참고). 그래프가 못 담는 서사 흐름 보강.

**DB 누적이 전제**: 이전 회차 결과가 남아야 컨텍스트가 된다 → 회차 인덱싱 모드에서는 `Neo4jWriter(clean_db=True)`(`pipeline.py:117`)와 harness `_reset_db`(`indexing_eval.py:215`)를 **끈다**. 변형 비교(OFAT) harness와 구분되는 실행 모드다.

**anchoring 방지 — 프롬프트 지침**: 컨텍스트(그래프 덤프·rolling summary)와 새 회차 원고가 충돌하면 **새 회차를 우선**하도록 프롬프트에 명시한다. 기존 컨텍스트는 엔티티 식별·맥락용이며 **새 회차가 진실의 원천**이다 — 이래야 충돌 탐지 대상인 모순이 기존 그래프에 맞춰 왜곡·은폐되지 않고 날것으로 추출된다.

**회차별 실행 흐름**:
1. 현재 DB 덤프 + rolling summary 로드 → novel_context 조합
2. 이 회차 파이프라인 run (DB 리셋/clean_db 안 함, 누적)
3. 이 회차 3~5문장 요약을 rolling summary 파일에 append. **요약 생성은 `gpt-5.4-mini`(EXTRACTION_MODEL)를 `reasoning_effort="high"`로** — `build_llm("high")` 재사용. 서사 요약은 인과·복선 판단이 필요해 추출보다 추론 강도를 높인다.

---

## 목표 1 + 3 통합 구현 (스키마 확정 후)

### 신규 파일 `poc/src/extractor.py` — 커스텀 템플릿 + 커스텀 extractor

채택 방식: **C — `{novel_context}`에 `{examples}`와 나란한 전용 placeholder를 부여**하고, 라이브러리가 안 채워주는 이 새 빈칸을 우리가 배선한다. (novel_context를 청크 추출 시점의 값으로 주입하는 라이브러리 정석 경로. A안=본문에 미리 박기 대비 코드는 많지만 배경지식/예시가 프롬프트상 깔끔히 분리됨.)

#### 원칙: 라이브러리 원본 프롬프트를 분석해 빠짐없이 이식 + 도메인 규칙 추가

한국어 프롬프트를 백지에서 쓰지 않는다. 먼저 `ERExtractionTemplate.DEFAULT_TEMPLATE`(prompts.py:163-193)를 정독해, 원본이 담고 있는 **일반 추출 지시를 하나도 누락 없이 한국어로 이식**한 뒤 그 위에 웹소설 도메인 규칙을 얹는다. 이식이 누락되면(특히 5·6번) 추출 구조가 깨져 오히려 퇴보한다.

원본에서 반드시 이식할 요소(체크리스트):
1. **역할 부여** — "지식 그래프 구축을 위해 구조화된 정보를 추출하는 최상위 알고리즘" 성격 규정.
2. **작업 정의** — 텍스트에서 엔티티(노드)와 그 타입을 뽑고, 노드 간 관계를 뽑는다.
3. **출력 구조 명세** — `nodes`/`relationships` JSON 구조와 형태 예시(원본의 `{{"nodes":[...],"relationships":[...]}}`에 대응).
4. **스키마 제약** — "주어진 노드/관계 타입만 사용"(`{schema}`와 연결).
5. **ID 규칙** — 각 노드에 문자열 unique ID를 부여하고, 관계 정의에서 그 ID를 재사용.
6. **관계 방향·타입 준수** — 관계의 source/target 노드 타입과 방향을 스키마 패턴대로 지킴.
7. **JSON 유효성 규칙 4개** — ①JSON 외 부가정보 금지 ②backtick 제거 ③list로 감싸지 않음 ④property명 큰따옴표.
   - 단 `use_structured_output=True`(V2)에서는 출력이 `Neo4jGraph` 스키마로 강제되므로 ②③④의 실효는 낮음. 그래도 프롬프트 텍스트는 그대로 전달되니 의미 왜곡 방지를 위해 간결히 유지하되, 비중은 5·6번과 도메인 규칙에 둔다.

#### 클래스 설계

- `KoreanWebNovelERTemplate(ERExtractionTemplate)`:
  - `DEFAULT_TEMPLATE`를 한국어 웹소설용으로 재작성 — 위 원본 체크리스트(1~7) 이식 + 웹소설 도메인 규칙(회차 마커 `[N화]` 해석, `CharacterState`/`ESTABLISHED_IN`/`HAS_STATE` 규칙, attribute/value 한국어 입도 통일)을 본문에 녹임.
  - **새 회차 우선 지침**(목표 3 anchoring 방지): `{novel_context}`(그래프 덤프·rolling summary)는 엔티티 식별·맥락용 참고 자료이며, 이와 새 회차 원고(`{text}`)가 충돌하면 **새 회차 원고를 진실의 원천으로 우선**하라고 명시. 기존 컨텍스트에 맞춰 새 사실을 왜곡·보정하지 말 것.
  - **soma-poc 프롬프트 자산 참고**(`soma-poc/mvp/src/llm_indexer.py:38-65`): ①별칭 해소를 "기존 그래프 상태를 대조"시키는 지시로 강화 ②good/bad 예시 대조 few-shot 포맷 — 우리 `extraction_examples.py` 예시 서술 방식에 참고. (soma의 Cypher 문자열 생성 규칙 자체는 우리 구조화 추출에 불필요.)
  - placeholder: `{novel_context}`(배경지식) + `{schema}` + `{examples}`(few-shot) + `{text}`(청크). JSON 예시 중괄호는 `{{`/`}}` 이스케이프.
  - `EXPECTED_INPUTS = ["text"]`.
  - **`format()` 오버라이드 필요**: 부모 `ERExtractionTemplate.format(self, schema, examples, text="")`는 novel_context 인자를 안 받아 그대로 두면 `TypeError`. `format(self, schema, examples, text="", novel_context="")`로 확장해 네 값 모두 `self.template.format(...)`에 전달.
- `NovelContextExtractor(LLMEntityRelationExtractor)`:
  - `__init__`에 `novel_context: str = ""`를 추가로 받아 인스턴스에 저장(나머지 인자는 부모에 위임).
  - `extract_for_chunk`만 오버라이드: `self.prompt_template.format(text=..., schema=..., examples=..., novel_context=self.novel_context)`로 배경지식을 채운 뒤, 부모와 동일한 V2/V1 분기 로직 수행. (부모 메서드 본문을 그대로 복제하되 format 인자만 추가 — 최소 변경.)

### 기존 파일 수정

- `poc/src/pipeline.py` (`build_pipeline`, `pipeline.py:86-131`):
  - 시그니처에 `novel_context: str = ""` 파라미터 추가.
  - `pipeline.py:108-113`의 extractor 생성부를 `NovelContextExtractor(llm=llm, prompt_template=KoreanWebNovelERTemplate(), novel_context=novel_context, use_structured_output=True, on_error=OnError.RAISE)`로 교체.
- **회차 누적 인덱싱 실행 경로** (변형 비교 harness와 별개 모드):
  - 실행 전 현재 DB를 텍스트 덤프(그래프 덤프) + rolling summary 파일 로드 → `novel_context` 조합.
  - `build_pipeline(..., novel_context=context)`로 전달. `data["extractor"]["examples"]`는 few-shot(`EXTRACTION_FEW_SHOT`)만 유지 — 컨텍스트는 별도 placeholder로 분리.
  - **DB 리셋/clean_db 끔**: `_reset_db` 미호출 + `Neo4jWriter(clean_db=False)`로 회차 누적. → `build_pipeline`에 clean_db 플래그 파라미터 추가 필요.
  - 추출 후 이 회차 3~5문장 요약을 rolling summary 파일에 append. 요약 생성은 `build_llm("high")`(gpt-5.4-mini, reasoning_effort=high)로.
  - 구현 위치는 열린 결정: `indexing_eval.py`에 "누적 모드" 플래그로 얹을지, 별도 실행 스크립트(예: `index_episode.py`)로 뺄지 — 변형 비교(매번 리셋)와 목적이 상반되므로 **별도 스크립트가 더 깔끔**.

---

## 검증

1. **목표 2**: `cd poc && uv run python src/schema_suggest.py` → 콘솔 리포트에서 "추가 후보" 목록 확인, `output/schema_suggested.json` 생성 확인. 사람이 후보를 검토해 schema.py 반영 여부 결정.
2. **배경지식 생성**: `cd poc && LOREKEEPER_INPUT=data/input_ch1_2.txt uv run python src/build_context.py` → `poc/data/context_input_ch1_2.md` 생성, 내용 육안 검수.
3. **목표 1·3 통합**: 배경지식 없이/있이 A-B 비교 —
   - 기존: `LOREKEEPER_INPUT=data/input_ch1_2.txt uv run python src/indexing_eval.py v_chapter_high`
   - 개선: `LOREKEEPER_INPUT=data/input_ch1_2.txt LOREKEEPER_CONTEXT=data/context_input_ch1_2.md uv run python src/indexing_eval.py v_chapter_high`
   - `output/report.md`와 `output/v_chapter_high.cypher` 덤프를 비교해 노드/관계 포착률·품질 변화 확인.
   - 프롬프트에 novel_context가 실제 주입됐는지: 첫 실행 시 `extract_for_chunk`에서 완성된 prompt를 임시로 로깅하거나, 커스텀 템플릿 렌더 결과를 단발 print로 확인.

## 미결/주의

- `NovelContextExtractor.extract_for_chunk`는 부모 메서드 본문 복제가 불가피(라이브러리가 format 인자를 하드코딩). 라이브러리 업그레이드 시 이 메서드가 바뀌면 동기화 필요 — 파일 상단 주석에 근거 버전(1.18.0) 명시.
- 배경지식을 매 청크에 prefix하면 청크당 입력 토큰이 늘지만, 회차 청킹(청크 수 적음) + `prompt_cache_key`(`pipeline.py:38`)로 캐시 히트가 기대돼 비용 영향은 제한적. 실측으로 확인.
