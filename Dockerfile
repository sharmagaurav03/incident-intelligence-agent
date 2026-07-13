FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
COPY config/ config/
COPY data/ data/
COPY sample_service/ sample_service/
ENV PYTHONPATH=/app/src
# Non-root: the agent needs no privileges.
RUN useradd -m agent && chown -R agent /app
USER agent
EXPOSE 8080
CMD ["python", "-m", "triage_agent", "serve", "--config", "config/default.yaml"]
