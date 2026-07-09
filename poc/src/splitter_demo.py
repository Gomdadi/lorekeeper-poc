"""
FixedSize / Kiwi / KSS 세 splitter를 동일한 예시 텍스트에 돌려
청크 결과를 Markdown 파일로 덤프하는 간단 데모 스크립트.

경계 전략의 차이만 눈으로 비교하려는 용도이므로, 챕터 태깅 래퍼 없이
각 splitter를 raw로 돌린다. 실행:  uv run python -m src.splitter_demo
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from neo4j_graphrag.experimental.components.text_splitters.fixed_size_splitter import (
    FixedSizeSplitter,
)

from src.splitters import (
    KiwiSentenceSplitter,
    KSSSentenceSplitter,
)

# 데모 전용 작은 청크 크기. 원문이 짧아 실서비스 값(1000)에서는 분할이 일어나지 않아
# 셋의 차이가 안 보이므로, 일부러 작게 잡아 FixedSize(문자 경계)와
# Kiwi/KSS(문장 경계)의 잘림 방식 차이를 드러낸다.
CHUNK_SIZE = 100
CHUNK_OVERLAP = 20

# 세 splitter의 경계 차이가 드러나도록 문장 길이가 들쭉날쭉한 짧은 무협 예시.
SAMPLE_TEXT = (
    "무당산 정상에 눈이 내렸다. 청운은 검을 뽑아 허공을 갈랐다. "
    "그의 스승 현진자는 삼십 년 전 마교와의 혈전에서 한쪽 팔을 잃었으나, "
    "그 상처는 오히려 그를 강호제일의 검객으로 단련시켰다. "
    "\"제자야, 검은 손이 아니라 마음으로 쥐는 것이다.\" 스승의 목소리가 귓가에 맴돌았다. "
    "청운은 눈을 감았다. 바람 소리, 눈이 쌓이는 소리, 자신의 심장 박동까지 또렷이 들렸다. "
    "그 순간 산 아래에서 살기가 피어올랐다. 마교의 척후 셋이 능선을 타고 오르고 있었다. "
    "청운은 천천히 자세를 낮췄다. 첫 번째 초식은 방어였고, 두 번째는 반격이었다. "
    "검이 빛보다 빠르게 움직였다. 눈발 속에서 붉은 피가 흩날렸다. "
    "싸움은 짧았다. 세 명의 척후는 무당산의 눈 위에 쓰러졌고, 청운은 다시 검을 거뒀다."
)


async def main() -> None:
    # (이름, splitter 인스턴스) 목록. 생성 비용이 있는 Kiwi/KSS는 여기서 한 번만 만든다.
    splitters = [
        ("FixedSizeSplitter", FixedSizeSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)),
        ("KiwiSentenceSplitter", KiwiSentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)),
        ("KSSSentenceSplitter", KSSSentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)),
    ]

    lines: list[str] = [
        "# Splitter 비교 데모",
        "",
        f"- chunk_size = {CHUNK_SIZE}, chunk_overlap = {CHUNK_OVERLAP}",
        f"- 원문 길이 = {len(SAMPLE_TEXT)}자",
        "",
        "## 원문",
        "",
        "```",
        SAMPLE_TEXT,
        "```",
        "",
    ]

    for name, splitter in splitters:
        result = await splitter.run(SAMPLE_TEXT)
        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"청크 개수: {len(result.chunks)}")
        lines.append("")
        for chunk in result.chunks:
            lines.append(f"### chunk {chunk.index} ({len(chunk.text)}자)")
            lines.append("")
            lines.append("```")
            lines.append(chunk.text)
            lines.append("```")
            lines.append("")

    out_path = Path(__file__).resolve().parents[1] / "output" / "splitter_demo.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"결과 저장: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
