# ขอบเขตของงาน (Terms of Reference)
# ระบบ OMRChecker — บริการตรวจกระดาษคำตอบด้วย Computer Vision

**วันที่จัดทำ:** มิถุนายน 2568  
**เวอร์ชันเอกสาร:** 1.0  
**ส่วนหนึ่งของ:** แพลตฟอร์ม SMHub (School Management Hub)

> เอกสาร TOR ครอบคลุมระบบ SMHub ทั้งหมด (Web + OMRChecker) อยู่ที่ `smhub_web/docs/TOR.md`
> เอกสารนี้เน้นเฉพาะส่วน OMRChecker

---

## 1. หลักการและเหตุผล

การตรวจกระดาษคำตอบแบบ Multiple Choice ด้วยมือหรือเครื่องตรวจ OMR เฉพาะทางมีต้นทุนสูงและใช้เวลานาน OMRChecker เป็นโซลูชันโอเพนซอร์สที่ใช้ Computer Vision (OpenCV) ในการประมวลผลภาพกระดาษคำตอบจากกล้องสมาร์ทโฟนหรือเครื่องสแกน ทำให้โรงเรียนสามารถตรวจข้อสอบได้รวดเร็ว แม่นยำ และประหยัดค่าใช้จ่าย

ระบบนี้พัฒนาต่อจาก OMRChecker (MIT License) โดย Udayraj123 และปรับปรุงเพิ่ม REST API สำหรับผสานกับแพลตฟอร์ม SMHub Web

---

## 2. วัตถุประสงค์

1. ตรวจกระดาษคำตอบ OMR จากภาพถ่ายหรือภาพสแกน โดยไม่ต้องใช้เครื่องตรวจ OMR เฉพาะทาง
2. ผสานกับระบบบันทึกคะแนนใน SMHub Web เพื่อบันทึกผลการตรวจโดยอัตโนมัติ
3. รองรับ Template กระดาษคำตอบแบบกำหนดเองได้สำหรับแต่ละโรงเรียน
4. จัดเก็บภาพกระดาษที่ตรวจแล้วแยกตามโรงเรียนและการสอบ

---

## 3. ขอบเขตของงาน

### 3.1 คุณสมบัติหลัก

| คุณสมบัติ | รายละเอียด |
|-----------|------------|
| **ความเร็ว** | 200+ OMR/นาที |
| **ความแม่นยำ** | ~100% (ภาพสแกน), ~90% (ภาพถ่ายมือถือ) |
| **Resolution ต่ำสุด** | 640×480 pixels |
| **รองรับมุมถ่าย** | ทุกมุม (auto-rotate/crop) |
| **รองรับภาพซ้ำ (xerox)** | ใช่ |
| **Template** | กำหนดเองได้ผ่าน `template.json` |
| **ขนาดไฟล์สูงสุด** | 20 MB |

### 3.2 การประมวลผลภาพ

ขั้นตอนการประมวลผล:
1. รับภาพ → ตรวจสอบ Extension (.jpg, .jpeg, .png)
2. หา Marker 4 มุม ด้วย CropOnMarkers
3. Crop และ Align กระดาษ
4. อ่านรหัสนักเรียน (Roll) จาก digit bubbles (ถ้า template กำหนด)
5. อ่านคำตอบแต่ละข้อ
6. คำนวณคะแนนตาม Evaluation JSON
7. บันทึกภาพ Checked OMR (annotated)
8. ส่งผลกลับเป็น JSON

---

## 4. API Endpoints

### Base URL
```
http://localhost:8080/api/omr
```

### 4.1 POST /check — ตรวจกระดาษคำตอบ

**Request (multipart/form-data):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | File | ✓ | ภาพกระดาษคำตอบ (.jpg/.jpeg/.png, ≤20MB) |
| `exam_id` | string | ✓ | รหัสการสอบจาก Laravel |
| `school_id` | string | | รหัสโรงเรียน |
| `template_id` | string | | รหัส template (default: `50q`) |
| `evaluate` | bool | | คำนวณคะแนนด้วย template evaluation (default: true) |
| `evaluation` | JSON string | | เกณฑ์คะแนนจาก Laravel (override template) |

**Response (200 OK):**
```json
{
  "request_id": "uuid-v4",
  "file_id": "upload.jpg",
  "responses": {
    "Roll": "12345",
    "q1": "A", "q2": "B", ...
  },
  "score": 45.0,
  "checked_omr_path": "outputs/scans/CheckedOMRs/school/xxx/exam/by-roll/12345.jpg",
  "checked_omr_filename": "school/xxx/exam/by-roll/12345.jpg",
  "evaluation": [ ... ]
}
```

**Error Responses:**
- `400` — ไฟล์ผิดประเภท, ขาด exam_id, ไม่พบ Marker, Roll ไม่ถูกต้อง
- `413` — ไฟล์ใหญ่เกิน 20 MB
- `500` — ข้อผิดพลาดภายใน

### 4.2 GET /templates — รายการ template

```json
{
  "templates": [ {"template_id": "50q"} ],
  "default": "50q"
}
```

### 4.3 GET /checked/{file_path} — ดูภาพ Checked OMR

เส้นทางภาพที่ตรวจแล้ว ใช้ `checked_omr_filename` จาก POST /check

### 4.4 DELETE /exam/{school_id}/{exam_id} — ลบไฟล์ของการสอบ

ใช้เมื่อลบข้อสอบใน Laravel

### 4.5 DELETE /school/{school_id} — ลบไฟล์ทั้งหมดของโรงเรียน

ใช้เมื่อลบโรงเรียนออกจากระบบ

### 4.6 GET /health — Health Check

```json
{ "status": "ok" }
```

---

## 5. Evaluation (เกณฑ์การให้คะแนน)

รองรับ 2 โหมด:

### 5.1 Template Evaluation
ใช้ไฟล์ `evaluation.json` ในโฟลเดอร์ template  
เหมาะสำหรับข้อสอบมาตรฐานที่ใช้เกณฑ์เดิมซ้ำๆ

### 5.2 Custom Evaluation (จาก Laravel)
ส่ง JSON ตรงผ่าน `evaluation` field ใน POST /check  
```json
{
  "source_type": "custom",
  "options": {
    "questions_in_order": ["q1", "q2", ..., "q50"],
    "answers_in_order": ["A", "B", ..., "C"]
  }
}
```
ข้อที่ `answers_in_order` เป็น `null` หรือ `""` จะถูกข้ามโดยอัตโนมัติ (partial answer key)

---

## 6. การจัดเก็บภาพ (Checked OMR Storage)

```
outputs/scans/CheckedOMRs/
  school/
    {school_id}/
      {exam_id}/
        by-roll/
          {roll}.jpg     ← เมื่อ template มี Roll; สแกนซ้ำจะ overwrite
        {YYYY-MM}/
          {uuid}_{stem}.jpg  ← เมื่อไม่มี Roll
```

**การบีบอัดภาพ:**
- ลดขนาดด้าน max ≤ 1600px
- เป้าหมาย < ~1MB
- JPEG quality step-down: 75 → 68 → 60 → 52 → 45

---

## 7. ความปลอดภัย

| จุด | มาตรการ |
|-----|--------|
| **API Authentication** | `Authorization: Bearer <OMR_INTERNAL_API_KEY>` ทุก request |
| **Timing Attack** | `secrets.compare_digest()` สำหรับเปรียบ API key |
| **Path Traversal** | ตรวจ `..`, `\\` และ resolve ภายใต้ CHECKED_OMR_DIR |
| **Input Validation** | `school_id`/`exam_id` regex `^[A-Za-z0-9_-]{1,64}$` |
| **File Size** | ปฏิเสธไฟล์ > 20 MB |
| **File Type** | รับเฉพาะ .jpg, .jpeg, .png |
| **API Docs** | ปิดใน Production: `OMR_ENABLE_DOCS=false` |

---

## 8. โครงสร้างโค้ด

```
omrchecker/
  api/
    main.py          ← FastAPI application, endpoints ทั้งหมด
  src/
    core.py          ← ตรรกะหลักการประมวลผล OMR
    entry.py         ← Entry point สำหรับเรียกจาก API
    evaluation.py    ← การคำนวณคะแนน
    template.py      ← การอ่านและจัดการ template
    logger.py        ← Logging
    processors/      ← Image processors (CropOnMarkers ฯลฯ)
    utils/           ← Utility functions
    schemas/         ← JSON Schema validation
    constants/       ← ค่าคงที่
  templates/
    50q/             ← Template กระดาษ 50 ข้อ
      template.json
      config.json
      omr_marker.jpg
  inputs/            ← Input samples
  outputs/           ← Output directory (scans, results)
  main.py            ← CLI entry point
  run_api.py         ← API server launcher
```

---

## 9. การ Deploy

**สำหรับ Development:**
```bash
python3 run_api.py
# หรือ
uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

**Environment Variables ที่สำคัญ:**
```env
OMR_INTERNAL_API_KEY=<secret-key>      # API key สำหรับ authentication
OMR_ENABLE_DOCS=false                  # ปิด /docs ใน Production
```

**ข้อกำหนด:**
- Python 3.x
- OpenCV 4.x (`opencv-python`, `opencv-contrib-python`)
- Pandas
- FastAPI + Uvicorn

---

## 10. การผสานกับ SMHub Web

1. **การตรวจกระดาษ:** Laravel ส่ง POST /check พร้อมภาพ + exam_id + school_id + evaluation JSON
2. **การแสดงภาพ:** Laravel ส่ง URL ของ `checked_omr_filename` ให้ frontend โหลดผ่าน GET /checked/{path}
3. **การล้างข้อมูล:** เมื่อลบข้อสอบ → Laravel เรียก DELETE /exam/{school_id}/{exam_id}
4. **Header ทุก request:** `Authorization: Bearer <OMR_INTERNAL_API_KEY>`

---

*เอกสารนี้จัดทำจากการวิเคราะห์ source code ณ เดือนมิถุนายน 2568*
