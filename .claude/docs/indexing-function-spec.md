# `indexing()` 함수 스펙

`poc/src/indexing.py` — 한국어 웹소설 회차 누적 인덱싱 진입점. **한 번 호출 = 한 회차 인덱싱**이며,
이전 회차까지 누적된 Neo4j 그래프 위에 새 회차를 얹는다(Neo4jWriter는 upsert만 하고 기존 데이터를
지우지 않는다).

## 시그니처

```python
async def indexing(
    chapter: int,   # 회차 번호. Event.chapter·Chapter.number·Chunk.chapter의 근거
    text: str,      # 회차 원고 전체
) -> dict
```

**반환 dict**: `{chapter, labels(라벨별 누적 노드 수), rels(관계 타입별 누적 수), tokens{request, response, total}, summary(이 회차 요약)}`

## CLI 실행

```bash
cd poc && LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py
```

| 환경변수 | 의미 | 기본값 |
| --- | --- | --- |
| `LOREKEEPER_CHAPTER` | 회차 번호(CLI 필수) | — |
| `LOREKEEPER_INPUT` | 원고 파일 경로 | `poc/data/input.txt` |

## 실행 흐름 (9단계)

| # | 단계 | 구현 | 비고 |
| --- | --- | --- | --- |
| 1 | 배경 컨텍스트 조립 | `context.dump_graph_text` + `context.load_summaries` + `context.build_context` | 아래 '배경 컨텍스트' 참조. 길이를 로그로 계측 |
| 2 | KSS 청킹 | `KSSSentenceSplitter(chunk_size=100, overlap=0)` | overlap=0: 경계 문장이 두 `[C{i}]` 마커에 중복 노출되면 evidence 번호가 모호해져서 끔 |
| 3 | Chunk/Chapter 레이어 | `chunks.write_chunk_layer` | Chunk 노드(결정적 uid `chunk-{chapter}-{index}`) + text-embedding-3-small 임베딩 + NEXT_CHUNK + Chapter MERGE + IN_CHAPTER + 벡터 인덱스 `chunk_emb`(1536, cosine) 보장 |
| 4 | 추출 텍스트 조립 | `[chapter:N]` 헤더 + 각 조각 앞 `[C{index}]` 마커 | 마커 번호 = Chunk.index → evidence_chunk 매핑 근거 |
| 5 | 추출 파이프라인 실행 | `pipeline.build_pipeline` → `pipe.run` | 회차 통째 단일 청크(WholeTextSplitter). DAG: splitter→extractor→pruner→writer→resolver, schema→extractor·pruner. few-shot(`EXTRACTION_FEW_SHOT`)·스키마(`NODE_TYPES/RELATIONSHIP_TYPES/PATTERNS`)는 run 데이터로 주입 |
| 6 | 근거 링크 | `evidence.link_evidence` | Event/CharacterState의 `evidence_chunk`('C3' 또는 'C3,C4') → 이번 회차 Chunk에 `EVIDENCED_BY` MERGE 후 임시 property 제거 |
| 7 | description collapse | `resolver.collapse_merged_descriptions` | 병합으로 배열이 된 description(`NOT n.description IS :: STRING`)을 LLM(luna high)으로 한 문자열로 합침. 멱등 |
| 8 | 요약 갱신 | `context.summarize_episode` → `Chapter.summary` SET → `context.update_global_summary` | 회차 요약(3~5문장, 원천 보존) 저장 후, 전역 요약 `Story.summary`를 일정 크기(15문장/1,200자 지시)로 갱신 + `(Chapter)-[:IN_STORY]->(Story)` MERGE |
| 9 | 결과 집계 | `_label_counts`/`_rel_counts` | 누적 DB 기준 라벨·관계 카운트 + 토큰 사용량 출력·반환 |

## 배경 컨텍스트 (novel_context)

3섹션을 이 순서로 결합해 각 추출 프롬프트에 주입한다. 빈 소스는 섹션 생략(첫 회차는 전부 빈 문자열).

1. `# 지금까지의 전체 줄거리(압축)` — `Story.summary` (전역 요약, O(1) 크기)
2. `# 최근 회차 요약` — 최근 3화(`_RECENT_WINDOW`)의 `Chapter.summary` 원문, `[N화] ...` 형식
3. `# 지금까지의 그래프` — 도메인 노드/관계의 엔티티 중심 중첩 덤프:
   - 관계는 전부 노드 줄에 인라인(`## 관계` 섹션 없음): Character 하위에 상태(HAS_STATE, 그 안에
     성립 회차·ABOUT 대상)와 RELATED_TO 중첩, Event 줄에 장소(HOSTS)·참여(APPEARS_IN),
     Location/Organization 줄에 상위(LOCATED_IN/PART_OF)
   - **Event/CharacterState는 이름·구조 정보만 싣고 description 제외**(크기 억제).
     Character/Item/Location/Organization은 description 포함
   - CharacterState 성립 회차는 ESTABLISHED_IN 대상 Event(폴백: EVIDENCED_BY Chunk)의 chapter로 판정
   - 제외: `__Entity__`/`__KGBuilder__`/Chunk/Chapter/Story 라벨, `__` 접두 속성, `evidence_chunk`

## Entity resolution (`PerLabelResolver`)

파이프라인 마지막 단계에서 라벨별 전략으로 병합한다.

| 라벨 | 전략 | 근거 |
| --- | --- | --- |
| Character | fuzzy (WRatio 0.85) | 짧은 이름 표기변형(coref) 흡수 |
| Item / Location / Organization | 정규화 exact-match (공백·괄호·따옴표류 제거, toLower 없음) | 정준 이름 표기 흔들림만 흡수, 숫자 차이("3807칸"≠"3907칸")는 분리 |
| CharacterState / Event | 무병합 | 서술형 name의 충돌·근거(EVIDENCED_BY) 소실 원천 차단 |

병합 시 name은 첫 노드 값, description은 combine(배열) 후 7단계에서 LLM collapse,
`produceSelfRel:false`로 병합 유발 self-loop 방지.

## 산출 그래프

```
도메인:     Character, Event, CharacterState, Item, Location, Organization  (+__Entity__/__KGBuilder__)
관계:       APPEARS_IN, HOSTS, HAS_STATE, ESTABLISHED_IN, LOCATED_IN, PART_OF, RELATED_TO, ABOUT
근거:       (Event|CharacterState)-[:EVIDENCED_BY]->(Chunk)
provenance: (Chunk)-[:NEXT_CHUNK]->(Chunk), (Chunk)-[:IN_CHAPTER]->(Chapter),
            (Chapter)-[:IN_STORY]->(Story {id:'main'})
요약:       Chapter.summary(회차별 원천), Story.summary(전역 압축)
벡터:       Chunk.embedding (인덱스 chunk_emb, 1536차원, cosine)
```

Chunk/Chapter/Story는 `__Entity__` 라벨이 없어 resolver 대상 밖이다.

## 전제조건·주의

- **회차는 오름차순으로 순차 실행**한다 — 배경 컨텍스트·전역 요약이 직전 회차까지의 누적 상태를 전제.
- 전체 재인덱싱 시 DB를 먼저 비운다: `MATCH (n) DETACH DELETE n;`
- 같은 회차 재실행은 Chunk(결정적 uid)·Chapter·Story에는 idempotent하지만, **추출 노드는 LLM
  비결정성으로 중복/변형이 생길 수 있다** — 회차 단위 재실행 전 해당 회차 산출물 정리 필요.
- 추출 실패는 조용히 삼키지 않는다(`on_error=RAISE`). KSS는 mecab 백엔드 강제.
- 비용 실측: 6화 누적 기준 추출 토큰 약 $0.4대(회차당 LLM 1회 + 요약 2회 + collapse 필요분).
