# 🎓 KHUSCSU 經費申請與核銷系統

一個以 Flask + SQLite3 製作的校內經費申請、審核與核銷管理系統，提供學生會、系會社團及老師使用。  
本系統設計以「線上化核銷流程」為核心，搭配簡潔管理介面與多層審核邏輯，支援文件上傳、歷程追蹤及角色權限管理。

---

## 🧩 系統功能總覽

### 🧑‍💻 一般使用者（系會 / 學生會）
- 線上填寫活動經費申請表  
- 自動計算總金額、申請編號  
- 可上傳多筆用途與金額項目  
- 退回後可編輯與重新送出  
- 活動完成後可建立核銷表單  
- 上傳收據、活動照、回饋單  
- 檢視歷史申請與核銷紀錄  

### 🧑‍🏫 審核者（老師 / 會長 / 議長 / 課指組）
- 審核申請與核銷資料  
- 可輸入意見、核定金額  
- 審核歷程會自動保存  
- 可退回補件並標記階段  

### 🧑‍🔧 管理員
- 建立 / 編輯 / 刪除使用者  
- 建立 / 刪除單位（系會）  
- 分配老師與系會關聯  
- 檢視所有申請與核銷紀錄  
- 匯出資料成 CSV / Excel / PDF  
- 後台全權管理（不受角色流程限制）

---

## ⚙️ 系統架構

| 技術 | 用途 |
|------|------|
| **Python 3.12+** | 系統主程式 |
| **Flask** | Web 框架 |
| **SQLite3** | 資料庫 |
| **TailwindCSS** | 前端樣式 |
| **Jinja2** | 模板引擎 |
| **openpyxl** | 匯出 Excel |
| **reportlab** | 匯出 PDF |
| **smtplib** | 寄送通知信（可選） |

---

## 📁 專案結構
```bash

fund_system/
├── app.py # 主程式
├── config.py # 郵件設定（可留空）
├── fund_app.db # SQLite 資料庫
├── requirements.txt # 套件清單
├── static/
│ └── uploads/ # 上傳檔案資料夾
│ └── reimbursements/
└── templates/ # HTML 模板
├── login.html
├── dashboard.html
├── new_application.html
├── view_application.html
├── reimburse_new.html
├── reimburse_view.html
├── reimburse_review.html
├── reimburse_edit.html
├── admin.html
├── admin_edit_user.html
├── admin_applications.html
├── admin_reimbursements.html
├── admin_panel.html
└── layout.html
```


---

## 🚀 安裝與啟動

### 1️⃣ 建立虛擬環境
```bash
python -m venv venv
source venv/bin/activate      # macOS / Linux
venv\Scripts\activate         # Windows
2️⃣ 安裝套件
bash
複製程式碼
pip install -r requirements.txt
（若無 requirements.txt，可執行以下命令自動安裝）


複製程式碼
pip install flask openpyxl reportlab
3️⃣ 啟動伺服器

複製程式碼
python app.py
系統啟動後可於瀏覽器開啟：
👉 http://127.0.0.1:5000
```
## 🧠 管理員操作

|項目|路徑|說明
|---|---|---
|登入頁	|/login|輸入管理員帳密
|管理員主頁|/admin|檢視使用者、單位、分配老師
|新增使用者|/admin/register|建立帳號（會長、出納、老師等）
|編輯使用者|/admin/user/<id>/edit|修改使用者資料
|刪除使用者|/admin/user/<id>/delete|從系統移除帳號
|管理申請|/admin/applications|檢視 / 刪除申請紀錄
|管理核銷|/admin/reimbursements|檢視 / 刪除核銷資料
|匯出報表|/export_csv, /export_xlsx, /export_pdf|	下載統計資料

## 🧾 資料表概覽

|資料表|用途|
|---|---|
|users|使用者資料（帳號、姓名、角色、單位）|
|applications|經費申請主表|
|line_items|經費申請明細|
|reviews|申請審核紀錄|
|organizations|單位（系會、學生會）|
|teacher_assignments|老師與系會對應關係|
|reimbursements|核銷主表|
|reimbursement_items|核銷收據明細|
|reimbursement_photos|上傳照片與回饋單|
|reimbursement_reviews|核銷審核歷程|

📧 寄信功能（選用）
可在 config.py 中設定 SMTP 資訊，例如：
```bash
python
複製程式碼
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "youremail@gmail.com"
SMTP_PASS = "your_app_password"
SMTP_SENDER = "學生會經費系統 <youremail@gmail.com>"
當有審核通知時，系統可自動寄信給相關人員。
```
## 🔒 權限與角色
角色|權限
|---|---|
admin|全權管理所有資料
org|系會社團：建立申請、核銷
org_teacher|	系會社團老師：審核系會申請
union_treasurer|	學生會出納：核銷審核
union_finance	|學生會財務：核銷審核
union_president	|學生會會長：申請 / 審核
parliament_chair|	學生議會議長：最終核定
instructor	|課指組老師：內部流程審核

🧹 注意事項
管理員帳號無法刪除。

系統會自動建立缺少的資料表與欄位（首次啟動時）。

上傳的檔案儲存在 static/uploads/reimbursements/<id>/。

若要重新初始化資料庫，刪除 fund_app.db 後重啟程式即可。

🧑‍💼 作者與維護
製作者： 李偉漢(第二十二屆會長&第十九屆議長)

版本： v1.3.2

日期： 2025/10
授權： MIT License

💬 若需協助或錯誤修正，請聯絡系統管理員或學生會負責人。