# Local development helpers. PYTHONPATH makes the workspace packages importable even when macOS marks uv's
# editable-install .pth files as hidden (Python 3.12+ skips hidden .pth files).
export PYTHONPATH := packages/common/src:services/llm-gateway/src:services/catalog-mcp/src:services/content-engine/src
RUN := uv run --no-sync

.PHONY: setup lint test up down logs eval
setup:
	uv sync
lint:
	$(RUN) ruff check .
test:
	$(RUN) pytest -q
up:
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f --tail=100
eval:
	$(RUN) python -m evals.run
