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

## Quick Start

### Using Docker Compose (Recommended)

```bash
docker compose up -d
```

Open <http://localhost:8000>

### Using Docker

```bash
docker run -p 8000:8000 --read-only --user 65534:65534 \
  ghcr.io/engturtle/firefly-transaction-merger:latest
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SESSION_SECRET_KEY` | Secret key for session encryption. Set this for persistent sessions across restarts. | Random (generated at startup) |
| `DEBUG` | Enable debug logging (`1`, `true`, or `yes`) | Disabled |

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

## Development

### Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Install dependencies
uv sync

# Run the development server
uv run uvicorn main:app --reload
```

### Building the Docker Image

```bash
docker build -t firefly-transaction-merger .
```
