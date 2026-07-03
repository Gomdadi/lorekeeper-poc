"""
TextSplitter 후보 모음.

neo4j-graphrag의 `TextSplitter` 인터페이스(`async def run(self, text) -> TextChunks`)를
구현하는 한국어용 커스텀 스플리터와, 챕터 마커를 청크에 주입하는 래퍼를 정의한다.

- `KiwiSentenceSplitter` : kiwipiepy 형태소 기반 문장 분리
- `KSSSentenceSplitter`   : KSS(Korean Sentence Splitter) 문장 분리
- `make_recursive_splitter`: LangChain RecursiveCharacterTextSplitter 어댑터 헬퍼
- `ChapterTaggingSplitter`: 원문을 `【N화】` 마커 단위로 선분할한 뒤 내부 splitter로 자르고,
                            모든 청크 앞에 해당 화 마커를 prefix한다. 마커 없는 중간 청크가
                            Event.chapter/story_order를 못 채우는 공백을 막고, 모든 변형에
                            동일 적용되어 OFAT 비교 공정성을 유지한다.

FixedSizeSplitter는 라이브러리 기본 컴포넌트를 그대로 쓰므로 여기 정의하지 않는다.
"""

from __future__ import annotations

import re

from neo4j_graphrag.experimental.components.text_splitters.base import TextSplitter
from neo4j_graphrag.experimental.components.text_splitters.langchain import (
    LangChainTextSplitterAdapter,
)
from neo4j_graphrag.experimental.components.types import TextChunk, TextChunks

# 모든 splitter가 공유하는 목표 청크 크기(글자 수)와 겹침.
# 값을 통일해야 "경계 전략의 차이"만 비교되고 "청크 크기의 차이"가 섞이지 않는다.
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

# 챕터 마커 정규식: 【3화】, 【 12 화】 등. 숫자만 회차로 인정한다.
_CHAPTER_MARKER = re.compile(r"【\s*\d+\s*화\s*】")


def make_recursive_splitter() -> LangChainTextSplitterAdapter:
    """
    LangChain RecursiveCharacterTextSplitter를 라이브러리 어댑터로 감싼 인스턴스를 만든다.
    구분자 우선순위(문단→줄→공백)로 자르므로 언어 무관하게 문장 잘림을 줄인다.
    """
    # 지연 import: langchain-text-splitters 미설치 환경에서 모듈 로드 자체가 실패하지 않도록.
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return LangChainTextSplitterAdapter(
        RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )
    )


def _group_sentences(
    sentences: list[str], chunk_size: int, overlap: int
) -> list[str]:
    """
    문장 리스트를 목표 글자 수(chunk_size)까지 이어 붙여 청크 문자열 목록으로 만든다.
    다음 청크는 직전 청크의 끝 문장들(합계 약 overlap 글자)을 다시 포함해 문맥을 잇는다.
    문장 경계로만 자르므로 문장이 중간에 잘리지 않는다.
    """
    chunks: list[str] = []
    current: list[str] = []          # 현재 청크에 담긴 문장들
    current_len = 0                  # 현재 청크의 누적 글자 수

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        # 현재 청크에 이 문장을 더하면 목표를 넘고, 이미 담긴 문장이 있으면 청크를 확정한다.
        if current and current_len + len(sent) > chunk_size:
            chunks.append(" ".join(current))
            # 겹침: 끝에서부터 문장을 되짚어 약 overlap 글자만큼 다음 청크의 시작으로 넘긴다.
            carried: list[str] = []
            carried_len = 0
            for prev in reversed(current):
                if carried_len + len(prev) > overlap:
                    break
                carried.insert(0, prev)
                carried_len += len(prev)
            current = carried
            current_len = carried_len
        current.append(sent)
        current_len += len(sent)

    if current:
        chunks.append(" ".join(current))
    return chunks


class KiwiSentenceSplitter(TextSplitter):
    """kiwipiepy 형태소 분석기의 문장 분리로 청크를 만드는 스플리터."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        # 지연 import: kiwipiepy 미설치 환경에서도 이 모듈 자체는 로드되게 한다.
        from kiwipiepy import Kiwi

        self.kiwi = Kiwi()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    async def run(self, text: str) -> TextChunks:
        # split_into_sents는 Sentence 객체 리스트를 반환하므로 .text로 문자열만 추출한다.
        sentences = [s.text for s in self.kiwi.split_into_sents(text)]
        pieces = _group_sentences(sentences, self.chunk_size, self.chunk_overlap)
        return TextChunks(
            chunks=[TextChunk(text=p, index=i) for i, p in enumerate(pieces)]
        )


class KSSSentenceSplitter(TextSplitter):
    """KSS(Korean Sentence Splitter)의 문장 분리로 청크를 만드는 스플리터."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        # kss는 import 비용이 크므로 인스턴스 생성 시점에 지연 import한다.
        import kss

        self._split = kss.split_sentences
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    async def run(self, text: str) -> TextChunks:
        sentences = list(self._split(text))
        pieces = _group_sentences(sentences, self.chunk_size, self.chunk_overlap)
        return TextChunks(
            chunks=[TextChunk(text=p, index=i) for i, p in enumerate(pieces)]
        )


class ChapterTaggingSplitter(TextSplitter):
    """
    내부 splitter를 감싸, 청크마다 소속 챕터 마커(【N화】)를 prefix하는 래퍼.

    동작:
    1. 원문을 `【N화】` 마커 경계로 (마커, 본문) 세그먼트들로 나눈다.
    2. 각 세그먼트 본문을 내부 splitter로 자른다(세그먼트가 화 경계를 넘지 않게 됨).
    3. 각 청크 텍스트 앞에 그 화의 마커를 붙이고 전체 index를 다시 매긴다.

    첫 마커 이전의 도입부처럼 마커가 없는 세그먼트는 prefix 없이 그대로 둔다.
    """

    def __init__(self, inner: TextSplitter):
        self.inner = inner

    @staticmethod
    def _segments(text: str) -> list[tuple[str | None, str]]:
        """
        원문을 (마커 or None, 본문) 튜플 목록으로 분해한다.
        마커는 다음 마커가 나오기 전까지의 본문에 적용된다.
        """
        segments: list[tuple[str | None, str]] = []
        last_end = 0
        current_marker: str | None = None
        for m in _CHAPTER_MARKER.finditer(text):
            # 직전 마커 위치부터 이번 마커 시작까지가 current_marker의 본문이다.
            body = text[last_end:m.start()]
            if body.strip():
                segments.append((current_marker, body))
            current_marker = m.group().strip()
            last_end = m.end()
        # 마지막 마커 이후의 꼬리 본문.
        tail = text[last_end:]
        if tail.strip():
            segments.append((current_marker, tail))
        return segments

    async def run(self, text: str) -> TextChunks:
        all_chunks: list[TextChunk] = []
        index = 0
        for marker, body in self._segments(text):
            sub = await self.inner.run(body)
            for chunk in sub.chunks:
                # 마커가 있으면 청크 맨 앞에 붙여 LLM이 회차를 인지하게 한다.
                tagged = f"{marker}\n{chunk.text}" if marker else chunk.text
                all_chunks.append(TextChunk(text=tagged, index=index))
                index += 1
        return TextChunks(chunks=all_chunks)
