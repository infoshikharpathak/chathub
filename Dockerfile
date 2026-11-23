FROM python:3.12-slim

WORKDIR /app

# git is needed for pip to fetch the garrison git dependency (and the anvil-ai / agent-forge it pins).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Needs OPENAI_API_KEY, CHATHUB_FOODDASH_KEY (the ak_ key FoodDash issued) and
# CHATHUB_IRONCLAD_POLICY_KEY (the public pk_ key Ironclad issued), e.g.
#   docker run -e OPENAI_API_KEY -e CHATHUB_FOODDASH_KEY -e CHATHUB_IRONCLAD_POLICY_KEY \
#     -e FOODDASH_AGENT_URL=http://host.docker.internal:8100 \
#     -e IRONCLAD_BASE_URL=http://host.docker.internal:8001 -p 8200:8200 chathub
EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
