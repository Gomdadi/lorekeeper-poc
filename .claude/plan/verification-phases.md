# LoreKeeper PoC — 전체 검증 Phase 계획

## 목표

원고 텍스트 → 소설 오류 탐지 파이프라인의 각 구성 요소를 독립적으로 검증한다.
각 Phase의 세부 로직(구체적 구현, 실험 설계, 코드)은 해당 Phase의 개별 plan에서 다룬다.

---

## Phase 의존도 그래프

```
Phase 1: KG 스키마 설계
    ↓                       ↓
Phase 2: Indexing        Phase 3: Search/Coverage
검증 인프라 설계          검증 인프라 설계
(Phase 3과 병렬)          (Phase 2와 병렬)
    ↓
Phase 4: Indexing
품질 검증
    ↓                       ↓
    └──── Phase 5: 검색 전략 설계 ────┘
                    ↓
           Phase 6: 커버 범위 검증
```

---

## Phase 1: KG 스키마 설계

**검증 목표**: 소설 오류 탐지에 필요한 KG 스키마를 확정한다.

**검증 내용**
- 노드/엣지 타입이 상태 변화, 시간 구조, 공간 계층을 표현할 수 있는가
- Cypher로 스키마 기반 조회가 올바르게 동작하는가

**성공 기준**: 상태 변화(e.g. 오른팔 상실)를 Cypher로 정확히 조회 가능
**의존도**: 없음 (최우선 선행)
**세부 계획**: `phase1-kg-schema.md`

---

## Phase 2: Indexing 검증 인프라 설계

**설계 목표**: Indexing 품질 검증(Phase 4)에서 재사용 가능한 평가 프레임워크를 설계한다.

**설계 내용**
- 원고를 직접 읽고 "KG가 반드시 답할 수 있어야 하는" Cypher query 세트를 수동 작성
- 각 query의 expected result를 label로 저장 → query-label set 구성
- 측정 파이프라인: Indexing 후 동일 query를 KG에 실행 → actual result vs label 일치율 측정

**성공 기준**: 동일 query-label set으로 반복 측정 가능한 KG 품질 평가 루프 구축
**의존도**: Phase 1 완료 (Phase 3과 병렬 실행 가능)
**세부 계획**: `phase2-indexing-eval-infra.md`

---

## Phase 3: Search/Coverage 검증 인프라 설계

**설계 목표**: 검색 전략(Phase 5), 커버 범위(Phase 6) 실험에서 재사용 가능한 평가 프레임워크를 설계한다.

**설계 내용**
- Ground Truth 세트: 충돌/정상 케이스 구성 기준 및 포맷
- Judge 기준: "탐지 성공" 정의, LLM Judge vs 룰 기반 결정
- 측정 파이프라인: claim 입력 → 검색 → 판정 → Precision/Recall/F1 계산 루프

**성공 기준**: 동일 Ground Truth로 반복 실험 가능한 평가 루프 구축
**의존도**: Phase 1 완료 (Phase 2와 병렬 실행 가능)
**세부 계획**: `phase3-search-eval-infra.md`

---

## Phase 4: Indexing 품질 검증

**검증 목표**: 원고 텍스트 → KG/Vector 추출 파이프라인의 구조화 품질을 검증한다.

**검증 내용**
- LLM 추출 결과가 Phase 1 스키마에 맞게 구조화되는가
- 그래프가 얼마나 잘 만들어지는가 (오탐/누락 측정은 Phase 6 담당)

**성공 기준**: 소규모 원고 기준 KG 구조가 스키마와 일치, 주요 엔티티/관계 조회 가능
**의존도**: Phase 2 완료
**세부 계획**: `phase4-indexing.md`

---

## Phase 5: 검색 전략 설계

**검증 목표**: 어떤 claim 유형에 어떤 검색 전략이 유효한지 결정한다.

**검증 내용**
- AdvancedRAG(Vector RAG) vs Graph-guided Vector RAG vs Graph RAG 비교
- 변인 통제: 일반적인 케이스만 사용, 특수 문제 범위는 Phase 6에서 정의
- 단일 파이프라인 유지 vs claim 유형별 라우팅 결정

**성공 기준**: 전략 간 F1 차이 정량화, 라우팅 기준 결정
**의존도**: Phase 3 + Phase 4 완료
**세부 계획**: `phase5-search-strategy.md`

---

## Phase 6: 커버 범위 검증

**검증 목표**: Phase 5에서 확정된 검색 전략으로 다양한 오류 유형을 얼마나 탐지할 수 있는지 파악한다.

**검증 내용**
- 오류 유형별 Precision/Recall/F1 측정
- 실패 원인 분류: (a) 정보 부재, (b) Retrieval 실패, (c) Judge 실패

**성공 기준**: 주요 오류 유형 F1 목표치 달성, 실패 원인 분류 완료
**의존도**: Phase 5 완료
**세부 계획**: `phase6-coverage.md`

---

## 실행 순서 요약

| 순서 | Phase | 병렬 여부 |
|------|-------|----------|
| 1 | Phase 1: KG 스키마 설계 | 단독 선행 필수 |
| 2 | Phase 2: Indexing 검증 인프라 설계 | Phase 3과 병렬 가능 |
| 2 | Phase 3: Search/Coverage 검증 인프라 설계 | Phase 2와 병렬 가능 |
| 3 | Phase 4: Indexing 품질 검증 | Phase 2 완료 후 |
| 4 | Phase 5: 검색 전략 설계 | Phase 3 + Phase 4 모두 완료 후 |
| 5 | Phase 6: 커버 범위 검증 | Phase 5 완료 후 |

---

## MVP 이후 고려 사항

### 커뮤니티 기반 검색 (MS GraphRAG 방식)

MVP의 핵심은 모순 탐지이며, 이는 KG의 시간/인과 구조를 정밀하게 탐색하는 것이 목적이다.
모순은 클러스터 경계를 가로질러 발생하기 때문에 커뮤니티 기반 접근이 오히려 역효과를 낼 수 있다.

검색/요약 기능을 추가할 시점에 KG 위에 커뮤니티 레이어를 얹는 방식으로 확장한다.

| 기능 | 적합한 방식 | 시점 |
|------|-----------|------|
| 모순 탐지 | 시간축 KG + multi-hop Cypher | MVP |
| "이 캐릭터는 어떤 인물?" 검색 | 커뮤니티 기반 local search | MVP 이후 |
| "이 화의 주요 사건 요약" | 커뮤니티 기반 global search | MVP 이후 |
