# LoreKeeper PoC 재설계 — 검증 문제 분리

## Context

기존 poc-error-detection 계획은 "KG 구조 + 오류 탐지 + 검색 전략"이 단일 파이프라인에 혼재되어 있어,
각 요소가 왜 필요한지, 언제 실패하는지가 불명확하다.

세 가지 독립된 질문으로 분리하고 순서대로 검증한다.

---

## 검증 순서

```
[C] KG 스키마 설계  →  [A] 커버 범위 검증  →  [B] 검색 구조 설계
    (기반 구조)            (무엇까지 탐지?)       (어떤 쿼리 전략?)
```

C가 기반이 되어야 A, B를 올바르게 실험할 수 있다.

---

## [C] KG 스키마 설계

**검증 목표**: 소설 오류 탐지에 필요한 KG 스키마를 결정한다.

현재 스키마 (`Entity -[RELATES_TO]-> Entity`)는 너무 flat해서 시간/공간/상태 변화를 표현 못 한다.
다음 세 차원을 스키마에 반영해야 한다.

### 노드 타입

| 노드 | 핵심 properties |
|------|----------------|
| `Character` | name, aliases, physical_state |
| `Place` | name, spatial_level (world/region/city/location) |
| `Item` | name, description |
| `Event` | name, chapter, scene_order, is_flashback, is_reliable |
| `Faction` | name, description |
| `Ability` | name, description |

### 엣지 타입

| 엣지 | 설명 | 핵심 properties |
|------|------|----------------|
| `HAS_STATE` | 캐릭터 → 신체/심리 상태 | valid_from_chapter, valid_until_chapter |
| `HAS_ITEM` | 캐릭터 → 아이템 | acquired_chapter, lost_chapter |
| `PARTICIPATED_IN` | 캐릭터 → 이벤트 | role |
| `LOCATED_AT` | 캐릭터/이벤트 → 장소 | chapter |
| `BELONGS_TO` | 캐릭터 → 세력 | valid_from_chapter |
| `CAUSED_BY` | 이벤트 → 이벤트 | — |
| `BEFORE` | 이벤트 → 이벤트 | — |
| `KNOWS` / `IS_ENEMY_OF` | 캐릭터 → 캐릭터 | first_chapter |

### 시간/공간/메타 모델링

- **작중 시간**: `Event.chapter` + `Event.scene_order` + `BEFORE/CAUSED_BY` 엣지
- **공간**: `Place.spatial_level` + `CONTAINS` 엣지 (계층 구조)
- **작품 외적 메타**: `Event.is_flashback`, `Event.is_reliable` (소문/거짓 발화 구분)
- **상태 유효 기간**: `HAS_STATE`, `HAS_ITEM` 엣지의 `valid_from/until_chapter`로 시점 추적

**검증 방법**: 소규모 3화 원고로 이 스키마 기반 KG를 구축 후
"오른팔을 잃은 카엘"처럼 상태 변화를 KG가 제대로 표현하는지 Cypher로 직접 확인.

---

## [A] 커버 범위 검증

**검증 목표**: 어느 수준(L1~L6)의 오류까지 탐지 가능한지 파악한다.

### 실험 설계

각 Level별로 최소 3개의 충돌 케이스 + 1개의 정상 케이스 설계.
VectorRAG만으로 먼저 실행 → Graph-guided 추가 후 비교.

| Level | 오류 유형 | 탐지에 필요한 것 |
|-------|-----------|----------------|
| L1 | 물리적 상태 모순 | 원문 청크 (Vector 충분) |
| L2 | 소유/관계 모순 | 원문 청크 or KG 1-hop |
| L3 | 인과 체인 | KG multi-hop 필수 |
| L4 | 시간 구조 (회상 등) | Event.is_flashback + chapter 메타 |
| L5 | 인식론적 오류 | Event.is_reliable 레이블링 |
| L6 | 정체성 해소 | Character.aliases + entity resolution |

### 측정

```
각 Level별: Precision / Recall / F1
실패 원인 분류:
  (a) 정보 자체가 KG+Vector에 없음
  (b) Retrieval 실패 (있는데 못 찾음)
  (c) Judge 실패 (찾았는데 판단 틀림)
```

**결과 활용**: 어떤 Level에서 어떤 원인으로 실패하는지가 [B]의 검색 전략 결정과
다음 PoC(L3+ 진입) 여부를 결정한다.

---

## [B] 검색 구조 설계

**검증 목표**: 어떤 claim에 어떤 검색 전략이 유효한지 결정한다.

**[A]의 실패 원인 분류 (b)가 이 실험의 입력값**이 된다.

### 두 전략 비교

| 전략 | 동작 방식 | 적합한 케이스 |
|------|-----------|-------------|
| **AdvancedRAG** | claim 텍스트 → Vector 검색 → top-k 청크 | 명시적 속성/상태, L1 단순 케이스 |
| **Graph-guided Vector** | claim → entity 추출 → KG 1-hop → entity 타겟 Vector 검색 | 관계 포함 claim, entity가 모호한 경우, L2 |

### 라우팅 가설

```
claim에 named entity가 명확하고
관계/상태 추론이 필요한가?
  YES → Graph-guided Vector
  NO  → AdvancedRAG (더 빠름, 충분)
```

**검증 방법**: [A]의 동일한 ground truth 세트로 두 전략을 각각 실행, F1 비교.
단일 파이프라인 유지 vs claim-type별 라우팅 여부를 F1 차이로 결정.

---

## 성공 기준 요약

| 실험 | 성공 기준 |
|------|----------|
| C | KG에서 `HAS_STATE.valid_until_chapter` 기반 Cypher가 상태 변화를 올바르게 조회 |
| A | L1+L2 F1 > 0.6, 실패 원인 분류 완료 |
| B | AdvancedRAG vs Graph-guided 전략 간 F1 차이 정량화 |
