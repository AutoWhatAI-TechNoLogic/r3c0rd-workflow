# r3c0rd-workflow

AI-assisted browser workflow recorder, search engine, and runner. Record sessions from a browser extension, enrich them with LLMs, search with hybrid semantic + keyword ranking, and replay them with a self-healing Playwright executor.

---

## Overview

- **Recorder**: A WXT + React sidepanel captures rrweb events, converts them into semantic workflow steps, and streams updates through a background script.
- **Enhancer + Store**: A Flask backend calls OpenAI to name, describe, and analyze workflows, then persists them in MongoDB and (optionally) Pinecone.
- **Search**: Hybrid ranking combines MongoDB `$text` scores with Pinecone vector similarity, exposed through REST or a dark-theme search UI (`backend/searchfront.html`).
- **Runner**: A Playwright executor in Python can replay workflows directly from MongoDB, heal failing steps with LLM prompts, and request run-time secrets like passwords.

---

## System Highlights

- **One-call AI enrichment** via `app/services.enhance_workflow_with_ai`, returning workflow titles, analyses, per-step copy, and password detection.
- **Background vectorization** queues dense summaries, embeds them with `text-embedding-3-small`, and upserts into Pinecone through `app.utils.executor`.
- **Hybrid search & re-rank** merges semantic matches with text scores, providing consistent ordering and score annotations for the search UI.
- **Self-healing execution** retries failing steps up to five times, asking OpenAI for DOM-aware fixes, and saves healed workflows back to MongoDB.
- **Granular recorder states** in the extension (`idle → recording → stopped`) keep the sidepanel reactive, while background polling ensures near-real-time updates.

---

## Repository Layout

- `backend/` – Flask service, Mongo/Pinecone integrations, AI workflow enrichment, Playwright runner, and Tailwind search page.
- `extension/` – WXT extension with React sidepanel, background recorder, message bus types, and Tailwind styling.

---

## Requirements

- Python 3.10+
- Node.js 18+
- MongoDB (local or Atlas)
- OpenAI API key (required for enrichment/self-healing)
- Optional: Pinecone account for semantic search
- Optional: Playwright browsers (installed via `python -m playwright install`)

---

## Environment Variables (`backend/.env`)

| Variable               | Required | Default                      | Purpose                                                       |
| ---------------------- | -------- | ---------------------------- | ------------------------------------------------------------- |
| `OPENAI_API_KEY`       | ✅       | –                            | Enables OpenAI calls for enhancement, embeddings, and healing |
| `PINECONE_API_KEY`     | ⚠️       | –                            | Needed for semantic search (skip to disable vector storage)   |
| `PINECONE_ENVIRONMENT` | ⚠️       | `us-east-1`                  | Pinecone serverless region                                    |
| `PINECONE_INDEX_NAME`  | ⚠️       | `workflow-embeddings`        | Pinecone index used for upserts/queries                       |
| `MONGODB_URI`          | ✅       | `mongodb://localhost:27017/` | Connection string for Mongo                                   |
| `MONGODB_DB_NAME`      | ✅       | `workflow_db`                | Database name                                                 |
| `MONGODB_COLLECTION`   | ✅       | `enhanced_workflows`         | Collection for workflows                                      |

⚠️ = required for the feature; the backend will still boot without it.

---

## Quick Start

1. **Backend**

   ```bash
   cd backend
   python -m venv .venv && .\.venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env  # or create manually using the table above
   python run.py
   ```

   - Serves REST API on `http://127.0.0.1:5001` with CORS enabled.
   - Create a Mongo text index once for best keyword search results:
     ```js
     db.enhanced_workflows.createIndex({
       name: "text",
       description: "text",
       "steps.description": "text",
     });
     ```

2. **Extension**

   ```bash
   cd extension
   npm install
   ```

   - Load the printed unpacked directory in `chrome://extensions` (Developer Mode).
   - The sidepanel shows recorder state, events, and enrichment status coming from the backend.

3. **(Optional) Search UI**
   - Serve `backend/searchfront.html` (e.g., `python -m http.server` from `backend/`).
   - Use the UI to issue hybrid searches and trigger runs via the REST API.

---

## Backend Services

- Flask factory (`app/__init__.py`) wires routes, CORS, logging, and lazy Pinecone initialization.
- ThreadPoolExecutor (`app/utils.py`) offloads vectorization and Playwright runs without blocking request threads.
- Pinecone manager (`app/vector_db.py`) lazily creates indices and caches handles per process.
- Mongo context manager (`app/db.py`) opens/closes clients per request to stay within shared-tier limits.
- Workflow enrichment (`app/services.py`):
  - `enhance_workflow_with_ai` – single OpenAI call produces name, description, analysis, per-step copy, password flag.
  - `generate_contextual_content` – 150–300 word dense document for embeddings.
  - `extract_data_with_llm` – optional HTML scraper for extraction steps.
- Routes (`app/routes.py`):
  - `POST /enhance-workflow` – validates input, merges AI response, persists to Mongo, queues vectorization.
  - `POST /search-workflows` – runs semantic + keyword lookups in parallel, merges scores, and re-ranks.
  - `GET /workflows` & `GET /workflows/<id>` – paginated listings and detail fetches.
  - `POST /run-workflow` – schedules Playwright execution (with optional password).
  - `GET /health` – surfaces backend, Mongo, Pinecone, and OpenAI status.

### Running the API

```bash
cd backend
python run.py
```

Logs include database, Pinecone, and OpenAI configuration hints. Customize host/port in `run.py` if needed.

---

## Workflow Execution

- **Async runner (`app/workflow_executor.py`)**
  - Launches Chromium, replays steps, and can prompt OpenAI for DOM repairs if an action fails.
  - Preserves healed workflows by updating MongoDB when retries succeed.
  - Accepts passwords at run-time without storing them.
  <!-- - **CLI runner (`workflow_runner.py`)**
  ```bash
  cd backend
  pip install playwright
  python -m playwright install
  python workflow_runner.py workflows/example.json --keep-open
  ```
  Flags:
  - `--dir` execute every JSON in a folder.
  - `--headless` run without a visible browser.
  - `--delay` control inter-step pauses. -->

---

## Browser Extension

- Built with WXT, React 19, TypeScript, Tailwind, and rrweb.
- Background script (`src/entrypoints/background.ts`):
  - Hooks Chrome tab events and rrweb callbacks while recording.
  - Normalizes events into `Step` objects, strips volatile fields, and hashes state to avoid redundant uploads.
  - Sends semantic steps to the backend (`POST /enhance-workflow`) and keeps the UI copy for the sidepanel.
  - Streams status updates (`recording`, `stopped`, `idle`) to all clients.
  - Posts raw events to `EVENT_LOGGING_ENDPOINT` (`http://127.0.0.1:7331/event` by default). Provide a listener there or update the constant if unused.
- Sidepanel (`src/entrypoints/sidepanel/`):
  - `WorkflowProvider` polls the background page during recording and exposes helper actions (`startRecording`, `stopRecording`, `discardAndStartNew`).
  - React views render loading/error states, current event timelines, and enriched metadata once available.
- Scripts (`package.json`):
  - `npm run dev`, `npm run dev:firefox`, `npm run build`, `npm run zip`, `npm run compile` (TypeScript check).

---

## Search Frontend (HTML Prototype)

`backend/searchfront.html` is a static Tailwind page for local debugging:

- Calls `POST /search-workflows` and renders hybrid scores and metadata tags.
- Allows triggering `POST /run-workflow`, prompting for passwords when needed.
- Serve the file with any static server (e.g., `python -m http.server 8000`) and open in your browser.

---

## Troubleshooting

- **OpenAI errors** – Verify `OPENAI_API_KEY`; the server refuses to boot without it.
- **Missing Pinecone** – Vector search gracefully skips; only keyword ranking remains.
- **Mongo timeouts** – Update `MONGODB_URI` or ensure the Atlas IP allow list includes your machine.
- **Playwright not installed** – Run `pip install playwright && python -m playwright install`.
- **Extension CORS** – Backend enables CORS for local dev; double-check you’re hitting `127.0.0.1:5001`.
- **Event logging server** – If you don’t need raw event archives, leave the endpoint unreachable or change it in `background.ts`.

---

## Contributing & Next Steps

- Add tests (Playwright) for new features where possible.
- License defaults to “all rights reserved” until specified; clarify before distributing.
<!-- - Document new routes, recorder states, or runner flags here when you extend the system. -->
