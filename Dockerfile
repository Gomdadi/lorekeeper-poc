FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY naive_indexing/ ./naive_indexing/

CMD ["python", "-m", "naive_indexing.main", "--input", "graphrag_workspace/input/manuscript.txt"]
