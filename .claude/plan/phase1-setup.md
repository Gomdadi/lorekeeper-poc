# Phase 1 실행 계획: Neo4j 환경 세팅

## Context

verification-phases.md Phase 1의 전제 작업.
Neo4j를 로컬에서 실행하고, neo4j-graphrag-python의 SchemaBuilder를 통해
스키마를 정의·검증할 수 있는 Python 환경을 구축한다.
실제 스키마 내용은 Phase 1 진행 중 결정하므로 이 계획에선 뼈대만 구성한다.
예시 그래프를 시드해 Neo4j Browser에서 시각화까지 확인하는 것이 완료 기준.

---

## 생성할 파일 구조

```
lorekeeper-poc/
├── docker-compose.yml          # Neo4j 컨테이너
├── .env.example                # 환경변수 템플릿
├── .gitignore                  # .env 추가
└── poc/
    ├── pyproject.toml          # uv 기반 Python 프로젝트
    ├── .python-version         # 3.11
    └── src/
        ├── client.py           # Neo4j 드라이버 연결
        ├── schema.py           # SchemaBuilder 뼈대
        └── seed_example.py     # 예시 그래프 시드 스크립트
```

---

## 단계별 작업

### Step 1: docker-compose.yml

Neo4j 5.x Community + APOC 플러그인.
- 포트 7474: Neo4j Browser (그래프 시각화)
- 포트 7687: Bolt (Python 연결)
- 볼륨 마운트로 데이터 영속성 확보
- `NEO4J_AUTH`, `NEO4J_PLUGINS` 환경변수 설정

### Step 2: .env.example + .gitignore

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=lorekeeper
NEO4J_DATABASE=neo4j
OPENAI_API_KEY=sk-...
```

.gitignore에 `.env` 추가 (없으면 파일 생성, 있으면 항목 추가).

### Step 3: poc/ Python 환경 초기화

`uv init`으로 프로젝트 생성 후 의존성 추가:
- `neo4j` — 공식 드라이버
- `neo4j-graphrag` — SchemaBuilder, SimpleKGPipeline 등
- `python-dotenv` — .env 로드

### Step 4: poc/src/client.py

`neo4j.GraphDatabase.driver()`로 연결 객체 생성.
`.verify_connectivity()`로 헬스체크하는 `get_driver()` 함수 포함.

### Step 5: poc/src/schema.py

e2e 동작 검증용 더미 스키마 포함.
Phase 1 스키마 설계 전까지 임시로 사용하며, 실제 스키마 확정 후 교체한다.

더미 스키마 구성 (SchemaBuilder 사용):
- NodeType: `Character` (name, status), `Location` (name), `Event` (title, chapter)
- RelationshipType: `APPEARS_IN` (Character→Event), `LOCATED_AT` (Character→Location), `INVOLVES` (Event→Character)

### Step 6: poc/src/seed_example.py

더미 스키마와 일치하는 예시 데이터를 Cypher로 삽입.
예시 데이터:
- Character: 카엘 (alive), 리오나 (alive)
- Location: 북부 요새
- Event: 3화 — 북부 요새 전투
- 관계: 카엘/리오나 APPEARS_IN 전투, 카엘 LOCATED_AT 북부 요새, 전투 INVOLVES 카엘/리오나

실행 전 기존 더미 데이터를 `MATCH (n) DETACH DELETE n`으로 초기화하는 옵션 포함.

### Step 7: 시각화 확인

스크립트 실행 후 Neo4j Browser(http://localhost:7474)에서 아래 쿼리로 확인:
```cypher
MATCH (n)-[r]->(m) RETURN n, r, m
```

---

## 검증 방법

1. `docker compose up -d` → 컨테이너 정상 기동 확인
2. `python poc/src/seed_example.py` → 예시 그래프 시드
3. http://localhost:7474 접속 → 그래프 시각화 확인
4. `python poc/src/schema.py` → import 에러 없이 실행 확인

---

## 완료 기준

- Neo4j 컨테이너 실행 및 Python 연결 성공
- SchemaBuilder import 가능한 Python 환경 구성
- 예시 노드/관계가 Neo4j Browser에서 그래프로 시각화됨
