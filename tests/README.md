# Tests

Test suite for Maneiro.ai

## Run Tests

```bash
pytest
```

## With Coverage

```bash
pytest --cov=app tests/
```

## Structure

```
tests/
├── conftest.py        # Test fixtures
├── test_auth.py       # Authentication tests
├── test_api.py        # API endpoint tests
└── test_models.py     # Database model tests
```
