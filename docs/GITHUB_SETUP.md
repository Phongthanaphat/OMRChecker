# คู่มือการอัปโหลดโปรเจกต์ขึ้น GitHub

คู่มือนี้ใช้สำหรับโปรเจกต์ที่ยังไม่มี Git หรือยังไม่ได้เชื่อมกับ GitHub

---

## สิ่งที่ต้องมีก่อนเริ่ม

1. **บัญชี GitHub** – สมัครที่ [github.com](https://github.com)
2. **Git** – ติดตั้งบนเครื่อง ([ดาวน์โหลด](https://git-scm.com/downloads))
3. **GitHub CLI (ไม่บังคับ)** – ถ้าอยากใช้คำสั่งจากเทอร์มินัลแทนการเปิดเว็บ

---

## ขั้นตอนที่ 1: สร้าง Repository บน GitHub

1. เข้า [github.com](https://github.com) แล้วล็อกอิน
2. กด **"+"** มุมขวาบน → **"New repository"**
3. ตั้งค่า:
   - **Repository name:** เช่น `OMRChecker`
   - **Description:** (ถ้ามี)
   - เลือก **Public** หรือ **Private**
   - **อย่า**ติ๊ก "Add a README" / ".gitignore" / "License" ถ้าในโปรเจกต์มีอยู่แล้ว
4. กด **"Create repository"**
5. จำ **URL ของ repo** เช่น `https://github.com/username/OMRChecker.git`

---

## ขั้นตอนที่ 2: เตรียมโปรเจกต์บนเครื่อง (ในโฟลเดอร์โปรเจกต์)

เปิด Terminal แล้ว `cd` เข้าโฟลเดอร์โปรเจกต์ จากนั้นรันคำสั่งตามลำดับ:

### 2.1 เริ่มใช้ Git (ครั้งแรกเท่านั้น)

```bash
cd /Users/icez/Documents/GitHub/OMRChecker   # หรือ path โปรเจกต์ของคุณ
git init
```

### 2.2 ตั้งชื่อและอีเมล (ถ้ายังไม่เคยตั้งแบบ global)

```bash
git config user.name "ชื่อของคุณ"
git config user.email "email@example.com"
```

หรือตั้งทั้งเครื่อง (ใช้กับทุก repo):

```bash
git config --global user.name "ชื่อของคุณ"
git config --global user.email "email@example.com"
```

### 2.3 ตรวจสอบ .gitignore

โปรเจกต์นี้มี `.gitignore` อยู่แล้ว จะกันโฟลเดอร์เช่น `inputs/*`, `venv/`, `__pycache__` ไม่ให้ถูก commit

### 2.4 Add และ Commit ครั้งแรก

```bash
git add .
git status   # ตรวจดูว่ามีไฟล์ที่ต้องการ commit
git commit -m "Initial commit"
```

---

## ขั้นตอนที่ 3: เชื่อมกับ GitHub และ Push

### 3.1 ใส่ Remote (ใช้ URL จากขั้นตอนที่ 1)

```bash
git remote add origin https://github.com/USERNAME/OMRChecker.git
```

ถ้าใช้ SSH:

```bash
git remote add origin git@github.com:USERNAME/OMRChecker.git
```

### 3.2 ตั้งชื่อ branch หลัก (ถ้าใช้ Git เวอร์ชันใหม่)

```bash
git branch -M main
```

### 3.3 Push ขึ้น GitHub

```bash
git push -u origin main
```

- ครั้งแรกอาจถามล็อกอิน (username/password หรือ Personal Access Token)
- ถ้าใช้ HTTPS และมี 2FA ต้องใช้ **Personal Access Token** แทนรหัสผ่าน

---

## การอัปเดตโค้ดในครั้งถัดไป

เมื่อแก้ไขไฟล์แล้วต้องการส่งขึ้น GitHub:

```bash
git add .                    # หรือ git add ไฟล์ที่ต้องการ
git status                   # ตรวจสอบ
git commit -m "ข้อความอธิบายการแก้ไข"
git push
```

---

## สรุปคำสั่งแบบย่อ (โปรเจกต์ใหม่ทั้งหมด)

```bash
cd /path/to/OMRChecker
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/USERNAME/OMRChecker.git
git branch -M main
git push -u origin main
```

---

## การล็อกอินกับ GitHub (HTTPS)

- **ถ้าเปิด Two-Factor Authentication (2FA):** ต้องใช้ **Personal Access Token (PAT)** แทนรหัสผ่าน
  - ไปที่ GitHub → Settings → Developer settings → Personal access tokens
  - สร้าง token (เช่นแบบ Classic) แล้วเลือก scope `repo`
  - เวลา `git push` ให้ใส่ token แทนรหัสผ่าน
- หรือใช้ **GitHub CLI:** `brew install gh` แล้ว `gh auth login` จะช่วยจัดการการล็อกอิน

---

## การล็อกอินกับ GitHub (SSH)

1. สร้างคีย์ SSH (ถ้ายังไม่มี):
   ```bash
   ssh-keygen -t ed25519 -C "your_email@example.com"
   ```
2. เปิด SSH agent แล้วเพิ่มคีย์:
   ```bash
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/id_ed25519
   ```
3. คัดลอก public key ไปใส่ใน GitHub → Settings → SSH and GPG keys
4. ใช้ URL แบบ `git@github.com:USERNAME/OMRChecker.git` เป็น `origin`

---

## คำสั่งที่มีประโยชน์

| คำสั่ง | ความหมาย |
|--------|----------|
| `git status` | ดูสถานะไฟล์ที่เปลี่ยน/ยังไม่ commit |
| `git log --oneline` | ดูประวัติ commit แบบย่อ |
| `git remote -v` | ดู remote ที่ผูกไว้ |
| `git pull` | ดึงโค้ดล่าสุดจาก GitHub มา merge |

ถ้ามี error หรือต้องการทำ branch / PR สามารถอ้างอิงคู่มือนี้แล้วบอกอาการที่เจอได้
