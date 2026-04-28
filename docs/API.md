# OMR Checker API

Backend API สำหรับตรวจข้อสอบ OMR รันบน **port 8080** (ไม่ชนกับ Laravel ที่มักใช้ 80/8000)

**Deploy บน VPS (พร้อม optimize):** ดูคู่มือเต็มใน **[docs/DEPLOY.md](DEPLOY.md)** — ตั้ง systemd, workers, RAM, Nginx

**บน VPS:**  
- **ใช้เฉพาะใน VPS (ให้แค่ Laravel เรียก):** รัน API ที่ `127.0.0.1` ไม่ต้องตั้ง Nginx ให้ API → มีแค่ Laravel บนเครื่องเดียวกันเรียกได้  
- **อยากให้คนนอกเข้าได้:** ตั้ง Nginx reverse proxy + ใช้ `--host 0.0.0.0` (ดูใน DEPLOY.md)

## โครงสร้างโฟลเดอร์มาตรฐาน

- **templates/{template_id}/** – เทมเพลตแต่ละแบบ (เช่น `templates/20q/`, `templates/30q/`, `templates/50q/`) แต่ละโฟลเดอร์ต้องมี `template.json`, `config.json`, `omr_marker.jpg`; เพิ่ม `evaluation.json` ได้ถ้าต้องการให้คิดคะแนนจากเทมเพลต หรือส่ง `evaluation` เป็น JSON จาก Laravel แทน
- **Default fallback:** ถ้า request ไม่ส่ง `template_id` API จะใช้ `templates/50q/` (ตั้งค่าใน `DEFAULT_TEMPLATE_ID` ใน `api/main.py`)

## ติดตั้งและรัน API

```bash
# ติดตั้ง dependencies (รวม FastAPI, uvicorn)
pip install -r requirements.txt

# รัน API (port 8080)
python run_api.py

# หรือระบุ port อื่น
python run_api.py --port 8081

# Production: ใช้หลาย workers เพื่อรับ request พร้อมกัน (แนะนำ 4–8 ตามจำนวน CPU)
python run_api.py --workers 4 --host 0.0.0.0 --port 8080
```

- **ขนาดรูปอัปโหลด:** สูงสุด 20 MB ต่อ request (เกินได้ 413)
- **Template cache:** ระบบโหลด template.json, config.json, omr_marker.jpg เข้า memory ครั้งแรกแล้วใช้ซ้ำ ลด disk I/O

บน VPS (รันคู่กับ Laravel):

```bash
# รันในพื้นหลัง หรือใช้ systemd ด้านล่าง
python run_api.py --host 0.0.0.0 --port 8080 --workers 4
```

### Deploy บน VPS ให้ใช้ได้เหมือน Laravel (Nginx + systemd)

ทำครั้งเดียว: ตั้ง Nginx เป็น reverse proxy + ใช้ systemd ให้ API รันอัตโนมัติ แล้วเข้าใช้งานผ่าน domain (port 80/443) ได้เลย ไม่ต้อง start เองหรือจำ port 8080

**1) โปรเจกต์ + Python**

```bash
cd /var/www/OMRChecker   # หรือ path ที่คุณ deploy
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

**2) systemd — ให้ API รันอัตโนมัติ (เปิดเครื่องแล้ว start เอง, crash แล้ว restart)**

```bash
sudo cp deploy/omr-checker-api.service /etc/systemd/system/
# แก้ WorkingDirectory, User, Group และ path ของ venv ในไฟล์ถ้าไม่ใช้ /var/www/OMRChecker

sudo systemctl daemon-reload
sudo systemctl enable omr-checker-api
sudo systemctl start omr-checker-api
sudo systemctl status omr-checker-api
```

**3) Nginx — ให้เข้าได้ผ่าน domain เหมือน Laravel**

```bash
sudo cp deploy/nginx-omr-api.conf /etc/nginx/sites-available/omr-api
sudo ln -s /etc/nginx/sites-available/omr-api /etc/nginx/sites-enabled/
# แก้ server_name ในไฟล์เป็น domain จริง (เช่น omr-api.yourdomain.com)
sudo nginx -t && sudo systemctl reload nginx
```

ถ้าใช้ HTTPS: ใส่ `listen 443 ssl` และ path ของ ssl_certificate ใน config (หรือใช้ certbot หลังตั้ง domain แล้ว)

จากนั้นเข้า `https://omr-api.yourdomain.com/` หรือ path ที่ตั้งไว้ จะไปถึง API โดยไม่ต้องเปิด port 8080 ตรงๆ

คำสั่งที่ใช้บ่อย:

- `sudo systemctl restart omr-checker-api` — restart หลังอัปเดตโค้ด
- `sudo journalctl -u omr-checker-api -f` — ดู log

## Authentication (Global API Key)

ทุก endpoint **ต้องส่ง** header

```
Authorization: Bearer <OMR_INTERNAL_API_KEY>
```

**ยกเว้น** path เหล่านี้ที่ไม่ต้องใช้ key (whitelist)

- `/health` — สำหรับ systemd / monitoring
- `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` — Swagger / ReDoc UI
- HTTP `OPTIONS` request (CORS preflight)

ถ้า env var `OMR_INTERNAL_API_KEY` **ไม่ถูกตั้งค่า** → middleware ปิดอัตโนมัติ (dev mode — ห้ามใช้บน production ที่ Nginx เปิดออก public)

### ตั้ง API Key

```bash
# Generate key (32 bytes hex = 64 chars)
openssl rand -hex 32

# Set in env (systemd, docker-compose, .env, etc.)
export OMR_INTERNAL_API_KEY="abc123...64chars"
python3 run_api.py
```

ดูรายละเอียดการตั้งบน production ใน [DEPLOY.md](DEPLOY.md)

## Endpoints

| Method | Path     | Auth | คำอธิบาย                    |
|--------|----------|------|-----------------------------|
| GET    | /        | **Key** | ข้อมูล service + ลิงก์ docs |
| GET    | /health  | -    | Health check (สำหรับ monitoring) |
| GET    | /docs, /redoc | -    | API documentation UI |
| POST   | /check   | **Key** | อัปโหลดรูป OMR → ได้ responses + score |
| GET    | /checked/{file_path}         | **Key** | รูปกระดาษที่ตรวจแล้ว (ใช้ `checked_omr_filename` จาก POST /check) |
| GET    | /outputs/scans/CheckedOMRs/{file_path} | **Key** | รูปกระดาษที่ตรวจแล้ว (alias) |
| DELETE | /exam/{school_id}/{exam_id}  | **Key** | ลบไฟล์ทั้งหมดของ exam |
| DELETE | /school/{school_id}          | **Key** | ลบไฟล์ทั้งหมดของโรงเรียน |

## POST /check

- **Body:** `multipart/form-data`
  - **image** (required): ไฟล์รูปกระดาษคำตอบ (.jpg, .jpeg, .png)
  - **template_id** (optional): ชื่อเทมเพลต = ชื่อโฟลเดอร์ใต้ `templates/` (เช่น `20q`, `30q`, `50q`). Default: `50q`
  - **evaluate** (optional): `true`/`false` (default: `true`) — ถ้า `false` จะไม่ใช้ evaluation เลย ได้แค่ raw responses
  - **evaluation** (optional): **JSON string** ของ evaluation config (ส่งจาก Laravel ได้) — ถ้าส่งมา API จะใช้ชุดนี้คิดคะแนนและส่ง `score` + `evaluation` กลับ และ**รูป Checked OMR จะวาดวงกลมสีเขียวทับข้อที่ถูก สีแดงทับข้อที่ผิด** (ไม่ใช้ evaluation.json ในเทมเพลต)
  - **school_id** (optional): รหัสโรงเรียน — ใช้จัดเก็บไฟล์เป็น `CheckedOMRs/<school_id>/<exam_id>/<YYYY-MM>/<file>` เพื่อให้ลบเป็นกลุ่มได้ภายหลัง  
    รูปแบบ: `[A-Za-z0-9_-]{1,64}` (เช่น `12`, `bkk_school_001`)
  - **exam_id** (optional): รหัสแบบทดสอบ — ใช้คู่กับ `school_id` (จะใส่อันใดอันหนึ่งโดด ๆ ก็ได้)
- **Response:** JSON
  - `request_id`, `file_id`, `score`, `responses` (Roll, q1, q2, …), `evaluation` (รายละเอียดข้อละข้อ ถ้ามีการคิดคะแนน)
  - `checked_omr_path` (ถ้ามี): path เช่น `outputs/scans/CheckedOMRs/12/42/2026-04/xxx.jpg` (path ขึ้นกับว่าส่ง school_id/exam_id มาไหม)
  - `checked_omr_filename` (ถ้ามี): subpath ภายใต้ `CheckedOMRs/` สำหรับโหลดรูป เช่น `12/42/2026-04/uuid_image.jpg` — **ใช้ค่านี้ใส่ใน URL โหลดรูป**

### Path layout ของ Checked OMR

ขึ้นกับว่าส่ง `school_id`/`exam_id` หรือไม่

| ส่ง | ไฟล์เก็บที่ |
|---|---|
| school_id + exam_id | `CheckedOMRs/<school_id>/<exam_id>/<YYYY-MM>/<file>` |
| เฉพาะ school_id | `CheckedOMRs/<school_id>/<YYYY-MM>/<file>` |
| ไม่ส่งเลย (legacy) | `CheckedOMRs/<YYYY-MM>/<file>` |

> แนะนำใช้ทั้ง school_id + exam_id เสมอ — รองรับการ cleanup ผ่าน `DELETE /exam/{school_id}/{exam_id}` ได้

- **เปิดรูป Checked OMR ผ่าน HTTP:** ใช้ **`base_url + "/checked/" + checked_omr_filename`** (เช่น `http://127.0.0.1:8080/checked/12/42/2026-04/uuid_image.jpg`)

### ตัวอย่าง (curl)

```bash
curl -X POST http://localhost:8080/check \
  -H "Authorization: Bearer YOUR_INTERNAL_KEY" \
  -F "image=@/path/to/sheet.jpg" \
  -F "template_id=50q" \
  -F "school_id=12" \
  -F "exam_id=42"
```

### ตัวอย่างส่ง evaluation จาก Laravel

ส่งชุดคำตอบ (answer key) เป็น JSON จาก Laravel เพื่อให้ API คิดคะแนนให้ รูปแบบต้องตรงกับ [evaluation schema](https://github.com/Udayraj123/OMRChecker/blob/master/src/schemas/evaluation_schema.py) เช่น:

```json
{
  "source_type": "custom",
  "options": {
    "questions_in_order": ["q1..10", "q11..20", "q21..30", "q31..40", "q41..50"],
    "answers_in_order": ["A","B","C", ... ],
    "should_explain_scoring": false,
    "enable_evaluation_table_to_csv": true
  },
  "marking_schemes": {
    "DEFAULT": { "correct": "1", "incorrect": "0", "unmarked": "0" }
  }
}
```

```bash
curl -X POST http://localhost:8080/check \
  -H "Authorization: Bearer YOUR_INTERNAL_KEY" \
  -F "image=@/path/to/sheet.jpg" \
  -F "evaluation={\"source_type\":\"custom\",\"options\":{...},\"marking_schemes\":{...}}"
```

Laravel ตัวอย่าง (พร้อม Bearer token + school_id / exam_id):

```php
$evaluation = [
    'source_type' => 'custom',
    'options' => [
        'questions_in_order' => ['q1..10', 'q11..20', 'q21..30', 'q31..40', 'q41..50'],
        'answers_in_order' => array_merge(...), // 50 ตัว A/B/C/D/E
        'should_explain_scoring' => false,
        'enable_evaluation_table_to_csv' => true,
    ],
    'marking_schemes' => [
        'DEFAULT' => ['correct' => '1', 'incorrect' => '0', 'unmarked' => '0'],
    ],
];

Http::withToken(config('omr.internal_api_key'))
    ->attach('image', $imageContent, 'sheet.jpg')
    ->post($apiUrl . '/check', [
        'template_id' => '50q',
        'school_id'   => (string) $school->id,
        'exam_id'     => (string) $exam->id,
        'evaluation'  => json_encode($evaluation),
    ]);
```

> `Http::withToken($key)` จะใส่ `Authorization: Bearer $key` อัตโนมัติ

## DELETE Endpoints (cleanup)

ใช้สำหรับลบไฟล์ที่ตรวจแล้วเมื่อ Laravel ลบ exam หรือ school — ต้องใส่ `Authorization: Bearer <key>` ตามนโยบาย global auth

### DELETE /exam/{school_id}/{exam_id}

ลบไฟล์ checked OMR ทั้งหมดของ exam หนึ่ง ๆ — ใช้ตอน Laravel ลบแบบทดสอบ

```bash
curl -X DELETE http://localhost:8080/exam/12/42 \
  -H "Authorization: Bearer YOUR_INTERNAL_KEY"
```

Response

```json
{ "deleted": true, "path": "12/42", "files_removed": 35 }
```

ถ้าไม่มีไฟล์อยู่ (ไม่เคยส่งสอบนี้)

```json
{ "deleted": false, "message": "Exam folder not found", "path": "12/42" }
```

### DELETE /school/{school_id}

ลบไฟล์ทั้งหมดของโรงเรียน — **ใช้ระวัง** (ลบทุก exam, ทุกเดือน, ทุกไฟล์ภายใต้ school_id)

```bash
curl -X DELETE http://localhost:8080/school/12 \
  -H "Authorization: Bearer YOUR_INTERNAL_KEY"
```

### Laravel ตัวอย่าง

```php
// ใน ExamObserver::deleted หรือ ExamController::destroy
Http::withToken(config('omr.internal_api_key'))
    ->delete(config('omr.api_url_base') . "/exam/{$school->id}/{$exam->id}");
```

`config/omr.php`

```php
'api_url_base'     => env('OMR_API_URL_BASE', 'http://127.0.0.1:8080'),
'internal_api_key' => env('OMR_INTERNAL_API_KEY', ''),
```

`.env` ของ Laravel

```bash
OMR_API_URL_BASE=http://127.0.0.1:8080
OMR_INTERNAL_API_KEY=YOUR_INTERNAL_KEY  # ค่าเดียวกับฝั่ง OMR API
```

## ⚠️ ผลกระทบ: การแสดงรูป Checked OMR ในเบราว์เซอร์

เพราะ `/checked/{path}` ก็ต้อง auth → `<img src="...">` ตรง ๆ ไม่ทำงานแล้ว

**ทางเลือก** (เลือกตามที่เหมาะ)

### A. Laravel proxy รูปให้ browser (แนะนำ)

สร้าง route ใน Laravel ที่รับ path → fetch รูปจาก OMR API ด้วย Bearer token → return ให้ browser

```php
// routes/web.php
Route::get('/omr/checked-image/{path}', [OmrController::class, 'checkedImage'])
    ->where('path', '.*');

// app/Http/Controllers/OmrController.php
public function checkedImage(string $path)
{
    $url = config('omr.api_url_base') . '/checked/' . $path;
    $response = Http::withToken(config('omr.internal_api_key'))->get($url);
    if ($response->status() !== 200) {
        abort(404);
    }
    return response($response->body())
        ->header('Content-Type', $response->header('Content-Type'));
}
```

แล้วใน Blade ใช้

```html
<img src="{{ url('/omr/checked-image/' . $checkedOmrFilename) }}" alt="Checked OMR">
```

### B. Signed URLs (advanced)

ถ้ามี traffic เยอะแล้ว Laravel proxy หนัก → ทำ signed URL ที่หมดอายุ (เช่น 5 นาที) ใน OMR API ให้ browser hit ตรง — แต่ต้อง implement เอง

### C. Whitelist `/checked/` (ไม่แนะนำ — ไม่ปลอดภัย)

ถ้ายอม trade off ความปลอดภัย สามารถเอา `/checked/` ออกจาก auth ได้ — แก้ใน `api/main.py`

```python
_AUTH_BYPASS_PREFIXES: tuple[str, ...] = ("/docs", "/redoc", "/openapi.json", "/checked/")
```

> ⚠️ ใครก็เข้าเว็บแล้ว guess UUID ได้จะดูรูปคนอื่น — ใช้ได้เฉพาะกรณีที่ filename ยากเดา (uuid v4) และข้อมูลไม่ sensitive

### ตัวอย่าง (Flutter / Laravel)

- **Flutter:** ส่ง `MultipartRequest` ไปที่ `https://omr-api.yourdomain.com/check` (หรือ `http://VPS_IP:8080/check` ถ้าไม่ผ่าน Nginx) พร้อม field `image` (ไฟล์) และถ้าต้องการคิดคะแนนฝั่ง API ส่ง field `evaluation` (JSON string)
- **Laravel:** ใช้ `Http::attach(...)->post(...)` แล้วส่ง `evaluation` เป็น `json_encode($array)` ถ้าต้องการให้ API คิดคะแนนจากชุดคำตอบที่ Laravel ส่งมา

## Port

- **8080** – ค่าเริ่มต้นของ OMR API (ไม่ชน Laravel 80/8000)
- เปลี่ยนได้ด้วย `python run_api.py --port 8081`
