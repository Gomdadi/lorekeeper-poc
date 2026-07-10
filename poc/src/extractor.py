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
- 회차 마커: 청크 앞에 [N화] 형태의 회차 표시가 있으면 그 숫자를 읽어, 그 회차에서 발생한
  각 Event의 chapter 속성에 넣는다. story_order는 원문에 명시적 시간 묘사가 없으면 chapter와
  같은 값(소수점 형태, 예: 3.0)을 쓰고, '3년 전' 같은 상대 시간 묘사가 있으면 그 순서에 맞는
  값으로 변환한다.
- 시간에 따라 변하고 나중에 다른 회차와 대조해 모순 여부를 판정해야 하는 사실
  (신체 부상, 생사 여부, 소속 변경, 능력·무공 습득/성장, 소지품 획득/상실)은 Character의
  속성으로 넣지 말고 CharacterState 노드로 따로 만든다. 상태가 바뀌면 기존 노드를 고치지 않고
  항상 새 CharacterState 노드를 만든다.
- CharacterState는 HAS_STATE로 해당 인물(Character)과 잇고, ESTABLISHED_IN으로 그 상태가
  성립된 사건(Event)과 잇는다.
- 능력(스킬·무공)과 소지품은 별도 노드로 만들지 않고 CharacterState의 attribute로 표현한다.
  소지품 소유가 인물 간 이동하면, 넘겨준 인물에는 value '상실', 받은 인물에는 value '보유'로
  각각 별도 CharacterState를 만든다.
- 장소 계층은 LOCATED_IN으로 한 단계씩만 잇는다(요새→도시→왕국). 단계를 건너뛰지 않는다.
- 조직·세력·문파는 Organization 노드로 만들고, 인물의 현재 소속은 MEMBER_OF로 잇는다.
- 인물과 인물 사이에 서사상 의미 있는 관계가 드러나면 놓치지 말고 RELATED_TO로 잇고, 관계 종류를
  type 속성에 담는다(예: 사제·동맹·적대·혈연·연인, 그리고 작가-독자·동료·사수처럼 직업적·일상적
  관계도 포함). 두 인물이 같은 장면에서 상호작용하거나 한쪽이 다른 쪽을 특정한 관계로 대하면,
  어떤 관계인지 적극적으로 판단해 RELATED_TO를 만든다 — 단순히 함께 등장했다는 이유만으로는 만들지
  않되, 관계의 성격이 드러나면 반드시 추출한다.
- attribute/value는 한국어로 짧게 쓴다. 같은 축(axis)의 상태는 매번 같은 표현·같은 입도로
  통일한다(생사 여부는 항상 '생사', 신체 부위는 '오른팔'처럼 부위 단위로 — '오른팔_부상'처럼
  쪼개지 않는다). value도 '상실', '온전함', '생존', '사망'처럼 짧은 상태 표현으로만 쓴다.

--- 지금까지의 배경 컨텍스트(참고용) ---
아래는 이전 회차까지 누적된 그래프 덤프와 줄거리 요약이다. 다음 용도로 참고하라.
- 별칭 정합: 이번 회차의 인물·장소·조직이 다른 호칭(별명·존칭·호칭 변화 등)으로 불려도, 배경
  그래프에 동일 대상으로 보이는 노드가 있으면 그 노드에 있는 name을 **그대로** 써서 같은 노드로
  추출하라(새 이름으로 분리하지 말라). 배경 그래프에 없는 완전히 새로운 대상만 새 노드로 만든다.
- 상태 갱신: 배경 그래프에 있는 상태(CharacterState)가 이번 회차에 바뀌면, 기존 노드를 고치지
  말고 새 CharacterState 노드를 만들어 갱신한다(과거 상태는 그대로 두어 시점별 이력을 보존).
중요: 이 배경 컨텍스트와 아래 새 회차 원문이 서로 충돌하면, **새 회차 원문을 진실의 원천으로
우선**하라. 기존 컨텍스트에 맞추려고 새 회차의 사실을 왜곡하거나 보정하지 말라 — 모순은 있는
그대로 추출되어야 나중에 충돌로 탐지된다. 배경 컨텍스트가 비어 있으면(첫 회차) 이 섹션을 무시하라.
{novel_context}

--- 유효한 JSON 생성 규칙 ---
- JSON 외의 부가 설명·문장을 함께 반환하지 말라(JSON만 출력).
- JSON을 backtick(```)으로 감싸지 말라.
- 전체를 list로 감싸지 말라 — 최상위는 nodes/relationships를 가진 하나의 JSON 객체다.
- property 이름은 반드시 큰따옴표로 감싼다.

예시:
{examples}

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
