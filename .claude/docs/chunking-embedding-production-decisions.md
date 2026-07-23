# 청킹·임베딩 — 프로덕션 전환 시 결정 사항

> 상태: 결정 대기 (POC에서는 대부분 보류)
>
> 원본: [`.claude/docs/chunking&embedding.md`](./chunking&embedding.md) (accepted target spec, 확정 2026-07-21)
>
> 대상 코드: `poc/src/` (splitters.py · chunks.py · indexing.py · evidence.py · pipeline.py)

## Context

`chunking&embedding.md`는 **프로덕션 ingestion 계약**으로 작성된 목표 사양이다. 그 안에는 두 종류가 섞여 있다.

- (a) **현재 POC가 이미 충족한 항목** — 재작업 불필요.
- (b) **POC 목적(KG 추출 품질)엔 과하지만, 프로덕션에선 반드시 정해야 할 항목.**

이 문서는 **(b)만 뽑아 결정 목록으로 정리**한다. 목적은 두 가지다. ① POC를 최소 상태로 유지하고(요청되지 않은 프로덕션 배선을 지금 넣지 않는다), ② 프로덕션 착수 시 이 목록을 체크리스트로 쓴다.

> **주의:** 원본 스펙의 "현재 구현과의 차이"(원본 L344-355) 섹션은 **오래된 코드 기준이라 신뢰할 수 없다.** 아래 "이미 스펙과 일치" 참고 — 스펙이 "차이"라고 적은 것 중 최소 3개가 이미 해소돼 있다.

---

## 이미 스펙과 일치 (재작업 불필요)

| 항목 | 스펙 요구 | 현재 코드 | 근거 |
| --- | --- | --- | --- |
| 임베딩 모델·차원 | `text-embedding-3-small` / 1536 | 일치 | pipeline.py:40, chunks.py:22 |
| 문장 경계 탐지 | KSS + MeCab (backend 고정) | 일치 | splitters.py:79-98 |
| 색인·쿼리 동일 모델 | 같은 embedding 공간 | 일치(resolver도 동일 모델) | resolver.py:130 |
| semantic metadata | Chunk에 **저장 안 함** | 일치(KG 노드로 분리, EVIDENCED_BY) | chunks.py:35-43 |

→ 스펙이 "차이"로 적은 **768-dim ko-sroberta / OpenAI adapter 없음 / semantic 배열 4종 저장**은 셋 다 **stale**(실제 코드는 이미 OpenAI 1536 / KSS·MeCab / semantic 미저장).

---

## 결정 목록

각 항목은 **스펙 요구 → 현재 상태 → 도입 트리거(언제 필요한가) → 트레이드오프 → 잠정 판단** 순으로 정리한다. 판단은 확정이 아니라 프로덕션 착수 시 재검토할 기본 방향이다.

### D1. 원문 정확 보존 + source offset

- **스펙 요구**: `chunk_text == content[start_char:end_char]` 불변식. 문장을 문자열로 재조립하지 않고 원문 문자 범위(`SentenceSpan`)로 다뤄, 공백·줄바꿈까지 손실 없이 보존(원본 L84-88, L107-117).
- **현재**: `splitters.py`가 KSS 문장 문자열을 `" ".join()`으로 이어 붙임 → 문장 사이 `\n`·중복 공백이 단일 스페이스로 뭉개짐. 원문 문자 offset 미추적. 청크가 원문의 어떤 substring과도 일치하지 않음.
- **도입 트리거**: 리더에서 **문자 단위 인용·하이라이트**("근거: 원문 1,204~1,287자"), 청크의 **원문 재정합**, 저장본의 **바이트 동일 감사** 중 하나라도 실제 소비처가 생길 때.
- **트레이드오프**: KSS 문장을 원문에 재탐색해 `SentenceSpan`으로 매핑하는 로직 + "KSS가 글자를 그대로 반환" 제약 + 실패 시 hard-fail 필요. 현재 근거 앵커는 offset을 안 쓰므로(문자열 index로 매칭) 지금은 기능적 이득이 0.
- **잠정 판단**: **보류.** 사후 도입 비용이 낮다(청크 생성부만 교체). 소비처가 생기면 그때.

### D2. 검색 청킹 granularity·overlap ↔ 근거 앵커 충돌  ⭐가장 큰 결정

- **스펙 요구**: core 1,000자 greedy grouping + 앞·뒤 1문장 overlap + 최종 1,600자 상한 + 1,000자 초과 단일 문장 hard split(원본 L119-180).
- **현재**: KSS 청크 ≈100자, **overlap=0**(indexing.py:57,115-117). 이 청크가 **두 역할을 겸함** — ① 검색 임베딩 단위(Chunk 노드), ② `[C{i}]` 근거 앵커(indexing.py:125 → evidence.py:26의 EVIDENCED_BY). 작고 안 겹치게 한 건 **근거 정밀도·번호 무모호성**을 위한 의도적 선택.
- **핵심 충돌**: 두 역할의 최적 청크 크기가 정반대다.
  - 근거 앵커: **작고 안 겹침**(문장 단위 정밀, 번호 유일).
  - 검색 임베딩: 보통 **크고 겹침**(벡터당 문맥, recall).
  - 스펙의 overlap을 켜면 경계 문장이 인접 두 `[C{i}]` 마커에 중복 등장해 `evidence_chunk` 번호가 **모호**해지고(정확히 indexing.py:113-114가 끈 이유), 큰 청크는 근거를 ≈10문장 덩어리로 **거칠게** 만든다.
- **선택지**:

  | 안 | 내용 | 근거 정밀도 | 검색 개선 | 작업량 |
  | --- | --- | --- | --- | --- |
  | A. 최소 변경 | 청크 크기·overlap=0 현행 유지 | 보존 | 없음 | 소 |
  | B. 2계층 분리 | 검색용 1,000/1,600 overlap 청크를 **새 레이어**로 추가 + 근거 앵커는 현재 작은 청크 유지 | 보존 | 확보 | 대 |
  | C. 스펙 통일 | 1,000/1,600 overlap으로 단일화, `[C{i}]` 근거 앵커 재설계, 정밀도 하락 감수 | 하락 | 확보 | 중~대 |

- **도입 트리거**: **검색(retrieval) 경로가 실제로 붙고(현재 미구현), 100자 청크의 검색 품질이 측정으로 부족하다고 드러날 때.** 그전엔 "검색용으로 키운다"는 이득이 가상.
- **잠정 판단**: 검색 미구현 상태에서 **아직 안 쓰는 이득(검색)을 위해 쓰는 자산(근거 정밀도)을 깨는 C는 손해.** 검색을 붙일 때 **B(2계층)** 로 가고, 그전까지는 **A**. → **D3와 묶어서 판단.**

### D3. 검색(retrieval) 경로 구현 + 품질 측정

- **스펙 요구**: query를 같은 모델로 임베딩, 동일 corpus·chunking·질문·top_k에서 Pass/Partial/Fail 측정(원본 L214, L340-342).
- **현재**: **검색 경로 없음.** 벡터 인덱스(`chunk_emb`)는 생성만 하고 쿼리하지 않음. `VectorRetriever` 류 코드 부재. (`embed_query`는 resolver의 엔티티 중복 제거용이지 검색용 아님 — resolver.py:156.)
- **도입 트리거**: RAG 답변 생성/질의 기능을 실제로 만들 때. **D2의 청크 크기 결정은 이 측정 없이는 이론에 불과** — 100자 vs 400자 vs 1,000자를 같은 질문 세트로 A/B 해야 답이 나온다.
- **참고(도메인 특성)**: 웹소설은 대명사·주어 생략이 심해 짧은 청크가 상호참조 미해소로 임베딩이 모호해질 수 있다 → overlap/소폭 증량의 근거. 단 이 시스템은 **NEXT_CHUNK 이웃 확장 + KG n-hop**이 있어 청크가 문맥을 전부 짊어질 필요가 줄어든다.
- **잠정 판단**: 프로덕션 QA의 필수 항목. **청킹 크기(D2)는 이 경로를 붙여 측정한 뒤 결정.**

### D4. Hexagonal 아키텍처 (domain / application / adapter)

- **스펙 요구**: 책임 3분할(원본 L115-117, L276-302).
  - **domain**: 순수 규칙(core 1,000 / overlap 1,600) + 값 객체(`SentenceSpan`, offset 불변식). KSS·MeCab·OpenAI·HTTP·저장기술 **import 금지**.
  - **application**: port(문장경계·`ChunkingStrategy`·`OverlapStrategy`·embedding) 주입받아 `detect→group→overlap→embed→result` 오케스트레이션. 전략 이름으로 분기 안 함(다형성).
  - **outbound adapter**: KSS+MeCab로 문장경계 port 구현, OpenAI로 embedding port 구현. bootstrap이 주입.
- **현재**: flat POC. 기술이 로직에 직결 — `KSSSentenceSplitter`가 클래스 안에서 `import kss` + 청킹 규칙 동거(splitters.py), `chunks.py`가 임베딩·그래프조립·저장을 한 함수에서 수행, `pipeline.py`가 OpenAI 객체 직접 생성.
- **도입 트리거**: 프로덕션 하드닝 — 기술 교체 용이성(KSS↔Kiwi, OpenAI↔로컬), 순수 도메인 단위테스트, 스펙 12개 검증 기준 충족이 필요할 때.
- **트레이드오프**: 순전한 구조 재편(기능 증가 0). 잘 도는 인덱싱 경로를 계층형 패키지로 전면 재배치.
- **잠정 판단**: **보류.** POC 목적이 KG 추출 품질이면 과하다. **중간 지점** — 계층·port 전체 대신 **순수 청킹 규칙만 offset 기반 함수로 분리**해 테스트 가능하게 하는 저비용 절충이 가능.

### D5. 메타데이터 계약 + 입력 모델(EpisodePayload)

- **스펙 요구**: Chunk에 `chunk_id`(UUID v4) / `document_id` / `version_id` / `work_id` / `source_type` / `episode_no` / `episode_title` / `arc_title` / `arc_part_no` / `chunk_no` / `source_reference{start/end/core_*}`(원본 L230-274).
- **현재**: Chunk 속성 = `text` / `index` / `chapter` / `embedding` / `uid`. 회차 식별이 정수 `chapter` 하나. 진입점 `indexing(chapter, text)`는 **회차 정수 + 원문**만 받음 — work_id·version·arc 구조가 입력에 아예 없음.
- **두 부류로 갈림**:
  - **청킹 산물**(`start/end/core_*`): D1(offset)을 따라감.
  - **문서 정체성**(`work_id`/`version_id`/`document_id`/`episode_title`/`arc_title`/`arc_part_no`/`source_type`): **소스가 없음.** 채우려면 스펙의 `EpisodePayload`처럼 **입력 계약 자체를 확장**해야 함(노드 속성 추가가 아니라 데이터 입구 변경).
- **도입 트리거**: **다작품 / 원문 버전관리 / provenance UI** 중 하나가 실제로 필요할 때. 단일 작품·단일 버전인 지금은 조기.
- **트레이드오프**: 전체 도입 시 소스 없는 정체성 필드를 `work_id="?"` 같은 **하드코딩 더미값**으로 채우게 됨 — 소비처 없이 노드만 오염.
- **잠정 판단**: **최소 유지(현행).** offset 필드는 D1 따라감. 다작품·버전관리 소비처가 생기면 그때 입력 모델과 함께 도입.

### D6. 운영 설정 — 스펙이 명시적으로 보류한 것

- **스펙 요구**: API timeout/retry/batch 크기/rate limit, dependency version pinning을 **임의값으로 정하지 말고 운영 설정으로 확정**(원본 L316-319).
- **현재**: OpenAI 호출은 라이브러리 기본값(neo4j-graphrag). retry/batch/timeout 미조정.
- **특기(offset 재현성)**: **KSS·MeCab 버전·사전이 바뀌면 문장 경계도 바뀐다.** D1(offset)을 도입하면, 서로 다른 런타임에서 같은 offset을 보장하려면 **의존성 pinning이 선행 조건**이다.
- **잠정 판단**: 프로덕션 운영 설정으로 확정. offset(D1) 도입 시 dependency pinning을 **묶어서** 결정.

### D7. 외부 전송·보안 경계

- **스펙 요구**: 회차 원문 `chunk_text`를 embedding 위해 OpenAI로 전송하는 것 허용하되, API key는 코드·metadata에 저장 않고 **runtime secret 주입**, 배포 시점 데이터 처리·보존 정책 별도 확인(원본 L216-221).
- **현재**: API key는 `OPENAI_API_KEY` 환경변수를 라이브러리가 읽음(코드에 직접 안 씀). `.env` 로드(client.py:10). 원문이 이미 OpenAI(추출 LLM + 임베딩)로 나감.
- **잠정 판단**: 프로덕션 배포 전 **OpenAI 데이터 보존 정책 재확인 + secret 관리 방식 확정**. 원문 외부 전송이 정책상 허용되는지 검토.

### D8. 실패 조건 — no silent fallback

- **스펙 요구**: KSS/MeCab 로드 실패, offset 대응 실패, 빈 chunk, `chunk_text`≠source slice, 크기 초과, vector 개수·차원 불일치에서 **조용히 폴백/부분성공 금지 — 실패시킴**(원본 L304-315).
- **현재**: 일부만 반영 — KSS는 `backend="mecab"` 강제로 pecab 조용한 폴백을 막음(splitters.py:90-94), 추출은 `on_error=RAISE`(pipeline.py). 하지만 빈 chunk / 차원 / slice 일치 같은 불변식 검증은 없음(offset·slice는 애초에 미추적).
- **도입 트리거**: D1(offset) 도입 시 slice 불변식 검증이 자연히 따라옴. 그 외 빈 chunk·차원 검증은 프로덕션 견고성 항목.
- **잠정 판단**: offset(D1) 도입과 **함께** 불변식 검증 추가. 독립적으로는 빈 vector/차원 검증 정도만 저비용으로 선반영 가능.

### D9. 배경 컨텍스트 선별 — 검색 기반 subgraph 추출

- **배경**: novel_context는 계층 요약(전역 Story.summary + 최근 K화) + 엔티티 중심 덤프(최근 창 밖
  Event/CharacterState는 이름만)로 압축돼 있다(context.py). 그래도 **이름 줄의 선형 성분은 남는다**
  (회차당 ~600자 수준) — 수백 화 스케일에선 덤프가 다시 커진다.
- **다음 단계(미구현)**: 회차 원고(또는 그 요약·등장 엔티티)를 질의로 삼아 **관련 subgraph만 선별**
  주입 — 예: 이번 회차 원고에 이름이 등장하는 엔티티 + 그래프 1-hop 이웃, 또는 청크 임베딩 유사도
  상위 회차의 엔티티. 단, 추출 전에 질의를 만들어야 하는 순서 문제(원고를 읽기 전엔 무엇이 관련인지
  모름)가 있어, "원고 선(先)스캔 → 엔티티 후보 추출 → subgraph 로드 → 본추출" 2-pass 구조가 필요하다.
- **도입 트리거**: 배경 컨텍스트 길이 로그가 다시 원고 크기(회차당 ~1만자)에 근접할 때 — 대략 수십~
  백여 화 누적 시점.
- **잠정 판단**: **보류.** 현 압축으로 증가율이 1/8로 꺾여 POC 범위(수십 화)에선 불필요. 트리거 도달
  시 2-pass 선별로.

---

## 축의 독립성 (계획 크기 산정용)

이 결정들은 서로 얽혀 보이지만 **상당 부분 독립 축**이라 따로 결정할 수 있다.

- **D2(청킹 크기)** ↔ **D1(offset)**: 독립. 큰 청크를 `" ".join()`으로 만드는 것도 가능.
- **D5(메타데이터)** ↔ **D2/D4**: 독립. 청킹·아키텍처 안 건드리고 메타데이터만 붙이거나 그 반대도 가능.
- **묶이는 것**: D2→D3(측정 없이 크기 결정 불가), D1→D6(offset 재현성엔 pinning 필요)→D8(slice 불변식).

## 요약 — 우선순위 제안

프로덕션 착수 시 순서 제안:

1. **D3(검색 경로 + 측정)** 먼저 — 청크 크기 논쟁(D2)을 이론이 아니라 데이터로 끝낸다.
2. 측정 결과 큰 청크가 유리하면 **D2를 B(2계층)** 로 — 근거 정밀도 보존하며 검색 청크 도입.
3. 다작품·버전·인용 UI 요구가 서면 **D1(offset)+D5(메타데이터)+D6(pinning)+D8(검증)** 을 한 묶음으로.
4. **D4(hexagonal)** 는 기술 교체·테스트 요구가 실제로 생길 때. 그전엔 순수 청킹 함수 분리 정도의 절충.

POC 단계에서는 **어느 것도 지금 착수하지 않는 것**이 기본값이다 — 위 트리거가 실제로 발생하기 전까지.
