# Firefly III Transaction Merger

A web tool for finding and merging duplicate withdrawal/deposit pairs in [Firefly III](https://firefly-iii.org/) that represent the same transfer between accounts.

## Problem

When importing transactions from multiple bank accounts, the same transfer often appears twice:

- A **withdrawal** from the source account
- A **deposit** to the destination account

This tool finds these matching pairs and merges them into a single transfer transaction.

## Features

- Search for matching transaction pairs by date range and account
- Match criteria: same amount, same currency, different accounts, within N business days
- One-click merge for individual pairs
- Bulk merge multiple selected pairs at once
- Session-based authentication with Firefly III Personal Access Token

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Firefly III instance with API access

## Setup

```bash
# Install dependencies
uv sync

# Run the server
uv run uvicorn main:app --reload
```

Open <http://localhost:8000>

## Usage

1. **Login** - Enter your Firefly III URL and Personal Access Token
2. **Search** - Set date range, optional account filter, and max business days apart
3. **Review** - Check the matched pairs shown in the results table
4. **Merge** - Click "Merge" on individual rows, or select multiple and click "Merge Selected"

## Creating a Personal Access Token

1. Go to your Firefly III instance
2. Navigate to Options > Profile > OAuth
3. Create a new Personal Access Token
4. Copy the token (it's only shown once)
