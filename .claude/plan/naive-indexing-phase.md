# Naive Indexing Phase POC — Plan

## Context

LoreKeeper의 핵심 기능은 웹소설 원고에서 지식 그래프를 자동 구축하고, 신규 회차 업로드 시 설정 충돌을 감지하는 것이다. 이 POC는 그 파이프라인의 첫 번째 단계를 검증한다:

- MS GraphRAG (OpenAI API)로 원고 → Knowledge Graph 추출이 제대로 되는가?
- Claude API로 생성한 Q&A 검증 set 기준으로, Neo4j에 저장된 그래프가 얼마나 정확한가?

---

## 선택된 설계

| 항목 | 결정 |
|------|------|
| Input | 평문 `.txt` 단일 파일 |
| 그래프 추출 | MS GraphRAG + OpenAI gpt-4o-mini |
| 그래프 DB | Neo4j (Docker Compose) |
| 검증 set 형태 | 자연어 Q&A `{query, label}` — Claude haiku-4-5 생성 |
| 매칭 판정 | LLM-as-judge (Claude haiku-4-5 재활용) |

---

## 전체 파이프라인

```
manuscript.txt
    │
    ├─[Step 1a] MS GraphRAG indexing (OpenAI API)
    │            └─ Output: parquet (entities, relationships)
    │
    ├─[Step 1b] Claude API → 자연어 Q&A 검증 set 생성
    │            └─ Output: [{query, label}, ...]
    │
    ├─[Step 2a] GraphRAG parquet → Neo4j 로드
    │            └─ Entity 노드 + RELATES_TO 관계 삽입
    │
    └─[Step 2b] Validation Loop (LLM-as-judge)
                 for each (query, label):
                   1. Neo4j full-text search로 관련 subgraph 조회
                   2. Claude에게 {query, label, graph_context} 전달
                   3. Claude가 일치 여부 판단 (pass/fail + reason)
                 └─ Output: 통과율 + 실패 케이스 상세
```

---

## 디렉토리 구조

```
lorekeeper-poc/
├── naive_indexing/
│   ├── graphrag_runner.py      # MS GraphRAG init + index 실행
│   ├── validation_generator.py # Claude API로 Q&A set 생성
│   ├── neo4j_loader.py         # parquet → Neo4j MERGE
│   ├── validator.py            # LLM-as-judge 루프
│   └── main.py                 # 파이프라인 오케스트레이션
├── graphrag_workspace/
│   ├── input/
│   │   └── manuscript.txt      # 테스트용 샘플 소설 텍스트
│   └── settings.yml            # GraphRAG 설정 (모델, 청크 크기 등)
├── docker-compose.yml          # Neo4j 컨테이너
├── requirements.txt
└── .env                        # OPENAI_API_KEY, ANTHROPIC_API_KEY, NEO4J_*
```

---

## 각 컴포넌트 상세

### graphrag_runner.py
- `graphrag init --root ./graphrag_workspace` 로 workspace 초기화
- `settings.yml` 에서 OpenAI 모델을 `gpt-4o-mini`로 설정 (비용 절감)
- `graphrag index --root ./graphrag_workspace` 실행
- 결과 parquet 파일 경로 반환: `output/create_final_entities.parquet`, `output/create_final_relationships.parquet`

### validation_generator.py
- 원고 텍스트 → Claude API 호출
- 프롬프트: 인물, 관계, 사건, 장소에 관한 Q&A 10~20개 JSON 생성 요청
- 모델: `claude-haiku-4-5-20251001`
- 출력: `List[{query: str, label: str}]`

### neo4j_loader.py
- pandas로 parquet 읽기
- neo4j Python driver로 연결
- Entity 노드: `MERGE (e:Entity {id: $id}) SET e.name = $name, e.type = $type, e.description = $description`
- 관계: `MATCH (a:Entity {id: $source}), (b:Entity {id: $target}) MERGE (a)-[r:RELATES_TO {description: $desc}]->(b)`
- Full-text index 생성: `CREATE FULLTEXT INDEX entity_name_idx FOR (e:Entity) ON EACH [e.name, e.description]`

### validator.py
```
for (query, label) in validation_set:
    # Neo4j full-text search
    cypher = """
    CALL db.index.fulltext.queryNodes('entity_name_idx', $keyword)
    YIELD node, score
    WITH node LIMIT 5
    MATCH (node)-[r:RELATES_TO]-(neighbor)
    RETURN node.name, node.description, r.description, neighbor.name
    """
    graph_context = neo4j.run(cypher, keyword=extract_keyword(query))
    
    # LLM-as-judge
    judge_prompt = f"""
    [그래프 컨텍스트]
    {graph_context}
    
    [질문] {query}
    [예상 답변] {label}
    
    그래프 컨텍스트를 바탕으로 예상 답변이 올바른지 판단하세요.
    JSON으로 응답: {{"pass": true/false, "reason": "..."}}
    """
    result = claude.judge(judge_prompt)
```

### docker-compose.yml
```yaml
services:
  neo4j:
    image: neo4j:5
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/password
    volumes:
      - neo4j_data:/data
volumes:
  neo4j_data:
```

---

## Neo4j 스키마

```
(Entity {id, name, type, description})
  -[RELATES_TO {description, weight}]->
(Entity)
```

MS GraphRAG 기본 parquet 스키마를 그대로 매핑. 인물 호칭 변형 등 고급 처리는 이후 POC에서 보완.

---

## 검증 방법

1. `docker-compose up -d` → Neo4j 기동 확인 (http://localhost:7474)
2. `python naive_indexing/main.py --input graphrag_workspace/input/manuscript.txt`
3. 콘솔 출력:
   - GraphRAG 추출 엔티티/관계 수
   - Neo4j 로드된 노드/엣지 수
   - **그래프 노드/엣지 구조 출력**
     ```
     [Nodes]
     - 카엘 (PERSON): 빛의 기사단 단장, 여명의 검 소지
     - 레이나 (PERSON): 마법사, 카엘의 동료
     - 아르곤 왕국 (GEO): ...

     [Edges]
     - 카엘 -[동료]→ 레이나: 오랜 동료 관계
     - 카엘 -[소속]→ 빛의 기사단: 단장으로 소속
     - ...
     ```
   - 검증 set Q&A 리스트
   - 통과율 (pass_count / total)
   - 실패 케이스 상세 (query, label, reason)
4. Neo4j Browser에서 `MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 50`으로 그래프 시각 확인

---

## 보류 사항 (이후 POC에서)
- 인물 호칭 변형 → entity resolution (같은 인물을 하나의 노드로)
- GraphRAG local/global search 활용
- 청크 크기, 추출 프롬프트 커스터마이징
- Community detection 결과 활용
