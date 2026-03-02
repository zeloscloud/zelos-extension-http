set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

# Install dependencies
install:
    uv sync --extra dev
    uv run pre-commit install

# Install dependencies for CI (no pre-commit hooks)
ci-install:
    uv sync --extra dev

# Format code
format:
    uv run ruff format .
    uv run ruff check --fix .

# Run checks
check:
    uv run ruff check .

# Run tests
test:
    uv run pytest

# Run extension locally
dev:
    uv run python main.py

# Clean build artifacts
clean:
    rm -rf dist build .pytest_cache .ruff_cache *.tar.gz .artifacts
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
