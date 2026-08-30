# Makes `tests/` a proper Python package so `from .conftest import ...`
# (relative imports) works reliably regardless of pytest's import-mode -
# see pytest.ini and CLAUDE.md "Test Suite" for why this was needed.
