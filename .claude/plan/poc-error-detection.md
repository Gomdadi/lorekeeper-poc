# LoreKeeper PoC 접근 전략 — 설정 오류 탐지 단계

## Context

Naive Indexing Phase(PoC 1)가 완료됐다. 이제 "구축한 KG + Vector로 실제 설정 충돌을 탐지할 수 있는가?"를 검증한다.

---

## 핵심 아키텍처 결정

### Graph-guided Vector (혼합 방식)

Vector RAG와 Graph RAG를 순차 또는 병렬로 분리하지 않는다. KG가 entity를 특정하고, 그 entity로 Vector 검색을 타겟팅하는 단일 파이프라인을 사용한다.

```
Claim: "카엘이 두 손으로 여명의 검을 쥐었다"
    │
    ├─ KG 조회 (Neo4j)
    │   → "카엘" entity + 1-hop 관계 조회
    │   → 구조/관계 컨텍스트 제공
    │
    └─ Vector 조회 (LanceDB) ← KG에서 추출한 entity명으로 쿼리 타겟팅
        → "카엘" 관련 원문 청크 top-2 반환
        → "3화: 적의 검에 오른팔을 잃은 카엘은..."
        → 사실이 기술된 원문 제공
            │
            └─ Claude judge
                → {claim, kg_context, chunk_context} → {conflict: bool, confidence, reason}
```

**KG와 Vector의 역할**:
- KG: 확립된 설정의 구조/관계 컨텍스트 (무엇이 설정인지)
- Vector: 그 설정이 원문 어디에 기술됐는지 (어떻게 쓰였는지)

KG가 상태 변화(State Change)를 description에 포착하지 못하는 한계를 Vector 원문 청크로 보완한다.

### Vector DB: 기존 LanceDB 재활용

GraphRAG 인덱싱 시 `graphrag_workspace/output/lancedb`에 원고 청크가 자동으로 임베딩된다. 별도 임베딩 API 호출이나 새로운 Vector DB 구축은 불필요하다.

GraphRAG의 `local_search` 모드가 이미 Graph-guided Vector를 내부적으로 구현한다 (KG entity 조회 + LanceDB 청크 검색 합산). PoC에서는 `local_search`를 직접 활용하거나 LanceDB를 직접 쿼리하는 방향으로 접근한다.

### 비용 구조

```
KG 조회 (Cypher 쿼리)   → 무시할 수 있는 수준
Vector 조회 (LanceDB)   → 무시할 수 있는 수준
LLM judge 호출          → 실질적 비용의 99%
```

Advanced RAG만으로 풀 수 있는 단순한 케이스라도 KG 조회를 추가하는 오버헤드는 없다. 단일 파이프라인으로 통일하고, claim type별 라우팅 최적화는 프로덕션 단계에서 고려한다.

### 컨텍스트 제약

- KG: **1-hop**으로 제한 (L1/L2는 2-hop 불필요, 노이즈 방지)
- Vector: **top-2 청크**로 제한 (노이즈 방지)
- LLM 호출은 claim 단위로 분리 (컨텍스트는 per-claim으로 bounded)

---

## 문제 범위: 계층화된 검증

오류 taxonomy를 정의하되, PoC는 L1+L2부터 시작하고 결과가 다음 단계 진입을 결정한다.

| Level | 유형 | 예시 | 구조적 요구사항 |
|---|---|---|---|
| **L1** | 물리적 상태 모순 | 오른팔 없음 → 두 손으로 검 | Entity 1-hop KG + Vector |
| **L2** | 소유/관계 모순 | 잃어버린 검을 사용 | Entity 1-hop KG + Vector |
| L3 | 인과 체인 (multi-hop) | A→B→C 추론 | Multi-hop KG traversal |
| L4 | 시간 구조 | 회상, 과거 시점 혼용 | Temporal metadata |
| L5 | 인식론적 오류 | 소문/거짓 발화를 사실로 처리 | Epistemic labeling |
| L6 | 정체성 해소 | 소년→소협→풍룡대주 | Entity resolution |

**PoC 2는 L1+L2만 대상으로 한다.**

---

## 테스트 데이터 전략

### KG 원본 원고: 3~5화 분량의 현실적인 원고

현재 `manuscript.txt`(27줄)는 오류 탐지 테스트에 부적합하다. 인물 20~30명, 관계 수십 개가 있는 수준이어야 KG 복잡도 문제가 드러난다.

옵션:
1. 직접 작성 — 오류 케이스를 의도적으로 설계 가능
2. Claude로 생성 — 빠른 데이터 확보

### 신규 회차: 기존 원고 기반 오류 주입

```
기존 원고 1~5화 → KG 구축  (이 원고는 "정답"으로 간주)
                      ↓
신규 회차 (6화 기반) ← 기존 원고에서 확립된 설정을 위반하는 오류 주입
```

신규 회차는 완전한 합성이 아니라, 6화 기준 원고를 먼저 작성하고 의도적 오류만 주입한다. 자연스러운 문체가 유지되어 실제 환경에 가깝고, 오류 주입 전 원본이 ground truth가 된다.

### ground_truth.json

```json
[
  {
    "claim_id": 1,
    "text": "카엘이 두 손으로 여명의 검을 쥐었다",
    "is_conflict": true,
    "reason": "카엘은 3화에서 오른팔에 치명상을 입었음"
  },
  {
    "claim_id": 2,
    "text": "레이나가 치유 마법을 시전했다",
    "is_conflict": false,
    "reason": "레이나의 치유 능력은 기존 설정과 일치"
  }
]
```

충돌 케이스 6개 + 정상 케이스 4개(최소 10개) 권장.

---

## 파이프라인 구조

```
new_chapter.txt
    │
    ├─[Step 1] Claim Extraction (Claude)
    │           → 신규 회차에서 검증 가능한 사실 주장 추출
    │           → [{subject, predicate, object, source_quote, claim_type}, ...]
    │
    ├─[Step 2] Graph-guided Vector Retrieval
    │           → Neo4j: subject 기준 1-hop 서브그래프 조회
    │           → LanceDB: subject entity명으로 원문 청크 top-2 검색
    │
    ├─[Step 3] Conflict Judge (Claude)
    │           → {claim, kg_context, chunk_context}
    │           → {conflict: bool, confidence: float, reason: str}
    │
    └─[Step 4] Evaluation
                → ground_truth.json 대조
                → Precision / Recall / F1
                → 실패 케이스 원인 분류:
                  (a) KG + Vector 모두 정보 없음
                  (b) Retrieval 실패 (정보는 있는데 못 찾음)
                  (c) Judge 실패 (찾았는데 판단 틀림)
```

---

## 디렉토리 구조

```
lorekeeper-poc/
├── error_detection/
│   ├── __init__.py
│   ├── claim_extractor.py     # 신규 회차 → 주장 목록
│   ├── retriever.py           # Neo4j 1-hop + LanceDB top-2 통합 조회
│   ├── conflict_judge.py      # Claude 충돌 판단 (validator.py 패턴 확장)
│   ├── evaluator.py           # precision/recall 측정
│   └── main.py
└── test_data/
    ├── manuscript_base.txt    # KG 구축용 원고 (3~5화)
    ├── chapter_new.txt        # 오류 주입된 신규 회차
    └── ground_truth.json
```

**재사용할 기존 파일**:
- `naive_indexing/validator.py` → `conflict_judge.py` 기반
- `naive_indexing/neo4j_loader.py` → `retriever.py` Neo4j 연결 부분 기반

---

## 검증 방법

1. `manuscript_base.txt`(3~5화)로 GraphRAG 인덱싱 → KG + LanceDB 구축
2. `chapter_new.txt` 작성 (6화 기반 + 의도적 오류 주입)
3. `python error_detection/main.py --chapter test_data/chapter_new.txt --ground-truth test_data/ground_truth.json`
4. 콘솔 출력: Precision / Recall / F1 + 실패 케이스별 원인 분류
5. 실패 원인 패턴으로 L3 진입 여부 결정

**성공 기준**:
- 1차 목표: F1 > 0.6
- 실패 원인 분류 완료 → 다음 단계(L3 또는 entity resolution) 진입 근거 확보

---

## L3+ 진입 기준 및 방향

| Level | 진입 조건 | 추가 필요 구조 |
|---|---|---|
| L3 | L1+L2 검증 후, multi-hop 실패 케이스가 주요 패턴 | Cypher 2-hop 확장 또는 GraphRAG local_search 활용 |
| L4 | 실제 다회차 원고 테스트 시 시간 구조 오류 발생 | 챕터 번호 메타데이터를 Edge에 추가 |
| L5 | 소문/거짓 발화 오판 케이스 확인 | Claim extraction에 epistemic 태깅 추가 |
| L6 | entity not found 실패가 30% 이상 | 인덱싱 시 Claude로 호칭 테이블 생성 |
