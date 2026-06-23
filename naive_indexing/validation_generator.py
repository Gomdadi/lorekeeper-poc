import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

CLIENT = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5-20251001"

PROMPT = """다음 소설 텍스트를 읽고, 등장인물·장소·사건·아이템·관계에 관한 Q&A 검증 세트를 생성하세요.

규칙:
- 텍스트에 명확히 나와 있는 사실만 질문으로 만드세요.
- 질문은 구체적이고 단답형으로 답할 수 있어야 합니다.
- 10~15개 생성하세요.

[텍스트]
{text}

JSON 배열로만 응답하세요 (다른 설명 없이):
[
  {{"query": "질문", "label": "예상 답변"}},
  ...
]"""


def generate_validation_set(manuscript_text: str) -> list[dict]:
    response = CLIENT.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT.format(text=manuscript_text)}],
    )

    raw = response.content[0].text.strip()

    # ```json ... ``` 블록 제거
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())
