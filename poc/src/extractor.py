"""
한국어 웹소설용 커스텀 추출 프롬프트 + 증분 컨텍스트 주입 extractor.

두 클래스로 구성된다.
- KoreanWebNovelERTemplate : neo4j-graphrag 기본 영어 프롬프트(ERExtractionTemplate)를
  한국어 웹소설 도메인용으로 재작성한 프롬프트 템플릿. 라이브러리 원본의 일반 추출
  지시(역할·작업정의·출력구조·스키마제약·ID규칙·관계방향·JSON유효성)를 빠짐없이 이식한 뒤
  회차 마커·CharacterState 시간축 등 도메인 규칙을 얹고, 전용 {novel_context} placeholder를
  하나 추가한다.
- NovelContextExtractor : 위 템플릿의 {novel_context} 빈칸을, 청크 추출 시점에 인스턴스가
  들고 있는 누적 컨텍스트(그래프 덤프 + rolling summary)로 채워 넣는 extractor.

주의: NovelContextExtractor.extract_for_chunk는 neo4j_graphrag 1.18.0의
LLMEntityRelationExtractor.extract_for_chunk 본문을 그대로 복제하되 self.prompt_template.format
호출에 novel_context 인자만 추가한 것이다. 라이브러리가 format 인자를 하드코딩하고 있어
오버라이드가 불가피하다. 라이브러리 업그레이드 시 원본 extract_for_chunk가 바뀌면 이 메서드도
동기화해야 한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from typing import Optional

from pydantic import ValidationError, validate_call

from neo4j_graphrag.exceptions import LLMGenerationError
from neo4j_graphrag.experimental.components.entity_relation_extractor import (
    LLMEntityRelationExtractor,
    OnError,
    fix_invalid_json,
)
from neo4j_graphrag.experimental.components.schema import GraphSchema
from neo4j_graphrag.experimental.components.types import (
    DocumentInfo,
    LexicalGraphConfig,
    Neo4jGraph,
    TextChunk,
    TextChunks,
)
from neo4j_graphrag.experimental.pipeline.exceptions import InvalidJSONError
from neo4j_graphrag.generation.prompts import ERExtractionTemplate, PromptTemplate
from neo4j_graphrag.types import LLMMessage

# 복제한 extract_for_chunk V1 분기가 쓰는 로거(원본과 동일 이름 규칙).
logger = logging.getLogger(__name__)


class KoreanWebNovelERTemplate(ERExtractionTemplate):
    """
    한국어 웹소설 KG 추출용 프롬프트 템플릿.

    ERExtractionTemplate을 상속하되 DEFAULT_TEMPLATE를 한국어로 전면 재작성한다.
    placeholder 4개: {novel_context}, {schema}, {examples}, {text}.
    JSON 리터럴의 중괄호는 .format() 충돌을 피하려 반드시 {{ }}로 이스케이프한다.
    """

    # 원본 ERExtractionTemplate의 7개 요소(역할·작업정의·출력구조·스키마제약·ID규칙·
    # 관계방향·JSON유효성)를 한국어로 이식하고, 웹소설 도메인 규칙과 증분 컨텍스트
    # 우선순위 지침을 추가한 프롬프트.
    DEFAULT_TEMPLATE = """\
당신은 지식 그래프(knowledge graph)를 구축하기 위해 텍스트에서 구조화된 정보를 추출하는 최상위 알고리즘이다.

주어진 텍스트에서 엔티티(노드)를 찾아 각각의 타입을 지정하고, 그 노드들 사이의 관계도 함께 추출하라.

결과는 반드시 아래 형식의 JSON으로 반환하라(nodes 배열과 relationships 배열):
{{"nodes": [ {{"id": "0", "label": "Character", "properties": {{"name": "홍길동"}} }}],
"relationships": [{{"type": "APPEARS_IN", "start_node_id": "0", "end_node_id": "1", "properties": {{}} }}] }}

아래에 주어진 노드/관계 타입만 사용하라(주어진 경우). 스키마에 없는 타입은 만들지 말라:
{schema}

각 노드에는 문자열로 된 고유 ID를 부여하고, 관계를 정의할 때 그 ID를 그대로 재사용하라.
관계는 스키마 패턴이 정한 source/target 노드 타입과 방향을 반드시 지켜라(예: Character 가 Event 를 향하는 APPEARS_IN).

--- 웹소설 도메인 추출 규칙 ---
- 회차/순서: [chapter:N] 마커의 N을 각 Event.chapter에 넣는다. story_order는 작중 시간순 값 —
  같은 회차 내 여러 사건은 발생 순서대로 N.0, N.1, N.2…로 0.1씩 증가(사건 하나면 N.0). 회상·과거
  사건은 더 작은 값, 1화보다 이전(프리퀄)이면 0이나 음수도 가능.
- 시간에 따라 변해 나중에 대조·모순 판정 대상이 되는 사실(부상, 생사, 소속, 능력·무공, 소지품
  획득/상실)은 Character 속성이 아니라 CharacterState 노드로 만들고, HAS_STATE(인물)·ESTABLISHED_IN
  (성립 Event)으로 잇는다. 상태가 바뀌면 기존 노드를 고치지 말고 새 노드를 만든다.
- 능력·무공은 CharacterState attribute로만. 이름 있는 소지품·물건(선물·첨부물 등 고유명이 없으면
  '작가의 선물'처럼 지시적 이름으로)은 Item 노드로 만들고, 소유는 CharacterState(attribute='소유',
  value='보유'/'상실') + ABOUT→Item으로 표현한다(이동 시 넘긴 인물 '상실'·받은 인물 '보유').
- 작품·사물을 저작/제작/열독한 인물은 INVOLVED_WITH(role='저자'/'제작자'/'독자')로 그 Item에 잇는다.
  이런 역할은 사람-사람 관계가 아니므로 RELATED_TO로 묶지 말 것. 단, 이 역할과 그 사물/작품 Item도
  아래 '비중 필터'를 통과할 때만 만든다.
- 사건의 구체적 물리 공간은 Location + HOSTS로, 상위 장소는 LOCATED_IN으로 한 단계씩 잇는다. 댓글창·
  게시판·앱 화면 같은 온라인·가상 공간은 Location으로 만들지 않는다(실제 물리 공간만).
- 조직·세력·회사·부서는 Organization으로, 인물 소속은 CharacterState(attribute='소속',
  value='소속'/'이탈') + ABOUT→Organization으로 표현한다. 인물이 소속된 조직은 지나가듯 언급돼도 반드시
  만든다(소속은 그 자체로 검증 대상). '회사원'·'계약직'·'정직원' 같은 신분·고용형태는 소속이 아니라
  attribute='신분'으로 분리.
- 인물↔인물의 서사적 관계(사제·동맹·적대·혈연·연인·동료 등)는 RELATED_TO로 잇고 종류를 type에
  담는다(단순 동반 등장만으로는 만들지 않음).
- attribute/value는 짧고 일관되게(생사는 항상 '생사', 부위는 '오른팔' 단위; value는 '상실'·'생존'처럼
  짧은 상태어).
- 비중 필터(스스로 판단): 각 대상이 이 이야기에서 서사적으로 의미가 있는지 직접 판단해 결정한다.
  지나가는 행인이나 순전히 배경·분위기·농담으로만 스치고 이후 아무 역할이 없는 사물·작품·조직은 빼되,
  인물의 행동·상태·관계·사건에 얽혀 서사적으로 중요해 보이는 대상은 포함한다(중요해 보이면 포함하는
  쪽으로 판단한다). 그렇게 제외되는 대상은 거기 딸린 관계·상대 노드도 함께 뺀다.
- 과추출 금지: 일시적 통증·피로·긴장처럼 그 회차에서 소모되고 대조 대상이 아닌 상태는 CharacterState로
  만들지 않는다(지속 상태만).
- 구조 완전성: description은 참고용이라 노드/관계와 중복돼도 되나, 구조로 표현 가능한 사실(소속·신분·
  소유·역할·관계·장소)은 반드시 해당 노드/관계로도 만든다.
- 소유·소속 CharacterState는 대상(Item/Organization)을 같은 출력에서 ABOUT으로 함께 낸다.
- evidence_chunk: 각 Event·CharacterState에, 그 사실을 뒷받침하는 원문 문장이 있는 청크 번호를
  채운다(예: "C3", 여럿이면 "C3,C4"). 실제 그 문장이 있는 청크만.

--- 유효한 JSON 생성 규칙 ---
- JSON 외의 부가 설명·문장을 함께 반환하지 말라(JSON만 출력).
- JSON을 backtick(```)으로 감싸지 말라.
- 전체를 list로 감싸지 말라 — 최상위는 nodes/relationships를 가진 하나의 JSON 객체다.
- property 이름은 반드시 큰따옴표로 감싼다.

예시:
{examples}

--- 지금까지의 배경 컨텍스트(참고용) ---
아래는 이전 회차까지의 그래프 덤프와 줄거리 요약이다(첫 회차면 비어 있으니 무시).
- 별칭 정합: 이번 회차의 대상이 다른 호칭으로 불려도, 배경 그래프에 같은 대상이 있으면 그 name을
  그대로 써서 같은 노드로 추출한다(새 이름으로 분리 금지).
- 상태 갱신: 배경의 CharacterState가 이번 회차에 바뀌면 기존 노드를 고치지 말고 새 노드를 만든다.
  단, 배경에 이미 있는 상태가 이번 회차에도 그대로 유지되면(값이 안 바뀌면) 다시 만들지 않는다 —
  같은 사실을 회차마다 중복 생성하지 말고, 값이 실제로 바뀔 때만 새 노드를 낸다.
- 충돌 시 새 회차 원문을 우선한다 — 배경에 맞추려 사실을 왜곡하지 말 것(모순은 그대로 둬야 나중에 탐지된다).
{novel_context}

입력 텍스트:

{text}
"""
    EXPECTED_INPUTS = ["text"]

    def format(
        self,
        schema: dict[str, Any],
        examples: str,
        text: str = "",
        novel_context: str = "",
    ) -> str:
        """
        네 값(schema/examples/text/novel_context)을 모두 템플릿에 채워 렌더한다.

        부모 ERExtractionTemplate.format은 novel_context 인자를 받지 않으므로(그대로 위임하면
        TypeError) 조부모 PromptTemplate.format을 직접 호출해 네 값을 모두 전달한다.
        """
        return PromptTemplate.format(
            self,
            text=text,
            schema=schema,
            examples=examples,
            novel_context=novel_context,
        )


class NovelContextExtractor(LLMEntityRelationExtractor):
    """
    청크 추출 프롬프트에 누적 컨텍스트(novel_context)를 주입하는 extractor.

    KoreanWebNovelERTemplate의 {novel_context} placeholder는 라이브러리 run 경로가 채워주지
    않는다(라이브러리는 text/schema/examples만 넘긴다). 이 클래스가 그 빈칸을 인스턴스에
    저장해 둔 self.novel_context로 배선한다.
    """

    def __init__(self, *args: Any, novel_context: str = "", **kwargs: Any) -> None:
        # 나머지 인자(llm/prompt_template/use_structured_output/on_error 등)는 부모에 위임한다.
        super().__init__(*args, **kwargs)
        self.novel_context = novel_context

    @validate_call
    async def run(
        self,
        chunks: TextChunks,
        document_info: Optional[DocumentInfo] = None,
        lexical_graph_config: Optional[LexicalGraphConfig] = None,
        schema: Optional[GraphSchema] = None,
        examples: str = "",
        **kwargs: Any,
    ) -> Neo4jGraph:
        """
        부모 LLMEntityRelationExtractor.run에 그대로 위임한다.

        Component 메타클래스가 서브클래스 본문에 run 정의를 요구하고(상속만으로는 인정 안 함),
        run 시그니처에서 파이프라인 입력 파라미터(chunks/schema/examples 등)를 읽어 배선한다.
        따라서 부모와 동일한 시그니처를 재선언해 위임만 한다.
        """
        return await super().run(
            chunks,
            document_info=document_info,
            lexical_graph_config=lexical_graph_config,
            schema=schema,
            examples=examples,
            **kwargs,
        )

    async def extract_for_chunk(
        self, schema: GraphSchema, examples: str, chunk: TextChunk
    ) -> Neo4jGraph:
        """Run entity extraction for a given text chunk.

        neo4j_graphrag 1.18.0의 원본 메서드를 그대로 복제하되, prompt_template.format 호출에
        novel_context=self.novel_context 인자만 추가했다(V2/V1 분기 로직은 원본 그대로 유지).
        """
        prompt = self.prompt_template.format(
            text=chunk.text,
            schema=schema.model_dump(exclude_none=True),
            examples=examples,
            novel_context=self.novel_context,
        )

        # Use structured output (V2) if enabled
        if self.use_structured_output:
            # Capability check
            # This should never happen due to __init__ validation
            if not self.llm.supports_structured_output:
                raise RuntimeError(
                    f"Structured output is not supported by {type(self.llm).__name__}"
                )

            messages = [LLMMessage(role="user", content=prompt)]
            llm_result = await self.llm.ainvoke(messages, response_format=Neo4jGraph)  # type: ignore[call-arg, arg-type]
            try:
                chunk_graph = Neo4jGraph.model_validate_json(llm_result.content)
            except ValidationError as e:
                if self.on_error == OnError.RAISE:
                    raise LLMGenerationError("LLM response has improper format") from e
                logger.error(
                    f"LLM response has improper format for chunk_index={chunk.index}"
                )
                logger.debug(f"Invalid response: {llm_result.content}")
                chunk_graph = Neo4jGraph()
            return chunk_graph

        # Use V1 prompt-based JSON extraction (default)
        llm_result = await self.llm.ainvoke(prompt)
        try:
            llm_generated_json = fix_invalid_json(llm_result.content)
            result = json.loads(llm_generated_json)
        except (json.JSONDecodeError, InvalidJSONError) as e:
            if self.on_error == OnError.RAISE:
                raise LLMGenerationError("LLM response is not valid JSON") from e
            logger.error(
                f"LLM response is not valid JSON for chunk_index={chunk.index}"
            )
            logger.debug(f"Invalid JSON: {llm_result.content}")
            result = {"nodes": [], "relationships": []}
        try:
            chunk_graph = Neo4jGraph.model_validate(result)
        except ValidationError as e:
            if self.on_error == OnError.RAISE:
                raise LLMGenerationError("LLM response has improper format") from e
            logger.error(
                f"LLM response has improper format for chunk_index={chunk.index}"
            )
            logger.debug(f"Invalid JSON format: {result}")
            chunk_graph = Neo4jGraph()
        return chunk_graph
