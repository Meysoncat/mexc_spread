# Design Document: Multi-Exchange Spread History

## Overview

Расширение модуля записи истории спредов (`history_worker`, `history_store`, ORM) для параллельного опроса множества бирж (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget). Данные всех бирж хранятся в единой SQLite таблице `spread_snapshots` с новой колонкой `exchange`. API и фронтенд получают фильтрацию по бирже и возможность сравнения спредов на одном графике.

## Architecture

### High-Level Flow

```
external_apis.json (history.exchanges)
        │
        ▼
┌─────────────────────────────────────────────────┐
│              History_Worker (_loop)              │
│  ┌───────────────────────────────────────────┐  │
│  │  ThreadPoolExecutor(max_workers=N)        │  │
│  │  ┌─────┐ ┌──────┐ ┌─────┐ ┌──────┐ ... │  │
│  │  │MEXC │ │Binance│ │Bybit│ │ OKX  │     │  │
│  │  └──┬──┘ └──┬───┘ └──┬──┘ └──┬───┘     │  │
│  └─────┼────────┼────────┼───────┼──────────┘  │
│         └────────┴────────┴───────┘             │
│                      │                          │
│              append_snapshot(exchange=...)       │
│                      │                          │
│         ┌────────────▼────────────┐             │
│         │   Retention Cleanup     │             │
│         └─────────────────────────┘             │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│         SQLite: spread_snapshots                │
│  id | exchange | market | symbol | observed_at  │
│     | bid | ask | spread_bps | ...              │
│  INDEX(exchange, market, symbol, observed_at)   │
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              FastAPI Backend                     │
│  GET /api/history?exchange=...&symbol=...       │
│  GET /api/history/compare?exchanges=...&symbol= │
│  GET /api/history/exchanges                     │
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              Frontend                           │
│  Exchange filter dropdown + ComparisonChart     │
└─────────────────────────────────────────────────┘
```

### Design Decisions

1. **Single table with exchange column** — проще, чем отдельные таблицы на биржу; composite index обеспечивает производительность фильтрации.
2. **ThreadPoolExecutor** — I/O-bound задачи (HTTP-запросы к биржам); GIL не мешает параллелизму сетевых вызовов.
3. **Fault isolation** — исключение одной биржи не прерывает цикл; результаты остальных сохраняются.
4. **Retention policy в том же цикле** — удаление старых записей после вставки новых, без отдельного планировщика.

## Components and Interfaces

### 1. ORM Model: SpreadSnapshot (modified)

**File:** `mexc_monitor/orm/models.py`

```python
class SpreadSnapshot(Base):
    __tablename__ = "spread_snapshots"
    __table_args__ = (
        Index("idx_spread_observed", "observed_at"),
        Index("idx_spread_market_sym_time", "market", "symbol", "observed_at"),
        Index("idx_spread_exchange_market_sym_time", "exchange", "market", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="mexc")
    observed_at: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    bid_qty: Mapped[float] = mapped_column(Float, nullable=False)
    ask_qty: Mapped[float] = mapped_column(Float, nullable=False)
    mid: Mapped[float] = mapped_column(Float, nullable=False)
    spread_abs: Mapped[float] = mapped_column(Float, nullable=False)
    spread_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h_base: Mapped[float] = mapped_column(Float, nullable=False)
    volume_24h_quote: Mapped[float] = mapped_column(Float, nullable=False)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_round_trip_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_spread_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    l1_max_executable_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    l1_max_notional_quote: Mapped[float | None] = mapped_column(Float, nullable=True)
```

### 2. Schema Migration

**File:** `mexc_monitor/orm/engine.py`

Расширение `_migrate_spread_snapshots_columns` для добавления колонки `exchange` с дефолтом `"mexc"` и создания нового индекса:

```python
def _migrate_spread_snapshots_columns(engine: Engine) -> None:
    insp = inspect(engine)
    if "spread_snapshots" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("spread_snapshots")}

    alters: list[str] = []
    # Existing migrations
    for col, sqltype in (
        ("fee_round_trip_bps", "REAL"),
        ("net_spread_bps", "REAL"),
        ("l1_max_executable_base", "REAL"),
        ("l1_max_notional_quote", "REAL"),
    ):
        if col not in existing:
            alters.append(f"ALTER TABLE spread_snapshots ADD COLUMN {col} {sqltype}")

    # New: exchange column
    if "exchange" not in existing:
        alters.append(
            "ALTER TABLE spread_snapshots ADD COLUMN exchange VARCHAR(32) NOT NULL DEFAULT 'mexc'"
        )

    if not alters:
        return
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))

    # Create composite index if not exists
    existing_indexes = {idx["name"] for idx in insp.get_indexes("spread_snapshots")}
    if "idx_spread_exchange_market_sym_time" not in existing_indexes:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_spread_exchange_market_sym_time "
                "ON spread_snapshots (exchange, market, symbol, observed_at)"
            ))
```

### 3. Configuration Extension

**File:** `mexc_monitor/config.py`

Новые поля в `Settings`:

```python
@dataclass(frozen=True)
class Settings:
    # ... existing fields ...

    # Multi-exchange history
    history_exchanges: tuple[str, ...] = ("mexc",)
    history_retention_days: int = 30
    history_max_workers: int = 4
```

**Config JSON** (`external_apis.json`, секция `history`):

```json
{
  "history": {
    "enabled": true,
    "db_path": "data/spread_history.sqlite",
    "interval_sec": 60,
    "markets": ["spot", "futures"],
    "exchanges": ["mexc", "binance", "bybit", "okx", "gateio", "htx", "bitget"],
    "retention_days": 30,
    "max_workers": 4
  }
}
```

### 4. History Worker (rewritten)

**File:** `mexc_monitor/history_worker.py`

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

SUPPORTED_EXCHANGES = frozenset({"mexc", "binance", "bybit", "okx", "gateio", "htx", "bitget"})


def _validate_exchanges(configured: tuple[str, ...]) -> tuple[str, ...]:
    """Filter and validate exchange names against supported set."""
    valid = []
    for name in configured:
        normalized = name.strip().lower()
        if normalized in SUPPORTED_EXCHANGES:
            valid.append(normalized)
        else:
            logger.warning("Unsupported exchange in history config: %r (ignored)", name)
    return tuple(valid)


def _poll_exchange(exchange: str, markets: tuple[str, ...], settings: Settings) -> list[tuple[str, pd.DataFrame]]:
    """Poll a single exchange for all configured markets. Returns list of (market, df) tuples."""
    results = []
    for market in markets:
        df, err = _load_exchange_snapshot(exchange, market, settings)
        if err:
            logger.warning("history snapshot %s/%s: %s", exchange, market, err)
            continue
        if df is not None and not df.empty:
            results.append((market, df))
    return results


def _loop() -> None:
    while not _stop.is_set():
        s = DEFAULT_SETTINGS
        interval = _effective_interval(s.history_interval_sec)

        if s.history_enabled:
            exchanges = _validate_exchanges(s.history_exchanges)
            if not exchanges:
                logger.warning("No valid exchanges configured for history")
            else:
                try:
                    _run_snapshot_cycle(s, exchanges)
                except Exception:
                    logger.exception("history tick failed")

        if _stop.wait(timeout=interval):
            break


def _effective_interval(configured: float) -> float:
    """Enforce minimum 10s interval."""
    return max(10.0, configured)


def _run_snapshot_cycle(s: Settings, exchanges: tuple[str, ...]) -> None:
    start_time = time.monotonic()
    path = resolve_history_db_path(s)
    total_rows = 0

    with ThreadPoolExecutor(max_workers=s.history_max_workers) as executor:
        futures = {
            executor.submit(_poll_exchange, ex, s.history_markets, s): ex
            for ex in exchanges
        }
        for future in as_completed(futures):
            exchange = futures[future]
            try:
                results = future.result()
                for market, df in results:
                    n = append_snapshot(path, market, df, exchange=exchange)
                    total_rows += n
            except Exception:
                logger.exception("history poll failed for exchange=%s", exchange)

    # Retention cleanup
    _apply_retention(path, s.history_retention_days)

    elapsed = time.monotonic() - start_time
    logger.debug("history cycle complete: %d rows, %.2fs elapsed", total_rows, elapsed)


def _apply_retention(path: Path, retention_days: int) -> None:
    """Delete records older than retention_days. Skip if retention_days == 0."""
    if retention_days <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    engine = get_engine(path)
    with Session(engine) as session:
        session.execute(
            delete(SpreadSnapshot).where(SpreadSnapshot.observed_at < cutoff)
        )
        session.commit()
```

### 5. Exchange Snapshot Loaders

**File:** `mexc_monitor/history_store.py` (extended)

Функция `_load_exchange_snapshot` маршрутизирует вызов к соответствующему клиенту биржи:

```python
def _load_exchange_snapshot(exchange: str, market: str, settings: Settings) -> tuple[pd.DataFrame | None, str | None]:
    """Load ticker snapshot from the specified exchange."""
    if exchange == "mexc":
        return safe_load_snapshot(market, settings)

    # For other exchanges, use their respective snapshot functions
    loader_map = {
        "binance": _load_binance_snapshot,
        "bybit": _load_bybit_snapshot,
        "okx": _load_okx_snapshot,
        "gateio": _load_gateio_snapshot,
        "htx": _load_htx_snapshot,
        "bitget": _load_bitget_snapshot,
    }
    loader = loader_map.get(exchange)
    if loader is None:
        return None, f"No loader for exchange: {exchange}"
    return loader(market)
```

### 6. History Store (extended)

**File:** `mexc_monitor/history_store.py`

Расширение `append_snapshot` для принятия параметра `exchange`:

```python
def append_snapshot(path: Path, market: str, df: pd.DataFrame, *, exchange: str = "mexc") -> int:
    """Append snapshot rows to the database with exchange identifier."""
    if df.empty:
        return 0
    init_db(path)
    batch: list[SpreadSnapshot] = []
    for _, row in df.iterrows():
        # ... existing row processing ...
        batch.append(
            SpreadSnapshot(
                exchange=exchange,
                observed_at=str(oa),
                market=market,
                # ... rest of fields ...
            )
        )
    # ... existing commit logic ...
```

Расширение `query_recent` для фильтрации по бирже:

```python
def query_recent(
    path: Path,
    *,
    market: str,
    symbol: str | None,
    since_iso: str | None,
    limit: int,
    exchange: str | None = None,
    exchanges: list[str] | None = None,
) -> list[dict]:
    """Query recent snapshots with optional exchange filtering."""
    # ... existing logic ...
    if exchange:
        stmt = stmt.where(SpreadSnapshot.exchange == exchange)
    elif exchanges:
        stmt = stmt.where(SpreadSnapshot.exchange.in_(exchanges))
    # ... rest of query ...
```

### 7. API Endpoints

**File:** `backend/main.py`

```python
@app.get("/api/history")
def history(
    market: str = Query("spot"),
    symbol: str | None = Query(None),
    since: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    exchange: str | None = Query(None, description="Filter by single exchange"),
) -> dict:
    path = resolve_history_db_path(DEFAULT_SETTINGS)
    rows = query_recent(path, market=market, symbol=symbol, since_iso=since, limit=limit, exchange=exchange)
    return {"ok": True, "rows": rows, "count": len(rows)}


@app.get("/api/history/compare")
def history_compare(
    symbol: str = Query(..., min_length=3),
    market: str = Query("spot"),
    exchanges: str = Query(..., description="Comma-separated exchange names"),
    since: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    """Return history grouped by exchange for comparison."""
    exchange_list = [e.strip().lower() for e in exchanges.split(",") if e.strip()]
    path = resolve_history_db_path(DEFAULT_SETTINGS)
    rows = query_recent(path, market=market, symbol=symbol, since_iso=since, limit=limit, exchanges=exchange_list)
    # Group by exchange
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        ex = row.get("exchange", "mexc")
        grouped.setdefault(ex, []).append(row)
    return {"ok": True, "symbol": symbol, "market": market, "exchanges": exchange_list, "data": grouped}


@app.get("/api/history/exchanges")
def history_exchanges() -> dict:
    """Return list of exchanges that have history data."""
    path = resolve_history_db_path(DEFAULT_SETTINGS)
    if not path.is_file():
        return {"ok": True, "exchanges": []}
    engine = get_engine(path)
    with Session(engine) as session:
        result = session.execute(
            select(SpreadSnapshot.exchange).distinct()
        ).scalars().all()
    return {"ok": True, "exchanges": sorted(result)}
```

### 8. Frontend: Exchange Filter & Comparison Chart

**File:** `frontend/src/pages/HistoryPage.tsx` (new/extended)

```typescript
interface HistoryPageProps {}

export function HistoryPage() {
  const [exchange, setExchange] = useState<string | null>(null);
  const [availableExchanges, setAvailableExchanges] = useState<string[]>([]);

  useEffect(() => {
    fetch("/api/history/exchanges")
      .then(r => r.json())
      .then(data => setAvailableExchanges(data.exchanges));
  }, []);

  return (
    <div>
      <ExchangeFilter
        exchanges={availableExchanges}
        selected={exchange}
        onChange={setExchange}
      />
      <HistoryTable exchange={exchange} />
    </div>
  );
}
```

**File:** `frontend/src/components/ComparisonChart.tsx` (new)

```typescript
interface ComparisonChartProps {
  symbol: string;
  market: string;
  exchanges: string[];
}

export function ComparisonChart({ symbol, market, exchanges }: ComparisonChartProps) {
  const [data, setData] = useState<Record<string, HistoryRow[]>>({});

  useEffect(() => {
    if (exchanges.length === 0) return;
    fetch(`/api/history/compare?symbol=${symbol}&market=${market}&exchanges=${exchanges.join(",")}`)
      .then(r => r.json())
      .then(resp => setData(resp.data));
  }, [symbol, market, exchanges]);

  // Render one line per exchange using lightweight-charts
  // Each exchange gets a unique color from EXCHANGE_COLORS map
  return <div ref={chartRef} />;
}

const EXCHANGE_COLORS: Record<string, string> = {
  mexc: "#2962FF",
  binance: "#F0B90B",
  bybit: "#F7A600",
  okx: "#000000",
  gateio: "#17E6A1",
  htx: "#2DAF68",
  bitget: "#00D4AA",
};
```

## Data Models

### Configuration Schema (history section)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| enabled | bool | false | Включить запись истории |
| db_path | string | "data/spread_history.sqlite" | Путь к SQLite файлу |
| interval_sec | float | 60 | Интервал между циклами (мин. 10) |
| markets | string[] | ["spot", "futures"] | Рынки для опроса |
| exchanges | string[] | ["mexc"] | Биржи для опроса |
| retention_days | int | 30 | Дней хранения (0 = бессрочно) |
| max_workers | int | 4 | Потоков в ThreadPoolExecutor |

### SpreadSnapshot Table Schema

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| id | INTEGER | NO | autoincrement | PK |
| exchange | VARCHAR(32) | NO | "mexc" | Идентификатор биржи |
| observed_at | TEXT | NO | — | ISO 8601 timestamp |
| market | VARCHAR(16) | NO | — | "spot" or "futures" |
| symbol | VARCHAR(64) | NO | — | e.g. "BTCUSDT" |
| bid | REAL | NO | — | Best bid price |
| ask | REAL | NO | — | Best ask price |
| bid_qty | REAL | NO | — | Bid quantity |
| ask_qty | REAL | NO | — | Ask quantity |
| mid | REAL | NO | — | Mid price |
| spread_abs | REAL | NO | — | Absolute spread |
| spread_bps | REAL | YES | — | Spread in basis points |
| volume_24h_base | REAL | NO | — | 24h volume (base) |
| volume_24h_quote | REAL | NO | — | 24h volume (quote) |
| funding_rate | REAL | YES | — | Funding rate (futures) |
| fee_round_trip_bps | REAL | YES | — | Round-trip fee |
| net_spread_bps | REAL | YES | — | Net spread after fees |
| l1_max_executable_base | REAL | YES | — | L1 max executable |
| l1_max_notional_quote | REAL | YES | — | L1 max notional |

### API Response Models

**GET /api/history**
```json
{
  "ok": true,
  "rows": [
    {
      "exchange": "binance",
      "observed_at": "2025-01-15T12:00:00Z",
      "market": "spot",
      "symbol": "BTCUSDT",
      "spread_bps": 1.5,
      "..."
    }
  ],
  "count": 100
}
```

**GET /api/history/compare**
```json
{
  "ok": true,
  "symbol": "BTCUSDT",
  "market": "spot",
  "exchanges": ["mexc", "binance"],
  "data": {
    "mexc": [{"observed_at": "...", "spread_bps": 2.1, "..."}],
    "binance": [{"observed_at": "...", "spread_bps": 1.3, "..."}]
  }
}
```

**GET /api/history/exchanges**
```json
{
  "ok": true,
  "exchanges": ["mexc", "binance", "bybit"]
}
```

## Error Handling

| Scenario | Handling |
|----------|----------|
| Exchange client HTTP timeout | Log warning, skip exchange, continue cycle |
| Exchange client returns invalid data | Log warning, skip exchange, continue cycle |
| All exchanges fail in a cycle | Log error, no rows stored, wait for next cycle |
| SQLite write failure | Log exception, retry on next cycle |
| Unsupported exchange in config | Log warning at startup, ignore entry |
| interval_sec < 10 | Clamp to 10 seconds |
| retention_days absent | Default to 30 |
| retention_days = 0 | Skip deletion (infinite retention) |
| DB file missing on query | Return empty result set |

## Testing Strategy

### Property-Based Tests (Hypothesis)

Property-based tests validate universal invariants across randomly generated inputs:

- **Exchange field integrity**: Generate random exchange names and ticker DataFrames, verify stored records always carry the correct exchange identifier.
- **Fault isolation**: Generate random subsets of failing/healthy exchanges, verify healthy exchange data is always stored.
- **Retention policy**: Generate random sets of records with various timestamps and random retention_days, verify post-cleanup state.
- **Configuration validation**: Generate random lists of strings (mix of valid/invalid exchange names), verify filtering logic.
- **API filtering**: Populate DB with records from random exchanges, verify query results match filter criteria.
- **Interval clamping**: Generate random float values, verify effective interval is always ≥ 10.

### Unit Tests (pytest)

Example-based tests for specific scenarios:

- Migration preserves existing records with exchange="mexc"
- Default retention_days is 30 when absent from config
- Default interval_sec is 60 when absent from config
- API endpoint returns correct exchange list
- Comparison endpoint groups results by exchange correctly
- Worker logs row count and elapsed time after cycle

### Integration Tests

- Full cycle with mocked exchange clients: verify end-to-end flow from config → poll → store → query
- Schema migration on existing database with pre-existing records

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Exchange field integrity

*For any* SpreadSnapshot record created by the History_Worker, the `exchange` field SHALL equal the identifier of the source exchange that was polled. When no exchange is explicitly specified (legacy path), the field SHALL default to `"mexc"`.

**Validates: Requirements 1.1, 1.2**

### Property 2: Fault isolation in parallel polling

*For any* subset of exchanges that raise exceptions during a Snapshot_Cycle, the History_Worker SHALL still successfully store snapshots from all non-failing exchanges. The number of stored rows from healthy exchanges SHALL be independent of failures in other exchanges.

**Validates: Requirements 2.3**

### Property 3: Retention policy correctness

*For any* set of SpreadSnapshot records with arbitrary `observed_at` timestamps and *for any* positive `retention_days` value, after retention cleanup no record with `observed_at` older than `now - retention_days` SHALL remain in the database. When `retention_days` equals 0, *for any* set of records, all records SHALL be preserved regardless of age.

**Validates: Requirements 3.2, 3.3**

### Property 4: Exchange configuration validation

*For any* list of exchange names in configuration, the History_Worker SHALL poll exactly those exchanges that are both present in the configured list AND members of the supported set `{mexc, binance, bybit, okx, gateio, htx, bitget}`. *For any* string not in the supported set, it SHALL be excluded from polling.

**Validates: Requirements 4.2, 4.3, 4.4**

### Property 5: API exchange filtering correctness

*For any* set of SpreadSnapshot records from multiple exchanges stored in the database: (a) when querying with a specific `exchange` parameter, all returned records SHALL have `exchange` equal to the requested value; (b) when querying without an `exchange` parameter, records from all exchanges present in the database SHALL be returned; (c) when querying with a comma-separated `exchanges` list, all returned records SHALL have `exchange` in the requested set.

**Validates: Requirements 5.2, 5.3, 6.2**

### Property 6: Interval minimum enforcement

*For any* configured `interval_sec` value, the effective polling interval used by the History_Worker SHALL equal `max(10, interval_sec)`. This ensures no interval below 10 seconds is ever applied.

**Validates: Requirements 7.3**
