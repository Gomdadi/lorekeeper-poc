# SourceSpan 근거 추적(provenance) 도입 — 방향 확정 (미착수)

## 상태
**방향만 확정. 구현 미착수.** 목표 1·3(프롬프트/배경지식)과 독립적인 별도 확장이다.

## 배경 / 문제

현재 각 사실 노드는 근거를 **문자열 속성**으로 들고 있다(`CharacterState.evidence`, `schema.py:178-187`). `Event`에는 근거 필드조차 없다. 문제:
- 같은 문장이 여러 사실에 **중복 복사**되고, 서로 미묘하게 다르게 인용돼 공유가 안 된다.
- "이 설정의 근거가 몇 화 어느 대목이냐"를 그래프로 역추적할 수 없다.

우리 그래프엔 이미 `Chunk` 노드 + `FROM_CHUNK`(neo4j_graphrag lexical graph 자동 생성)가 있어 출처를 추적하지만, **우리는 회차=1청크**라 해상도가 "회차 전체(수천 자)"로 거칠다. 근거 역추적엔 더 세밀한 단위가 필요하다.

## 확정된 방향

kgdb의 SourceSpan(문단당 증거 노드 + `EVIDENCED_BY`)을 차용하되, **정규화 단위를 "문단"이 아니라 기존 스플리터의 작은 조각**으로 한다. 웹소설은 대사마다 개행돼 문단 경계가 들쭉날쭉하지만, `splitters.py`의 문장/고정크기 분할은 크기가 일정해 더 안정적이다.

핵심 원칙: **추출 청크(회차, 크게)와 근거 조각(작게)은 별개의 분할**이다. 목적이 다르니 크기가 다르다 — 추출은 맥락(coreference)용, 근거는 정밀 역추적용.

```
원고 한 회차
 ├─ 추출용:  FixedSizeSplitter(12000)  → Chunk 1개 (회차 통째)        ← LLM 추출·coreference
 └─ 근거용:  작은 splitter (재귀/문장, ~수백 자) → SourceSpan N개      ← 역추적·공유
```

## 메커니즘 (kgdb "번호 매긴 원문" 방식 차용)

1. 원고를 근거용 작은 splitter로 분할해 각 조각을 `SourceSpan` 노드로 만든다(조각 텍스트 + chapter + 순서 인덱스 저장).
2. 추출 프롬프트에 주는 회차 청크 텍스트에 **조각 경계 마커**를 삽입 — `[S1] …문장들… [S2] …`.
3. LLM이 각 `CharacterState`/`Event`에 대해 근거 **조각 번호**(`evidence_span: "S3"`)를 반환.
4. 후처리로 번호 → `SourceSpan` 노드 → `EVIDENCED_BY` 연결.

조각 번호가 안정적 단위이므로 여러 fact가 **같은 SourceSpan을 공유(dedup)** 하고 역추적이 성립한다.

## 유의점 — 조각 크기 트레이드오프
- 너무 잘게(문장 1개): 근거가 여러 문장에 걸치면 번호 하나로 못 담아 애매.
- 너무 크게: 회차에 근접해 정밀도 이득 감소.
- **몇 문장 묶음(수백 자)** 이 균형점. `splitters.py` 재귀/문장 스플리터를 작은 `chunk_size`로.

## 건드릴 범위 (구현 시)
- **스키마**(`schema.py`): `SourceSpan` NodeType(text/chapter/span_index 등) + `EVIDENCED_BY` RelationshipType + 패턴(`CharacterState`/`Event` → `EVIDENCED_BY` → `SourceSpan`). `CharacterState.evidence` 문자열은 대체 또는 병행 여부 결정.
- **프롬프트/추출**: 청크에 조각 마커 삽입, fact가 `evidence_span` 번호 반환.
- **파이프라인**: 근거용 분할 → SourceSpan 노드 생성 → 추출 결과의 span 번호를 실제 노드에 매핑·연결하는 후처리 컴포넌트.

## 열린 결정
- `evidence` 문자열을 SourceSpan으로 **완전 대체**할지, 당분간 **병행**할지.
- 근거용 splitter 종류/크기(재귀 vs 문장, chunk_size 값) — 실측으로 튜닝.
- 한 fact가 **여러 조각**에 걸친 근거를 가질 때 다중 `EVIDENCED_BY` 허용 여부.

## 참고
- kgdb SourceSpan 정의: `kgdb/docs/schema/schema.cypher:37-78`, 속성 `kgdb/docs/lorekeeper/data-and-workflows/auto-ingestion-contract.md`(SourceSpan 절), 관계 `kgdb/docs/guides/kgdb-knowledge-layer-guide.md`(EVIDENCED_BY).
- 우리 splitter 자산: `poc/src/splitters.py`(`make_recursive_splitter`, `KiwiSentenceSplitter`, `CHUNK_SIZE`).
