# Data Analysis Agent V2 — Snekbox Sandbox

A production-ready data analysis agent that executes Python code inside **Snekbox** (NsJail-based sandbox) instead of local `PythonAstREPLTool`. Provides kernel-level process isolation with CPU/memory limits, network blocking, and filesystem restrictions.

## Prerequisites

- **Python 3.11+**
- **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux)
- **Groq API key** (set as `GROQ_API_KEY` environment variable)

## Quick Start

### 1. Start Snekbox

```bash
cd DataAnalysisAgentV2
docker compose up -d --build
```

Verify it's running:

```bash
curl -X POST http://localhost:8060/eval -H "Content-Type: application/json" -d '{"input":"print(1+1)"}'
# Expected: {"stdout":"2\n"}
```

### 2. Install Python dependencies

```bash
pip install langchain langchain-groq langchain-experimental langgraph httpx pandas numpy openpyxl fastapi uvicorn python-dotenv pydantic
```

### 3. Set environment variables

Create a `.env` file in the `DataAnalysisAgentV2/` directory:

```
GROQ_API_KEY=your_key_here
```

### 4. Run

**Library mode (demo):**

```bash
python run.py
```

**API server mode:**

```bash
python main.py
# Server starts at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## Windows Setup (Docker Desktop)

1. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
2. Enable WSL 2 backend in Docker Desktop settings
3. Open a terminal in `DataAnalysisAgentV2/`
4. Run `docker compose up -d --build`
5. Run `python run.py`

## Linux/Ubuntu Setup (Docker Engine)

```bash
# Install Docker Engine
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl start docker
sudo usermod -aG docker $USER
# Log out and back in for group change

# Start Snekbox
cd DataAnalysisAgentV2
docker compose up -d --build

# Run
python run.py
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | App health check |
| GET | `/health/snekbox` | Snekbox connectivity check |
| POST | `/sessions` | Create a new session |
| GET | `/sessions/{id}` | Get session info |
| DELETE | `/sessions/{id}` | Delete a session |
| POST | `/sessions/{id}/upload` | Upload CSV/Excel file |
| POST | `/sessions/{id}/ask` | Ask a question (non-streaming) |
| POST | `/sessions/{id}/ask/stream` | Ask a question (SSE streaming) |

## Scaling (Multiple Snekbox Containers)

For high-concurrency deployments, uncomment the `snekbox-2` and `nginx` services in `docker-compose.yaml`, then create an `nginx.conf`:

```nginx
events { worker_connections 1024; }

http {
    upstream snekbox {
        server snekbox:8060;
        server snekbox-2:8060;
    }
    server {
        listen 8060;
        location / {
            proxy_pass http://snekbox;
        }
    }
}
```

Then: `docker compose up -d --build`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | (required) | Groq API key for LLM |
| `SNEKBOX_URL` | `http://localhost:8060/eval` | Snekbox eval endpoint |

## Troubleshooting

**"Could not connect to Snekbox"**
- Check Docker is running: `docker ps`
- Check Snekbox logs: `docker compose logs snekbox`
- Verify port 8060 isn't in use: `netstat -an | grep 8060`

**Snekbox returns empty output**
- Ensure your code uses `print()` to display results
- Check that the data file is mounted correctly in `/data/`

**"ModuleNotFoundError: pandas" inside Snekbox**
- Rebuild the image: `docker compose build --no-cache`

**Timeout errors**
- Increase `SNEKBOX_TIMEOUT` in `config.py` (default: 30s)
- Check if the code is doing heavy computation
