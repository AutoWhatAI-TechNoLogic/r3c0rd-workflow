## r3c0rd-workflow

AI-assisted browser workflow recorder, search, and runner.

This project has two parts:
- Backend: Python/Flask service for AI enhancement, storage, and hybrid search over recorded workflows, plus a Playwright-based runner.
- Browser Extension: WXT + React sidepanel to record, view, and trigger workflows.


### Features
- **Record and describe workflows**: Turn raw steps into human-readable names, descriptions, and per-step explanations using OpenAI.
- **Store workflows**: Persist in MongoDB with optional vector embeddings in Pinecone for semantic search.
- **Hybrid search**: Combine MongoDB keyword search with Pinecone semantic results and re-rank.
- **Execute workflows**: Run recorded workflows via Playwright with support for dropdowns, scrolling, key presses, and LLM-powered data extraction steps.
- **Password handling**: If a workflow requires a password, the backend runner accepts a user-supplied password securely at run-time.


### Repository layout
- `backend/`: Flask app, services, vector DB integration, and CLI runner.
- `extension/`: WXT (Web eXtension Toolkit) + React sidepanel and scripts.


## Prerequisites
- Python 3.10+
- Node.js 18+
- MongoDB (local or Atlas)
- Optional: Pinecone account (for semantic search), OpenAI API key
- Optional (runner): Playwright and its browser binaries


## Backend: Setup & Run
1) Create a `.env` file in `backend/` with at least:

```env
OPENAI_API_KEY=your_key_here            # required for AI features
PINECONE_API_KEY=your_key_here          # optional but recommended for semantic search
PINECONE_ENVIRONMENT=us-east-1          # default provided
PINECONE_INDEX_NAME=workflow-embeddings # default provided
MONGODB_URI=mongodb://localhost:27017/  # or your Atlas URI
MONGODB_DB_NAME=workflow_db             # default provided
MONGODB_COLLECTION=enhanced_workflows   # default provided
```

2) Install dependencies and run the server:

```bash
cd backend
pip install -r requirements.txt
python run.py
```

- Server starts on `http://127.0.0.1:5001`.
- CORS is enabled.

3) (Optional) Ensure MongoDB text index for keyword search on your collection:

```js
// In Mongo shell or driver, run once on the target collection:
db.enhanced_workflows.createIndex({ name: "text", description: "text", "steps.description": "text" })
```


## Backend: API Endpoints
Base URL: `http://127.0.0.1:5001`

- `POST /enhance-workflow`
  - Body: `{ name?: string, steps: Step[] }`
  - Response: Saved workflow with AI fields (`name`, `description`, `workflow_analysis`, `step_descriptions`, `requires_password`) and queue status for vectorization.

- `POST /search-workflows`
  - Body: `{ query: string, top_k?: number }`
  - Response: `{ query, results: Workflow[], count }` ranked by hybrid score (semantic + keyword).

- `GET /workflows`
  - Query: `page?`, `limit?`
  - Response: Paginated list of workflows with minimal fields.

- `GET /workflows/:id`
  - Response: Full workflow document.

- `POST /run-workflow`
  - Body: `{ workflow_id: string, password?: string }`
  - Behavior: Runs the workflow asynchronously using Playwright. If a password field is detected and a `password` is provided, it will be used instead of any recorded placeholder.

- `GET /health`
  - Response: Basic service and integration status (MongoDB, Pinecone, OpenAI).


## Workflow Runner (CLI)
There are two runners in `backend/`:

1) API-driven runner (used by the server): `app/workflow_executor.py`
   - Accepts in-memory workflow objects and supports advanced features like LLM extraction and password handling.

2) Standalone JSON runner: `workflow_runner.py`
   - Use this to execute `.json` workflow files directly.

Install Playwright (first-time only):

```bash
pip install playwright
python -m playwright install
```

Run a specific workflow JSON:

```bash
cd backend
python workflow_runner.py workflows/example.json --keep-open
```

Run all workflows in a directory:

```bash
python workflow_runner.py --dir ./workflows
```

Flags:
- `--headless`: run without a visible browser
- `--keep-open`: keep the browser open after completion
- `--delay <seconds>`: delay between steps (default 1.0)


## Browser Extension: Setup & Run
The extension is built with WXT, Vite, TypeScript, React, and Tailwind.

1) Install dependencies:

```bash
cd extension
npm install
```

2) Start in development mode (Chrome):

```bash
npm run dev
```

3) Load the extension in your browser:
- The dev command prints a path to an unpacked build.
- In Chrome, open `chrome://extensions`, enable Developer Mode, click "Load unpacked", and select the printed directory.

Scripts:
- `npm run dev`: start dev server (Chromium by default)
- `npm run dev:firefox`: dev for Firefox
- `npm run build`: production build
- `npm run zip`: zip the build for submission


## Architecture & Data Flow
- The extension records user actions and can send workflow data to the backend.
- `POST /enhance-workflow` enriches the workflow using OpenAI and stores it in MongoDB.
- A background task generates a dense text representation, embeds it with OpenAI, and stores it in Pinecone.
- `POST /search-workflows` performs hybrid search (MongoDB text + Pinecone vector) and re-ranks.
- Workflows can be executed through the API (`/run-workflow`) or via the standalone runner.

Key backend modules:
- `app/services.py`: AI enhancement, extraction, embedding, vectorization, and Mongo persistence.
- `app/routes.py`: Flask routes (enhancement, search, listing, run, health).
- `app/vector_db.py`: Pinecone manager (connect/create index, upsert/query support).
- `app/db.py`: MongoDB context manager optimized for shared/free tiers.
- `app/workflow_executor.py`: Playwright executor with dropdown handling, input rules, and LLM extraction UI.


## Troubleshooting
- "OpenAI client failed to initialize": ensure `OPENAI_API_KEY` is in `backend/.env`.
- Pinecone warnings: the app works without Pinecone; only semantic search is degraded.
- MongoDB `$text` search returns no results: confirm you created a text index on fields you care about.
- Playwright errors: run `python -m playwright install` and ensure Chrome/Chromium is available.
- CORS during extension dev: backend has CORS enabled; ensure you’re hitting `http://127.0.0.1:5001`.


## Contributing & License
- PRs welcome. Keep code readable, typed where relevant, and avoid deep nesting.
- Make sure to update the README if you add endpoints or flags.
- License: Add your preferred license; if none provided, treat as All Rights Reserved by default.


## Quick Start (TL;DR)
- Backend: set `backend/.env`, `pip install -r backend/requirements.txt`, `python backend/run.py`.
- Extension: `cd extension && npm i && npm run dev`, then load unpacked in your browser.
- Enhance via API: `POST /enhance-workflow` with steps; search via `POST /search-workflows`; run via `POST /run-workflow`.
