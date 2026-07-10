# LoreKeeper — 웹소설 지식그래프 인덱싱 PoC

한국어 웹소설 원고를 회차 단위로 읽어 **Neo4j 지식그래프(KG)**로 구축하는 인덱싱 파이프라인 PoC.
최종 목표는 "새 회차가 기존 세계관 설정과 충돌하는지 탐지"하는 것이며, 이 저장소는 그 **인덱싱 계층**(원고 → KG)을 설계·검증한다.

핵심 설계 질문은 세 가지였다.

1. **시간에 따라 변하는 사실**(부상·생사·소속·능력·소지품)을 어떻게 시점별로 추적하고 모순을 판정할 수 있게 저장하나?
2. **같은 인물/장소가 회차마다 다른 호칭**으로 불려도 하나의 노드로 유지하려면?
3. 회차를 **순차·증분**으로 인덱싱하며 이전 회차 맥락을 어떻게 이어 주나?

---

## 아키텍처 개요

```
원고(.txt, [N화] 마커)
   │
   ▼  ChapterTaggingSplitter → embedder → SchemaBuilder
   │                                │
   │        NovelContextExtractor(커스텀 한국어 프롬프트 + 배경 컨텍스트 주입)
   │                                │
   │             GraphPruning(스키마 밖 속성 제거) → Neo4jWriter
   │                                │
   ▼                          EntityResolver(표기 변형 병합)
Neo4j KG  (Character / Location / Event / CharacterState / Organization)
```

- **인프라**: Neo4j 5.26 Community(APOC 포함) — `docker-compose.yml`. Bolt `7687`, Browser `7474`.
- **런타임**: Python 3.11 + [uv](https://github.com/astral-sh/uv), 라이브러리 [`neo4j-graphrag`](https://github.com/neo4j/neo4j-graphrag-python) 1.18.0.
- **LLM**: 추출·요약에 OpenAI `gpt-5.4-mini`(기본), 임베딩 `text-embedding-3-small`.

`SimpleKGPipeline` 대신 `Pipeline`을 직접 조립하는 이유: (1) extractor에 few-shot `examples`를 넘길 경로가 필요하고, (2) resolver를 커스텀으로 교체해야 하기 때문. — `poc/src/pipeline.py`

---

## KG 스키마 설계 (`poc/src/schema.py`)

### 노드 타입

| 노드 | 역할 | 핵심 속성 |
|---|---|---|
| `Character` | 인물의 **신원**(고정 식별자) | `name`, `description` |
| `Location` | 장소. `LOCATED_IN`으로 공간 계층 표현 | `name`, `description` |
| `Event` | 사건. **시간축의 기준점** | `title`, `description`, `chapter`, `story_order` |
| `CharacterState` | 인물의 **시점별 상태 사실**(변화·검증 대상) | `attribute`, `value`, `evidence` |
| `Organization` | 조직·세력·문파(다대다 소속 공유) | `name`, `description` |

### 관계 타입

`APPEARS_IN`(인물→사건), `HOSTS`(장소→사건), `HAS_STATE`(인물→상태), `ESTABLISHED_IN`(상태→성립 사건), `LOCATED_IN`(장소→상위 장소), `MEMBER_OF`(인물→조직), `RELATED_TO`(인물→인물, `type` 속성으로 관계 종류).

### 설계 철학 (이 스키마의 핵심)

- **시간축 = `Event.chapter` + `Event.story_order`**
  `chapter`는 연재 회차(충돌 탐지 기준), `story_order`는 작중 연대기 순서. `story_order`는 `FLOAT`으로 두어 **두 값 사이에 항상 삽입 가능**(fractional indexing) — 나중에 "3화와 4화 사이" 시점이 밝혀져도 3.5로 끼워 넣는다.

- **상태 변화 = append-only + 순서 무효화**
  상태가 바뀌면 기존 노드를 고치지 않고 **항상 새 `CharacterState`를 추가**한다. "현재 유효한 값"은 `ESTABLISHED_IN → Event.chapter`가 **조회 시점 이하 중 가장 큰 것**으로 계산한다. 덕분에 종료 시점(`until`)을 몰라도, 과거를 소급 수정하지 않아도 시점별 조회·모순 탐지가 가능하다.

- **능력·소지품도 `CharacterState`로** — 스킬/무공/소지품은 별도 노드가 아니라 인물 종속 상태(`attribute`)로 표현한다. 소지품 이동은 "넘긴 인물 `상실` + 받은 인물 `보유`" 두 상태로.

- **장소 계층 = `LOCATED_IN` 한 단계씩** — 요새→도시→왕국처럼 한 단계 위만 가리켜, 그래프 순회로 전체 계층을 복원한다.

- **`attribute`/`value`는 한국어 자유값** — 회차 간 비교용 식별자이므로 같은 축은 같은 표현·같은 입도로 통일한다(예: 생사 여부는 항상 `생사`). enum으로 못 박지 않고 운영 데이터가 쌓이면 사후 정규화한다.

---

## 인덱싱 파이프라인 구현

### 커스텀 프롬프트 · extractor (`poc/src/extractor.py`)

- **`KoreanWebNovelERTemplate`** — 라이브러리 기본 영어 프롬프트(`ERExtractionTemplate`)를 상속해, 원본의 일반 추출 지시(역할·출력 JSON 구조·노드 unique ID 재사용·관계 방향 준수·JSON 유효성 규칙)를 **빠짐없이 한국어로 이식**한 뒤, 웹소설 도메인 규칙(`[N화]` 해석, `CharacterState` 시간축, `attribute` 입도 통일 등)을 얹었다. 전용 `{novel_context}` placeholder 하나를 추가한다.
- **`NovelContextExtractor`** — `{novel_context}`(누적 배경 컨텍스트)를 청크 추출 시점에 주입하는 extractor. 라이브러리는 `text/schema/examples`만 프롬프트에 넣으므로, `extract_for_chunk`를 오버라이드해 네 번째 값을 채운다. (neo4j-graphrag 1.18.0 기준 복제 — 업그레이드 시 동기화 필요)
- few-shot 예시: `poc/src/extraction_examples.py`.

### 스플리터 (`poc/src/splitters.py`)

- **`ChapterTaggingSplitter`** — 원문을 `[N화]` 마커로 **먼저** 분할한 뒤 내부 splitter로 자르고, 각 청크에 회차 마커를 prefix한다. 이 선분할 덕에 **청크 크기와 무관하게 회차 경계가 유지**된다(회차 통째를 한 청크로 넣어도 다른 회차와 안 섞임).
- 후보 내부 splitter: `FixedSizeSplitter`(라이브러리), `KiwiSentenceSplitter`/`KSSSentenceSplitter`(한국어 문장 분리), `make_recursive_splitter`(LangChain 어댑터).

### 리졸버 (`poc/src/resolver.py`)

표기 변형(별칭·존칭)을 하나의 노드로 병합. 3종 모두 병합 시 `description`을 배열로 **combine**해 정보 유실을 막는다.

- `CombiningExactMatchResolver` — 같은 라벨 + 같은 `name` 정확 일치 병합
- `CombiningFuzzyResolver` — RapidFuzz WRatio 문자열 유사도 병합
- `OpenAIEmbeddingResolver` — OpenAI 임베딩 코사인 유사도 병합

### 토큰 계측 (`poc/src/pipeline.py`)

`TokenCountingLLM`이 `ainvoke`마다 usage를 누적해 변형·회차별 토큰 사용량을 집계한다.

---

## 두 가지 실행 모드

### 1. 회차 누적 인덱싱 — `poc/src/index_episode.py` (실제 운영에 가까운 경로)

한 번 실행 = **한 회차** 인덱싱. DB를 리셋하지 않고(`clean_db=False`) 이전 회차 위에 누적하며, 각 실행은 **이전 회차까지의 결과를 배경 컨텍스트로 주입**한다.

**증분 컨텍스트(`novel_context`)** = 두 소스의 결합:
1. **그래프 덤프** — 현재 DB의 도메인 노드/관계를 텍스트로 직렬화(엔티티 식별·별칭 정합용)
2. **rolling summary** — 회차마다 3~5문장 서사 요약을 `output/rolling_summary.md`에 누적(서사 흐름 보강)

**anchoring 방지**: 프롬프트가 "배경 컨텍스트와 새 회차 원문이 충돌하면 **새 회차를 진실의 원천으로 우선**"하도록 명시한다 — 충돌 탐지 대상인 모순이 기존 그래프에 맞춰 왜곡되지 않게.

```bash
cd poc
LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/index_episode.py   # 1화
LOREKEEPER_INPUT=data/input_ch2.txt uv run python src/index_episode.py   # 2화 (1화 컨텍스트 주입)
```

### 2. 변형 비교 harness — `poc/src/indexing_eval.py`

동일 입력에 splitter/resolver/reasoning 후보를 하나씩 바꿔가며(OFAT) 돌리고 구조 지표를 자동 집계, `output/report.md`로 비교표를 쓴다. 각 변형 그래프는 `output/<variant>.cypher`로 덤프해 육안 비교한다.

```bash
cd poc && uv run python src/indexing_eval.py               # 전체 변형
cd poc && uv run python src/indexing_eval.py v_chapter     # 특정 변형만
```

---

## 설치 & 실행

```bash
# 1. Neo4j 기동 (레포 루트)
docker compose up -d

# 2. 환경변수 (.env) — 레포 루트에 작성
#    OPENAI_API_KEY=sk-...
#    NEO4J_URI=bolt://localhost:7687
#    NEO4J_USER=neo4j
#    NEO4J_PASSWORD=lorekeeper       # docker-compose의 NEO4J_AUTH와 일치

# 3. 의존성 설치 & 실행 (poc)
cd poc && uv sync
uv run python src/index_episode.py            # 회차 누적 인덱싱
```

Neo4j Browser(`http://localhost:7474`, `neo4j`/`lorekeeper`)에서 그래프를 시각적으로 검토할 수 있다.

### 주요 환경변수

| 변수 | 의미 | 기본값 |
|---|---|---|
| `LOREKEEPER_INPUT` | 인덱싱할 원고 경로 | `data/input.txt` |
| `LOREKEEPER_MODEL` | 추출/요약 LLM 모델(A/B 테스트용) | `gpt-5.4-mini` |
| `LOREKEEPER_REASONING` | 추출 reasoning effort(미지정 시 미전달=`none`) | (없음) |
| `NEO4J_DATABASE` | Neo4j DB 이름 | `neo4j` |

---

## 디렉토리 구조

```
lorekeeper-poc/
├─ docker-compose.yml        # Neo4j 5.26 Community + APOC
├─ poc/
│  ├─ src/
│  │  ├─ schema.py           # KG 스키마 정의(노드/관계/패턴)
│  │  ├─ pipeline.py         # build_pipeline(DAG 조립) + build_llm
│  │  ├─ extractor.py        # 커스텀 한국어 프롬프트 + 컨텍스트 주입 extractor
│  │  ├─ extraction_examples.py  # 추출 few-shot 예시
│  │  ├─ splitters.py        # ChapterTaggingSplitter 등 스플리터
│  │  ├─ resolver.py         # 표기 변형 병합 resolver 3종
│  │  ├─ client.py           # Neo4j 드라이버 + .env 로드
│  │  ├─ index_episode.py    # 회차 누적 인덱싱(증분 컨텍스트)
│  │  └─ indexing_eval.py    # OFAT 변형 비교 harness
│  ├─ data/                  # 원고([N화] 마커 포함)
│  └─ output/                # 그래프 덤프·rolling summary·비교 리포트·백업
└─ .claude/                  # 설계 계획·분석 문서(plan/, docs/)
```

---

## 설계 문서

더 상세한 설계 근거는 `.claude/` 아래에 있다.

- `.claude/plan/korean-novel-extraction-plan.md` — 프롬프트 최적화·스키마 자동추출·증분 컨텍스트 통합 계획
- `.claude/plan/sourcespan-provenance-plan.md` — 근거 추적(SourceSpan) 도입 방향(미착수)
- `.claude/docs/schema-augmentation-candidates.md` — 형제 프로젝트 스키마 차용 검토
