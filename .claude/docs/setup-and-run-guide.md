# LoreKeeper 설치 → 실행 안내

이 저장소를 **clone부터 검색 실행까지** 따라 할 수 있는 실전 안내서다. LoreKeeper는 한국어
웹소설 원고를 회차 단위로 읽어 Neo4j 지식그래프(KG)로 구축하고(**인덱싱**), 그 KG를 조회하는
프레임워크 중립 검색 도구를 제공한다(**검색**). 오케스트레이션(LangGraph 배선·충돌 판정 등)은
import하는 쪽의 몫이다.

---

## 1. 사전 준비

### 1-1. Neo4j 기동

레포 루트의 `docker-compose.yml`이 Neo4j 5.26 Community(APOC + 네이티브 벡터 인덱스)를 띄운다.

```bash
# 레포 루트에서
docker compose up -d
```

| 항목 | 값 |
| --- | --- |
| Bolt | `bolt://localhost:7687` |
| Browser | `http://localhost:7474` |
| 인증 | `neo4j` / `lorekeeper` (`NEO4J_AUTH`) |
| 플러그인 | APOC |

Neo4j Browser(`http://localhost:7474`)에서 그래프를 시각적으로 검토할 수 있다.

### 1-2. Python 3.11 + uv

- Python `>=3.11` (pyproject `requires-python`).
- [uv](https://github.com/astral-sh/uv) 권장(일반 `pip`도 가능).

---

## 2. 설치

```bash
git clone <this-repo>
cd lorekeeper-poc/poc
uv pip install -e .        # 또는: pip install -e .
```

- 배포 패키지명은 `lorekeeper`이고, `src/` 디렉토리가 `lorekeeper` 패키지 본문으로 매핑된다
  (`pyproject.toml`의 `package-dir`). 즉 `src/indexing.py` → `lorekeeper.indexing`.
- 설치하면 내부 모듈 경로를 몰라도 공개 API를 import 할 수 있다.

```python
from lorekeeper import (
    indexing,               # async 인덱싱 진입점
    build_retrievers,       # dict[str, Retriever]
    build_retrieval_tools,  # list[neo4j_graphrag.tool.Tool]
)
```

`from lorekeeper import ...`가 에러 없이 되면 설치 성공이다.

---

## 3. 환경변수 (`.env`)

Neo4j 접속·OpenAI 호출 값을 **레포 루트의 `.env`** 로 관리한다(`client.py`가 `load_dotenv()`로
로드). OpenAI 키는 `openai` 라이브러리가 `OPENAI_API_KEY`를 **내부적으로** 읽으므로 환경에만
있으면 된다.

| 변수 | 용도 | 확인 위치 | 기본값 | 필수 |
| --- | --- | --- | --- | --- |
| `NEO4J_URI` | Bolt 접속 URI | `client.py` (`os.environ["NEO4J_URI"]`) | 없음 | ✅ |
| `NEO4J_USER` | Neo4j 사용자 | `client.py` | 없음 | ✅ |
| `NEO4J_PASSWORD` | Neo4j 비밀번호 | `client.py` | 없음 | ✅ |
| `OPENAI_API_KEY` | 추출·요약·임베딩·text2cypher LLM | `openai` 라이브러리가 환경에서 읽음 | 없음 | ✅ |
| `NEO4J_DATABASE` | Neo4j DB 이름 | `indexing.py`·`retrieval.py` | `neo4j` | 선택 |
| `LOREKEEPER_MODEL` | 추출/요약/text2cypher LLM 모델 | `pipeline.py` | `gpt-5.6-luna` | 선택 |
| `LOREKEEPER_REASONING` | 추출 reasoning effort(`low`/`medium`/`high`/`xhigh`) | `indexing.py` | `high` | 선택 |

> - `LOREKEEPER_CHAPTER` / `LOREKEEPER_INPUT`은 CLI 실행(`python src/indexing.py`) 전용이다
>   (`indexing(chapter, text)`를 프로그램적으로 부를 땐 불필요).
> - 임베딩 모델(`text-embedding-3-small`, 1536차원)은 환경변수가 아니라 코드 상수다(`pipeline.py`
>   `EMBEDDING_MODEL`). 인덱싱과 검색이 같은 상수를 써 벡터 공간이 일치한다.

`.env` 예시:

```dotenv
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=lorekeeper       # docker-compose의 NEO4J_AUTH와 일치
OPENAI_API_KEY=sk-...
```

---

## 4. 실행 1) 인덱싱 (원고 → KG)

`indexing`은 **async 함수**다. **한 번 호출 = 한 회차** 누적 인덱싱이며, 이전 회차 위에 얹는다
(Neo4jWriter는 upsert만 하고 기존 데이터를 지우지 않는다).

```python
import asyncio
from lorekeeper import indexing

text = open("data/input_ch1.txt", encoding="utf-8").read()
result = asyncio.run(indexing(1, text))   # 1화를 KG로 인덱싱
print(result["labels"], result["tokens"])
```

- **회차는 오름차순으로 순차 실행**한다 — 각 회차는 이전 회차까지 누적된 배경 컨텍스트(전역
  줄거리 요약 + 최근 회차 요약 + 그래프 덤프)를 추출 프롬프트에 주입하므로, 1화 → 2화 → 3화 …
  순서를 지켜야 한다.

```python
for ch in range(1, 7):
    text = open(f"data/input_ch{ch}.txt", encoding="utf-8").read()
    asyncio.run(indexing(ch, text))   # 회차마다 순차로
```

- 반환 dict: `{chapter, labels, rels, tokens{request,response,total}, summary}`.
- CLI로도 실행할 수 있다:

```bash
cd poc
LOREKEEPER_CHAPTER=1 LOREKEEPER_INPUT=data/input_ch1.txt uv run python src/indexing.py
```

- 검색에 필요한 인덱스(벡터 `chunk_emb` · 풀텍스트 `chunk_text_ft` cjk)는 **인덱싱이 자동
  생성**한다 → 인덱싱한 DB면 바로 검색 가능하다(별도 준비 불필요).
- 전체 재인덱싱 시 DB를 먼저 비운다: `MATCH (n) DETACH DELETE n;`

---

## 5. 실행 2) 검색 (KG → LLM-ready 텍스트)

### 5-1. retriever를 직접 `.search()`

```python
from lorekeeper import build_retrievers

retrievers = build_retrievers()   # dict[str, Retriever]

# 1) 벡터 검색 + 그래프 확장
r = retrievers["vector_cypher"].search("김남운이 다친 사건", top_k=5)
for item in r.items:
    print(item.content)           # 프롬프트에 그대로 넣을 자족 텍스트

# 2) 하이브리드(벡터 + 풀텍스트 cjk) — 고유명·정확 어휘에 유리
r = retrievers["hybrid_cypher"].search("유상아", top_k=5)

# 3) 인물 상태 이력 — up_to_chapter로 특정 시점 스냅샷
r = retrievers["entity_state_history"].search(entity_name="독자", up_to_chapter=5)

# 4) 자연어 → Cypher(집계·개방형 구조 질의)
r = retrievers["text2cypher"].search("3화부터 5화 사이에 일어난 사건들")
print(r.metadata.get("cypher"))   # 생성된 Cypher는 검색 수준 메타에
```

각 도구의 인자:

| 도구 키 | `.search(...)` 인자 |
| --- | --- |
| `vector_cypher` | `query_text`, `top_k`(기본 5) |
| `hybrid_cypher` | `query_text`, `top_k`(기본 5) |
| `entity_state_history` | `entity_name`, `up_to_chapter`(선택) |
| `text2cypher` | `query_text` |

### 5-2. 에이전트용 Tool 얻기

```python
from lorekeeper import build_retrieval_tools

tools = build_retrieval_tools()   # list[neo4j_graphrag.tool.Tool] — 프레임워크 중립
```

- 각 Tool에는 한국어 name/description과 파라미터 설명이 붙어 있다(LLM 도구 선택·인자 지정용). 단
  `neo4j_graphrag.tool.Tool`은 LangChain 타입이 아니라 LangGraph에 **바로는 못 넣는다** → 아래 **6. LangGraph 연동** 참고.

### 5-3. content를 프롬프트에 그대로 넣기

모든 검색 결과의 `item.content`는 "원문 발췌 + 관련 그래프"(벡터/하이브리드) 또는 상태 한 줄
(entity_state_history), `key: value` 렌더(text2cypher)처럼 **자족적 LLM-ready 텍스트**다. 소비
쪽은 별도 가공 없이 프롬프트에 이어 붙이면 된다. 근거 추적·필터가 필요하면 `item.metadata`의
구조화 필드(`chapter`/`chunk_index`/`score`/`nodes`/`relationships` 등)를 쓴다.

```python
context_block = "\n\n".join(item.content for item in r.items)
prompt = f"다음 자료를 근거로 답하라.\n\n{context_block}\n\n질문: ..."
```

---

## 6. LangGraph 연동

`build_retrieval_tools()`가 주는 것은 `neo4j_graphrag.tool.Tool`이라 **LangGraph에 바로는 못 넣는다**(LangGraph는 LangChain `BaseTool`을 기대). content가 이미 LLM-ready 텍스트이므로, 각 retriever의 `.search()`를 LangChain `@tool`로 얇게 감싸면 된다.

```python
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from lorekeeper import build_retrievers

r = build_retrievers()   # 모듈 레벨에서 한 번만: 내부 드라이버·임베더가 lazy singleton

@tool
def vector_search(query: str, top_k: int = 5) -> str:
    """의미가 가까운 원문·사건을 검색한다."""
    res = r["vector_cypher"].search(query_text=query, top_k=top_k)
    return "\n\n".join(i.content for i in res.items)

@tool
def hybrid_search(query: str, top_k: int = 5) -> str:
    """벡터+풀텍스트로 고유명·정확 어휘를 잡아 검색한다."""
    res = r["hybrid_cypher"].search(query_text=query, top_k=top_k)
    return "\n\n".join(i.content for i in res.items)

@tool
def entity_state_history(entity_name: str, up_to_chapter: int | None = None) -> str:
    """특정 인물의 상태 타임라인(선택적으로 특정 회차까지)을 조회한다."""
    res = r["entity_state_history"].search(entity_name=entity_name, up_to_chapter=up_to_chapter)
    return "\n".join(i.content for i in res.items)

@tool
def text2cypher_search(query: str) -> str:
    """자연어 질의를 Cypher로 변환해 집계·구조 질의에 답한다."""
    res = r["text2cypher"].search(query_text=query)
    return "\n".join(i.content for i in res.items)

agent = create_react_agent(
    model,   # 예: ChatOpenAI(...) 등 LangChain chat model
    tools=[vector_search, hybrid_search, entity_state_history, text2cypher_search],
)
```

- 반환을 문자열(각 `item.content`를 join)로 두면 LLM이 바로 읽는다. 근거 메타데이터가 필요하면 tool이
  `item.metadata`(`chapter`/`chunk_index`/`score`/`nodes`/`relationships`)를 포함한 dict/JSON을 반환하도록 바꾼다.
- `langgraph`·`langchain-core`(+ chat model 패키지, 예: `langchain-openai`)는 **소비 프로젝트 의존성**이다.
  이 저장소(`lorekeeper`)에는 포함돼 있지 않으므로 소비 쪽에서 설치한다.
- 제네릭 어댑터: `build_retrieval_tools()`의 `Tool.get_parameters()`를 Pydantic 모델로 변환해
  `StructuredTool.from_function(func=t.execute, ...)`로 감쌀 수도 있으나, 위 `@tool` 수동 래핑이 더 단순·명확하다.

---

## 7. 트러블슈팅

- **검색 결과가 비어 있다** — 인덱싱하지 않은 DB일 가능성이 높다. `indexing()`으로 최소 1회차를
  넣었는지 확인한다. 확인 쿼리:

  ```cypher
  MATCH (c:Chunk) RETURN count(c);          -- 0이면 인덱싱 안 됨
  SHOW VECTOR INDEXES;                        -- chunk_emb 존재 확인
  SHOW FULLTEXT INDEXES;                       -- chunk_text_ft 존재 확인
  ```

- **하이브리드 검색이 한국어를 못 잡는다 / cjk analyzer 미지원** — 풀텍스트 인덱스가 cjk로
  만들어졌는지, 환경이 cjk를 지원하는지 확인한다:

  ```cypher
  CALL db.index.fulltext.listAvailableAnalyzers();   -- 목록에 'cjk'가 있어야 함
  ```

  cjk가 없으면 하이브리드의 풀텍스트 recall이 크게 떨어진다(벤치: cjk 93% vs standard 27%).

- **text2cypher가 쓰기 쿼리를 거부한다** — 의도된 동작이다. 라이브러리가 생성 Cypher를 검사해
  read-only만 실행하고, 쓰기(CREATE/MERGE/DELETE 등)는 거부한다("Refusing to execute
  non-read-only Cypher"). 조회 질의로 다시 시도한다.

- **인증/접속 실패** — `.env`의 `NEO4J_PASSWORD`가 `docker-compose.yml`의 `NEO4J_AUTH`
  (`neo4j/lorekeeper`)와 일치하는지, `NEO4J_URI`가 `bolt://localhost:7687`인지 확인한다.

- **OpenAI 관련 오류** — `OPENAI_API_KEY`가 환경에 있는지 확인한다. 벡터/하이브리드는 질의
  임베딩에, text2cypher는 Cypher 생성 LLM에 키를 쓴다(entity_state_history는 순수 Cypher라
  키가 필요 없다).
