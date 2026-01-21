# Python Code Cleanup Tasks

This document identifies potential code cleanup and improvement tasks for future consideration.

## Completed ✅
- [x] Extract merge logic into `merge_service.py` module

## High Priority

### 1. Add Type Hints Throughout Codebase
**Files**: All Python files
**Effort**: Medium

Many functions lack proper type hints. Adding them improves IDE support and catches errors early.

**Example locations**:
- `firefly_client.py` - Most functions lack return type hints
- `matcher.py` - Several functions need parameter type hints
- `utils.py` - Decorators and utility functions

**Benefits**:
- Better IDE autocomplete
- Type checking with mypy
- Self-documenting code

### 2. Extract Transaction Data Extraction Helper
**File**: `merge_service.py`, `matcher.py`
**Effort**: Low

The pattern `transaction.get("attributes", {}).get("transactions", [{}])[0]` is repeated many times.

**Proposed helper**:
```python
def get_transaction_split(transaction: dict, index: int = 0) -> dict:
    """Extract transaction split from API response."""
    return transaction.get("attributes", {}).get("transactions", [{}])[index]
```

**Benefits**:
- DRY principle
- Less error-prone
- Single place to update if API changes

### 3. Extract Search Filtering Logic
**File**: `main.py` - `/search` endpoint (lines ~175-245)
**Effort**: Medium

The search endpoint is long and handles multiple concerns. Extract filtering logic.

**Proposed refactoring**:
```python
# In new search_service.py
def filter_deposits_by_accounts(deposits: list[dict], account_ids: set[str]) -> list[dict]:
    """Filter deposits to only those matching account IDs."""
    ...

def sort_transactions_by_date(transactions: list[dict], descending: bool = True) -> list[dict]:
    """Sort transactions by date."""
    ...
```

**Benefits**:
- Testable filtering logic
- Reusable components
- Cleaner search endpoint

## Medium Priority

### 4. Add Docstrings to All Public Functions
**Files**: All Python files
**Effort**: Low-Medium

Many functions have minimal or no docstrings.

**Target format** (Google style):
```python
def function_name(param1: str, param2: int) -> bool:
    """Short description.

    Longer description if needed.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value

    Raises:
        ValueError: When X condition occurs
    """
```

**Priority functions**:
- All public API in `merge_service.py`
- All endpoints in `main.py`
- Complex matcher functions in `matcher.py`

### 5. Improve Error Messages
**Files**: `main.py`, `merge_service.py`
**Effort**: Low

Some error messages could be more descriptive.

**Example improvements**:
```python
# Before
return {"error": "Session expired", "status": "error"}

# After
return {
    "error": "Your session has expired. Please log in again.",
    "status": "error",
    "error_code": "SESSION_EXPIRED"
}
```

**Benefits**:
- Better user experience
- Easier debugging
- Consistent error format

### 6. Add Configuration Module
**Files**: New `config.py`, `main.py`, `merge_service.py`
**Effort**: Medium

Environment variables and magic numbers scattered throughout code.

**Proposed structure**:
```python
# config.py
from pydantic import BaseSettings

class Settings(BaseSettings):
    SESSION_SECRET_KEY: str = None
    DEBUG: bool = False
    JOB_CLEANUP_INTERVAL: int = 300  # seconds
    JOB_RETENTION_TIME: int = 3600  # seconds
    POLL_INTERVAL: int = 500  # milliseconds

    class Config:
        env_file = ".env"

settings = Settings()
```

**Benefits**:
- Centralized configuration
- Type validation with Pydantic
- Easy to override with env vars
- Self-documenting settings

### 7. Extract Account Filtering Logic
**File**: `main.py` - `/search` endpoint
**Effort**: Low

The account filtering logic is inline and could be a helper function.

**Current code** (lines ~216-225):
```python
if account_id:
    account_ids = set(account_id)
    deposits = [
        d
        for d in deposits
        if d.get("attributes", {})
        .get("transactions", [{}])[0]
        .get("destination_id")
        in account_ids
    ]
```

**Proposed**:
```python
# In search_service.py or utils.py
def filter_by_destination_accounts(deposits: list[dict], account_ids: list[str]) -> list[dict]:
    """Filter deposits by destination account IDs."""
    account_id_set = set(account_ids)
    return [
        d for d in deposits
        if get_transaction_split(d).get("destination_id") in account_id_set
    ]
```

## Low Priority

### 8. Add Logging Levels Consistently
**Files**: All Python files
**Effort**: Low

Some logs use proper levels (info, error, debug), others don't.

**Review and standardize**:
- `logger.info()` - Normal operations
- `logger.warning()` - Potential issues
- `logger.error()` - Errors that need attention
- `logger.debug()` - Detailed debugging info (only when DEBUG=True)

### 9. Extract JSON Serialization Logic
**File**: `main.py` - `/search` endpoint (lines ~231-241)
**Effort**: Low

The alternatives JSON serialization is inline and could be a helper.

**Proposed**:
```python
# In matcher.py or utils.py
def serialize_alternatives(alternatives: list[WithdrawalMatch]) -> str:
    """Serialize alternative matches to JSON string for template."""
    alternatives_dicts = [
        {
            "withdrawal": alt.withdrawal,
            "withdrawal_split": alt.withdrawal_split,
            "days_apart": alt.days_apart,
        }
        for alt in alternatives
    ]
    return json.dumps(alternatives_dicts, default=json_serial)
```

### 10. Add Constants File
**Files**: New `constants.py`
**Effort**: Low

Magic numbers and strings scattered throughout code.

**Proposed**:
```python
# constants.py
# HTTP Status codes
HTTP_302_REDIRECT = 302

# Job retention
JOB_CLEANUP_INTERVAL_SECONDS = 300
JOB_RETENTION_SECONDS = 3600

# Polling
DEFAULT_POLL_INTERVAL_MS = 500

# Search defaults
DEFAULT_SEARCH_DAYS = 30
DEFAULT_BUSINESS_DAYS = 5
DEFAULT_SEARCH_LIMIT = 50
DEFAULT_ORDER = "desc"
```

### 11. Add Validators for API Inputs
**File**: New `validators.py` or in existing modules
**Effort**: Low-Medium

Add input validation for form data.

**Example**:
```python
def validate_date_range(start: date, end: date) -> None:
    """Validate that date range is reasonable."""
    if start > end:
        raise ValueError("Start date must be before end date")

    days_apart = (end - start).days
    if days_apart > 365:
        raise ValueError("Date range cannot exceed 365 days")

    if days_apart < 0:
        raise ValueError("Invalid date range")
```

### 12. Add Unit Tests
**Files**: New `tests/` directory
**Effort**: High

Currently no tests. Add pytest-based tests for:

**Priority test targets**:
- `merge_service.py` - All merge functions
- `matcher.py` - Matching logic
- `utils.py` - Utility functions
- `firefly_client.py` - API client (with mocking)

**Test structure**:
```
tests/
├── __init__.py
├── conftest.py  # Fixtures
├── test_merge_service.py
├── test_matcher.py
├── test_utils.py
└── test_firefly_client.py
```

## Future Enhancements (Nice to Have)

### 13. Add Structured Logging
**Effort**: Medium

Replace print-style logging with structured JSON logs.

**Example with structlog**:
```python
logger.info(
    "merge_job_completed",
    job_id=job_id,
    deposit_id=deposit_id,
    withdrawal_id=withdrawal_id,
    duration_ms=duration
)
```

**Benefits**:
- Easier log parsing
- Better monitoring/alerting
- Standardized log format

### 14. Add API Response Models with Pydantic
**Effort**: High

Define Pydantic models for API responses instead of raw dicts.

**Example**:
```python
class MergeResult(BaseModel):
    source_name: str
    destination_name: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    error: Optional[str] = None
    result: Optional[MergeResult] = None
```

**Benefits**:
- Automatic validation
- OpenAPI documentation
- Type safety

### 15. Add Database for Job Persistence
**Effort**: Very High

Currently jobs are in-memory. For production, persist to database.

**Options**:
- SQLite for simple deployments
- PostgreSQL for production
- Redis for fast in-memory with persistence

**Benefits**:
- Jobs survive restarts
- Audit trail
- Historical data

### 16. Add Metrics/Monitoring
**Effort**: Medium

Add Prometheus metrics or similar.

**Metrics to track**:
- Merge success/failure rate
- Average merge duration
- Queue length
- Active jobs
- API response times

## Summary

**Quick wins** (Low effort, high impact):
1. Extract transaction split helper
2. Add constants file
3. Improve error messages
4. Add type hints to main functions

**Medium-term improvements**:
1. Extract search filtering
2. Add configuration module
3. Add comprehensive docstrings
4. Add input validators

**Long-term enhancements**:
1. Add unit tests
2. Add database persistence
3. Add monitoring
4. Structured logging
