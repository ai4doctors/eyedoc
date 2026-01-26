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
├── test_auth.py       # Authentication tests
├── test_api.py        # API endpoint tests
├── test_models.py     # Database model tests
└── conftest.py        # Test fixtures
```

## TODO

- [ ] Add test_auth.py
- [ ] Add test_api.py
- [ ] Add test_models.py
- [ ] Add conftest.py
- [ ] Setup CI/CD with GitHub Actions
