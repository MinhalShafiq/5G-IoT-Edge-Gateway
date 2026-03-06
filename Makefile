.PHONY: help start dev up down logs test lint clean build

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

start: ## One-command start: all services + monitoring + dashboard
	bash start.sh

dev: ## Start local development stack
	docker compose up -d

up: ## Start all services (build first)
	docker compose up --build -d

down: ## Stop all services
	docker compose down

logs: ## Tail all service logs
	docker compose logs -f

test: ## Run all tests
	python -m pytest tests/ -v

test-unit: ## Run unit tests only
	python -m pytest tests/unit/ -v

test-integration: ## Run integration tests
	docker compose -f docker-compose.test.yml up -d
	python -m pytest tests/integration/ -v
	docker compose -f docker-compose.test.yml down

lint: ## Run linters
	python -m ruff check .
	python -m ruff format --check .

format: ## Auto-format code
	python -m ruff format .
	python -m ruff check --fix .

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name *.egg-info -exec rm -rf {} + 2>/dev/null || true

build: ## Build all Docker images
	docker compose build

simulate: ## Run IoT device simulator
	docker compose --profile simulate up -d simulator

protos: ## Compile protobuf definitions
	bash scripts/generate-protos.sh
