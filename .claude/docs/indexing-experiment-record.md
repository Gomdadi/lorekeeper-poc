# 인덱싱 컴포넌트 검증 실험 기록 (Phase 2)

> 웹소설 원고 → KG 추출 파이프라인의 **어떤 컴포넌트 조합이 최적인지** OFAT로 비교한 실험의 과정·결과·결론 기록.
> 실험 코드(`poc/src/indexing_eval.py`, `poc/main.py`)와 산출물(`poc/output/v_*.cypher`, `*.metrics.json`, `report.md`, `backup_*.cypher`)은 결론이 `indexing.py`에 반영되어 이 문서로 대체·제거되었다.

## 문제

한국어 웹소설 원고에서 KG(Character/Location/Event/CharacterState/Organization)를 추출할 때, 파이프라인의 세 컴포넌트 축을 어떻게 고를지 근거가 없었다.

- **splitter**: 원고를 어떤 단위로 자를 것인가 (고정 크기 vs 문장 인식 vs 회차 통째)
- **resolver**: 표기 변형(별칭·존칭)을 어떤 방식으로 한 노드로 병합할 것인가
- **reasoning effort**: 추출 LLM의 추론 강도를 얼마로 둘 것인가

정답 라벨(gold set)이 없으므로 자동 채점 대신 **구조 지표 자동 집계 + 그래프 덤프 육안 판정**으로 best-fit을 찾기로 했다.

## 방법 — OFAT harness

`indexing_eval.py`가 동일 입력에 대해 **한 번에 한 컴포넌트만 바꿔가며(OFAT)** 파이프라인을 돌렸다.

- Neo4j Community는 단일 DB만 쓰므로 **변형마다 `MATCH (n) DETACH DELETE n`로 리셋** 후 실행.
- 각 변형 그래프를 `apoc.export.cypher.all`로 `output/<variant>.cypher`에 덤프(재적재 육안 비교용).
- 지표: 라벨별 노드 수, 관계 타입별 수, `PruningStats`(스키마 밖 속성 제거), resolver 병합 수(대상/생성), 추출 토큰(req/resp/total), 소요 시간.
- 지표는 변형별 `*.metrics.json`에 저장되어 `report.md` 비교표로 합쳐졌다.

## 변형 그리드와 결과

베이스라인: FixedSizeSplitter(회차 태깅) + ExactMatch resolver.

| variant | chunks | 라벨별 노드 | resolve(대상/병합) | 토큰(total) | 소요(초) |
|---|---|---|---|---|---|
| v_baseline (FixedSize + ExactMatch) | 37 | Char:40, Event:54, Loc:14, CS:42 | 228/54 | 194300 | 39.6 |
| v_recursive (Recursive + ExactMatch) | 37 | Char:33, Event:52, Loc:13, CS:47 | 226/46 | 194398 | 40.0 |
| v_kiwi (Kiwi문장 + ExactMatch) | 34 | Char:30, Event:42, Loc:14, CS:44 | 203/44 | 179561 | 35.5 |
| v_kss (KSS문장 + ExactMatch) | 33 | Char:28, Event:39, Loc:16, CS:35 | 191/44 | 174063 | 34.6 |
| v_resolver_embed (FixedSize + Embedding) | 37 | Char:34, Event:50, Loc:14, CS:47 | 127/17 | 194410 | 54.2 |
| v_resolver_fuzzy (FixedSize + Fuzzy) | 37 | Char:29, Event:55, Loc:9, CS:47 | 132/17 | 195055 | 36.2 |
| v_kss_fuzzy (KSS문장 + Fuzzy) | 33 | Char:28, Event:46, Loc:12, CS:35 | 120/17 | 175089 | 38.1 |

**reasoning effort 스윕** (회차 통째 = 1청크, 소입력 기준):

| variant | 라벨별 노드 | 토큰(total) | 소요(초) |
|---|---|---|---|
| v_chapter (none) | Char:2, Event:3 | 6481 | 10.7 |
| v_chapter_medium | Char:2, Event:2 | 6910 | 10.4 |
| v_chapter_high | Char:3, Event:7, Loc:1, CS:1 | 26289 | 59.6 |
| v_chapter_xhigh | Char:2, Event:3, Loc:2 | 52661 | 293.8 |

## 축별 분석

- **splitter**: 문장 인식 splitter(KSS/Kiwi)는 고정 크기 대비 **청크 수가 줄고(37→33~34)** 문장 중간이 잘리지 않아 경계가 깨끗했다. KSS가 가장 안정적. 다만 Character 수가 줄어(40→28) 노드 분열 감소인지 엔티티 누락인지는 덤프 육안 판정이 필요했다.
- **resolver**: ExactMatch는 표기 변형을 못 잡아 병합 후 생성 노드가 많았다(54). Fuzzy·Embedding은 별칭을 적극 병합해 최종 노드를 크게 줄였다(생성 17). **Embedding은 별도 임베딩 API 호출로 느렸고(54.2초)**, Fuzzy는 비슷한 병합을 저비용으로 달성(36.2초).
- **reasoning effort**: `high`가 `none`/`medium`보다 구조를 더 풍부하게 잡았다(CharacterState·Location 포착). `xhigh`는 응답 토큰이 폭증(46k)하고 293초로 느려 **효용 대비 비용이 나빠** 배제.
- **청크 입도(핵심 발견)**: 여러 소청크로 자르면 회차 내 동일 인물이 청크마다 다른 노드로 분열됐다. **회차 통째를 한 청크(chunk_size=12000)**로 넣으면 회차 내 coreference를 한 컨텍스트에서 해소해 분열이 줄었다. `ChapterTaggingSplitter`가 `[N화]`로 선분할하므로, 내부 FixedSizeSplitter의 chunk_size를 가장 긴 회차보다 크게 잡아 회차가 통째로 한 청크가 되게 했다.

## best-fit 결론 (→ `indexing.py`에 채택)

- **splitter**: `ChapterTaggingSplitter(FixedSizeSplitter(chunk_size=12000))` — 회차 통째 1청크(coreference 보존).
- **resolver**: `CombiningFuzzyResolver` — 별칭 병합 효과는 Embedding과 대등하면서 빠르고 API 비용 없음. 병합 시 `description`을 배열로 combine해 정보 유실 방지.
- **reasoning effort**: 기본 미전달(none), `LOREKEEPER_REASONING` 환경변수로 A/B 오버라이드. 실회차 누적 검증(`backup_*_ch1_6` 등)은 `high`로 별도 확인.
- **모델**: 추출·요약 `gpt-5.4-mini`, 임베딩 `text-embedding-3-small`.

이 조합이 회차 누적 인덱싱(`indexing.py`)의 기본 구성이 되었고, 변형 비교 harness는 소임무를 마쳐 제거했다.
