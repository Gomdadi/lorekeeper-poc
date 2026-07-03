"""
PoC 엔트리포인트. Indexing 검증 harness(src/indexing_eval.py)를 실행한다.

src/ 모듈들은 서로 bare import(`from client import ...`)로 연결돼 있으므로,
src 디렉토리를 sys.path에 올린 뒤 harness의 main을 호출한다.

    cd poc && uv run python main.py
(동일하게 `uv run python src/indexing_eval.py`로 직접 실행해도 된다.)
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from indexing_eval import main as run_eval  # noqa: E402


def main():
    asyncio.run(run_eval())


if __name__ == "__main__":
    main()
