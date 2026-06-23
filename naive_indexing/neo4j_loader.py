import os

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


def _get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def load_to_neo4j(parquet_paths: dict) -> dict:
    entities_df = pd.read_parquet(parquet_paths["entities"])
    relationships_df = pd.read_parquet(parquet_paths["relationships"])

    # GraphRAG 버전에 따라 컬럼명이 title 또는 name
    name_col = "title" if "title" in entities_df.columns else "name"

    driver = _get_driver()
    with driver.session() as session:
        # 기존 데이터 초기화
        session.run("MATCH (n) DETACH DELETE n")

        # Full-text index 생성 (이미 존재하면 무시)
        session.run("""
            CREATE FULLTEXT INDEX entity_name_idx IF NOT EXISTS
            FOR (e:Entity) ON EACH [e.name, e.description]
        """)

        # 엔티티 로드
        node_count = 0
        for _, row in entities_df.iterrows():
            session.run(
                """
                MERGE (e:Entity {id: $id})
                SET e.name = $name,
                    e.type = $type,
                    e.description = $description
                """,
                id=str(row["id"]),
                name=str(row.get(name_col, "")),
                type=str(row.get("type", "UNKNOWN")),
                description=str(row.get("description", "")),
            )
            node_count += 1

        # 관계 로드 (source/target은 엔티티 name 값)
        edge_count = 0
        for _, row in relationships_df.iterrows():
            result = session.run(
                """
                MATCH (a:Entity {name: $source}), (b:Entity {name: $target})
                MERGE (a)-[r:RELATES_TO]->(b)
                SET r.description = $description,
                    r.weight = $weight
                RETURN r
                """,
                source=str(row.get("source", "")),
                target=str(row.get("target", "")),
                description=str(row.get("description", "")),
                weight=float(row.get("weight", 1.0)),
            )
            if result.single():
                edge_count += 1

    driver.close()
    return {"nodes": node_count, "edges": edge_count}


def get_graph_structure() -> dict:
    driver = _get_driver()
    with driver.session() as session:
        nodes = session.run(
            """
            MATCH (e:Entity)
            RETURN e.name AS name, e.type AS type, e.description AS description
            ORDER BY e.type, e.name
            """
        ).data()

        edges = session.run(
            """
            MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
            RETURN a.name AS source, b.name AS target, r.description AS description
            ORDER BY a.name
            """
        ).data()

    driver.close()
    return {"nodes": nodes, "edges": edges}
