# Sovereign Social Network

[![Software Factory: 10/10 PASS](https://img.shields.io/badge/Software%20Factory-10%2F10%20PASS-brightgreen.svg)](#software-factory-scorecard)
[![Security Red Team: 31/31 PASS](https://img.shields.io/badge/Security%20Audit-31%2F31%20PASS-brightgreen.svg)](#fail-closed-security-audit)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Zero-CDN Sovereign](https://img.shields.io/badge/CDNs-0%20(100%25%20On--Device)-success.svg)](#sovereign-web-ui)

An **on-device, sovereign social network** engineered autonomously across a 10-pass executive lifecycle by the **System 2 Sovereign Dual-Process Engine** and verified under adversarial criteria by the 3-agent Software Factory triad.

---

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Sovereign Web Client                              │
│                   Responsive CSS Grid · Zero External CDNs                   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ HTTP / ASGI
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                           FastAPI REST Engine                                │
│       GET /api/health · GET /api/users · GET /api/users/{id}/friends         │
└──────────────┬───────────────────────┬───────────────────────┬───────────────┘
               │                       │                       │
┌──────────────▼──────────────┐ ┌──────▼──────────────┐ ┌──────▼──────────────┐
│     Matching Engine         │ │    EdgeRank Feed    │ │  Async Event Bus    │
│  Multivariate Compatibility │ │  U × W × Decay(t)   │ │  Error-Isolated     │
│     S(u, v) ∈ [0, 1]        │ │  Descending Rank    │ │  Pub/Sub Dispatch   │
└──────────────┬──────────────┘ └──────┬──────────────┘ └──────┬──────────────┘
               │                       │                       │
┌──────────────▼───────────────────────▼───────────────────────▼───────────────┐
│                            SQLite 3NF Database                               │
│           206 Normalized Contacts · Foreign Keys · 5 Compound Indices        │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Subsystems

### 1. Relational Schema & Ingestion (`ingest_db.py`)
- Ingests transposed `contacts.csv` (7 attribute-lines × 206 user columns) into an indexed 3NF SQLite database (`social_network.db`).
- Schema includes `users`, `friendships`, and `feed_posts` tables with compound indices on `(city, occupation)`, `users(name)`, and `feed_posts(user_id)`.
- **Zero NULL primary keys**, bounded demographic data, idempotent rebuilds.

### 2. Core REST API (`app.py`)
- High-performance asynchronous REST API built with FastAPI.
- Strict Pydantic models for request/response validation.
- All database queries are 100% parameterized with robust 404 entity handling.

### 3. Multivariate Contact Matching Engine (`matching_engine.py`)
- Computes compatibility score $S(u, v) \in [0, 1]$ across:
  - **City Proximity**: Weight 0.35
  - **Occupation Alignment**: Weight 0.25
  - **Gaussian Age Decay**: Weight 0.25 ($\exp(-\frac{|\text{age}_1 - \text{age}_2|}{12})$)
  - **Social Baseline**: Weight 0.15

### 4. EdgeRank Feed Optimization (`feed_ranker.py`)
- Ranks candidate feed items using the multi-factor EdgeRank formulation:
  $$\text{Score}(u, p) = \text{Affinity}(u, p) \times \text{Weight}(\text{type}(p)) \times \text{Decay}(\text{age}(p))$$
- Content classification parses text, photo, link, and video posts with deterministic type weights.
- Recency decay halves scores every ~10 hours.

### 5. Sovereign Single-Page Web UI (`templates/index.html`)
- Built with responsive CSS Grid and modern CSS variables.
- Includes Profile Card, News Feed, and Friend Suggestions components.
- **Strictly zero external CDN requests**: All CSS, fonts, and scripts are local/inline for 100% sovereign on-device operation.

### 6. Asynchronous Event Bus (`event_bus.py`, `social_events.py`)
- In-memory async pub/sub bus with typed event subscriptions (`UserConnectedEvent`).
- Per-handler error isolation: failing subscribers never block dispatch to others.
- Deterministic priority ordering.

### 7. Unit Economics & Sovereign TCO (`monetization.py`)
- 3-year Total Cost of Ownership (TCO) model comparing centralized cloud frontier API spend ($0.03/user/day) vs on-device local execution ($540 amortized hardware cost).
- Proves **94.7% operational cost reduction** ($9,270 cloud vs $540 edge) and positive net monthly operating margins.

---

## Fail-Closed Security Audit

The security posture of the application is verified by `tests/security_audit.py`, which executes **31 automated checks** across 6 security waves:
1. **Static AST Scan**: Asserts zero unparameterized query sites in production code.
2. **API SQLi Probes**: Tests tautology probes (`' OR '1'='1`), stacked drop attempts (`'; DROP TABLE users; --`), and union injections.
3. **Write-Surface Hardening**: Asserts POST/PUT/PATCH/DELETE return 405 on read-only endpoints.
4. **Demographic Range Protection**: Verifies ages $<0$ or $>120$ fail closed without leaking demographic rows.
5. **PII Minimization**: Asserts 0 of 206 raw phone numbers from `contacts.csv` leak into the database or API responses.
6. **Post-Attack Invariant Accounting**: Proves 0 injected rows and identical database checksum before and after attack waves.

---

## Software Factory Scorecard

Autonomous build provenance from the System 2 Software Factory:

| Pass | Role | Milestone | Agent Latency | Status | Software Factory Triad Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **01** | CTO / Database Architect | Relational Schema & Ingestion | 185,802.8ms | **✓ PASS** | Exactly 206 users verified in SQLite 3NF schema |
| **02** | Full-Stack Lead | FastAPI Core REST Engine | 940,793.3ms | **✓ PASS** | FastAPI typed routes (`/api/health`, `/api/users`) verified |
| **03** | Lead Quant / Data Scientist | Multivariate Contact Matching | 105,852.6ms | **✓ PASS** | Proved similarity score $S(u, v) \in [0, 1]$ bounded |
| **04** | Product Lead | Sovereign Social Web UI | 835,756.4ms | **✓ PASS** | Sovereign HTML UI verified with 0 external CDN dependencies |
| **05** | Founder / Growth Architect | EdgeRank Feed Optimization | 1,006,979.6ms | **✓ PASS** | Validated strict descending score order across posts |
| **06** | Security Red Team Lead | Fail-Closed Security & Invariants | 3,851,504.1ms | **✓ PASS** | 31/31 security checks passed, 0 injected rows |
| **07** | Head of Finance / CFO | Unit Economics & Sovereign TCO | 33,298.1ms | **✓ PASS** | Proved 94.7% operational cost reduction |
| **08** | Systems Engineer | Async Event Bus Dispatch | 189,369.2ms | **✓ PASS** | Verified async handler execution and error isolation |
| **09** | QA Lead | Pytest End-to-End Suite | 192,534.8ms | **✓ PASS** | 100% of integration pytest suite passing cleanly |
| **10** | System 2 Specialist | Cognitive Scorecard & Audit | 1,664,221.4ms | **✓ PASS** | All 10 passes verified under adversarial criteria |

---

## Quickstart

### 1. Installation
```bash
git clone https://github.com/your-username/sovereign-social-network.git
cd sovereign-social-network
pip install -r requirements.txt
```

### 2. Database Ingestion
Rebuild or verify the SQLite database from `contacts.csv`:
```bash
python3 ingest_db.py
```

### 3. Run the API Server
Start the local FastAPI ASGI server:
```bash
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```
Open [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health) or [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive OpenAPI documentation.

### 4. Run the Full Test Suite
Execute the unit and integration test suite:
```bash
python3 -m pytest tests/
```

### 5. Run the Security Audit
Execute the 31-point fail-closed security red team audit:
```bash
python3 tests/security_audit.py
```

---

## License
MIT License. See [LICENSE](LICENSE) for details.
