# จุดที่ Laravel ต้องปรับ เมื่อใช้ OMR API รันด้วย systemctl

OMR API รันด้วย systemd ที่ `127.0.0.1:8080` (ไม่เปลี่ยน) — ฝั่ง Laravel แค่ปรับ **วิธีสร้าง URL สำหรับลิงก์ “ดูกระดาษคำตอบ”** ให้ browser เปิดได้

---

## 1. สิ่งที่ไม่ต้องเปลี่ยน

- **เรียก OMR API จาก Laravel (server-side)** ยังใช้ `http://127.0.0.1:8080` ได้ตามเดิม  
  - เช่น `POST http://127.0.0.1:8080/check`  
  - Laravel รันบน VPS เดียวกับ OMR API ดังนั้น 127.0.0.1:8080 ใช้ได้จาก Laravel

---

## 2. ปัญหาที่ต้องแก้

- ลิงก์ “ดูกระดาษคำตอบ” ตอนนี้ชี้ไปที่ **`http://127.0.0.1:8080/outputs/scans/CheckedOMRs/...`**
- **127.0.0.1** ใน browser = เครื่องของผู้ใช้ ไม่ใช่เซิร์ฟเวอร์ → browser เปิดไม่ได้ (connection refused)
- ดังนั้น **ห้ามใช้ URL แบบ 127.0.0.1:8080 ในลิงก์ที่ส่งไปให้ frontend/browser**

---

## 3. ทางแก้ (เลือกอย่างใดอย่างหนึ่ง)

### แบบ A: ให้ browser เรียกรูปผ่าน domain เดียวกับเว็บ (แนะนำ)

- ตั้ง **Nginx** ให้ proxy path หนึ่ง (เช่น `/omr-api/`) ไปที่ `http://127.0.0.1:8080/`  
  - ใช้ config ใน `deploy/nginx-omr-api.conf` ได้ (อาจใส่ under server เดียวกับ Laravel แทน subdomain ก็ได้)
- **ฝั่ง Laravel:** ตอนสร้าง URL รูป “ดูกระดาษคำตอบ” ใช้ **URL ผ่าน domain** แทน 127.0.0.1:8080  
  - ตัวอย่าง: `https://yourdomain.com/omr-api/outputs/scans/CheckedOMRs/2026-02/xxx_image.jpg`  
  - หรือใช้ `url('/omr-api/outputs/scans/CheckedOMRs/' . $path)` / `config('app.url') . '/omr-api/...'`  
- ผล: ผู้ใช้กดลิงก์ → browser เรียก yourdomain.com → Nginx ส่งต่อไป OMR API ที่ 127.0.0.1:8080 → ได้รูป

### แบบ B: ให้ Laravel เป็นคนส่งรูปให้ browser (proxy ผ่าน Laravel)

- สร้าง **route ใน Laravel** ที่รับ path รูป (เช่น `2026-02/xxx_image.jpg`)  
  - ตัวอย่าง: `GET /omr/checked-image/{path}` (path อาจเป็น segment หลายส่วน)
- ใน **controller:**  
  - ดึงรูปจาก `http://127.0.0.1:8080/outputs/scans/CheckedOMRs/{path}` (ใช้ Http::get หรือ Guzzle จาก Laravel)  
  - return response เป็นรูป (Content-Type: image/jpeg เป็นต้น)
- ลิงก์ “ดูกระดาษคำตอบ” ชี้ไปที่ **route นี้**  
  - ตัวอย่าง: `https://yourdomain.com/omr/checked-image/2026-02/xxx_image.jpg`

---

## 4. สรุปสั้นๆ ให้ทีม Laravel

| รายการ | ทำอย่างไร |
|--------|-----------|
| เรียก OMR API (เช่น POST /check) | ใช้ `http://127.0.0.1:8080` ตามเดิม (server-side) |
| URL ที่ส่งไปให้ frontend สำหรับ “ดูกระดาษคำตอบ” | **อย่าใช้** 127.0.0.1:8080 — ใช้ URL ผ่าน domain (แบบ A หรือ B ด้านบน) |
| ใช้ systemctl อยู่แล้ว | ไม่ต้องเปลี่ยน — OMR API ยังรันที่ 127.0.0.1:8080 เหมือนเดิม |

ไฟล์ config ตัวอย่าง Nginx อยู่ที่ `deploy/nginx-omr-api.conf` (รวมการ proxy ไป 127.0.0.1:8080 และ `client_max_body_size 20M`).
