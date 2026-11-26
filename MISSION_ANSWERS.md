# Day 14 Lab — Mission Answers

> Mọi output dưới đây lấy từ lệnh đã chạy trên máy (WSL2 Ubuntu 24.04, Docker Engine 29.1.3, Python 3.12).
> Output đầy đủ, chưa cắt, nằm trong thư mục [`evidence/`](evidence/).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

1. **Hardcode secret** — `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key ngay, kể cả khi xoá ở commit sau (vẫn còn trong lịch sử).
2. **Log ra secret** — `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Log thường được gửi sang hệ thống khác (Datadog, Loki…), nhiều người đọc được.
3. **`print()` thay vì logging có cấu trúc** — không có level, không có timestamp, không parse được bằng máy.
4. **Không có health check** — `GET /health` trả 404 (đã thử, xem bên dưới). Platform không biết app chết để restart.
5. **Bind `host="localhost"`** — trong container chỉ nhận kết nối từ chính container, bên ngoài không gọi vào được.
6. **Port cố định `port=8000`** — Railway/Render cấp port qua biến `PORT`; port cứng thì app không nhận traffic.
7. **`reload=True` và `DEBUG = True` cứng** — reloader theo dõi file, tốn tài nguyên, không dùng trong production; không bật/tắt được theo môi trường.
8. **Không xử lý shutdown** — không có lifespan, không dọn dẹp kết nối khi nhận SIGTERM.
9. **Không có config management** — `MAX_TOKENS = 500` cố định trong code, đổi phải sửa code và deploy lại.

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

--- stdout của server ---
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this      ← secret lộ ra log
```

Nó chạy, nhưng chưa production-ready: không health check, secret nằm trong code và trong log.

### Exercise 1.3: So sánh develop vs production

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`OPENAI_API_KEY = "sk-..."`, `DEBUG = True`) | `config.py` đọc env (`os.getenv("PORT")`, `AGENT_API_KEY`…), có `validate()` fail-fast khi production thiếu key | Một image chạy được ở dev/staging/prod chỉ bằng đổi env; secret không vào git |
| Secret trong log | In thẳng API key | Chỉ log `question_length`, `client_ip` | Log được nhiều người/hệ thống đọc |
| Health check | Không có (404) | `/health` (liveness, có uptime/version) + `/ready` (readiness) | Platform tự restart khi chết, LB chỉ route khi sẵn sàng |
| Logging | `print()` | JSON qua `logging` | Log aggregator parse, lọc, đếm được |
| Shutdown | Đột ngột | `lifespan` dọn dẹp; uvicorn xử lý SIGTERM (log: `Shutting down` → `Application shutdown complete`) | Không cắt ngang request khi deploy/scale down |
| Host | `localhost` | `0.0.0.0` | Nhận kết nối từ ngoài container |
| Port | `8000` cố định | `PORT` từ env | Platform cấp port động |
| Reload | `reload=True` luôn bật | Chỉ khi `DEBUG=true` | Không theo dõi file trong production |
| CORS | Không có | `allowed_origins` từ env | Giới hạn origin được gọi API |

Kết quả bản production:

```text
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Agent đang hoạt động tốt! ...","model":"gpt-4o-mini"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.9,"version":"1.0.0","environment":"development",...}
$ curl http://localhost:8000/ready
{"ready":true}
```

Quan sát thêm: `production/app.py` gọi `signal.signal(SIGTERM, handle_sigterm)` lúc import, nhưng `uvicorn.run()` đăng ký handler của chính nó sau đó nên handler tự viết bị ghi đè. Shutdown vẫn êm vì uvicorn tự lo (log `Shutting down` → `Finished server process`).

---

## Part 2: Docker

### Exercise 2.1: `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` (bản full, có cả compiler và công cụ build).
2. **Working directory:** `/app`.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo layer. Requirements ít đổi, source đổi liên tục. Copy requirements + `pip install` trước thì sửa code chỉ làm mất cache từ bước `COPY app.py` trở đi, không phải cài lại thư viện. Nếu `COPY . .` trước thì mỗi lần sửa code đều cài lại toàn bộ dependency.
4. **CMD vs ENTRYPOINT:** `ENTRYPOINT` là chương trình chính của container, tham số của `docker run` được nối thêm vào sau. `CMD` là lệnh/tham số mặc định, bị thay hoàn toàn khi `docker run image <lệnh khác>`. Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`.

### Exercise 2.2: Build và chạy

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":4.0,"container":true}
```

### Exercise 2.3: Multi-stage build

- **Stage 1 (builder, `python:3.11-slim`):** cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`.
- **Stage 2 (runtime, `python:3.11-slim`):** tạo user `appuser`, chỉ copy `/root/.local` từ builder + `main.py` + `utils/`, chạy bằng non-root, có `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.
- **Vì sao nhỏ hơn:** base slim thay cho full; compiler, header, apt cache và pip cache ở lại stage builder, không vào image cuối.

Đo bằng `docker images my-agent`:

```text
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   bd7c8ed6e64b        240MB         57.4MB
my-agent:develop    241f6f22943d       1.67GB          426MB
```

- Develop: **1.67 GB** (disk usage)
- Production: **240 MB**
- Chênh lệch: (1670 − 240) / 1670 ≈ **85.6 %** nhỏ hơn. Tính theo content size nén (426 MB → 57.4 MB) là ≈ 86.5 %.

Image Part 6 (`06-lab-complete/Dockerfile`, multi-stage + venv): **63 MB** theo `check_production_ready.py`.

### Exercise 2.4: Docker Compose stack

```text
                    ┌──────────── network "internal" (bridge) ────────────┐
 client ── :80 ──▶  │  nginx (reverse proxy, rate limit 10 r/s/IP)        │
                    │     │ proxy_pass http://agent_backend (agent:8000)  │
                    │     ▼                                               │
                    │  agent (FastAPI, 2 uvicorn workers)                 │
                    │     ├──▶ redis:6379   (session cache, rate limit)   │
                    │     └──▶ qdrant:6333  (vector DB cho RAG)           │
                    └─────────────────────────────────────────────────────┘
```

Services được start (`docker compose -p k5-day14-docker ps`):

```text
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 15 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 15 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 29 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 29 seconds (healthy)   6379/tcp
```

Giao tiếp: chỉ nginx mở port ra host. Các service gọi nhau bằng tên service qua DNS nội bộ của network `internal` (`redis://redis:6379/0`, `http://qdrant:6333`). Agent `depends_on` redis + qdrant với `condition: service_healthy` nên chỉ start khi hai service kia healthy.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":14.9,"version":"2.0.0",...}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). ..."}
```

Header `Server: nginx` và các security header cho thấy request đi qua Nginx rồi mới tới agent.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway (nền tảng chính) — agent Part 6

- URL: https://agent-production-6f3ed500.up.railway.app (chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md))
- Cách làm: `cd 06-lab-complete && railway init` → `railway add --database redis` → `railway add --service agent` với các biến (`AGENT_API_KEY` ngẫu nhiên, `REDIS_URL=${{Redis.REDIS_URL}}`, `ENVIRONMENT=production`, …) → `railway up --service agent` → `railway domain`.
- 4 lệnh tự kiểm CP6: `/health` **200**, `/ready` **200**, `/ask` không key **401**, `/ask` có key **200**. Rate limit trên cloud: 429 từ request thứ 11 trong phút.
- Screenshot: `screenshots/dashboard.png`, `screenshots/running.png`, `screenshots/test.png`

### Exercise 3.2: So sánh `railway.toml` với `render.yaml` (trong `06-lab-complete/`)

| | `railway.toml` | `render.yaml` |
|---|---|---|
| Phạm vi | Cấu hình **một** service (build + deploy) | **Blueprint** cả hạ tầng: web service + Redis (Key Value) trong một file |
| Build | `builder = "DOCKERFILE"` | `runtime: docker`, `dockerContext` / `dockerfilePath` (file nằm trong thư mục con nên phải khai báo đường dẫn) |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | Render tự restart, không có khoá cấu hình |
| Biến môi trường | Không khai báo trong file; đặt bằng `railway variables` / dashboard | Khai báo trong file: giá trị thường ghi thẳng, secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` |
| Redis | Tạo service Redis riêng trong project, `REDIS_URL` tham chiếu `${{Redis.REDIS_URL}}` | `fromService: {type: keyvalue, property: connectionString}` tự nối `REDIS_URL` |
| PORT | Railway inject `PORT`, CMD dùng `${PORT:-8000}` | Render inject `PORT` (mặc định 10000), cùng CMD đó |

Vì sao khác: Railway coi file config là "cách build/chạy service này", còn hạ tầng (Redis, biến) nằm trên dashboard/CLI. Render Blueprint là infrastructure-as-code, mô tả cả service lẫn liên kết giữa chúng, nên phải có cơ chế riêng để không ghi secret vào file.

### Exercise 3.3: Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

1. **test** — `cloudbuild.yaml` chạy bước đầu trong `python:3.11-slim`: cài requirements + pytest, chạy `pytest tests/`. Test fail thì pipeline dừng.
2. **build** — `docker build` gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push** — đẩy cả hai tag lên Container Registry.
4. **deploy** — `gcloud run deploy` image theo `$COMMIT_SHA` (truy vết được commit nào đang chạy) lên `asia-southeast1`; min 1 / max 10 instance, 512Mi, 1 CPU, secret `OPENAI_API_KEY` lấy từ Secret Manager.
5. `service.yaml` mô tả cùng service dạng Knative khai báo: `containerConcurrency: 80`, autoscaling 1–10, `AGENT_API_KEY`/`OPENAI_API_KEY` qua `secretKeyRef`, liveness + startup probe gọi `/health`.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop/app.py`)

- **Check ở đâu:** dependency `verify_api_key()` dùng `APIKeyHeader(name="X-API-Key")`, gắn vào `/ask` bằng `Depends(verify_api_key)`.
- **Sai key:** thiếu key → **401**, sai key → **403** (Part 6 của mình trả 401 cho cả hai theo yêu cầu đề).
- **Rotate key:** key đọc từ env `AGENT_API_KEY` → đổi biến trên platform rồi restart/redeploy, không sửa code. Muốn không downtime thì cho phép tạm thời 2 key (cũ + mới) trong một khoảng chuyển tiếp.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). ..."}
```

### Exercise 4.2: JWT (`04-api-gateway/production/`)

Flow: `POST /auth/token` với username/password → server ký JWT HS256 (`sub`, `role`, `exp` = 60 phút) bằng `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token()` kiểm chữ ký + hạn, không cần tra DB.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***REDACTED***","token_type":"bearer","expires_in_minutes":60,...}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** sliding window (mỗi user một `deque` timestamp, bỏ các timestamp cũ hơn 60 s rồi đếm).
- **Giới hạn:** user 10 req/phút (`rate_limiter_user`), admin 100 req/phút (`rate_limiter_admin`).
- **Bypass cho admin:** app chọn limiter theo `role` trong JWT; token có `role=admin` (tài khoản `teacher`) dùng bucket 100/phút.
- **Hạn chế:** state nằm trong RAM của process → nhiều worker/instance mỗi cái đếm riêng, restart là reset. Part 6 chuyển sang Redis sorted set.

Vòng lặp 20 request với token `student` (request đầu tiên của 4.2 đã dùng 1 lượt):

```text
request 1 -> 200   ...   request 9 -> 200
request 10 -> 429  ...   request 20 -> 429

HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost guard

Rate limit chỉ giới hạn **số lần** gọi; một request với prompt dài vẫn tốn nhiều tiền. Cost guard giới hạn **tiền**.

Cách mình làm trong Part 6 (`06-lab-complete/app/cost_guard.py`):

1. `check_budget()` gọi **trước** khi gọi LLM: `MGET cost:daily:<YYYY-MM-DD> cost:monthly:<YYYY-MM>` (UTC). Vượt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` → **402**.
2. `record_cost()` gọi **sau** khi có câu trả lời: ước lượng token (~4 ký tự/token), tính tiền theo giá gpt-4o-mini, `INCRBYFLOAT` vào cả key ngày và key tháng trong một pipeline, đặt `EXPIRE` 2 ngày / 32 ngày → tự reset theo ngày/tháng, không cần cron.
3. State nằm trong Redis nên mọi worker/instance cộng chung một tổng. Redis mất kết nối → `check_budget` trả 503 (không đo được thì không cho chi tiền).

Script chấm xác nhận: `DAILY_BUDGET_USD=0.000001` → `402`.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks

- **`/health` (liveness):** process còn sống không. Không phụ thuộc dependency bên ngoài — nếu Redis chết mà `/health` trả lỗi thì platform restart vô ích hàng loạt container.
- **`/ready` (readiness):** có nên nhận traffic không (đang khởi động, đang shutdown, Redis/DB chưa kết nối) → 503 để LB tạm bỏ instance ra.

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.8,"version":"1.0.0","environment":"development",...,"checks":{"memory":{"status":"ok","used_percent":11.1}}}
$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Gửi request rồi `kill -TERM` ngay sau 50 ms:

```text
in-flight request result: {"answer":"Agent đang hoạt động tốt! ..."} [HTTP 200]
process exited, code=0

INFO:     Shutting down
INFO:     127.0.0.1:37026 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK   ← request vẫn hoàn thành
INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Finished server process [707]
```

Uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong → chạy phần shutdown của `lifespan` → thoát code 0. Trong Part 6 mình không tự đăng ký signal handler (dễ ghi đè handler của uvicorn khiến process không tắt), chỉ dùng `lifespan`; thêm `--timeout-graceful-shutdown 8` để `docker stop` (chờ 10 s) không phải SIGKILL. Đo được: `docker stop` exit 0 trong 0.7 s.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session trong Redis (`session:<id>`, `SETEX` TTL 3600 s) thay vì dict trong RAM. Khi có 3 instance, mỗi instance có RAM riêng: dict trong RAM thì request 2 rơi vào instance khác sẽ không thấy history của request 1, và restart/scale down làm mất hết. Đưa state ra Redis thì instance nào cũng phục vụ được bất kỳ request nào. Part 6 áp dụng tương tự cho rate limit và cost.

### Exercise 5.4: Load balancing

`docker compose -p k5-day14-scaling up -d --build --scale agent=3`:

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 16 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 16 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 16 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 16 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 30 seconds (healthy)   6379/tcp
```

10 request `POST /chat` qua Nginx (cổng 8080), `served_by` xoay vòng đều:

```text
instance-3331bb, instance-0db040, instance-fafeab, instance-3331bb, instance-0db040,
instance-fafeab, instance-3331bb, instance-0db040, instance-fafeab, instance-3331bb
```

Nginx round-robin giữa 3 container (DNS `agent` trả về 3 IP). Nếu 1 instance chết, Nginx đánh dấu upstream lỗi và chuyển request sang instance còn lại.

### Exercise 5.5: `python test_stateless.py`

```text
Request 1: [instance-0db040]  Q: What is Docker?
Request 2: [instance-fafeab]  Q: Why do we need containers?
Request 3: [instance-3331bb]  Q: What is Kubernetes?
Request 4: [instance-0db040]  Q: How does load balancing work?
Request 5: [instance-fafeab]  Q: What is Redis used for?
Instances used: {'instance-3331bb', 'instance-0db040', 'instance-fafeab'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

Giải thích: 5 câu trong cùng `session_id` đi qua cả 3 instance, nhưng `GET /chat/<id>/history` (do agent-2 phục vụ) vẫn trả đủ 10 message (5 user + 5 assistant), vì history nằm trong Redis chứ không ở RAM của instance nào. Script không tắt instance nào; nếu tắt một instance giữa chừng, history vẫn còn vì Redis vẫn sống.

---

## Part 6: Final Project

`python check_production_ready.py` (chạy đầy đủ, có Docker, trong WSL2 — `os.name == "posix"`):

```text
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

Output đầy đủ: [`evidence/check_production_ready.txt`](evidence/check_production_ready.txt).

Tóm tắt thiết kế:

| File | Làm gì |
|---|---|
| `app/config.py` | Dataclass đọc mọi cấu hình từ env bằng `default_factory` (đọc lúc tạo, không lúc import module khác); không có secret mặc định |
| `app/auth.py` | `APIKeyHeader("X-API-Key")` + `secrets.compare_digest`; thiếu/sai/server chưa đặt key → 401 |
| `app/redis_client.py` | `redis.from_url` (lazy, không ping lúc import → app khởi động được khi chưa có Redis); `redis_healthy()` ping với timeout 2 s |
| `app/rate_limiter.py` | Sliding window bằng sorted set `ratelimit:<sha256(key)[:16]>`: `ZREMRANGEBYSCORE` → `ZCARD` → `ZADD` + `EXPIRE`; vượt → 429 + `Retry-After` |
| `app/cost_guard.py` | Tổng chi phí theo ngày/tháng trong Redis (`INCRBYFLOAT`); vượt → 402 |
| `app/main.py` | `JsonFormatter` (mỗi dòng 1 JSON), middleware log mỗi request (`method`, `path`, `status`, `ms`, `request_id`), `/health` luôn 200, `/ready` 503 khi Redis down hoặc đang shutdown, `lifespan` cho startup/shutdown, handler 422/500 không lộ stack trace |
| `Dockerfile` | 2 stage `python:3.11-slim`, venv copy sang runtime, user `appuser` (uid 10001), `HEALTHCHECK`, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000}"` → uvicorn là PID 1. Image 63 MB |
| `docker-compose.yml` | `agent` (build, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis: service_healthy`, `.env` `required: false`) + `redis:7-alpine` có healthcheck và volume |

---

## AI log

Theo [RULES.md](RULES.md) mục 1.

| # | Hỏi / nhờ gì | Nhận được gì | Mình tự sửa / kiểm tra thế nào |
|---|---|---|---|
| 1 | Nhờ Claude Code (Anthropic) đọc README, RUBRIC, CHECKPOINTS, `check_production_ready.py` và viết các TODO của Part 6 | Code cho `app/*.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy `python check_production_ready.py` bản đầy đủ trong WSL2 → PASS 12/12; đọc lại từng file để giải thích được (rate limiter sliding window, vì sao `/health` không phụ thuộc Redis, vì sao `exec` trong CMD) |
| 2 | Nhờ chạy các lệnh Part 1, 2, 4, 5 và ghi output | Output thô lưu ở `evidence/` | Số MB, mã HTTP, `served_by` trong bài này là copy từ các file đó, không ước lượng |
| 3 | Nhờ trả lời câu hỏi lý thuyết (CMD vs ENTRYPOINT, liveness vs readiness, so sánh railway/render, pipeline Cloud Run) | Bản nháp câu trả lời | Đối chiếu với file nguồn trong repo (`cloudbuild.yaml`, `service.yaml`, `rate_limiter.py`…) |
| 5 | Nhờ deploy agent Part 6 lên Railway bằng CLI | Project `k5-day14-agent` (agent + Redis), URL public | Chạy 4 lệnh CP6 (200/200/401/200) và `check_production_ready.py --public-url` → ✅ |
| 6 | Nhờ chạy CP8 (stack Prometheus/Grafana/Jaeger, Locust headless, thí nghiệm phá hệ thống) | Sửa `production/Dockerfile` (thiếu `PYTHONUSERBASE` nên agent crash), CSV Locust, 4 ảnh Part 7, log thời gian alert firing | Phát hiện lượt benchmark scaling đầu bị sai số instance (`docker compose run` kéo agent về 2 replica), đã bỏ và chạy lại với `--no-deps`; số trong `BENCHMARK_REPORT.md` đối chiếu với CSV và `evidence/chaos.txt` |
| 4 | Nhờ thêm metric `agent_question_length_chars` (Exercise 7.1) và chạy load test / eval Part 7 | Sửa `develop/app.py`, file JSON trong `reports/` | Kiểm tra metric xuất hiện trong `/metrics`; số trong `BENCHMARK_REPORT.md` lấy từ JSON |
