# Chunk 근거 추적 + Neo4j-native 벡터 RAG + 진입점 함수화

> 용어: 아래에서 **"추출 청크"** = 회차 통째를 LLM에 넣는 단위(비영속, lexical graph 껐으므로 노드로 저장 안 됨). **`Chunk` 노드** = KSS로 잘게 쪼개 임베딩·근거·벡터RAG에 쓰는 영속 노드. 둘 다 "청크"지만 목적·크기·영속성이 다르다.

## Context

현재 KG는 **회차 하나를 통째로 추출 청크(chunk_size=12000)**로 넣어 추출한다. 그런데:
- 실행 구조가 테스트용 env-var 기반 script `main()`이라 임의적 — 재사용 가능한 **호출식 진입점 함수**로 묶고자 한다.
- 회차 1개 입력을 전제하므로 `[N화]` 마커 파싱과 `FixedSizeSplitter`/`ChapterTaggingSplitter` 청킹은 KG 경로에 불필요하다. 함수 입력을 `(chapter, text)`로 직접 받는다.
- 근거 추적 해상도가 "회차 전체"로 거칠다. 라이브러리 자동 `(Event)-[:FROM_CHUNK]->(Chunk)`는 있으나 그 Chunk가 회차 통째라 "몇 화 어느 대목이 근거냐"를 못 짚는다. `CharacterState.evidence`(schema.py:182-190)는 자유 인용 **문자열**이라 그래프 역추적이 안 된다. 벡터 RAG 단위도 회차 통째라 무의미하다.

목표: (1) 인덱싱을 진입점 함수로 컴포넌트화, (2) 원고를 **KSS로 잘게 쪼갠 `Chunk` 노드**(임베딩 포함)로 만들고 Event/CharacterState가 **어느 조각이 근거인지** 정확히 가리키게 해 충돌 탐지의 정밀 근거 역추적 + Neo4j-native 벡터 RAG를 확보.

## 확정된 설계 결정

- **진입점 함수화**: 인덱싱 로직을 `indexing(chapter: int, text: str, driver=None, ...) -> dict`로 묶어 호출식으로 쓴다. 현 env-var 기반 `main()`은 이 함수를 부르는 **얇은 CLI 래퍼**로 축소.
- **회차 단위 단일 추출 청크(KG 추출)**: 회차 1개 = 추출 청크 1개 전제. `[N화]` 파싱·`FixedSizeSplitter`·`ChapterTaggingSplitter` 제거, 원고 전체를 그대로 1개 `TextChunk`로 내보내는 **최소 단일-청크 splitter**로 교체. `chapter`는 인자로 받고, extractor가 `Event.chapter`를 채우도록 텍스트 맨 앞에 인자 기반 **명시적 마커 `[chapter:{chapter}]`** 를 합성해 붙인다(파싱 아님, 본문의 "화" 표기와 충돌 없는 machine 마커). 이에 맞춰 프롬프트·few-shot의 회차 마커도 `[N화]`→`[chapter:N]`로 통일.
- **추출 경로 lexical graph 제거(확정)**: `Chunk` 노드는 KSS 근거 레이어가 전담하므로, 추출기가 자동 만드는 lexical graph(회차 통째 Chunk + FROM_CHUNK + 회차 임베딩)는 중복. extractor `create_lexical_graph=False`로 끄고 DAG에서 **embedder 제거**(splitter→extractor 직접 연결). 회차당 불필요 임베딩 1회·노드 제거. (FROM_CHUNK 근거는 EVIDENCED_BY→Chunk로 대체.)
- **저장소**: Neo4j-native. 임베딩을 `Chunk.embedding` 벡터 property로 저장 + vector index. (Qdrant 대비 단일 저장소·동기화 불필요·`VectorCypherRetriever` 단일 쿼리. PoC 규모=수만 벡터라 HNSW로 충분. 규모 커지면 `QdrantNeo4jRetriever` 전환 가능 → lock-in 아님.)
- **청킹(B1)**: 추출 청크(회차 통째)와 근거 `Chunk`(KSS, 작게)는 별개 분할. 추출 텍스트에 `[C1] … [C2] …` 청크 마커 삽입 → LLM이 각 사실에 근거 청크 번호(`evidence_chunk`) 반환 → 후처리로 `EVIDENCED_BY` 연결.
- **근거 범위**: `Event` + `CharacterState` 둘 다 `EVIDENCED_BY → Chunk`. Event는 검색 앵커, CharacterState는 충돌 탐지용 정밀 근거.
- **Chapter 노드 승격(확정)**: `Chunk` 레이어의 `document_info`를 활용해 **회차를 1급 노드**로 만든다(`document_node_label="Chapter"`, `chunk_to_document_relationship_type="IN_CHAPTER"`). `(Chunk)-[:IN_CHAPTER]->(Chapter{number})`. Chapter는 회차 메타데이터 앵커 — 특히 **rolling summary를 외부 `.md`에서 `Chapter.summary` 노드 property로 흡수**(그래프 자기완결). **Event→Chapter 직접 관계는 두지 않는다**(중복): Event는 `EVIDENCED_BY→Chunk→IN_CHAPTER→Chapter` 2-hop으로 회차에 도달하고(evidence_chunk은 항상 현 회차 조각이라 단일 Chapter로 수렴), `Event.chapter` 정수 property로 `Chapter.number` 직접 join도 된다.

## 아키텍처

```
indexing(chapter, text)   ← 진입점 함수(컴포넌트)
   │
   ├─ 추출용:  단일-청크 splitter → 추출청크 1개(= "[chapter:{chapter}]\n[C1]…[C2]…")  ← LLM 추출·coreference
   │            (embedder / lexical graph 없음 → 추출청크는 노드로 저장 안 됨)
   │                       │
   │        extractor → pruner → writer → resolver
   │                       │  LLM이 각 Event/CharacterState.evidence_chunk="C3"(또는 "C3,C4") 반환
   │
   └─ 근거용:  KSSSentenceSplitter(~800자) → Chunk N개(임베딩) -[:IN_CHAPTER]-> Chapter{number,summary}
                       │  후처리(resolver 뒤, chapter 스코프)
       (Event|CharacterState) -[:EVIDENCED_BY]-> Chunk{chapter, index}
       (Event→회차 도달: EVIDENCED_BY→Chunk→IN_CHAPTER→Chapter, 직접 관계 없음)
```

핵심: **`Chunk`/`EVIDENCED_BY`는 LLM이 만들지 않는다.** 진입점 함수가 KSS 분할·임베딩·노드생성·링크를 담당하고, LLM은 `evidence_chunk` 번호만 반환한다.

## 파일별 변경

### `poc/src/indexing.py` — 진입점 함수로 컴포넌트화 (가장 큰 변경)
- 신규 진입점 `indexing(chapter: int, text: str, driver=None, *, database=DATABASE, reasoning=None, kss_chunk_size=800) -> dict`. 내부 순서:
  1. **배경 컨텍스트**: `dump_graph_text`(도메인 노드; Chapter/Chunk 제외) + **Chapter.summary 조회**(`number` 순 정렬해 줄거리 이어붙임) → `novel_context`. (기존 `_load_rolling_summary`의 `.md` 읽기를 그래프 조회로 대체.)
  2. **KSS 분할**: `KSSSentenceSplitter(chunk_size≈800)`(splitters.py)로 `text`를 `TextChunks`로. 각 청크에 **결정적 uid `f"chunk-{chapter}-{i}"`**, `index=i`, `metadata={"chapter":chapter}` 부여(idempotent upsert·chapter property·마커 정렬용). **마커 번호 = `Chunk.index`** 로 일치시킨다.
  3. **Chunk 생성·임베딩 — 라이브러리 컴포넌트 재사용**: 손Cypher 대신 `build_embedder()`(=`TextChunkEmbedder(OpenAIEmbeddings)`)`.run()` → `LexicalGraphBuilder().run(embedded)`(기본 config = 라벨 `Chunk`, property `index`/`text`/`embedding`, 순차 `NEXT_CHUNK`) → `Neo4jWriter(driver,db,clean_db=False).run(graph)`. 결과: `Chunk{index, text, chapter}`+`embedding` 벡터 property + 인접 `NEXT_CHUNK`. 결정적 uid(`chunk-{chapter}-{i}`)라 재실행 시 upsert.
     - **resolver-safe 근거**: 기본 `LexicalGraphConfig`의 lexical 라벨에 `Chunk`가 포함돼 Neo4jWriter가 `__Entity__`를 안 붙인다(kg_writer.py:229) → resolver(`MATCH (:__Entity__)`)가 Chunk를 안 건드림. (실행 검증: `__Entity__` 수 = 도메인 노드 수, Chunk 제외 확인.)
  3b. **Chapter 노드 + IN_CHAPTER — 후처리 Cypher**: `document_info`(metadata가 `Dict[str,str]`이라 number가 문자열이 됨)를 쓰지 않고 `MERGE (c:Chapter {number:$chapter})`(int) + `MATCH (ck:Chunk {chapter:$chapter}) MATCH (c:Chapter {number:$chapter}) MERGE (ck)-[:IN_CHAPTER]->(c)`로 생성. 직접 Cypher라 `__Entity__` 미부여(resolver-safe), MERGE라 idempotent, number를 int로 정확히 넣어 `Event.chapter` join 성립.
  4. **vector index 보장(idempotent)**: `create_vector_index(driver,"chunk_emb",label="Chunk",embedding_property="embedding",dimensions=1536,similarity_fn="cosine",fail_if_exists=False)`(라이브러리 indexes.py:37).
  5. **추출 텍스트 조립**: `f"[chapter:{chapter}]\n" + " ".join(f"[C{i}] {clean_text}")` → `build_pipeline` data의 `splitter.text`로. (마커 `i` = 위 `Chunk.index`.)
  6. **pipe.run** (단일-청크 splitter, embedder/lexical graph 없음).
  7. **EVIDENCED_BY 후처리(Cypher, resolver 뒤)**: 이 chapter의 Event(`Event.chapter=cur`)·CharacterState(`(:CharacterState)-[:ESTABLISHED_IN]->(:Event {chapter:cur})`) 중 `evidence_chunk` 보유 노드마다 쉼표 분리 → 각 `C{n}` → `MATCH (ck:Chunk {chapter:cur, index:n})` → `MERGE (fact)-[:EVIDENCED_BY]->(ck)` → 링크 후 `REMOVE fact.evidence_chunk`. (Event→Chapter 직접 링크 없음 — Chunk 경유 도달.)
  8. **회차 요약 → Chapter.summary**: `_summarize_episode`(기존 재사용, :200-213)로 3~5문장 요약 생성 → `MATCH (c:Chapter {number:cur}) SET c.summary=$summary`. (`_append_summary`의 `.md` 파일 쓰기 제거 — 요약은 그래프에.)
  9. 결과 dict 반환(라벨/관계 카운트·토큰).
- 기존 `_episode_header`/`_CHAPTER_MARKER`(:62,189-197) 및 `[N화]` 파싱 로직 제거(chapter는 인자).
- `__main__`은 얇은 CLI 래퍼로 축소: `LOREKEEPER_CHAPTER`(또는 인자)로 chapter, `LOREKEEPER_INPUT`으로 파일 읽어 `indexing(chapter, text, get_driver())` 호출.

### `poc/src/pipeline.py` (`build_pipeline`, :90-148)
- splitter 인자를 **단일-청크 splitter**로 받게 하고, DAG에서 **embedder 컴포넌트 제거** → `pipe.connect("splitter","extractor",{"chunks":"splitter"})` 직결(embedder 경유 삭제, :139-140).
- extractor 생성 시 `create_lexical_graph=False` 추가(:122-128) → 추출 경로 Chunk/FROM_CHUNK 미생성.
- **단일-청크 splitter**: 원고 전체를 1개 `TextChunk(text=..., index=0)`로 내보내는 최소 splitter. splitters.py에 소형 클래스 추가(아래).

### `poc/src/splitters.py`
- 신규 `WholeTextSplitter(TextSplitter)` — `async def run(self, text) -> TextChunks(chunks=[TextChunk(text=text, index=0)])`. (기존 `ChapterTaggingSplitter`/`FixedSizeSplitter` 조합을 KG 경로에서 대체. `KSSSentenceSplitter`는 근거용으로 계속 재사용.)

### `poc/src/schema.py`
- `EVENT`, `CHARACTER_STATE`에 `evidence_chunk` PropertyType(STRING, optional) 추가 — "근거 문장이 속한 청크 마커 번호(C3), 여러 개면 쉼표(C3,C4)". `additional_properties=False`·구조화 출력 `extra="forbid"` 때문에 선언 필수.
- 근거 레이어(Chunk/Chapter/EVIDENCED_BY/IN_CHAPTER/NEXT_CHUNK)는 추출기 밖에서 생성되므로 **미사용 NodeType 객체를 만들지 않고 주석으로만 문서화**했다(dead code 회피). 이들은 `NODE_TYPES`/`RELATIONSHIP_TYPES`/`PATTERNS`(:331-336)에 넣지 않는다(LLM이 직접 생성하지 못하게). `evidence_chunk`은 Event/CharacterState의 실제 property라 추출 스키마에 자동 포함된다.

### `poc/src/extractor.py` (`KoreanWebNovelERTemplate`, :52-150)
- 회차 마커 지시를 `[N화]`→**`[chapter:N]`** 로 변경(텍스트 맨 앞 `[chapter:N]`에서 `Event.chapter`를 읽어 채운다).
- 도메인 규칙(:100 부근)에 추가: 원문에 `[C1][C2]…` 청크 마커가 있고 **각 Event/CharacterState는 근거 문장이 속한 청크 번호를 `evidence_chunk`에 반드시 채운다**(추측 금지, 실제 그 문장이 있는 청크만, 여러 문장이면 쉼표). `extract_for_chunk`(:193-254)는 구조 변경 불필요(마커가 입력 텍스트에 이미 있음).

### `poc/src/extraction_examples.py` (`EXTRACTION_FEW_SHOT`, :26-138)
- 예시 입력의 회차 마커를 `[N화]`→**`[chapter:N]`** 로 통일, `[C1][C2]…` 청크 마커 삽입, CharacterState/Event 예시에 `"evidence_chunk":"C2"` 시연 추가.

### 재사용 (신규 작성 금지)
- **Chunk+Chapter 레이어**: `TextChunkEmbedder`(embedder.py) + `LexicalGraphBuilder`+`LexicalGraphConfig`(lexical_graph.py, types.py:239) + `DocumentInfo` + `Neo4jWriter` — Chunk 노드·임베딩·NEXT_CHUNK·Chapter 노드·IN_CHAPTER를 손Cypher 없이 생성(벡터 property write까지 라이브러리가 처리). 청크 쪽 라벨/property는 **라이브러리 기본값 그대로** 활용.
- 임베딩 `OpenAIEmbeddings`(resolver.py 기존 import) / `EMBEDDING_MODEL`(pipeline.py:40), KSS `KSSSentenceSplitter`(splitters.py), 드라이버 `get_driver`(client.py), vector index `create_vector_index`(neo4j_graphrag/indexes.py:37), 컨텍스트 덤프·요약(indexing.py:96-220).
- **FROM_CHUNK은 재사용 불가**: "어느 추출 청크에서 뽑혔나"(=회차 통째)라 문장 단위 근거가 안 됨. 근거 provenance는 LLM `evidence_chunk` 기반 커스텀 `EVIDENCED_BY`로만. (추출 경로 lexical graph도 끄므로 FROM_CHUNK 미생성.)

## 코드 정리 (컴포넌트화 부수 작업)

컴포넌트화 리팩터가 만들어낸 orphan만 정리한다(내 변경이 만든 미사용 import/함수/파일). 기존 죽은 코드나 교체용 클래스는 건드리지 않는다.

- **제거 (이번 변경이 만든 orphan)**:
  - `indexing.py`: `_CHAPTER_MARKER`·`_episode_header`(`[N화]` 파싱 — chapter가 인자로 대체), `_load_rolling_summary`·`_append_summary`·`ROLLING_SUMMARY_PATH`(요약이 `Chapter.summary`로 이동), WholeTextSplitter/KSS로 교체되며 미사용이 된 import(`ChapterTaggingSplitter`, `FixedSizeSplitter`).
  - `pipeline.py`: 추출 DAG에서 embedder 배선 제거로 생기는 미사용 정리. 단 `build_embedder`(pipeline.py:85)는 Chunk 레이어에서 재사용하므로 **유지**.
  - `poc/output/rolling_summary.md`: 요약이 그래프(`Chapter.summary`)로 이동해 obsolete → 삭제.
  - `splitters.py`의 `ChapterTaggingSplitter`·`make_recursive_splitter`(+ `LangChainTextSplitterAdapter`/`re`/`_CHAPTER_MARKER`): 사용자 지시로 splitter는 KSS/Kiwi/Whole만 남기고 제거.
- **보존 (컴포넌트 교체(A/B) 대비 — 미사용이어도 남김)**: `resolver.py`의 `CombiningExactMatchResolver`·`OpenAIEmbeddingResolver`, `splitters.py`의 `KiwiSentenceSplitter`·`CHUNK_SIZE`/`CHUNK_OVERLAP`(Kiwi/KSS가 공유). (resolver·splitter 스왑 여지 유지. splitter는 KSS/Kiwi/Whole 3종으로 축소.)

## 검색(소비) 목표
```python
VectorCypherRetriever(
    driver, index_name="chunk_emb",
    embedder=OpenAIEmbeddings(model="text-embedding-3-small"),
    retrieval_query="""
      MATCH (node)<-[:EVIDENCED_BY]-(e:Event)
      OPTIONAL MATCH (c:Character)-[:APPEARS_IN]->(e)
      OPTIONAL MATCH (cs:CharacterState)-[:ESTABLISHED_IN]->(e)
      RETURN node.text, e.title, e.chapter,
             collect(DISTINCT c.name), collect(DISTINCT cs.attribute+'='+cs.value)
    """,
)
```
벡터로 `Chunk`를 찾고 → Event 앵커 → 1-hop 확장. (검색 계층 전체 구현은 범위 밖. 여기서는 그래프를 그 소비가 가능한 형태로 만든다.)

## 검증 (end-to-end)
1. Neo4j healthy. `cd poc && uv sync`.
2. 진입점 호출(CLI 래퍼): `LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py` → ch2. (또는 파이썬에서 `indexing(1, text, driver)` 직접 호출.)
3. **Chunk·임베딩**: `SHOW VECTOR INDEXES`에 `chunk_emb`. `MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c)`=0. Chunk 수 ≈ KSS 조각 수(회차당 다수).
4. **추출 경로 lexical graph 제거 확인**: `MATCH (:Event)-[:FROM_CHUNK]->() RETURN count(*)`=0 (extractor create_lexical_graph off — Chunk 근거는 EVIDENCED_BY로만).
5. **EVIDENCED_BY**: `MATCH (:Event)-[:EVIDENCED_BY]->(:Chunk) RETURN count(*)`>0, CharacterState도 >0. `evidence_chunk` 잔여 property 없음.
6. **근거 정합성(육안)**: CharacterState의 `evidence` 문자열 ↔ 연결된 `Chunk.text`가 실제로 그 문장을 담는지 5건 스팟체크.
7. **Chapter 노드**: `MATCH (c:Chapter) RETURN c.number, c.summary` — 회차별 노드·요약 존재. `MATCH (:Chunk)-[:IN_CHAPTER]->(:Chapter) RETURN count(*)`>0. Event→회차 도달: `MATCH (e:Event)-[:EVIDENCED_BY]->(:Chunk)-[:IN_CHAPTER]->(c:Chapter) RETURN e.title, c.number LIMIT 5`. Chapter/Chunk에 `__Entity__` 라벨 없음(resolver 안전) 확인. `rolling_summary.md` 미생성(요약이 그래프에).
8. **벡터 RAG 스모크**: 위 `VectorCypherRetriever`로 질의 1개 → 관련 Chunk + 확장 Event/Character.

## 열린 주의점
- Event.chapter는 합성 `[chapter:{chapter}]` 헤더로 LLM이 채운다 — 신뢰 안 되면 후처리로 `SET e.chapter=$chapter` 강제 가능(옵션). Chunk의 IN_CHAPTER는 chapter 인자 기반이라 항상 정확.
- Chapter 노드의 `createdAt`(라이브러리 자동)은 재실행마다 갱신됨 — 무해(노드 identity는 결정적 id로 고정).
- KSS 조각 크기(≈800)는 근거 정밀도 vs 마커 과다 균형 — 실측 튜닝.
- 회차 재실행 시 Chunk/Chapter는 결정적 id로 upsert, EVIDENCED_BY/IN_CHAPTER는 MERGE로 중복 방지.
- **novel_context는 현행 full-dump 유지**(전체 그래프 덤프 + 전 회차 Chapter.summary). 이번 플랜 변경은 요약 저장을 `.md`→`Chapter.summary`로 옮기고 덤프 제외 라벨에 Chunk/Chapter를 추가하는 것뿐. 회차 누적 시 컨텍스트 토큰이 선형 증가하는 확장성 이슈는 인지하되, **Chunk 벡터를 활용한 retrieval 기반 컨텍스트는 이번 범위 밖의 별도 후속 과제**로 미룬다.
