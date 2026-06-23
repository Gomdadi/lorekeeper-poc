import os
import subprocess
import shutil
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

GRAPHRAG_ROOT = Path("graphrag_workspace")


def run_graphrag(manuscript_path: Path) -> dict:
    # 입력 파일을 graphrag input 디렉토리로 복사 (같은 파일이면 건너뜀)
    input_dir = GRAPHRAG_ROOT / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    dest = input_dir / manuscript_path.name
    if manuscript_path.resolve() != dest.resolve():
        shutil.copy(manuscript_path, dest)

    # GRAPHRAG_API_KEY = OPENAI_API_KEY (graphrag가 이 변수명을 사용)
    os.environ["GRAPHRAG_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")

    # settings.yaml 없으면 graphrag init 실행
    # graphrag 3.x는 settings.yaml (yml 아님)
    settings_path = GRAPHRAG_ROOT / "settings.yaml"
    if not settings_path.exists():
        print("  graphrag init 실행 중...")
        # 두 개의 인터랙티브 프롬프트(chat model, embedding model)에 자동 응답
        subprocess.run(
            ["graphrag", "init", "--root", str(GRAPHRAG_ROOT)],
            input="gpt-4o-mini\ntext-embedding-3-small\n",
            text=True,
            check=True,
        )
        _patch_settings(settings_path)

    # graphrag index
    print("  graphrag index 실행 중 (수 분 소요)...")
    subprocess.run(
        ["graphrag", "index", "--root", str(GRAPHRAG_ROOT)],
        check=True,
    )

    return _find_parquet_paths()


def _patch_settings(settings_path: Path):
    # yaml.safe_load는 주석을 날리므로 텍스트 치환 방식 사용
    text = settings_path.read_text(encoding="utf-8")

    # entity_types 고정 (graphrag 3.x 포맷: extract_graph 하위)
    # 기본값: [organization,person,geo,event] → 우리 용도에 맞게 변경
    text = text.replace(
        "entity_types: [organization,person,geo,event]",
        "entity_types: [person,place,event,item,organization]",
    )

    settings_path.write_text(text, encoding="utf-8")
    print("  settings.yaml 패치 완료 (entity_types 고정)")


def _find_parquet_paths() -> dict:
    output_dir = GRAPHRAG_ROOT / "output"

    # graphrag 3.x: output/ 바로 아래
    entities = output_dir / "create_final_entities.parquet"
    if entities.exists():
        return {
            "entities": str(entities),
            "relationships": str(output_dir / "create_final_relationships.parquet"),
        }

    # 일부 버전: output/{timestamp}/artifacts/ 구조
    if output_dir.exists():
        for run_dir in sorted(output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            artifacts = run_dir / "artifacts"
            entities = artifacts / "create_final_entities.parquet"
            if entities.exists():
                return {
                    "entities": str(entities),
                    "relationships": str(artifacts / "create_final_relationships.parquet"),
                }

    raise FileNotFoundError(
        f"parquet 파일을 찾을 수 없습니다. {output_dir} 디렉토리를 확인하세요."
    )
