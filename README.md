# LoreKeeper — 웹소설 지식그래프 인덱싱 PoC

한국어 웹소설 원고를 회차 단위로 읽어 **Neo4j 지식그래프(KG)**로 구축하는 인덱싱 파이프라인 PoC.
최종 목표는 "새 회차가 기존 세계관 설정과 충돌하는지 탐지"하는 것이며, 이 저장소는 그 **인덱싱 계층**(원고 → KG)을 설계·검증한다.

핵심 설계 질문은 네 가지다.

1. **시간에 따라 변하는 사실**(부상·생사·소속·능력·소지품)을 어떻게 시점별로 추적하고 모순을 판정할 수 있게 저장하나?
2. **같은 인물/장소가 회차마다 다른 호칭**으로 불려도 하나의 노드로 유지하려면?
3. 회차를 **순차·증분**으로 인덱싱하며 이전 회차 맥락을 어떻게 이어 주나?
4. 각 사실의 **근거가 몇 화 어느 대목인지** 그래프로 역추적하고, 벡터 검색(RAG)을 함께 쓰려면?

---

## 아키텍처 개요

한 회차를 넣으면 **두 개의 병렬 산출**을 만든다. 추출용과 근거·벡터용은 목적이 달라 **별개로 분할**한다.

```
indexing(chapter, text)              ← 진입점 함수 (poc/src/indexing.py)
   │
   ├─ 추출(KG):  WholeTextSplitter (회차 통째 1청크, "[chapter:N]\n[C0]…[C1]…" 마커)
   │              → extractor(커스텀 한국어 프롬프트 + novel_context 주입)
   │              → GraphPruning(스키마 밖 속성 제거) → Neo4jWriter → resolver(표기 변형 병합)
   │              ⇒ Character / Location / Event / CharacterState / Organization / Item
   │
   └─ 근거·벡터(Chunk):  KSSSentenceSplitter(~100자, overlap 0) → TextChunkEmbedder → LexicalGraphBuilder → Neo4jWriter
                          ⇒ Chunk(임베딩) + Chapter, 관계 NEXT_CHUNK / IN_CHAPTER, 벡터 인덱스 chunk_emb
   │
   후처리: LLM이 각 사실에 반환한 근거 조각 번호(evidence_chunk="C3")를
           (Event|CharacterState)-[:EVIDENCED_BY]->(Chunk) 로 연결
```

- **회차 통째를 추출 청크로** 넣어 회차 내 coreference(대명사·별칭 해소)를 한 컨텍스트에서 처리한다. 라이브러리 자동 lexical graph(회차 통째 Chunk)는 끄고(`create_lexical_graph=False`), Chunk 노드는 아래 KSS 근거 레이어가 전담한다.
- **인프라**: Neo4j 5.26 Community(APOC + 네이티브 벡터 인덱스) — `docker-compose.yml`. Bolt `7687`, Browser `7474`.
- **런타임**: Python 3.11 + [uv](https://github.com/astral-sh/uv), 라이브러리 [`neo4j-graphrag`](https://github.com/neo4j/neo4j-graphrag-python) 1.18.0.
- **LLM**: 추출·요약에 OpenAI `gpt-5.6-luna`(기본, reasoning=high), 임베딩 `text-embedding-3-small`(1536차원).

`SimpleKGPipeline` 대신 `Pipeline`을 직접 조립하는 이유: (1) extractor에 few-shot `examples`를 넘길 경로가 필요하고, (2) resolver를 커스텀으로 교체해야 하기 때문. — `poc/src/pipeline.py`

---

## KG 스키마 설계 (`poc/src/schema.py`)

### 도메인 노드 (LLM 추출)

| 노드 | 역할 | 핵심 속성 |
|---|---|---|
| `Character` | 인물의 **신원**(고정 식별자) | `name`, `description` |
| `Location` | 장소. `LOCATED_IN`으로 공간 계층 표현 | `name`, `description` |
| `Event` | 사건. **시간축의 기준점** | `title`, `description`, `chapter`, `story_order` |
| `CharacterState` | 인물의 **시점별 상태 사실**(변화·검증 대상) | `attribute`, `value`, `evidence` |
| `Organization` | 조직·세력·문파·회사(다대다 소속 공유) | `name`, `description` |
| `Item` | 소지품·작품·선물 등 **대상화되는 사물**(정체성) | `name`, `description` |

관계: `APPEARS_IN`(인물→사건), `HOSTS`(장소→사건), `HAS_STATE`(인물→상태), `ESTABLISHED_IN`(상태→성립 사건), `LOCATED_IN`(장소→상위 장소), `RELATED_TO`(인물→인물, `type`), `INVOLVED_WITH`(인물→사물, `role`=저자/독자/제작자), `ABOUT`(상태→대상 — 소유는 `Item`, 소속은 `Organization`).

### 근거·벡터 레이어 (추출기 밖에서 생성)

| 노드/관계 | 역할 |
|---|---|
| `Chunk` | KSS로 쪼갠 원문 조각. `chapter`, `index`, `text`, `embedding`(벡터) |
| `Chapter` | 회차 노드(메타데이터 앵커). `number`, `summary` |
| `(Event\|CharacterState)-[:EVIDENCED_BY]->(Chunk)` | 사실의 **근거 문장** 링크(문장 단위 역추적) |
| `(Chunk)-[:IN_CHAPTER]->(Chapter)` | 조각의 회차 소속 |
| `(Chunk)-[:NEXT_CHUNK]->(Chunk)` | 조각 순서 |

- `Chunk`/`Chapter`는 LLM이 만들지 않는다. `NODE_TYPES`/`RELATIONSHIP_TYPES`에도 넣지 않는다(넣으면 LLM이 직접 생성하려 든다).
- `Event`/`CharacterState`에는 추출 시점 임시 속성 `evidence_chunk`(예: `"C3"`, `"C3,C4"`)가 실리고, 후처리가 이를 `EVIDENCED_BY`로 바꾼 뒤 제거한다.

### 설계 철학 (스키마의 핵심)

- **시간축 = `Event.chapter` + `Event.story_order`** — `chapter`는 연재 회차(충돌 탐지 기준), `story_order`는 작중 연대기 순서. `story_order`는 `FLOAT`이라 **두 값 사이에 항상 삽입 가능**(fractional indexing).
- **상태 변화 = append-only** — 상태가 바뀌면 기존 노드를 고치지 않고 **항상 새 `CharacterState`를 추가**한다. "현재 유효한 값"은 `ESTABLISHED_IN → Event.chapter`가 **조회 시점 이하 중 가장 큰 것**으로 계산 → 과거를 소급 수정하지 않고 시점별 조회·모순 탐지가 가능.
- **소유·소속은 reified 상태 + `ABOUT`** — 능력·무공은 인물 종속 상태(`attribute`)로 두되, 이름 있는 소지품·작품은 `Item` 노드로 만들고 소유를 `CharacterState(attribute='소유', value='보유'/'상실')`로 표현해 `ABOUT`으로 그 `Item`에 잇는다. 소속도 `CharacterState(attribute='소속')`+`ABOUT`→`Organization`으로 표현한다(대상을 문자열이 아니라 그래프 노드로 식별). 소지품 이동은 "넘긴 인물 `상실` + 받은 인물 `보유`" 두 상태로. **`MEMBER_OF`는 제거**하고 소속을 시점 추적 가능한 상태로 통일했다.
- **서사적 비중 필터** — 지나가는 엑스트라 인물, 농담·소품으로 스치는 사물·조직, 일시적 통증 같은 소모성 상태는 만들지 않는다. 직업유형·고용형태(`회사원`·`계약직`)는 소속이 아니라 `attribute='신분'`으로 분리한다.
- **장소 계층 = `LOCATED_IN` 한 단계씩** — 그래프 순회로 전체 계층을 복원. 댓글창·게시판 같은 온라인·가상 공간은 `Location`으로 만들지 않는다(실제 물리 공간만).
- **근거는 Chunk로 승격** — `CharacterState.evidence`(자유 인용 문자열)만으로는 그래프 역추적이 안 되므로, `EVIDENCED_BY → Chunk`로 "몇 화 어느 조각이 근거냐"를 연결한다. 같은 조각을 여러 사실이 공유할 수 있다.
- **회차는 1급 노드(`Chapter`)** — 회차 요약(`Chapter.summary`)을 노드에 담아 그래프가 자기완결적이 된다(외부 파일 불필요).

---

## 인덱싱 파이프라인 구현

### 커스텀 프롬프트 · extractor (`poc/src/extractor.py`)

- **`KoreanWebNovelERTemplate`** — 라이브러리 기본 영어 프롬프트(`ERExtractionTemplate`)를 상속해 원본의 일반 추출 지시(역할·출력 JSON 구조·ID 재사용·관계 방향·JSON 유효성)를 **빠짐없이 한국어로 이식**하고, 웹소설 도메인 규칙을 얹었다. 회차 마커 `[chapter:N]` 해석, `CharacterState` 시간축, `attribute` 입도 통일, 그리고 **각 사실의 근거 조각 번호(`evidence_chunk`)를 `[C{i}]` 마커에서 읽어 채우는** 규칙을 담는다. 전용 `{novel_context}` placeholder 추가.
- **`NovelContextExtractor`** — `{novel_context}`(누적 배경 컨텍스트)를 청크 추출 시점에 주입한다. `create_lexical_graph=False`로 회차 통째 Chunk/FROM_CHUNK 자동 생성을 끈다.
- few-shot 예시: `poc/src/extraction_examples.py`(`[chapter:N]`·`[C{i}]`·`evidence_chunk` 시연 포함).

### 스플리터 (`poc/src/splitters.py`)

- **`WholeTextSplitter`** — 회차 원고 전체를 자르지 않고 1개 청크로 내보낸다(회차=단일 추출 청크). `[chapter:N]`·`[C{i}]` 마커가 박힌 원고를 통째로 넘겨 회차 내 coreference를 보존한다.
- **`KSSSentenceSplitter` / `KiwiSentenceSplitter`** — 한국어 문장 분리 기반 청킹(문장 중간이 잘리지 않음). 근거·벡터용 KSS 청킹에 `KSSSentenceSplitter(chunk_size=100, overlap=0)`을 쓴다 — 청크당 ~3문장으로 근거를 문장 단위로 정밀하게 짚고, 겹침을 0으로 둬 `[C{i}]` 마커 중복 모호성을 없앤다.

### 리졸버 (`poc/src/resolver.py`)

표기 변형(별칭·존칭)을 하나의 노드로 병합. 병합 시 `description`을 배열로 **combine**해 정보 유실을 막는다. `Chunk`/`Chapter`는 `__Entity__` 라벨이 없어 resolver가 건드리지 않는다.

- `CombiningFuzzyResolver`(기본, RapidFuzz WRatio) · `CombiningExactMatchResolver` · `OpenAIEmbeddingResolver`(교체용으로 보존)

### 벡터 RAG (Neo4j 네이티브)

- `Chunk.embedding`에 벡터 인덱스 `chunk_emb`(cosine, HNSW)를 건다 → 별도 벡터 DB 없이 Neo4j 하나로 검색.
- 검색 계층은 `VectorCypherRetriever`로 **벡터 검색 → 그래프 확장**을 단일 쿼리로 수행한다(설계 목표, 이 저장소 범위 밖):
  ```
  벡터로 Chunk를 찾고 → (Chunk)<-[:EVIDENCED_BY]-(Event) 앵커 → 1-hop으로 Character/CharacterState 확장
  ```

---

## 모듈 구성

인덱싱은 관심사별로 나뉜다. `indexing.py`가 이들을 순서대로 엮는 오케스트레이터다.

| 모듈 | 담당 |
|---|---|
| `indexing.py` | 진입점 함수 `indexing(chapter, text)` — 전체 흐름 오케스트레이션 + 결과 집계 + CLI |
| `context.py` | 배경 컨텍스트(`novel_context`) 조립 — 그래프 덤프 + 회차 요약 로드/생성 |
| `chunks.py` | Chunk/Chapter provenance 레이어 생성(임베딩·NEXT_CHUNK·IN_CHAPTER·벡터 인덱스) |
| `evidence.py` | `EVIDENCED_BY` 후처리(evidence_chunk 번호 → Chunk 연결) |
| `pipeline.py` | 추출 DAG 조립(`build_pipeline`) + LLM/embedder 생성 + 토큰 계측(`TokenCountingLLM`) |
| `extractor.py` / `extraction_examples.py` | 커스텀 프롬프트 + few-shot |
| `splitters.py` / `resolver.py` / `schema.py` / `client.py` | 스플리터 / 리졸버 / 스키마 / 드라이버 |

---

## 회차 누적 인덱싱

한 번 실행 = **한 회차** 인덱싱. DB를 리셋하지 않고(`clean_db=False`) 이전 회차 위에 누적하며, 각 실행은 **이전 회차까지의 결과를 배경 컨텍스트로 주입**한다.

**증분 컨텍스트(`novel_context`)** = 두 소스의 결합:
1. **그래프 덤프** — 현재 DB의 도메인 노드/관계를 텍스트로 직렬화(엔티티 식별·별칭 정합용)
2. **회차 요약** — 이전 회차까지 누적된 `Chapter.summary`(서사 흐름 보강). 요약은 매 회차 `gpt-5.6-luna`(reasoning=high)로 생성해 `Chapter.summary`에 저장.

**anchoring 방지**: 프롬프트가 "배경 컨텍스트와 새 회차 원문이 충돌하면 **새 회차를 진실의 원천으로 우선**"하도록 명시한다 — 충돌 탐지 대상인 모순이 기존 그래프에 맞춰 왜곡되지 않게.

```bash
cd poc
LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py   # 1화
LOREKEEPER_CHAPTER=2 LOREKEEPER_INPUT=data/input_ch2.txt uv run python src/indexing.py   # 2화 (1화 컨텍스트 주입)
```

프로그램적으로는 `await indexing(chapter, text, driver)`로 직접 호출한다(드라이버를 넘기면 여러 회차에 재사용).

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
LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py
```

Neo4j Browser(`http://localhost:7474`, `neo4j`/`lorekeeper`)에서 그래프를 시각적으로 검토할 수 있다.

### 주요 환경변수

| 변수 | 의미 | 기본값 |
|---|---|---|
| `LOREKEEPER_CHAPTER` | 인덱싱할 회차 번호(필수) | (없음) |
| `LOREKEEPER_INPUT` | 인덱싱할 원고 경로 | `data/input.txt` |
| `LOREKEEPER_MODEL` | 추출/요약 LLM 모델 | `gpt-5.6-luna` |
| `LOREKEEPER_REASONING` | 추출 reasoning effort(`low`/`medium`/`high`/`xhigh`) | `high` |
| `NEO4J_DATABASE` | Neo4j DB 이름 | `neo4j` |

### 검증 쿼리(예시)

```cypher
SHOW VECTOR INDEXES;                                              -- chunk_emb 존재
MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c);        -- 0 (전부 임베딩됨)
MATCH (:Event)-[:EVIDENCED_BY]->(:Chunk) RETURN count(*);         -- 근거 링크
MATCH (e:Event)-[:EVIDENCED_BY]->(:Chunk)-[:IN_CHAPTER]->(c:Chapter)
  RETURN e.title, c.number LIMIT 5;                               -- Event→회차 2-hop 도달
```

---

## 디렉토리 구조

```
lorekeeper-poc/
├─ docker-compose.yml        # Neo4j 5.26 Community + APOC + 벡터 인덱스
├─ poc/
│  ├─ src/
│  │  ├─ indexing.py         # 진입점 indexing(chapter, text) — 오케스트레이터
│  │  ├─ context.py          # 배경 컨텍스트(그래프 덤프 + 회차 요약)
│  │  ├─ chunks.py           # Chunk/Chapter provenance 레이어 + 벡터 인덱스
│  │  ├─ evidence.py         # EVIDENCED_BY 후처리
│  │  ├─ pipeline.py         # build_pipeline(DAG 조립) + LLM/embedder + 토큰 계측
│  │  ├─ extractor.py        # 커스텀 한국어 프롬프트 + 컨텍스트 주입 extractor
│  │  ├─ extraction_examples.py  # 추출 few-shot 예시
│  │  ├─ splitters.py        # WholeTextSplitter / KSS·Kiwi 문장 스플리터
│  │  ├─ resolver.py         # 표기 변형 병합 resolver 3종
│  │  └─ schema.py           # KG 스키마 정의(도메인 노드/관계 + 근거 레이어 문서화)
│  ├─ data/                  # 원고(input_ch1~6.txt)
│  └─ output/                # (런타임 산출물)
└─ .claude/                  # 설계 계획·분석 문서(plan/, docs/)
```

---

## 설계 문서

더 상세한 설계 근거는 `.claude/` 아래에 있다.

- `.claude/plan/sourcespan-vector-rag-plan.md` — 근거 추적(Chunk/EVIDENCED_BY) + Neo4j 네이티브 벡터 RAG + 진입점 함수화 계획(구현 완료)
- `.claude/plan/schema-item-completeness-plan.md` — `Item` 노드 + reified 소유/소속(`ABOUT`)·저작/독자(`INVOLVED_WITH`) + 비중 필터·구조 완전성 계획(구현 완료)
- `.claude/docs/indexing-experiment-record.md` — splitter/resolver/reasoning OFAT 비교 실험 기록과 best-fit 결론
- `.claude/plan/korean-novel-extraction-plan.md` — 프롬프트·증분 컨텍스트 통합 계획
- `.claude/docs/schema-augmentation-candidates.md` — 형제 프로젝트 스키마 차용 검토
