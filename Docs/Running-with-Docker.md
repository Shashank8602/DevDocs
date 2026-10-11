# Running DevDocs with Docker

Copy-paste commands for Windows PowerShell. Run every block from the project root `c:\Projects\DevDocs` unless a step says otherwise.

Use three terminals: one for Docker, one for the backend, one for the frontend.

---

## First time only

### 1. Create the `.env` file

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### 2. Create the backend virtual environment and install packages

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
```

### 3. Install the frontend packages

```powershell
cd frontend
npm install
cd ..
```

---

## Every time you start working

### Terminal 1: start Postgres and Elasticsearch

Make sure Docker Desktop is running first (wait until it says "Engine running"), then:

```powershell
docker compose up -d
```

Check that both containers are up and healthy:

```powershell
docker compose ps
```

Wait until both show `healthy` (about 30–60 seconds for Elasticsearch). If you want to check Elasticsearch directly:

```powershell
curl.exe http://localhost:9200/_cluster/health
```

### Terminal 2: apply database migrations and start the backend

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

Leave this terminal running. Check it at:

- http://localhost:8000/health
- http://localhost:8000/docs

### Terminal 3: start the frontend

```powershell
cd frontend
npm run dev
```

Open http://localhost:5173 in your browser.

---

## Stopping for the day

Press `Ctrl+C` in the backend and frontend terminals, then:

```powershell
docker compose stop
```

Your data is kept. Data lives in the Docker volumes `pgdata` and `esdata`, and uploads and `secret.key` live in `data\`.

---

## Starting again the next day

```powershell
docker compose up -d
```

Then repeat **Terminal 2** and **Terminal 3** from above. You do not need to run the first-time steps again.

---

## Useful commands

| Purpose | Command |
|---|---|
| See container status | `docker compose ps` |
| View Postgres logs | `docker compose logs -f postgres` |
| View Elasticsearch logs | `docker compose logs -f elasticsearch` |
| Open a Postgres shell | `docker exec -it devdocs-postgres psql -U devdocs -d devdocs` |
| Run backend tests | `cd backend` then `.\.venv\Scripts\python.exe -m pytest` |
| Build frontend (type-check + bundle) | `cd frontend` then `npm run build` |

---

## Danger: these commands delete your data

Do **not** run these unless you want to wipe the database and search index:

```powershell
docker compose down -v
docker volume rm devdocs_pgdata devdocs_esdata
docker system prune --volumes
```

Plain `docker compose down` is safe. It removes the containers but keeps the data.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `error during connect` or `cannot connect to the Docker daemon` | Open Docker Desktop and wait until it says "Engine running", then retry. |
| Port 5432 or 9200 already in use | Stop the other program using that port, or run `docker compose down` and then `docker compose up -d` again. |
| Elasticsearch stays `starting` or exits | Give Docker Desktop more memory (Settings → Resources), at least 4 GB. Then run `docker compose restart elasticsearch`. |
| `.\.venv\Scripts\...` not found | Run step 2 under "First time only" again. |
| `running scripts is disabled` when activating a venv | You do not need to activate the venv. The commands above call the venv's executables directly. |
| Backend cannot reach the database | Check that `.env` exists and that `docker compose ps` shows `postgres` as healthy. |
