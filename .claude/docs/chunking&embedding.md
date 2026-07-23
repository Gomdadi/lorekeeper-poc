# Episode Ingestion Specification

> 상태: `accepted`
>
> 확정일: 2026-07-21
>
> 구현 상태: 목표 사양이며 현재 코드에는 아직 반영되지 않았다.

## 목적

이 문서는 웹소설 회차 원문을 검색 가능한 chunk로 만들고 OpenAI embedding으로
변환하는 기준을 정의한다. Chunking, embedding 입력, metadata와 원문 위치 추적
규칙을 하나의 계약으로 고정한다.

이 사양의 초기 입력 범위는 `source_type: episode`인 회차 원문이다. 설정 자료,
메모, outline, semantic metadata 추출, 답변 생성과 충돌 판정은 범위 밖이다. 특정
데이터베이스의 저장 형식과 갱신 정책도 이 사양에서 다루지 않는다.

## 확정 사항

| 항목 | 확정값 |
| --- | --- |
| 문장 경계 탐지 | KSS |
| KSS backend | MeCab 필수 |
| 원문 기준 | Application이 받은 `EpisodePayload.content` |
| core grouping | 문장 단위를 순서대로 1,000자까지 greedy하게 묶음 |
| 최종 chunk 최대 크기 | overlap 포함 1,600자 |
| overlap | 핵심 범위의 바로 앞 문장 1개와 바로 뒤 문장 1개 |
| overlap 초과 예산 | core 보존 후 남은 공간을 앞·뒤에 균등 배분 |
| core 초과 처리 | 1,000자를 넘는 단일 문장은 원문을 강제 분할할 수 있음 |
| overlap 초과 처리 | 앞·뒤 문장을 포함한 후보가 1,600자를 넘으면 overlap을 자름 |
| chunk 본문 | 입력 원문의 연속 구간을 공백과 줄바꿈까지 그대로 보존 |
| embedding provider | OpenAI API |
| embedding model | `text-embedding-3-small` |
| vector dimension | 1,536 |
| embedding 입력 | `chunk_text`만 사용 |
| metadata 범위 | 기본 식별·출처 필드와 원문 위치만 사용 |
| semantic metadata | 저장하지 않음 |

## 결정 이유

- KSS와 MeCab으로 한국어 문장 경계를 먼저 찾고, 검색 대상의 핵심 내용인 core는
  최대 1,000자로 제한한다.
- Core에 앞·뒤 한 문장을 문맥으로 추가하며, 일반적인 최종 chunk 길이는 약 1,200자
  전후로 예상한다. 이는 관찰을 위한 예상값이며 강제해야 하는 목표 크기는 아니다.
- 앞·뒤 문장의 합이 600자를 넘는 예외도 고려하되 최종 크기가 무제한 늘어나지 않도록
  보수적인 상한을 1,600자로 둔다. Core가 1,000자이면 overlap에 최대 600자를 사용하고,
  전체 후보가 1,600자를 넘으면 core를 보존하면서 양쪽 문맥을 같은 우선순위로 잘라낸다.
- 정규화된 재조립 문자열 대신 application이 받은 원문 범위를 그대로 사용해 검색
  결과와 근거 위치를 일치시킨다.
- OpenAI `text-embedding-3-small` 하나를 색인과 검색 query에 함께 사용해 동일한
  embedding 공간을 유지한다.
- 이번 metadata는 식별과 provenance에 필요한 값으로 제한하고, 별도 추출이 필요한
  semantic metadata는 포함하지 않는다.

## 전체 흐름

```text
EpisodePayload.content
→ KSS + MeCab 문장 경계 탐지
→ 원문 문자 범위를 유지한 최대 1,000자 core grouping
→ 앞·뒤 한 문장 overlap
→ 최종 1,600자 절대 상한 적용
→ chunk_text와 source offsets 생성
→ OpenAI text-embedding-3-small 호출
→ 1,536차원 vector 검증
→ DB 중립적인 ingestion 결과 생성
```

검색 query도 `text-embedding-3-small`로 변환한다. 저장된 chunk와 query에 서로 다른
모델, 차원 또는 embedding 공간을 사용하면 안 된다.

## 입력 원문 계약

원문 보존의 기준은 application이 전달받은 `EpisodePayload.content`다. 파일의 byte
배열이나 transport adapter가 처리하기 전의 값이 아니다.

- Application이 `content`를 받은 뒤에는 chunking을 위해 공백, 줄바꿈 또는 문장부호를
  정규화하지 않는다.
- 문자 위치는 `content`에 대한 0-based Unicode 문자 index다.
- `end_char`는 Python slice와 같은 exclusive index다.
- Byte offset은 사용하지 않는다.

모든 최종 chunk는 다음 불변조건을 만족해야 한다.

```text
chunk_text == content[start_char:end_char]
```

## KSS·MeCab 문장 경계

KSS는 문장 경계를 찾는 로컬 라이브러리로 사용하며 외부 API로 호출하지 않는다.
Backend는 `mecab`으로 고정한다.

- MeCab 또는 필요한 Python binding이 없으면 ingestion을 실패시킨다.
- 다른 backend로 자동 전환하지 않는다.
- KSS가 반환한 문장 문자열을 공백으로 다시 합쳐 `chunk_text`를 만들지 않는다.
- KSS 결과는 원문 안의 단조 증가하는 문자 범위인 `SentenceSpan`으로 변환한다.
- 문장 결과를 원문 범위로 손실 없이 대응시킬 수 없으면 ingestion을 실패시킨다.

KSS가 문자열 목록을 반환하면 adapter는 반환 순서대로 각 문장 본문을 직전 본문의
끝 이후에서 찾는다. 같은 문자열이 여러 번 나타나면 현재 탐색 위치에서 가장 가까운
첫 번째 exact match를 사용하며, 찾지 못하거나 순서를 보존할 수 없으면 실패한다. 이
왼쪽부터의 대응 규칙으로 반복 문장이 있는 원문에서도 같은 KSS 결과를 같은 위치에
연결한다.

`SentenceSpan`은 KSS가 인식한 문장 본문뿐 아니라 원문의 모든 구분 문자를 손실 없이
소유해야 한다. 구체적인 범위 규칙은 다음과 같다.

- 첫 sentence span은 `content`의 index 0에서 시작한다.
- 문장 뒤부터 다음 문장 시작 전까지의 공백과 줄바꿈은 앞 문장 span에 포함한다.
- 마지막 sentence span은 `len(content)`에서 끝난다.
- 결과 span들은 겹치지 않고 `[0, len(content))` 전체를 순서대로 분할한다.

KSS와 MeCab은 기술 의존성이므로 domain이 직접 import하지 않는다. Application이 소유한
문장 경계 탐지 계약을 outbound adapter가 구현하고, domain의 순수 chunking 규칙은
기술 독립적인 문장 범위를 입력으로 받는다.

## Chunking 규칙

### 1. Core grouping

Core grouping은 overlap을 알지 못하는 독립 전략이다. 문장 단위를 원문 순서대로 현재
core에 추가하고, 다음 문장을 추가한 원문 범위가 1,000자를 넘으면 현재 core를 확정하는
greedy 규칙을 사용한다. Core는 하나 이상의 연속 문장 단위로 구성한다.

- Core 범위들의 합집합은 입력 원문을 순서대로 빠짐없이 덮어야 한다.
- Core 범위끼리는 중복하지 않는다.
- 공백과 줄바꿈도 원문 범위와 1,000자 계산에 포함한다.
- Core grouping 결과를 만든 뒤 별도의 overlap 전략을 적용한다.

### 2. 앞·뒤 한 문장 overlap

각 core에 다음 범위를 문맥으로 추가한다.

- Core 바로 앞에 문장이 있으면 그 문장 1개
- Core 바로 뒤에 문장이 있으면 그 문장 1개
- 첫 core에는 앞 문장이 없고 마지막 core에는 뒤 문장이 없다.

앞 문장의 시작부터 뒤 문장의 끝까지 하나의 연속 원문 범위로 저장한다. 따라서
overlap된 문장 사이의 원문 공백과 줄바꿈도 그대로 포함된다. 인접 chunk 사이의 중복은
의도된 동작이다.

### 3. 최종 1,600자 절대 상한

최종 `chunk_text`는 overlap을 포함해 1,600자를 넘을 수 없다. 우선순위는 다음과 같다.

1. 모든 core 내용이 전체 chunk 집합에서 빠짐없이 포함되어야 한다.
2. 최종 chunk는 원문의 연속 구간이어야 한다.
3. 앞·뒤 한 문장을 온전히 포함하되, 1,600자 상한과 충돌하면 상한이 우선한다.
4. 상한을 지키기 위해 overlap 문장이 중간에서 잘릴 수 있다. Core는 overlap 단계에서
   자르지 않고, 1,000자를 넘는 단일 문장의 분할은 core 단계에서 수행한다.

앞 문장, core, 뒤 문장의 전체 범위가 1,600자 이내이면 세 범위를 모두 온전히 포함한다.
1,600자를 넘으면 core를 먼저 보존하고 남은 문자 예산을 앞·뒤 overlap에 균등하게
배분한다.

```text
overlap_budget = 1600 - core_length
left_budget = overlap_budget // 2
right_budget = overlap_budget - left_budget
```

- 600자는 고정된 overlap 크기가 아니다. Core가 정확히 1,000자일 때 남는 최대
  overlap 예산이며, core가 더 짧으면 앞·뒤 문장에 사용할 수 있는 예산은 더 커진다.
- 앞쪽은 바로 앞 문장의 core에 가까운 suffix를 사용한다.
- 뒤쪽은 바로 뒤 문장의 core에 가까운 prefix를 사용한다.
- 한쪽 문장이 배정된 예산보다 짧으면 남은 예산을 반대쪽에 넘긴다.
- 한쪽 문장만 존재하면 전체 overlap 예산을 그 방향에 사용한다.
- 두 방향의 길이가 같지 않을 때 생기는 홀수 1자는 뒤쪽에 먼저 배정한다.

이 규칙으로 구한 앞쪽 suffix, core, 뒤쪽 prefix는 원문에서 하나의 연속 범위여야 한다.
동일한 `content`와 동일한 순서의 KSS 문장 결과에는 항상 같은 `start_char`와
`end_char`가 생성되어야 한다.

KSS가 찾은 문장 하나가 1,000자를 넘으면 해당 원문 범위를 연속된 최대 1,000자 조각으로
강제 분할한다. 분할 조각은 core grouping과 overlap 단계에서 하나의 경계 단위로 취급한다.
길이가 1,000자인 core 조각도 최대 600자의 overlap 예산을 가진다. 이 예외에서는 core
분할에 문장 경계보다 1,000자 제한이 우선하고, 이후 overlap에는 최종 1,600자 제한을
적용한다.

### 4. Chunk 번호와 원문 범위

- `chunk_no`는 원문 순서에 따라 1부터 연속 증가한다.
- `start_char`와 `end_char`는 overlap을 포함한 최종 `chunk_text` 범위다.
- `core_start_char`와 `core_end_char`는 overlap을 제외한 핵심 범위다.
- 모든 범위는 `start <= core_start <= core_end <= end`를 만족해야 한다.
- Hard split으로 core가 여러 조각이 되면 각 조각이 독립된 core 범위가 된다.

## Embedding 계약

### Provider와 모델

Embedding은 OpenAI의 `POST /v1/embeddings` endpoint를 사용한다.

```text
model = text-embedding-3-small
effective dimensions = 1536
input = chunk_text
```

OpenAI 공식 문서에 따르면 `text-embedding-3-small`의 기본 출력 길이는 1,536이며,
Embeddings API는 입력 텍스트와 모델명을 받아 vector를 반환한다. 이 사양에서는 차원을
축소하지 않는다.

### 입력 규칙

- 원문에서 잘라 저장할 `chunk_text`와 API에 전달할 문자열은 완전히 같아야 한다.
- `work_id`, 회차 제목, metadata, 구분용 prefix 또는 설명 문구를 입력에 붙이지 않는다.
- 여러 chunk를 한 요청으로 batch 처리하더라도 응답 순서를 입력 순서와 정확히 대응한다.
- 각 chunk마다 정확히 하나의 vector를 생성한다.
- 응답 vector가 비어 있거나 1,536차원이 아니면 ingestion을 실패시킨다.

검색 query는 metadata를 섞지 않은 query text를 같은 모델로 embedding한다.

### 외부 전송 경계

회차 원문 `chunk_text`를 embedding 생성을 위해 OpenAI API로 전송하는 것을 허용한다.
이 결정은 원문의 장기 보관을 OpenAI에 맡긴다는 의미가 아니다. API key는 코드나
metadata에 저장하지 않고 runtime secret으로 주입한다. 실제 배포 시점의 데이터 처리와
보존 정책은 OpenAI의 현재 공식 정책을 별도로 확인한다.

## Metadata 계약

Semantic metadata인 `characters`, `locations`, `events`, `setting_categories`는 이
사양의 chunk metadata에 포함하지 않는다. Embedding 호출도 이 값을 추출하지 않는다.

### Chunk metadata

```json
{
  "chunk_id": "550e8400-e29b-41d4-a716-446655440000",
  "document_id": "work:sample-work:episode:1",
  "version_id": "v003",
  "work_id": "sample-work",
  "source_type": "episode",
  "episode_no": 1,
  "episode_title": "1화",
  "arc_title": "비밀의 화원",
  "arc_part_no": 1,
  "chunk_no": 7,
  "chunk_text": "원문의 공백과 줄바꿈을 그대로 유지한 연속 구간",
  "source_reference": {
    "source_id": "source-store-id",
    "version_id": "v003",
    "start_char": 1200,
    "end_char": 2187,
    "core_start_char": 1284,
    "core_end_char": 2103
  }
}
```

### 필드 규칙

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `chunk_id` | 예 | UUID v4 |
| `document_id` | 예 | `work:{work_id}:episode:{episode_no}` |
| `version_id` | 예 | Source store가 부여한 원문 버전 |
| `work_id` | 예 | 작품 식별자 |
| `source_type` | 예 | 현재는 `episode`만 허용 |
| `episode_no` | 예 | 1 이상의 정수 |
| `episode_title` | 예 | 회차 표시 제목 |
| `arc_title` | 아니요 | 없으면 `null` |
| `arc_part_no` | 아니요 | 없으면 `null` |
| `chunk_no` | 예 | 문서 안에서 1부터 연속 증가 |
| `chunk_text` | 예 | `content[start_char:end_char]`와 동일 |
| `source_reference.source_id` | 예 | 입력값이 없으면 `document_id` 사용 |
| `source_reference.version_id` | 예 | 최상위 `version_id`와 동일 |
| `source_reference.start_char` | 예 | 최종 chunk의 0-based 시작 문자 위치 |
| `source_reference.end_char` | 예 | 최종 chunk의 exclusive 끝 문자 위치 |
| `source_reference.core_start_char` | 예 | overlap 제외 core 시작 위치 |
| `source_reference.core_end_char` | 예 | overlap 제외 core exclusive 끝 위치 |

## Application과 Domain 책임

### Domain

- 기술 독립적인 `SentenceSpan`과 source offset 불변조건
- 문장 범위 grouping의 core 1,000자 제한과 양방향 overlap의 최종 1,600자 제한 규칙
- `TextChunk`의 원문 범위와 빈 문자열 방지
- KSS, MeCab, OpenAI SDK, HTTP 또는 구체적인 저장 기술을 import하지 않음

### Application

- 문장 경계 탐지 port, `ChunkingStrategy`, `OverlapStrategy`를 각각 주입받음
- `detect → core grouping → overlap → embedding → result` 순서로 orchestration
- 전략 이름을 확인해 application service 안에서 분기하지 않음
- `chunk_text`를 embedding port에 전달
- vector 개수와 1,536차원 검증
- Metadata와 vector를 결합한 DB 중립적인 결과 model 생성

### Outbound adapter

- KSS와 MeCab으로 문장 경계 탐지 port 구현
- OpenAI Embeddings API로 기존의 중립적인 embedding port 구현

Bootstrap은 KSS adapter와 OpenAI client를 생성해 주입하고 한 command 실행 동안
재사용한다. Domain과 application은 concrete client 생성이나 API key 로딩을 담당하지
않는다. Metadata B를 적용한 목표 구조에서는 `SemanticMetadataExtractor`를 ingestion의
필수 의존성으로 두지 않는다.

## 실패 조건

다음 조건에서는 조용히 fallback하거나 일부만 성공한 것으로 처리하지 않는다.

- KSS 또는 MeCab을 불러올 수 없음
- KSS 문장 결과를 원문 문자 범위에 대응시킬 수 없음
- 빈 chunk 생성
- `chunk_text`와 source slice 불일치
- 1,000자를 넘는 core 또는 1,600자를 넘는 최종 chunk 생성
- OpenAI가 입력 chunk 수와 다른 개수의 vector를 반환
- 빈 vector 또는 1,536차원이 아닌 vector 반환

API timeout, retry, batch 크기, rate limit 제어와 dependency version pinning은 이
문서에서 임의의 값으로 정하지 않는다. 구현 전에 별도의 운영 설정으로 확정한다.
KSS·MeCab 버전과 사전이 달라지면 문장 경계도 달라질 수 있으므로, 서로 다른 runtime
환경에서까지 같은 offset을 보장하는 것은 dependency pinning을 확정한 뒤의 계약이다.

## 검증 기준

구현은 최소한 다음 자동 검증을 통과해야 한다.

1. 모든 chunk에서 `chunk_text == content[start_char:end_char]`다.
2. 모든 core의 길이는 1 이상 1,000 이하이고, 최종 `chunk_text`는 1 이상 1,600 이하다.
3. 전체 후보가 1,600자 이내인 일반 경계에서는 core의 앞·뒤 한 문장이 overlap으로
   온전히 포함된다.
4. 첫 chunk와 마지막 chunk는 존재하지 않는 방향의 overlap을 요구하지 않는다.
5. 1,000자를 넘는 단일 문장은 원문을 변경하지 않은 연속 조각으로 강제 분할된다.
6. Core 범위의 합집합이 입력 원문을 빠짐없이 덮는다.
7. Sentence span들이 공백과 줄바꿈을 포함해 `0, len(content))`를 정확히 분할한다.
8. 1,600자를 넘는 overlap 후보의 남은 예산은 확정된 균등 배분 규칙을 따른다.
9. Stored `chunk_text`와 OpenAI embedding 입력이 동일하다.
10. 모든 vector가 1,536차원이고 검색 query도 같은 embedding model을 사용한다.
11. Metadata에는 확정된 기본 필드와 source offsets만 있고 semantic metadata는 없다.
12. Domain과 application은 KSS, MeCab, OpenAI 또는 구체적인 저장 구현을 import하지
    않는다.

검색 품질 평가는 같은 corpus, 같은 chunking, 같은 질문과 같은 `top_k` 조건에서 수행한다.
Spec 채택과 retrieval 품질 검증은 별개이므로, 구현 후 기존 구조화 질문 세트로
Pass/Partial/Fail 결과를 다시 기록한다.

## 현재 구현과의 차이

현재 코드는 이 사양과 다음 부분이 다르다.

- 문단 최대 5개·1,600자·overlap 없음 전략을 사용한다.
- `jhgan/ko-sroberta-multitask` 로컬 모델로 768차원 vector를 만든다.
- `TextChunk`가 source offsets를 갖지 않는다.
- 공백과 줄바꿈을 일부 정규화한다.
- Metadata에 semantic metadata 배열 4종을 포함한다.
- OpenAI embedding adapter와 KSS·MeCab sentence boundary adapter가 없다.

따라서 이 문서는 현재 동작 설명이 아니라 다음 구현이 따라야 할 채택된 목표 사양이다.

## 공식 참고 자료

- [KSS 공식 저장소
- OpenAI Vector embeddings 가이드
- OpenAI `text-embedding-3-small` 모델 문서
- OpenAI API 데이터 제어 문서