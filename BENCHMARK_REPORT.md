# Benchmark & Observability Report — Day 14

**Họ tên:** Phạm Hoàng Huy — 2A202603836
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Intel Core i7-13650HX, WSL2 Ubuntu 24.04 (20 vCPU, 7 GB RAM), Python 3.12, Docker Engine 29.1.3

> Số liệu mục 2–3 lấy từ `reports/*.json`, mục 4 từ Prometheus/Jaeger (`evidence/chaos.txt`, `evidence/prom_queries.txt`, `evidence/trace_logs.txt`), mục 5 từ `benchmark-results/*_stats.csv`. Output console đầy đủ trong `evidence/`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock mất ~150–220 ms/câu; 500 ms cho dư ~2× để chịu tải mà người dùng vẫn thấy "tức thì". Đặt theo p95 vì average che mất đuôi chậm (xem 2.2) |
| Error rate `/ask` | < 2 % | Upstream LLM giả lập lỗi 1 % (`MOCK_ERROR_RATE=0.01`); 2 % chừa error budget cho lỗi upstream nhưng vẫn bắt được sự cố thật (vd tăng lên 20 %) |
| Accuracy trên golden dataset | ≥ 85 % | Ngưỡng của job CI; v2 hiện đạt 100 %. Không đặt 100 % làm gate CI vì golden dataset sẽ thêm câu khó, chặn tuyệt đối làm mọi PR bị kẹt |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 request mỗi mức, `MOCK_SLOW_RATE=0.03` mặc định)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.99 | 185.9 | 219.6 | 1693.4 | 0.33 % | ✅ PASS |
| 10 | 37.32 | 187.1 | 216.9 | 1682.8 | 0.67 % | ✅ PASS |
| 50 | 112.33 | 192.9 | 234.1 | 1700.0 | 1.00 % | ✅ PASS |
| 100 | 86.93 | 255.3 | 1676.3 | 1780.1 | 1.67 % | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c{1,10,50,100}.json`.

**Nhận xét:** RPS tăng gần tuyến tính từ 1 → 10 (3.99 → 37.32, ×9.4) vì mỗi request chủ yếu là *chờ* (sleep giả lập LLM), một process async chồng được nhiều request chờ cùng lúc. Từ 10 → 50 chỉ còn ×3, và ở 100 RPS **giảm** (112 → 87) trong khi p50 tăng 193 → 255 ms và p95 vọt lên 1676 ms. Agent chạy 1 process, 1 event loop: tới ~50 request đồng thời thì chi phí xử lý (JSON, middleware, metrics, log) và chính client load test trên cùng máy thành nút thắt, request phải xếp hàng. Thêm vào đó, mỗi lượt có ~3 % request chậm thêm 1.5 s; ở concurrency 100 với chỉ 300 request (3 lượt), các request chậm chiếm tỉ trọng lớn trong tổng thời gian chạy. Muốn tăng tiếp phải scale ngang (nhiều worker/instance), không phải tăng concurrency.

### 2.2 Tail latency (concurrency 20, 300 request)

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 190.4 | 215.0 | 218.1 |
| 0.03 (mặc định) | 232.8 | 217.5 | 1699.4 |
| 0.1 | 349.2 | 1692.8 | 1709.9 |

Nguồn: `reports/load_v2_slow_{0,0.03,0.1}.json`.

**Nhận xét:** Từ 0 → 0.03, avg chỉ tăng 22 % (190 → 233 ms) và p95 gần như không đổi, nhưng **p99 tăng ~8×** (218 → 1699 ms): 3 % request chậm nằm hết ở trên phân vị 97, nên chỉ p99 "nhìn thấy". Ở 0.1, request chậm chiếm 10 % nên kéo cả p95 lên 1693 ms và SLO p95 fail. Average gộp 97 request nhanh với 3 request rất chậm thành một con số "trông ổn", trong khi người dùng gặp request chậm vẫn chờ 1.7 s. Vì vậy SLO đặt theo p95/p99: nó trả lời "x % người dùng chờ không quá bao lâu", và nhạy với đúng loại sự cố hay gặp (một dependency thỉnh thoảng chậm).

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 57.0 | 109.0 | 116.6 |
| Tổng cost (USD) | 0.000403 | 0.000760 | 0.000816 |
| Cost / câu đúng (USD) | 0.00040275 | 0.00003619 | 0.00003547 |
| Quality gate | ❌ FAIL (min 80 %) | ✅ PASS (min 80 %, 0 regression) | ✅ PASS (min 100 %, 0 regression) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| security-02 ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu keyword `["jwt", "api key"]` vào `KNOWLEDGE_BASE`: API key là chuỗi bí mật cố định; JWT là token có chữ ký số, mang thời điểm hết hạn, server chỉ kiểm chữ ký |
| scaling-03 ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm tài liệu keyword `["hpa", "kubernetes", "autoscal"]`: HPA scale theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |

Nguyên nhân: retrieval của v2 không tìm thấy tài liệu nào khớp keyword nên LLM rơi vào câu trả lời mặc định. Trước khi thêm, đã kiểm tra `"jwt"`, `"api key"`, `"hpa"`, `"kubernetes"` không xuất hiện ở tài liệu/câu hỏi nào khác → không "cướp" retrieval của câu khác. Eval cuối với `--baseline reports/eval_v2.json --min-accuracy 1.0`: cải thiện 2 câu, regression 0. Bản sửa đã chép sang `production/utils/mock_llm.py` cho stack Docker.

**Trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- v2 tốn gần **gấp đôi token/câu** (57 → 109) vì nhét tài liệu retrieve được vào prompt, nên tổng cost tăng 89 %. Nhưng accuracy từ 4.3 % lên 91.3 %, nên **cost / câu đúng giảm ~11×** (0.000403 → 0.0000362). So "cost / câu" thì v1 rẻ hơn; so "cost / câu đúng" mới thấy v1 thực ra đắt hơn nhiều vì gần như mọi câu trả lời đều vô dụng.
- Latency: bước retrieval thêm ~20 ms (p50 156 → 183 ms). p95 của lượt eval v2 (1537.6 ms) cao là do có 2/23 câu rơi vào request chậm ngẫu nhiên (`docker-01`, `obs-06` ~1.69 s), không phải do retrieval; ở lượt eval sau khi sửa, p95 là 212.8 ms. Với 23 mẫu, 2 request chậm đủ đẩy p95 — eval nhỏ không nên dùng để kết luận về latency, việc đó để load test làm.
- Sửa KB thêm ~7.6 token/câu (109 → 116.6) cho +8.7 điểm accuracy; cost / câu đúng tiếp tục giảm nhẹ.

**Job CI (Exercise 7.3):** [`.github/workflows/eval.yml`](.github/workflows/eval.yml) — mỗi PR: cài requirements, bật agent v2 với `MOCK_ERROR_RATE=0`, chờ `/health`, chạy `run_eval.py --min-accuracy 0.85`. `run_eval.py` trả exit code 1 khi accuracy dưới ngưỡng hoặc có regression → step fail → job fail; bật branch protection "require status check `quality-gate / eval`" thì PR không merge được.

**Exercise 7.1 — metrics** (`evidence/part7_metrics.txt`):

- Counter: `http_requests_total{method="POST",route="/ask",status="200"} 1.0`
- Gauge: `agent_in_flight_requests 0.0`
- Histogram: `llm_request_duration_seconds_bucket{le="0.2",model="mock-llm-small"} 1.0` (cùng `_sum`, `_count`)
- Đã thêm Histogram `agent_question_length_chars` (buckets 20…2000) vào `develop/app.py`, quan sát trong `/ask`.
- Nếu label `route` dùng URL thật: mỗi URL lạ bot quét (`/wp-admin`, `/.env`, `/a1b2c3`…) tạo một time series mới cho mọi metric có label đó (× method × status × bucket của histogram). Cardinality tăng không giới hạn → Prometheus tốn RAM/disk theo số series, query chậm, có thể OOM. Dùng `route.path` (template) hoặc `unmatched` giữ số series cố định.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build` trong `production/`.

> Có 2 chỗ phải sửa hoặc làm khác hướng dẫn mới chạy được:
> 1. **`production/Dockerfile` có sẵn bị lỗi:** container agent crash với `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân: `useradd -d /app` đặt HOME của user `agent` là `/app`, nên Python tìm package ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Đã sửa bằng một dòng `ENV PYTHONUSERBASE=/home/agent/.local`.
> 2. **`docker compose run locust` tự kéo service `agent` về `deploy.replicas: 2`.** Lượt đo "1 instance" đầu tiên thực ra có 2 instance từ phút thứ 2, lượt "3 instance" bị hạ về 2 (thấy trong Prometheus: `count(up{job="agent"}==1)`). Đã bỏ kết quả đó và chạy lại với `run --rm --no-deps`; số container ghi trong `evidence/scale_instances.txt`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải — [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) (100 user Locust, 42.5 req/s, error 0.92 %, p95 296 ms)
- [x] Một trace chậm trong Jaeger — [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (span có event `exception`, HTTP 503) — [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus, **FIRING (2)**: `AgentHighErrorRate` + `LLMCostBurnRateHigh` — [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS` (`evidence/promtool_test.txt`).

**Exercise 7.4** (số lấy ở mốc baseline 08:30:30 UTC, Locust 30 user, 2 instance — `evidence/prom_queries.txt`):

1. **p95 = 290 ms, avg = 219 ms.** p95 cao hơn avg ~70 ms vì có đuôi chậm: ~3 % request bị chậm thêm 1.5 s (`MOCK_SLOW_RATE=0.03`). Phần lớn request ở mức ~190 ms, nhưng một nhóm nhỏ rất chậm kéo p95/p99 lên trong khi avg chỉ tăng nhẹ. Khoảng cách avg ↔ p99 là thước đo tail latency.
2. **Có, chia gần đều:** `172.18.0.4` 8.92 req/s, `172.18.0.2` 8.95 req/s (chênh 0.3 %). Nginx round-robin; trong ảnh Grafana (lượt 100 user) hai đường cũng chồng gần sát nhau.
3. **Input 1384 tokens/s > output 731 tokens/s** vì v2 nhét tài liệu retrieve được (RAG context) cùng prompt vào input của mỗi câu, còn câu trả lời thì ngắn. Đổi sang v1 (không retrieval) thì input giảm còn 608 tokens/s.
4. **`curl http://localhost:8080/metrics` → 404.** Nginx chặn `/metrics` ở cổng public: metrics lộ thông tin nội bộ (route, số request, chi phí, version) và nếu ai cũng gọi được thì dễ bị lạm dụng. Prometheus scrape thẳng từng container agent qua network nội bộ `obs_net` (DNS service discovery), không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Locust 30 user chạy liên tục; mỗi thay đổi áp dụng bằng `docker compose up -d --no-deps --scale agent=2 agent` với biến môi trường tương ứng. Thời gian đo bằng script poll `/api/v1/alerts` mỗi 5 giây, tính từ lúc chạy lệnh đổi cấu hình (gồm cả thời gian recreate container) — `evidence/chaos.txt`.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | **88 s** (error rate 1.15 % → 18.2 %) | Error rate (đỏ), Requests/s theo status code (503 tăng), LLM calls theo kết quả |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | **149 s** (p95 0.29 s → 1.92 s, avg chỉ 0.67 s) | Latency p50/p95/p99 — p95/p99 vọt lên, avg tăng ít hơn |
| C | `LLM_MODEL=mock-llm-large` (giữ A) | `LLMCostBurnRateHigh` | **154 s** (cost $0.72/giờ → $13.67/giờ, ~19×; p95 0.66 s) | Cost burn rate (USD/giờ), LLM latency p95 theo model |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` | **46 s** (`sum(up)` = 1) | Instances up (2 → 1), Requests/s theo instance |
| E | `AGENT_VERSION=v1` | Không có (sau 3 phút: `alerts: none`) | — | Retrieval hit ratio mất dữ liệu (v1 không retrieve), Tokens/s giảm (input 1384 → 608/s), bảng "Phiên bản đang chạy" đổi sang v1 |

Thời gian khớp với `for` của từng rule cộng độ trễ của cửa sổ `rate(...)` và chu kỳ scrape/evaluate: A (`for: 1m`) ~1.5 phút; B, C (`for: 2m`) ~2.5 phút; D (`for: 30s`) ~46 s. C dùng cửa sổ `[5m]` nhưng vẫn firing sớm vì chi phí model large vượt ngưỡng $2/giờ rất xa, chỉ cần một phần cửa sổ là đủ.

**Thí nghiệm E không làm alert nào firing. Phát hiện bằng cách nào?**

Metric hạ tầng (latency, lỗi, instance up) đều "xanh", thậm chí đẹp hơn: p95 giảm từ 290 ms xuống 198 ms vì v1 bỏ bước retrieval. Nhưng accuracy rơi từ 91–100 % xuống 4.3 % (mục 3). Cách bắt loại sự cố này:

- **Chặn trước khi deploy:** quality gate eval trên golden dataset trong CI (`run_eval.py --min-accuracy ... --baseline ...`), như job `.github/workflows/eval.yml`. Đây là lớp chính.
- **Giám sát sau khi deploy:** chạy eval định kỳ (synthetic) trên production và đẩy accuracy thành metric có alert; alert trên metric proxy như retrieval hit ratio giảm đột ngột, tokens/s hay độ dài câu trả lời thay đổi bất thường, `agent_info{version}` đổi ngoài lịch release; thu feedback người dùng và lấy mẫu câu trả lời để LLM-as-judge chấm.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được (lượt 1 instance, đang ramp 100 user):

- `trace_id`: `7e5d8c41640e5fc11af738b0eb652d6c`
- Tổng thời gian: **4642.5 ms** (`POST /ask`, HTTP 200)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` — 4623.4 ms (99.6 %)**; `agent.retrieve_context` chỉ 17.2 ms.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep <trace_id>`):

```json
{"ts": "2026-11-26T08:11:56.299149+00:00", "level": "INFO", "service": "observable-agent", "instance": "2f704587223f", "event": "llm_call", "trace_id": "7e5d8c41640e5fc11af738b0eb652d6c", "span_id": "af182c0214717f38", "request_id": "5457dcd759c7", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 73, "output_tokens": 35, "cost_usd": 3.2e-05, "llm_ms": 1687.1}
{"ts": "2026-11-26T08:11:56.299427+00:00", "level": "INFO", "service": "observable-agent", "instance": "2f704587223f", "event": "http_request", "trace_id": "7e5d8c41640e5fc11af738b0eb652d6c", "span_id": "af182c0214717f38", "request_id": "5457dcd759c7", "method": "POST", "route": "/ask", "status": 200, "ms": 1705.8}
```

Phân tích: log ghi `llm_ms=1687` (≈ 120 ms latency mock + 1500 ms của request "slow") nhưng span `chat` dài 4623 ms. Phần chênh ~2.9 s là thời gian coroutine chờ event loop chạy lại sau `await`: container agent bị giới hạn `cpus: 0.5` và lúc đó chỉ có 1 instance gánh tải đang tăng, nên event loop bị nghẽn CPU. Trace cho thấy *đâu* chậm (bước LLM, không phải retrieval); đối chiếu log theo `trace_id` cho thấy *vì sao*: một request vốn đã chậm do upstream, cộng thêm hàng đợi CPU trong process.

Trace lỗi: `trace_id` `8b35aa530dcdbcf1b32bf7e8240d8761`, 858 ms, HTTP 503, event `exception: Upstream LLM error: 503 Service Unavailable (mock)` trên span `agent.run`; log khớp: `"event": "llm_error", "trace_id": "8b35aa530dcdbcf1b32bf7e8240d8761"` (`evidence/trace_logs.txt`).

---

## 5. Benchmark scaling (production, Locust headless, 100 users, ramp 10/s, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.11 (`/ask`: 54.52) | 190 | 220 | 1700 | 62 / 7883 (0.79 %) |
| 3 | 61.41 (`/ask`: 55.72) | 190 | 220 | 1700 | 84 / 7875 (1.07 %) |

Nguồn: hàng `Aggregated` (hàng `POST /ask` trong ngoặc) của `benchmark-results/scale_1_stats.csv`, `scale_3_stats.csv`. Số container agent lúc chạy: 1 và 3 (`evidence/scale_instances.txt`).

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không cải thiện** gì: RPS +2 %, p50/p95/p99 giữ nguyên, chênh lệch failure nằm trong nhiễu của `MOCK_ERROR_RATE=1 %`. Lý do:

1. **Tải không đủ làm 1 instance bão hoà.** Locust là closed-loop: mỗi user gửi request, chờ trả lời, rồi nghỉ `between(0.5, 2)` s (trung bình 1.25 s). 100 user / (1.25 s + ~0.21 s) ≈ 68 req/s là trần do chính Locust đặt ra; đo được 60–61 req/s, gần trần đó. 1 instance đã phục vụ kịp, thêm instance không có việc để làm.
2. **Nút thắt latency là LLM, không phải agent.** p50 ≈ 190 ms gần bằng latency mock LLM (120 ms + jitter + retrieval ~20 ms); p99 = 1700 ms là các request "slow" (+1500 ms) của upstream. Thêm instance không làm LLM nhanh hơn, nên p95/p99 không đổi (panel "LLM latency p95" trong ảnh Grafana đứng ở ~196–199 ms dù tải tăng).

Scale ngang chỉ giúp khi chính agent là nút thắt (CPU/event loop bão hoà, như trace 4.6 s ở mục 4.3 khi 1 instance 0.5 CPU đang ramp). Muốn thấy khác biệt phải tăng tải tới mức 1 instance bão hoà (nhiều user hơn, hoặc bỏ wait time). Còn tail latency do upstream thì cần cách khác: timeout + retry/hedging, cache, hoặc model nhanh hơn.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **SLO phải đặt theo percentile.** 3 % request chậm làm p99 tăng 8× trong khi avg chỉ tăng 22 % (mục 2.2); thí nghiệm B: p95 1.92 s nhưng avg 0.67 s.
  2. **So phiên bản bằng "cost / câu đúng", và lỗi chất lượng không làm metric hạ tầng đỏ.** v2 đắt gấp đôi mỗi câu nhưng rẻ hơn 11× mỗi câu đúng; còn đổi về v1 thì dashboard xanh hơn (p95 giảm) dù accuracy còn 4 %. Eval trong CI là lớp bắt được loại lỗi này.
  3. **Phải kiểm tra mình đang đo đúng thứ định đo.** `docker compose run` âm thầm đổi số instance làm hỏng benchmark scaling lượt đầu; chỉ phát hiện nhờ đối chiếu `up{job="agent"}` trong Prometheus. Scale không giúp khi nút thắt là upstream hoặc khi load generator tự giới hạn tải.
- Nếu đưa agent này lên production thật, sẽ thêm:
  - **Metric chất lượng** từ eval định kỳ trên production, alert khi accuracy giảm; alert khi retrieval hit ratio giảm đột ngột.
  - **Giám sát ở tầng load balancer và synthetic check từ bên ngoài**: tỉ lệ 5xx/504 của Nginx (lỗi ở Nginx không hiện trong metric của agent).
  - **SLO burn-rate alert nhiều cửa sổ** (vd 1 h + 6 h) thay cho ngưỡng tĩnh, để vừa nhanh vừa ít báo động giả.
  - **Saturation**: CPU throttling của container và độ trễ event loop — trace 4.6 s ở mục 4.3 do nghẽn CPU nhưng không alert nào bắt được.
  - **Chi phí theo ngày/tháng so với budget** (như cost guard Part 6), không chỉ burn rate theo giờ.
