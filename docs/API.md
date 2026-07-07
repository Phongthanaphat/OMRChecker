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

# รัน API (port 8080) — ต้องตั้ง OMR_INTERNAL_API_KEY ก่อน ไม่งั้น app ไม่ยอม start
# (local dev ที่ยังไม่อยากตั้ง key: ใช้ OMR_ALLOW_NO_AUTH=1 แทน)
OMR_INTERNAL_API_KEY=$(openssl rand -hex 32) python run_api.py

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
- `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` — Swagger / ReDoc UI (เฉพาะตอน `OMR_ENABLE_DOCS=true`)

ถ้า env var `OMR_INTERNAL_API_KEY` **ไม่ถูกตั้งค่า** → app จะ **ไม่ยอม start** (RuntimeError)
เว้นแต่ตั้ง `OMR_ALLOW_NO_AUTH=1` ไว้ชัดเจน (สำหรับ local dev เท่านั้น — ห้ามใช้บน production)

แนะนำ production:

```bash
OMR_ENABLE_DOCS=false
```

เมื่อปิด docs แล้ว จะไม่ expose `/docs`, `/redoc`, `/openapi.json` เลย (404)

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
| GET    | /templates | **Key** | รายการ `template_id` ที่มี `template.json` |
| GET    | /health  | -    | Health check (สำหรับ monitoring) |
| GET    | /docs, /redoc | - (when enabled) | API documentation UI (ปิดได้ด้วย `OMR_ENABLE_DOCS=false`) |
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
  - **school_id** (optional): รหัสโรงเรียน — ใช้จัดโฟลเดอร์ใต้ `CheckedOMRs/school/<school_id>/...` (ถ้าไม่ส่งจะใช้ `_unknown`)  
    รูปแบบ: `[A-Za-z0-9_-]{1,64}` (เช่น `12`, `bkk_school_001`)
  - **exam_id** (**บังคับ**): รหัสแบบทดสอบ/วิชา — Laravel ต้องส่งทุกครั้ง ไม่ส่งจะได้ **HTTP 400** (`detail` อธิบายว่าประมวลผลไม่ได้)  
    รูปแบบ: `[A-Za-z0-9_-]{1,64}` — ใช้จัดเก็บรูป checked และให้สแกนซ้ำของ Roll เดิมทับไฟล์เดิมได้ (ดู Path layout)
  - **require_roll** (optional): `true`/`false` (default: `true`) — `false` = **ข้าม Roll validation** สำหรับ exam โหมด anonymous (ตรวจคะแนนอย่างเดียว นักเรียนไม่ฝนรหัส) กระดาษที่ Roll ว่าง/ไม่ครบจะตรวจผ่านได้ปกติ และรูป checked จะเก็บแบบ `<YYYY-MM>/<uuid>_<ชื่อไฟล์>.jpg` (ไม่ใช่ by-roll)
- **Response:** JSON
  - `request_id`, `file_id`, `score`, `responses` (Roll, q1, q2, …), `evaluation` (รายละเอียดข้อละข้อ ถ้ามีการคิดคะแนน)
  - `checked_omr_path` (ถ้ามี): path ภายใต้โปรเจกต์ เช่น `outputs/scans/CheckedOMRs/school/12/42/by-roll/12345.jpg`
  - `checked_omr_filename` (ถ้ามี): subpath ภายใต้ `CheckedOMRs/` สำหรับโหลดรูป — **ใช้ค่านี้ใส่ใน URL โหลดรูป** (รูปแบบขึ้นกับว่ามี Roll ในเทมเพลตหรือไม่ ดูตารางด้านล่าง)

### การตรวจ Roll (เมื่อเทมเพลตมี `customLabels.Roll`)

- หลังอ่านกระดาษแล้ว API ตรวจค่า **Roll** (สตริงที่ต่อจากช่อง roll ตาม `customLabels.Roll` ใน `template.json`)
- ต้องเป็น **ตัวเลข 0–9 ล้วน** และความยาว **อย่างน้อย 4** และ **ไม่เกินจำนวนช่อง roll** ในเทมเพลต (เช่น 50q มี 5 ช่อง → รับเฉพาะ 4–5 หลัก)
- ถ้าไม่ผ่าน (เช่น อ่านได้ `33` หรือ `13424235454` มักเพราะภาพไม่ชัดหรือกรอบไม่ครบ) จะได้ **HTTP 400** และข้อความใน `detail` — **ไม่มี** `responses` / `score` / รูป checked ใน response สำเร็จ และ**ไม่บันทึก**ภาพ Checked OMR

### เฉลยไม่ครบได้ — ตรวจเฉพาะข้อที่มีคีย์ (ข้อเหลือไม่วัดความผิดถูก)

เทมเพลตอาจเป็น 50 ข้อแต่อาจให้มีเฉลยแค่บางช่วง เช่น 30 ข้อ — พฤติกรรมที่ได้รับรองอยู่ใน API อยู่แล้วดังนี้

| สิ่งที่ทำใน API | ความหมาย |
|------------------|----------|
| **คู่ว่างได้** (`null` / `""` / ช่องว่างอย่างเดียว) | ใน `evaluation` + `source_type: custom` จะถูก**ตัดออกจากคีย์เฉลยก่อนคิดคะแนน** — ถือว่า “ไม่บังคับประเมินข้อนั้น” |
| **ให้คะแนนตามรายที่เหลือ** | เทียบเฉลยและวาดเขียบเฉพาะข้อที่ยังอยู่ในรายการหลังตัด |
| **`responses` เต็มเทมเพลต** | ให้จากการอ่านกระดาษทั้งแบบ (รวม `q1…q50` เป็นต้น) ถึงเฉลยจะเหลือ 30 คูก็ยังเห็นข้อความผู้สอบทุกข้อได้ |

**วิธีส่งจาก Laravel (เลือกอย่างใดอย่างหนึ่ง)**  
- ส่งเฉพาะคู่ที่มีเฉลย: `questions_in_order` กับ `answers_in_order` **ยาวเท่ากัน**เท่าที่มีคีย์จริง (เช่น 30 คู่)  
- หรือส่งยาวเท่าเทมเพลต (เช่น 50) แล้วใช้ **`null`** ใน `answers_in_order` ตำแหน่งที่ไม่มีเฉลย — API **ตัดออกให้เหมือนกันก่อนคิดคะแนน**

**ข้อควรมีหลังตัด:** หลังตัดช่องว่างออกจากคีย์เฉลย ต้องเหลืออย่างน้อยหนึ่งคู่ (question + answer) ที่จะใช้คิดคะแนน — ถ้าตัดจนหมดแล้วยังส่ง `evaluate=true` จะได้ HTTP 400 เพราะไม่มีข้อให้เปรียบเทียบกับเฉลย

### ควรส่ง A/B/C/D หรือตัวเลขเป็นคีย์?

- **`answers_in_order`** ควรเป็น **สตริงเดียวกับ label ฟองใน `template.json`** เช่น **`A`–`E`** (ส่วนใหญ่)
- ถ้าส่งตัวเลขจาก Laravel เป็น integer จากนั้น JSON เป็น number — ฝั่ง API coerce เป็น **`"1"`** เป็นต้น — ผลจากการอ่านเฉลยจากกระดาษมักเป็น **`A`** ไม่ใช่ **`1`** เลยประโยความแม่นผิดได้ — **แปลงใน Laravel จากดัชนีคอลัมน์เป็น `"A"+"index"` (`chr(65 + offset)` เมื่อป้ายฟองเป็น A–D ติดกัน)**

### Path layout ของ Checked OMR

โฟลเดอร์หลักคือ `outputs/scans/CheckedOMRs/school/...` (มี prefix `school/` เสมอ)

**เมื่อเทมเพลตมี `customLabels.Roll` และอ่าน Roll ได้เป็นตัวเลขล้วน** (ผ่าน validation แล้ว) API จะเก็บรูป checked แบบ **ทับไฟล์เดิมเมื่อสแกนซ้ำ** โดยใช้ Roll จากกระดาษเป็นชื่อไฟล์ (`exam_id` ส่งบังคับทุกครั้ง):

| ส่ง school_id | ไฟล์เก็บที่ (ตัวอย่าง `exam_id=42`, Roll=`12345`) |
|---|---|
| มี | `CheckedOMRs/school/<school_id>/42/by-roll/12345.jpg` |
| ไม่มี | `CheckedOMRs/school/_unknown/42/by-roll/12345.jpg` |

**เทมเพลตไม่มี Roll หรือ Roll ว่าง/ไม่ใช่ตัวเลขล้วน** — ใช้โฟลเดอร์เดือน + ชื่อไฟล์ `uuid` (สร้างใหม่ทุกครั้ง):

| ส่ง school_id | ไฟล์เก็บที่ |
|---|---|
| มี | `CheckedOMRs/school/<school_id>/<exam_id>/<YYYY-MM>/<uuid>_<ชื่ออัปโหลด>.jpg` |
| ไม่มี | `CheckedOMRs/school/_unknown/<exam_id>/<YYYY-MM>/<uuid>_....jpg` |

> แนะนำส่ง `school_id` ด้วยเสมอ — จะได้ลบกลุ่มด้วย `DELETE /exam/{school_id}/{exam_id}` และไม่ปนกับโรงอื่นภายใต้ `_unknown`

- **เปิดรูป Checked OMR ผ่าน HTTP:** ใช้ **`base_url + "/checked/" + checked_omr_filename`** (เช่น `http://127.0.0.1:8080/checked/school/12/42/by-roll/12345.jpg`)

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
