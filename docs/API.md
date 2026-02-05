# OMR Checker API

Backend API สำหรับตรวจข้อสอบ OMR รันบน **port 8080** (ไม่ชนกับ Laravel ที่มักใช้ 80/8000)

**Deploy บน VPS (พร้อม optimize):** ดูคู่มือเต็มใน **[docs/DEPLOY.md](DEPLOY.md)** — ตั้ง systemd, workers, RAM, Nginx

**บน VPS:**  
- **ใช้เฉพาะใน VPS (ให้แค่ Laravel เรียก):** รัน API ที่ `127.0.0.1` ไม่ต้องตั้ง Nginx ให้ API → มีแค่ Laravel บนเครื่องเดียวกันเรียกได้  
- **อยากให้คนนอกเข้าได้:** ตั้ง Nginx reverse proxy + ใช้ `--host 0.0.0.0` (ดูใน DEPLOY.md)

## โครงสร้างโฟลเดอร์มาตรฐาน

- **templates/default/** – เทมเพลตหลัก (template.json, config.json, omr_marker.jpg; evaluation.json ถ้าต้องการให้คิดคะแนนจากเทมเพลต หรือส่ง evaluation เป็น JSON จาก Laravel แทน)

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

## Endpoints

| Method | Path     | คำอธิบาย                    |
|--------|----------|-----------------------------|
| GET    | /        | ข้อมูล service + ลิงก์ docs |
| GET    | /health  | Health check                |
| POST   | /check   | อัปโหลดรูป OMR → ได้ responses + score |
| GET    | /checked/{file_path}         | รูปกระดาษที่ตรวจแล้ว (ใช้ `checked_omr_filename` จาก POST /check เช่น `2025-02/xxx.jpg`) |
| GET    | /outputs/scans/CheckedOMRs/{file_path} | รูปกระดาษที่ตรวจแล้ว (alias) |

## POST /check

- **Body:** `multipart/form-data`
  - **image** (required): ไฟล์รูปกระดาษคำตอบ (.jpg, .jpeg, .png)
  - **template_id** (optional): ชื่อเทมเพลต (default: `default`)
  - **evaluate** (optional): `true`/`false` (default: `true`) — ถ้า `false` จะไม่ใช้ evaluation เลย ได้แค่ raw responses
  - **evaluation** (optional): **JSON string** ของ evaluation config (ส่งจาก Laravel ได้) — ถ้าส่งมา API จะใช้ชุดนี้คิดคะแนนและส่ง `score` + `evaluation` กลับ และ**รูป Checked OMR จะวาดวงกลมสีเขียวทับข้อที่ถูก สีแดงทับข้อที่ผิด** (ไม่ใช้ evaluation.json ในเทมเพลต)
- **Response:** JSON
  - `request_id`, `file_id`, `score`, `responses` (Roll, q1, q2, …), `evaluation` (รายละเอียดข้อละข้อ ถ้ามีการคิดคะแนน)
  - `checked_omr_path` (ถ้ามี): path เช่น `outputs/scans/CheckedOMRs/2025-02/xxx.jpg` (แยกโฟลเดอร์ตามเดือน)
  - `checked_omr_filename` (ถ้ามี): path สำหรับโหลดรูป เช่น `2025-02/5d52e9f5-ef0c-411e-985e-aeccbf42a3e4_image.jpg` — **ใช้ค่านี้ใส่ใน URL โหลดรูป**

- **เปิดรูป Checked OMR ผ่าน HTTP:** ใช้ **`base_url + "/checked/" + checked_omr_filename`** (เช่น `http://127.0.0.1:8080/checked/2025-02/5d52e9f5-ef0c-411e-985e-aeccbf42a3e4_image.jpg`)

### ตัวอย่าง (curl)

```bash
curl -X POST http://localhost:8080/check \
  -F "image=@/path/to/sheet.jpg" \
  -F "template_id=default"
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
  -F "image=@/path/to/sheet.jpg" \
  -F "evaluation={\"source_type\":\"custom\",\"options\":{...},\"marking_schemes\":{...}}"
```

Laravel ตัวอย่าง:

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
Http::attach('image', $imageContent, 'sheet.jpg')
    ->post($apiUrl . '/check', [
        'evaluation' => json_encode($evaluation),
    ]);
```

### ตัวอย่าง (Flutter / Laravel)

- **Flutter:** ส่ง `MultipartRequest` ไปที่ `https://omr-api.yourdomain.com/check` (หรือ `http://VPS_IP:8080/check` ถ้าไม่ผ่าน Nginx) พร้อม field `image` (ไฟล์) และถ้าต้องการคิดคะแนนฝั่ง API ส่ง field `evaluation` (JSON string)
- **Laravel:** ใช้ `Http::attach(...)->post(...)` แล้วส่ง `evaluation` เป็น `json_encode($array)` ถ้าต้องการให้ API คิดคะแนนจากชุดคำตอบที่ Laravel ส่งมา

## Port

- **8080** – ค่าเริ่มต้นของ OMR API (ไม่ชน Laravel 80/8000)
- เปลี่ยนได้ด้วย `python run_api.py --port 8081`
