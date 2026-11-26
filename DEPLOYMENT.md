# Deployment Information

## Public URL

https://agent-production-6f3ed500.up.railway.app

## Platform

Railway — project `k5-day14-agent`, environment `production`, 2 service:

- `agent`: build từ `06-lab-complete/Dockerfile` (`railway.toml`: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`), deploy bằng `railway up --service agent` trong `06-lab-complete/`
- `Redis`: database Redis của Railway; agent kết nối qua private network (`redis.railway.internal`)

## Test Commands

Đặt `BASE` là URL trên và `AGENT_API_KEY` là key đã đặt trên Railway (không ghi trong repo). Mỗi lệnh chỉ in mã HTTP:

```bash
BASE=https://agent-production-6f3ed500.up.railway.app
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-26 (`evidence/cp6_railway.txt`):

```text
200
200
401
200
--- body /ready và /ask (có key) ---
{"status":"ready","redis":"ok"}
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được."}
--- rate limit: 12 request liên tiếp (2 request trước đó đã dùng 2 lượt) ---
200 200 200 200 200 200 200 200 429 429 429 429
```

`python check_production_ready.py --static-only --public-url https://agent-production-6f3ed500.up.railway.app`:

```text
✅ Public URL: GET https://agent-production-6f3ed500.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), không ghi trong code:

| Biến | Giá trị |
|---|---|
| `AGENT_API_KEY` | chuỗi ngẫu nhiên 32 ký tự (secret, không ghi ở đây) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` (reference variable tới service Redis) |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Railway tự cấp; `CMD` của Dockerfile dùng `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
