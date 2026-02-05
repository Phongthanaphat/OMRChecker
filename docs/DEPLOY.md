# Deploy OMR Checker API บน VPS (พร้อม Optimize)

คู่มือ deploy API ขึ้น VPS ให้รันถาวร พร้อมการตั้งค่าเพื่อรับโหลดได้ดี (หลาย workers, limit ขนาดรูป, template cache)

---

## 1. ความต้องการของ VPS

| รายการ | ขั้นต่ำ (รันร่วม Laravel) | แนะนำสำหรับ production |
|--------|----------------------------|---------------------------|
| **RAM** | 2 GB (1 worker) | 4 GB+ (4 workers ใช้ ~1.5–2.5 GB) |
| **CPU** | 1 core | 4–8 cores (workers = จำนวนงานพร้อมกัน) |
| **Disk** | 1 GB สำหรับโปรเจกต์ | 10 GB+ (รวมโฟลเดอร์ `outputs/` รูป Checked OMR) |
| **OS** | Linux (Ubuntu 20.04+ / Debian) | - |

**เมื่อรันอยู่:** API จะใช้ RAM ตามจำนวน workers ตลอด (ไม่คืนเมื่อ idle)  
- 1 worker ≈ 300–600 MB  
- 4 workers ≈ 1.2–2.4 GB  

---

## 2. สิ่งที่ Optimize อยู่แล้ว

- **หลาย workers** — รับ request พร้อมกันได้ (ใช้ `--workers 4` ใน production)
- **จำกัดขนาดอัปโหลด 20 MB** — ป้องกัน abuse และประหยัด memory
- **Template cache** — โหลด template/config/marker ครั้งแรกแล้วใช้จาก memory ลด disk I/O

---

## 3. ขั้นตอน Deploy

### 3.1 โคลนโปรเจกต์และเข้าโฟลเดอร์

```bash
cd /var/www   # หรือ path ที่ใช้เก็บโปรเจกต์
# ถ้าใช้ git:
# git clone <repo-url> OMRChecker
cd OMRChecker
```

### 3.2 สร้าง Python venv และติดตั้ง dependencies

บน Debian/Ubuntu ถ้าสร้าง venv แล้ว error เรื่อง `ensurepip is not available` ให้ติดตั้งแพ็กเกจก่อน:

```bash
# ดูเวอร์ชัน Python (เช่น 3.10, 3.11) แล้วติดตั้งให้ตรง
python3 --version
sudo apt update
sudo apt install python3.10-venv   # หรือ python3.11-venv ตามเวอร์ชัน
```

จากนั้นสร้าง venv และติดตั้ง dependencies:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

ตรวจสอบว่าไม่มี error จาก pip

### 3.3 ตรวจสอบเทมเพลต

```bash
ls templates/default/
# ต้องมี: template.json, config.json, omr_marker.jpg
```

ถ้าส่ง evaluation จาก Laravel ไม่ต้องมี `evaluation.json` ในเทมเพลต

### 3.4 ทดสอบรัน (ไม่ใช้ systemd)

```bash
# รันที่ 127.0.0.1 = ให้เฉพาะเครื่องนี้ (เช่น Laravel) เรียก
./venv/bin/python run_api.py --host 127.0.0.1 --port 8080 --workers 4
```

จากเครื่องเดียวกันทดสอบ:

```bash
curl -s http://127.0.0.1:8080/health
# ควรได้ {"status":"ok"}
```

กด Ctrl+C เพื่อหยุด แล้วไปขั้นตอน systemd

---

## 4. ตั้ง systemd (ให้ API รันอัตโนมัติ + Optimize)

### 4.1 คัดลอก unit และแก้ path

```bash
sudo cp deploy/omr-checker-api.service /etc/systemd/system/
sudo nano /etc/systemd/system/omr-checker-api.service
```

แก้ค่าให้ตรงกับ VPS:

| ค่า | แก้เป็น |
|-----|---------|
| `WorkingDirectory` | path โปรเจกต์จริง เช่น `/var/www/OMRChecker` |
| `User` / `Group` | user ที่รันเว็บ (มัก `www-data`) |
| `Environment="PATH=..."` | `PATH=/var/www/OMRChecker/venv/bin` (path ถึง venv) |
| `ExecStart` | ใช้ path venv จริง เช่น `/var/www/OMRChecker/venv/bin/python3 run_api.py ...` |

ตัวอย่าง ExecStart ที่ใช้แล้ว (พร้อม optimize):

```ini
ExecStart=/var/www/OMRChecker/venv/bin/python3 run_api.py --host 127.0.0.1 --port 8080 --workers 4
```

- **`--host 127.0.0.1`** — ให้เฉพาะ Laravel บน VPS เรียกได้ (ไม่เปิด port 8080 ออกนอก)
- **`--workers 4`** — 4 งานพร้อมกัน (ปรับตามจำนวน CPU ได้ เช่น 2 หรือ 6)

### 4.2 เปิดใช้และสตาร์ท

```bash
sudo systemctl daemon-reload
sudo systemctl enable omr-checker-api
sudo systemctl start omr-checker-api
sudo systemctl status omr-checker-api
```

ควรเห็น `Active: active (running)` และไม่มี error สีแดง

### 4.3 คำสั่งที่ใช้บ่อย

```bash
# ดูสถานะ
sudo systemctl status omr-checker-api

# Restart (หลังอัปเดตโค้ดหรือแก้ .service)
sudo systemctl restart omr-checker-api

# ดู log แบบ realtime
sudo journalctl -u omr-checker-api -f

# ดู log ย้อนหลัง
sudo journalctl -u omr-checker-api -n 200 --no-pager
```

---

## 5. Nginx (ถ้าต้องการให้คนนอกเข้า API ผ่าน domain)

ใช้เมื่อต้องการให้เรียก API ผ่าน `https://omr-api.yourdomain.com` ไม่ใช่แค่ Laravel ใน VPS

### 5.1 เปิด API ออกนอก (ถ้าเดิมใช้ 127.0.0.1)

แก้ไฟล์ systemd ให้ bind ทุก interface:

```ini
ExecStart=... run_api.py --host 0.0.0.0 --port 8080 --workers 4
```

จากนั้น `sudo systemctl daemon-reload && sudo systemctl restart omr-checker-api`

### 5.2 ตั้ง Nginx reverse proxy

```bash
sudo cp deploy/nginx-omr-api.conf /etc/nginx/sites-available/omr-api
sudo ln -s /etc/nginx/sites-available/omr-api /etc/nginx/sites-enabled/
```

แก้ `server_name` ในไฟล์เป็น domain จริง (เช่น `omr-api.yourdomain.com`)

```bash
sudo nginx -t && sudo systemctl reload nginx
```

- **client_max_body_size 20M** ใน config ตรงกับ limit ฝั่ง API (20 MB) แล้ว

### 5.3 HTTPS (แนะนำ)

ใช้ certbot หรือใส่ certificate เอง แล้วเปิดใช้ `listen 443 ssl` กับ path ของ ssl_certificate ใน config ตามใน `deploy/nginx-omr-api.conf`

---

## 6. การใช้ RAM และการปรับ Workers

| Workers | RAM โดยประมาณ | ใช้เมื่อ |
|---------|----------------|----------|
| 1 | ~300–600 MB | ทดสอบ หรือ VPS RAM น้อย |
| 2 | ~600 MB–1.2 GB | โหลดไม่สูง |
| 4 | ~1.2–2.4 GB | **แนะนำบน VPS 4–8 cores** |
| 6–8 | ~2–4 GB+ | VPS RAM เยอะ และต้องการรับงานพร้อมกันมาก |

- RAM ที่ใช้จะถูก “ถือ” ไว้ตลอดขณะที่ service รัน (แม้ไม่มี request)
- ถ้าไม่ใช้ OMR ชั่วคราว: `sudo systemctl stop omr-checker-api` จะคืน RAM ให้ระบบ

---

## 7. โฟลเดอร์ output และ disk

- รูป Checked OMR เก็บที่ `outputs/scans/CheckedOMRs/YYYY-MM/` (แยกตามเดือน)
- ควรมีพื้นที่ว่างพอสำหรับรูปที่เพิ่มขึ้นเรื่อยๆ
- ถ้าต้องการ ลด disk: ตั้ง cron ลบหรืออาร์ไคฟ์โฟลเดอร์เดือนเก่า (เช่น เก็บ 6 เดือน)

---

## 8. เช็คหลัง Deploy

1. **Health**
   ```bash
   curl -s http://127.0.0.1:8080/health
   ```
   ต้องได้ `{"status":"ok"}`

2. **จาก Laravel** เรียก `POST /check` ไปที่ `http://127.0.0.1:8080/check` (หรือผ่าน Nginx ตามที่ตั้ง)

3. **ดู log หลังยิง request**
   ```bash
   sudo journalctl -u omr-checker-api -n 50 --no-pager
   ```

---

## 9. สรุป Optimize ที่ใช้ใน Production

- รันด้วย **`--workers 4`** (หรือตามจำนวน core)
- ใช้ **`--host 127.0.0.1`** ถ้าให้เฉพาะ Laravel เรียก (ปลอดภัยกว่า)
- **Limit อัปโหลด 20 MB** ฝั่ง API และ `client_max_body_size 20M` ใน Nginx
- **Template cache** ทำงานอัตโนมัติ ไม่ต้องตั้งค่าเพิ่ม

รายละเอียด API (endpoints, body, response) ดูใน [API.md](API.md)
