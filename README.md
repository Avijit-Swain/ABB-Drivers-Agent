# ABB Driver Analysis Copilot

ABB Driver Analysis Copilot is a local React and Python application for exploring business drivers, forecast scenarios, and driver contributions for ABB divisions.

## Project Structure

```text
bubu_agent/
  assets/       ABB logo and static images
  backend/      Python HTTP server, LangGraph agent, tools, dashboard APIs
  data/         SQLite knowledge base and unstructured KPI definitions
  frontend/     React-style browser UI served by the Python backend
  requirements.txt
  setup.sh
  run_server.sh
```

## Setup

From a fresh clone:

```bash
cd bubu_agent
./setup.sh
```

Create a `.env` file in `bubu_agent/` with the runtime secrets:

```env
OPENAI_API_KEY=your_openai_api_key

EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=465
EMAIL_USE_SSL=true
EMAIL_USER=your_sender_email
EMAIL_PASSWORD=your_app_password
PROFILE_NAME=Anisha Mahanty
PROFILE_TITLE=Data Scientist
```

## Run

```bash
cd bubu_agent
./run_server.sh 8500
```

Then open [http://localhost:8500](http://localhost:8500).

## Runtime Outputs

Generated plots, structured query exports, virtual environments, local editor settings, and SQLite sidecar files are intentionally ignored by Git.
