# ECUS Windows Agent

HTTP service trên máy Windows có **ECUS5-VNACCS**. Nhận job từ n8n (**ASSISTANT 001. MEN-CHUEN SEND ECUS**) rồi RPA: mở EDA xuất khẩu → điền tab → **Ghi**.

## Luồng

```
n8n  POST /jobs  →  hàng đợi FIFO (1 worker)  →  stub | pywinauto
n8n  GET /jobs/{jobId}  →  queued | running | success | failed
```

## Cài nhanh (máy Windows)

1. Cài [Python 3.11+](https://www.python.org/downloads/) (tick **Add to PATH**).
2. Copy thư mục `ecus-windows-agent`, rồi:

```powershell 
copy .env.example .env
# sửa API_KEY
# ECUS_EXE_PATH mặc định: C:\ECUSDATA\ECUS5VNACCS.exe
.\run.ps1
```

3. Kiểm tra:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
.\test-job.ps1
```

## Nối n8n

Trong node **Normalize ECUS Jobs**:

```js
const AGENT_URL = 'http://10.x.x.x:8787/jobs';
const AGENT_API_KEY = 'same-as-.env-API_KEY';
```

Firewall: mở TCP `8787` chỉ cho máy/VPN n8n.

## Chế độ RPA

| `RPA_BACKEND` | Hành vi |
|---------------|---------|
| `stub` | Không mở ECUS — log payload, giả success (test mạng n8n). |
| `pywinauto` | Attach/start `ECUS5VNACCS.exe`, mở **Đăng ký mới tờ khai xuất khẩu (EDA)**, điền form, bấm **Ghi**. |

### POC RPA thật (không cần Inspect.exe)

1. Login ECUS sẵn (agent **không** tự login), giữ cửa sổ ECUS5-VNACCS hiện trên màn hình.
2. Dump UI (dùng `.cmd` để tránh lỗi ExecutionPolicy):

```bat
dump_ecus_ui.cmd
```

Dòng đầu `logs\ecus-ui-dump.txt` phải là `WINDOW: ECUS5-VNACCS...` — **không** được là `Taskbar`.

3. `.env`:

```env
RPA_BACKEND=pywinauto
ECUS_EXE_PATH=D:\ECUSDATA\ECUS5VNACCS.exe
ECUS_PROCESS_NAME=ECUS5VNACCS
```

4. Chạy agent (`run.bat`), rồi 1 job mẫu:

```bat
run_one_job_local.cmd
```

Hoặc: `.\venv\Scripts\python.exe scripts\run_one_job_local.py`

5. Nếu lỗi: xem `logs\ecus-agent.log` + `screenshots\`, gửi kèm `logs\ecus-ui-dump.txt`.

**Lưu ý PowerShell:** nếu `.\scripts\xxx.ps1` báo ExecutionPolicy, dùng file `.cmd` ở trên hoặc:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_one_job_local.ps1
```

## API

### `GET /health`

`busy`, `holder` là job đang chạy trên ECUS. `queueDepth` là số job đang chờ.

### `POST /jobs`

Header: `X-Api-Key: <API_KEY>`  
Body: 1 tờ (contract n8n).

Agent nhận job vào hàng và trả ngay. Không chờ ECUS nhập xong.

- `202` job mới: `status` là `queued` (hoặc `running` nếu vừa tới lượt), kèm `position`. `position` 1 là job đang chạy hoặc job sắp chạy.
- `200` cùng `jobId` đang `queued` hoặc `running`: không thêm một lượt nữa.
- `409` nếu `jobId` đã Ghi thành công.
- `429` nếu hàng chờ đầy (`JOB_QUEUE_MAX`, mặc định 200).
- Job đã `failed`: POST lại thì vào cuối hàng.

### `GET /jobs/{jobId}`

Cùng header `X-Api-Key`. Hỏi đến khi `status` là `success` hoặc `failed`. Body khi xong giữ `so_to_khai`, `hang_hoa_filled`, `warnings`.

```json
{
  "success": true,
  "message": "ECUS Ghi completed",
  "so_to_khai": "",
  "jobId": "...",
  "status": "success",
  "backend": "pywinauto"
}
```

n8n phải poll `GET` sau `POST`. Chỉ nhìn `success` trên response của `POST` sẽ coi job đang chờ là thất bại.

- Nhiều job cùng lúc, hoặc user khác gửi thêm khi job trước chưa xong: xếp theo thứ tự tới, chạy lần lượt. Không trả `409` chỉ vì ECUS đang bận.
- Mỗi job tối đa `JOB_TIMEOUT_SEC` (mặc định 540s). Hết giờ thì job đó `failed`, job sau vẫn chạy.
- Thành công khi điền đủ dòng hàng hóa và Ghi không lỗi.
- Hàng nằm ở `logs/job_queue.json`. Agent khởi động lại thì job `queued` chạy tiếp. Job đang `running` lúc process chết được đánh `failed` (trừ khi đã kịp ghi nhận thành công) và không tự chạy lại.

## Vận hành

- Session Windows **luôn đăng nhập**, ECUS đã login user robot.
- Không sleep/lock màn hình.
- 1 agent / 1 session ECUS. Một worker nhập từng tờ.
- Log: `logs/ecus-agent.log`

Chạy nền (Task Scheduler / NSSM):

```text
...\ecus-windows-agent\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8787
```

Working directory = `ecus-windows-agent`.
