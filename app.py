from flask import Response, Flask, render_template, request, redirect, url_for, session, flash, g
import sqlite3, os, uuid
from datetime import datetime, timedelta
import hashlib
from random import randint

# ===== 基本設定 =====
DB = os.path.join(os.path.dirname(__file__), 'fund_app.db')

def now_tw():
    # 帶秒數，利於追蹤
    return (datetime.utcnow() + timedelta(hours=8)).isoformat(timespec='seconds')

app = Flask(__name__)
import config  # 若沒有則可建立空檔或註解寄信用功能

# 狀態中文對照
status_labels = {
    'submitted': '已送出',
    'in_progress': '審核中',
    'approved': '通過',
    'rejected': '退回',
    'completed': '已完成',
}
app.jinja_env.globals['status_label'] = lambda s: status_labels.get(s, s)

# 審核階段中文對照
step_labels = {
    'dept_teacher': '系會社團老師',
    'parliament_chair': '學生議會議長',
    'union_president': '學生會會長',
    'union_treasurer': '學生會出納',
    'union_finance': '學生會財務',
    'instructor': '課指組老師',
    'completed': '已結案',
    'rejected': '退回',
}
app.jinja_env.globals['step_label'] = lambda s: step_labels.get(s, s)


# 可申請 / 可審核角色
can_apply_roles  = ['org','union_treasurer','union_finance','union_other','union_president','parliament_chair']
can_review_roles = ['org_teacher','union_president','parliament_chair','instructor','admin']

app.secret_key = 'change_this_secret'

role_labels = {
    'admin':'管理員','org':'系會社團','org_teacher':'系會社團老師',
    'union_treasurer':'學生會出納','union_finance':'學生會財務','union_other':'學生會其他幹部',
    'union_president':'學生會會長','parliament_chair':'學生議會議長','instructor':'課指組老師'
}
app.jinja_env.globals['role_label'] = lambda r: role_labels.get(r, r)


# ===== DB 輔助 =====
def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(error):
    db = getattr(g, '_db', None)
    if db is not None:
        db.close()

def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows

def ex(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

def sha(p): 
    return hashlib.sha256(p.encode('utf-8')).hexdigest()

def row_get(row, key, default=None):
    """從 sqlite3.Row 安全取值"""
    if row is None: 
        return default
    try:
        if key in row.keys():
            return row[key]
        return default
    except Exception:
        return default

def me():
    if 'uid' in session:
        return q('SELECT * FROM users WHERE id=?', (session['uid'],), one=True)
    return None

def require(role=None):
    u = me()
    if not u:
        flash('請先登入')
        return redirect(url_for('login'))
    if role and u['role']!=role:
        flash('權限不足')
        return redirect(url_for('dashboard'))
    return None

def role_in(*roles):
    u = me()
    return u and u['role'] in roles

def ensure_email_column():
    try:
        cols = q("PRAGMA table_info(users)")
        names = [c['name'] for c in cols]
        if 'email' not in names:
            ex("ALTER TABLE users ADD COLUMN email TEXT")
    except Exception:
        pass

def ensure_app_columns():
    """補齊 applications 常用欄位，避免 OperationalError"""
    try:
        cols = q("PRAGMA table_info(applications)")
        names = [c['name'] for c in cols]
        need_cols = [
            ('current_step', 'TEXT'),
            ('status', 'TEXT'),
            ('type', 'TEXT'),
            ('bypass_teacher', 'INTEGER DEFAULT 0'),
            ('last_reject_step', 'TEXT'),
            ('amount_approved', 'REAL'),
            ('created_at', 'TEXT'),
            ('updated_at', 'TEXT'),
            ('total_amount', 'REAL'),
            # 常見基本欄位（預防舊 DB 缺）
            ('form_number','TEXT'),
            ('applicant_id','INTEGER'),
            ('org_id','INTEGER'),
            ('title','TEXT')
        ]
        for col, typ in need_cols:
            if col not in names:
                ex(f"ALTER TABLE applications ADD COLUMN {col} {typ}")
    except Exception:
        pass

def ensure_teacher_assignments():
    """若 teacher_assignments 表不存在則建立"""
    try:
        q("SELECT 1 FROM teacher_assignments LIMIT 1", one=True)
    except Exception:
        ex('''CREATE TABLE IF NOT EXISTS teacher_assignments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_user_id INTEGER,
                organization_id INTEGER
            )''')

def ensure_reviews():
    try:
        q("SELECT 1 FROM reviews LIMIT 1", one=True)
    except Exception:
        ex('''CREATE TABLE IF NOT EXISTS reviews(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER,
                reviewer_id INTEGER,
                role TEXT,
                step TEXT,
                decision TEXT,
                amount_approved REAL,
                comment TEXT,
                created_at TEXT
            )''')

def ensure_line_items():
    try:
        q("SELECT 1 FROM line_items LIMIT 1", one=True)
    except Exception:
        ex('''CREATE TABLE IF NOT EXISTS line_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER,
                name TEXT,
                purpose TEXT,
                amount REAL
            )''')

def ensure_organizations():
    try:
        q("SELECT 1 FROM organizations LIMIT 1", one=True)
    except Exception:
        ex('''CREATE TABLE IF NOT EXISTS organizations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE
            )''')
    # 確保「學生會」存在
    try:
        union = q("SELECT id FROM organizations WHERE name='學生會'", one=True)
        if not union:
            ex("INSERT INTO organizations(name) VALUES('學生會')")
    except Exception:
        pass

def ensure_schema():
    """盡量只補欄位與缺表，不覆蓋你既有資料"""
    ensure_email_column()
    ensure_app_columns()
    ensure_teacher_assignments()
    ensure_reviews()
    ensure_line_items()
    ensure_organizations()

# ===== 核銷系統資料表 =====
def ensure_reimbursements_schema():
    ex('''CREATE TABLE IF NOT EXISTS reimbursements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER,
        applicant_id INTEGER,
        total_amount REAL,
        approved_amount REAL,
        comment TEXT,
        status TEXT,
        current_step TEXT,
        created_at TEXT,
        updated_at TEXT
    )''')

    ex('''CREATE TABLE IF NOT EXISTS reimbursement_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reimbursement_id INTEGER,
        item_name TEXT,
        purpose TEXT,
        amount REAL,
        receipt_path TEXT
    )''')

    ex('''CREATE TABLE IF NOT EXISTS reimbursement_photos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reimbursement_id INTEGER,
        type TEXT,  -- 'activity' or 'feedback'
        path TEXT
    )''')

with app.app_context():
    ensure_reimbursements_schema()

with app.app_context():
    try:
        ensure_schema()
    except Exception as e:
        print("初始化資料庫結構時發生錯誤：", e)


# ===== 登入/登出 =====
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        u = request.form['username']; p = request.form['password']
        user = q('SELECT * FROM users WHERE username=?', (u,), one=True)
        if user and user['password_hash']==sha(p):
            session['uid']=user['id']; session['role']=user['role']; session['name']=user['display_name']
            return redirect(url_for('dashboard'))
        flash('帳號或密碼錯誤')
    return render_template('login.html', user=me())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ===== 入口/儀表板 =====
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    u = me()
    if not u:
        return redirect(url_for('login'))

    # ===== 我的申請 =====
    my_apps = q('''
        SELECT 
            a.*, 
            o.name AS org_name,
            r.id AS reimb_id,
            r.status AS reimb_status,
            r.current_step AS reimb_step
        FROM applications a
        LEFT JOIN organizations o ON o.id = a.org_id
        LEFT JOIN reimbursements r ON r.application_id = a.id
        WHERE a.applicant_id = ?
        ORDER BY a.created_at DESC
    ''', (u['id'],))

    # ===== 一般申請待審核清單 =====
    pending = []
    if u['role']=='org_teacher':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       JOIN teacher_assignments ta ON ta.organization_id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       LEFT JOIN organizations o ON o.id=a.org_id
                       WHERE ta.teacher_user_id=? AND a.current_step='dept_teacher' ''', (u['id'],))
    elif u['role']=='parliament_chair':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       WHERE a.current_step='parliament_chair' ''')
    elif u['role']=='union_president':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       WHERE a.current_step='union_president' ''')
    elif u['role']=='instructor':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       WHERE a.current_step='instructor' ''')
    elif u['role']=='admin':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       ORDER BY a.created_at DESC LIMIT 20''')

    # ===== 一般申請我審核過的 =====
    reviewed = q('''
        SELECT a.*, o.name as org_name, r.decision, r.created_at as reviewed_at
        FROM reviews r
        JOIN applications a ON a.id=r.application_id
        LEFT JOIN organizations o ON o.id=a.org_id
        WHERE r.reviewer_id=?
        ORDER BY r.created_at DESC
        LIMIT 50
    ''', (u['id'],))

    # ===== 核銷審核資料 =====
    pending_reimbursements = []
    reviewed_reimbursements = []

    if u['role'] in ['union_finance','union_treasurer','union_president','parliament_chair']:
        pending_reimbursements = q('''
            SELECT r.id, r.total_amount, r.current_step, a.title, usr.display_name AS applicant_name
            FROM reimbursements r
            JOIN applications a ON r.application_id = a.id
            JOIN users usr ON r.applicant_id = usr.id
            WHERE r.status NOT IN ('completed','rejected')
              AND r.current_step = ?
        ''', (u['role'],))

        reviewed_reimbursements = q('''
            SELECT r.id, a.title, usr.display_name AS applicant_name, rr.decision, rr.created_at AS reviewed_at
            FROM reimbursement_reviews rr
            JOIN reimbursements r ON rr.reimbursement_id = r.id
            JOIN applications a ON r.application_id = a.id
            JOIN users usr ON usr.id = r.applicant_id
            WHERE rr.reviewer_id = ?
            ORDER BY rr.created_at DESC
            LIMIT 50
        ''', (u['id'],))


    # ===== Render 頁面 =====
    return render_template('dashboard.html',
        user=u,
        my_apps=my_apps,
        pending=pending,
        reviewed=reviewed,
        can_apply=(u['role'] in can_apply_roles),
        can_review=(u['role'] in can_review_roles),
        pending_reimbursements=pending_reimbursements,
        reviewed_reimbursements=reviewed_reimbursements
    )

    # 以下區塊是原始檔案中的重複段，保留但不會執行到（因為上面已 return）
    pending = []
    if u['role']=='org_teacher':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       JOIN teacher_assignments ta ON ta.organization_id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       LEFT JOIN organizations o ON o.id=a.org_id
                       WHERE ta.teacher_user_id=? AND a.current_step='dept_teacher' ''', (u['id'],))
    elif u['role']=='parliament_chair':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       WHERE a.current_step='parliament_chair' ''')
    elif u['role']=='union_president':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       WHERE a.current_step='union_president' ''')
    elif u['role']=='instructor':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       WHERE a.current_step='instructor' ''')
    elif u['role']=='admin':
        pending = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name FROM applications a
                       LEFT JOIN organizations o ON o.id=a.org_id
                       JOIN users usr ON usr.id=a.applicant_id
                       ORDER BY a.created_at DESC LIMIT 20''')

    reviewed = q('''
        SELECT a.*, o.name as org_name, r.decision, r.created_at as reviewed_at
        FROM reviews r
        JOIN applications a ON a.id=r.application_id
        LEFT JOIN organizations o ON o.id=a.org_id
        WHERE r.reviewer_id=?
        ORDER BY r.created_at DESC
        LIMIT 50
    ''', (u['id'],))

    return render_template('dashboard.html',
                           user=u,
                           my_apps=my_apps,
                           pending=pending,
                           reviewed=reviewed,
                           can_edit=(u['role'] in can_apply_roles),
                           can_review=(u['role'] in can_review_roles))

# ===== Admin 區 =====
@app.route('/admin')
def admin_home():
    r = require('admin')
    if r: return r
    users = q('SELECT * FROM users ORDER BY id')
    orgs = q('SELECT * FROM organizations ORDER BY id')
    assigns = q('''SELECT ta.id, ta.organization_id as org_id, u.display_name as teacher, o.name as org_name
                   FROM teacher_assignments ta
                   JOIN users u ON u.id=ta.teacher_user_id
                   JOIN organizations o ON o.id=ta.organization_id
                   ORDER BY ta.id''')
    return render_template('admin.html', user=me(), users=users, orgs=orgs, assigns=assigns)

@app.route('/admin/orgs/add', methods=['POST'])
def admin_add_org():
    r = require('admin')
    if r: return r
    name = request.form['name'].strip()
    if not name:
        flash('單位名稱不可空白'); return redirect(url_for('admin_home'))
    try:
        ex('INSERT INTO organizations(name) VALUES(?)', (name,))
        flash('已新增單位')
    except:
        flash('新增失敗，可能重複')
    return redirect(url_for('admin_home'))

@app.route('/admin/orgs/delete/<int:oid>', methods=['POST'])
def admin_delete_org(oid):
    r = require('admin')
    if r: return r
    org = q('SELECT * FROM organizations WHERE id=?', (oid,), one=True)
    if org and org['name']=='學生會':
        flash('學生會為系統單位，無法刪除'); return redirect(url_for('admin_home'))
    try:
        ex('DELETE FROM organizations WHERE id=?', (oid,))
        flash('已刪除單位')
    except:
        flash('刪除失敗（可能已有關聯）')
    return redirect(url_for('admin_home'))

@app.route('/admin/register', methods=['GET','POST'])
def admin_register():
    r = require('admin')
    if r: return r
    roles = [('org','系會社團'),('org_teacher','系會社團老師'),
             ('union_treasurer','學生會出納'),('union_finance','學生會財務'),
             ('union_other','學生會其他幹部'),('union_president','學生會會長'),
             ('parliament_chair','學生議會議長'),('instructor','課指組老師'),
             ('admin','管理員')]
    orgs = q('SELECT * FROM organizations ORDER BY id')
    if request.method=='POST':
        username = request.form['username'].strip()
        display = (request.form.get('display_name') or username).strip()
        role = request.form['role']; pw = request.form['password']
        org_id = request.form.get('org_id'); org_id = int(org_id) if org_id else None
        try:
            ex('INSERT INTO users(username,password_hash,role,display_name,org_id,email) VALUES(?,?,?,?,?,?)',
               (username, sha(pw), role, display, org_id if role=='org' else None, request.form.get('email','').strip() or None))
            flash('使用者已建立'); return redirect(url_for('admin_home'))
        except:
            flash('建立失敗，帳號可能已存在')
    return render_template('admin_register.html', user=me(), roles=roles, orgs=orgs)

@app.route('/admin/user/<int:uid>/edit', methods=['GET','POST'])
def admin_edit_user(uid):
    r = require('admin')
    if r: return r
    u = q('SELECT * FROM users WHERE id=?',(uid,), one=True)
    if not u:
        flash('找不到使用者')
        return redirect(url_for('admin_home'))
    roles = [('org','系會社團'),('org_teacher','系會社團老師'),
             ('union_treasurer','學生會出納'),('union_finance','學生會財務'),
             ('union_other','學生會其他幹部'),('union_president','學生會會長'),
             ('parliament_chair','學生議會議長'),('instructor','課指組老師'),
             ('admin','管理員')]
    orgs = q('SELECT * FROM organizations ORDER BY id')
    teachers = q("SELECT * FROM users WHERE role='org_teacher' ORDER BY display_name")
    current_teacher = None
    if u and u['org_id']:
        ct = q('SELECT teacher_user_id FROM teacher_assignments WHERE organization_id=?', (u['org_id'],), one=True)
        if ct:
            current_teacher = ct['teacher_user_id']
    if request.method=='POST':
        display = request.form.get('display_name') or u['display_name']
        role = request.form.get('role') or u['role']
        new_username = (request.form.get('username') or u['username']).strip()
        exist = q('SELECT id FROM users WHERE username=? AND id<>?', (new_username, uid), one=True)
        if exist:
            flash('帳號已存在，請更換')
            return redirect(url_for('admin_edit_user', uid=uid))
        pw = request.form.get('password','').strip()
        org_id = request.form.get('org_id'); org_id = int(org_id) if org_id else None
        if pw:
            ex('UPDATE users SET username=?, display_name=?, role=?, password_hash=?, org_id=?, email=? WHERE id=?',
               (new_username, display, role, sha(pw), org_id if role=='org' else None, request.form.get('email','').strip() or None, uid))
        else:
            ex('UPDATE users SET username=?, display_name=?, role=?, org_id=?, email=? WHERE id=?',
               (new_username, display, role, org_id if role=='org' else None, request.form.get('email','').strip() or None, uid))
        if role=='org' and org_id:
            teacher_id = request.form.get('assigned_teacher_id')
            if teacher_id:
                ex('DELETE FROM teacher_assignments WHERE organization_id=?', (org_id,))
                ex('INSERT INTO teacher_assignments(teacher_user_id, organization_id) VALUES(?,?)', (teacher_id, org_id))
        flash('使用者已更新')
        return redirect(url_for('admin_home'))
    return render_template('admin_edit_user.html', user=me(), u=u, roles=roles, orgs=orgs, teachers=teachers, current_teacher=current_teacher)

@app.route('/admin/user/<int:uid>/delete', methods=['POST'])
def admin_delete_user(uid):
    r = require('admin')
    if r: return r

    # 保護機制：禁止刪除自己或管理員
    me_user = me()
    if me_user['id'] == uid:
        flash('❌ 不能刪除自己')
        return redirect(url_for('admin_home'))

    target = q('SELECT * FROM users WHERE id=?', (uid,), one=True)
    if not target:
        flash('找不到該使用者')
        return redirect(url_for('admin_home'))

    if target['role'] == 'admin':
        flash('⚠️ 不能刪除管理員帳號')
        return redirect(url_for('admin_home'))

    # 執行刪除（同時清除關聯）
    ex('DELETE FROM teacher_assignments WHERE teacher_user_id=?', (uid,))
    ex('DELETE FROM users WHERE id=?', (uid,))

    flash(f"✅ 已刪除使用者：{target['display_name']}")
    return redirect(url_for('admin_home'))


@app.route('/admin/applications')
def admin_applications():
    r = require('admin')
    if r: return r

    # 同時查詢申請與對應核銷資料
    apps = q('''
        SELECT 
            a.id,
            a.form_number,
            a.title,
            a.total_amount,
            a.status AS app_status,
            r.status AS reimburse_status,
            usr.display_name AS applicant_name,
            o.name AS org_name,
            COALESCE(r.id, 0) AS reimburse_id
        FROM applications a
        LEFT JOIN reimbursements r ON r.application_id = a.id
        LEFT JOIN users usr ON usr.id = a.applicant_id
        LEFT JOIN organizations o ON o.id = a.org_id
        ORDER BY a.updated_at DESC
    ''')

    return render_template(
        'admin_applications.html',
        user=me(),
        apps=apps
    )

@app.route('/admin/applications/<int:aid>/delete', methods=['POST'])
def admin_delete_application(aid):
    r = require('admin')
    if r: return r

    import sqlite3
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # 刪除核銷與申請
    cur.execute('DELETE FROM reimbursements WHERE application_id=?', (aid,))
    cur.execute('DELETE FROM applications WHERE id=?', (aid,))
    conn.commit()
    conn.close()

    flash('✅ 已刪除申請與相關核銷資料', 'success')
    return redirect(url_for('admin_applications'))


@app.route('/admin/assign', methods=['POST'])
def admin_assign_teacher():
    r = require('admin')
    if r: return r
    teacher_id = request.form['teacher_id']; org_id = request.form['org_id']
    try:
        ex('INSERT INTO teacher_assignments(teacher_user_id, organization_id) VALUES(?,?)', (teacher_id, org_id))
        flash('分配完成')
    except:
        flash('分配已存在或失敗')
    return redirect(url_for('admin_home'))

# ===== 申請建立/編輯/重送 =====
def allowed_to_apply(u):
    if not u: return False
    if u['role'] in ('org_teacher','instructor','admin'):
        return False
    return u['role'] in ('org','union_president','union_finance','union_treasurer','union_other','parliament_chair')

def internal_union_role(u):
    return u['role'] in ('union_president','union_finance','union_treasurer','union_other','parliament_chair')

def calc_first_step_on_submit(u_role, app_type):
    if app_type == 'org':
        return 'dept_teacher'
    # union internal
    if u_role == 'union_president':
        return 'instructor'
    return 'union_president'

def calc_step_on_resubmit(app_row):
    """
    重新送審時決定起始關卡。
    org 類型：
      - 若上次退回為議長或會長，或 bypass_teacher=1，直送議長
      - 否則回老師
    union 類型（學生會內部）：
      - 流程：會長 -> 課指組 -> 議長
      - 誰退回就回給誰（last_reject_step）
      - 若無紀錄則預設送會長
    """
    app_type = row_get(app_row, 'type', 'org')
    last_reject = row_get(app_row, 'last_reject_step', None)
    bypass = row_get(app_row, 'bypass_teacher', 0) or 0

    if app_type == 'org':
        # 系會社團：議長/會長退回都直送議長
        if bypass == 1 or last_reject in ('parliament_chair', 'union_president'):
            return 'parliament_chair'
        return 'dept_teacher'

    elif app_type == 'union':
        # 學生會內部：誰退回就回給誰（會長→課指→議長）
        if last_reject in ('union_president', 'instructor', 'parliament_chair'):
            return last_reject
        # 沒紀錄預設送會長
        return 'union_president'

    # 預設回老師
    return 'dept_teacher'



@app.route('/application/new', methods=['GET','POST'])
def new_application():
    u = me()
    if not u: return redirect(url_for('login'))
    if u['role'] not in can_apply_roles:
        flash('此身分無法建立申請'); return redirect(url_for('dashboard'))
    if not allowed_to_apply(u):
        flash('此身分無法建立申請'); return redirect(url_for('dashboard'))

    union = q("SELECT id, name FROM organizations WHERE name='學生會'", one=True)
    fixed_org = None
    if u['role']=='org':
        fixed_org = q('SELECT o.* FROM organizations o JOIN users us ON us.org_id=o.id WHERE us.id=?',(u['id'],), one=True)
    elif internal_union_role(u):
        fixed_org = union

    if request.method=='POST':
        today_str = datetime.utcnow().strftime('%Y%m%d')
        org_id_str = str(fixed_org['id'] if fixed_org else 0).zfill(2)  # 兩位數ID，不足補0
        rand_str = str(randint(1000, 9999))
        form_number = f"{today_str}{org_id_str}{rand_str}"
        title = request.form['title']; leader_class = request.form['leader_class']; leader_name = request.form['leader_name']
        co_org = request.form.get('co_org',''); start_at = request.form['start_at']; end_at = request.form['end_at']
        expected_people = request.form.get('expected_people') or 0; location = request.form.get('location','')
        target = request.form.get('target',''); purpose = request.form.get('purpose','')
        names = request.form.getlist('item_name[]'); purps = request.form.getlist('item_purpose[]'); amts = request.form.getlist('item_amount[]')

        total = 0.0
        for aamt in amts:
            try: total += float(aamt or 0)
            except: pass

        if u['role']=='org':
            if not fixed_org:
                flash('您的帳號尚未綁定單位，請聯絡管理員'); return redirect(url_for('dashboard'))
            org_id = fixed_org['id']; app_type = 'org'
        else:
            org_id = union['id'] if union else None; app_type = 'union'

        step = calc_first_step_on_submit(u['role'], app_type)
        now = now_tw()

        ex('''INSERT INTO applications(form_number,applicant_id,org_id,title,leader_class,leader_name,co_org,start_at,end_at,expected_people,location,target,purpose,total_amount,type,status,current_step,bypass_teacher,last_reject_step,amount_approved,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
           (form_number,u['id'],org_id,title,leader_class,leader_name,co_org,start_at,end_at,expected_people,location,target,purpose,total,app_type,'submitted',step,0,None,None,now,now))

        aid = q('SELECT id FROM applications WHERE form_number=?',(form_number,), one=True)['id']
        for n,p,aamt in zip(names,purps,amts):
            if n.strip():
                try: amt=float(aamt or 0)
                except: amt=0.0
                ex('INSERT INTO line_items(application_id,name,purpose,amount) VALUES(?,?,?,?)',(aid,n,p,amt))

        flash('申請已送出，編號：'+form_number); return redirect(url_for('dashboard'))
    return render_template('new_application.html', user=u, fixed_org=fixed_org)

@app.route('/application/<int:aid>')
def view_application(aid):
    u = me()
    if not u:
        return redirect(url_for('login'))

    # 撈取申請資料
    a = q('''SELECT a.*, o.name as org_name, usr.display_name as applicant_name
             FROM applications a
             LEFT JOIN organizations o ON a.org_id=o.id
             JOIN users usr ON usr.id=a.applicant_id
             WHERE a.id=?''', (aid,), one=True)
    
    reimb = q('SELECT id, status, current_step FROM reimbursements WHERE application_id=?', (aid,), one=True)
    if reimb:
        a = dict(a)
        a['reimb_id'] = reimb['id']
        a['reimb_status'] = reimb['status']
        a['reimb_step'] = reimb['current_step']


    if not a:
        flash('找不到申請')
        return redirect(url_for('dashboard'))

    # 若曾審核過，允許檢視
    reviewed_before = q('SELECT 1 FROM reviews WHERE application_id=? AND reviewer_id=?', (aid, u['id']), one=True)

    # 權限判斷：申請人 / 管理員 / 現任審核人 / 曾審核者
    allowed = (
        u['id'] == a['applicant_id']
        or u['role'] == 'admin'
        or can_review(u, a)
        or bool(reviewed_before)
    )

    if not allowed:
        flash('權限不足：您沒有查看此申請的權限')
        return redirect(url_for('dashboard'))

    # 撈明細與審核紀錄
    items = q('SELECT * FROM line_items WHERE application_id=?', (aid,))
    reviews = q('''SELECT r.*, u.display_name as reviewer_name
                   FROM reviews r
                   LEFT JOIN users u ON u.id=r.reviewer_id
                   WHERE application_id=? ORDER BY r.created_at''', (aid,))

    can_edit_flag = (u['id'] == a['applicant_id'] and a['status'] == 'rejected') or (u['role'] == 'admin')

    return render_template('view_application.html',
                           user=u,
                           app=a,
                           items=items,
                           reviews=reviews,
                           can_edit=can_edit_flag,
                           can_review=(u['role'] in can_review_roles))


@app.route('/application/<int:aid>/edit', methods=['GET','POST'])
def edit_application(aid):
    u = me()
    if not u: return redirect(url_for('login'))
    a = q('SELECT * FROM applications WHERE id=?', (aid,), one=True)
    if not a: 
        flash('找不到申請'); return redirect(url_for('dashboard'))
    if not (u['role']=='admin' or (u['id']==a['applicant_id'] and a['status']=='rejected')):
        flash('權限不足：僅退回狀態之申請人可編輯'); return redirect(url_for('dashboard'))

    if request.method=='POST':
        fields = ('title','leader_class','leader_name','co_org','start_at','end_at','expected_people','location','target','purpose')
        vals = [request.form.get(k, a[k]) for k in fields]
        ex('''UPDATE applications SET title=?, leader_class=?, leader_name=?, co_org=?, start_at=?, end_at=?, expected_people=?, location=?, target=?, purpose=?, updated_at=? WHERE id=?''',
           (*vals, now_tw(), aid))

        # 更新明細
        ex('DELETE FROM line_items WHERE application_id=?', (aid,))
        names = request.form.getlist('item_name[]'); purps = request.form.getlist('item_purpose[]'); amts = request.form.getlist('item_amount[]')
        total = 0.0
        for n,p,amt in zip(names,purps,amts):
            if n.strip():
                try: v=float(amt or 0)
                except: v=0.0
                total += v
                ex('INSERT INTO line_items(application_id,name,purpose,amount) VALUES(?,?,?,?)',(aid,n,p,v))
        ex('UPDATE applications SET total_amount=?, updated_at=? WHERE id=?', (total, now_tw(), aid))

        # 若為退回狀態，自動重新送審
        if a['status'] == 'rejected':
            next_step = calc_step_on_resubmit(a)
            ex('UPDATE applications SET status=?, current_step=?, updated_at=? WHERE id=?',
               ('submitted', next_step, now_tw(), aid))
            ex('INSERT INTO reviews(application_id, reviewer_id, role, step, decision, amount_approved, comment, created_at) VALUES (?,?,?,?,?,?,?,?)',
               (aid, u['id'], 'applicant', 'resubmit', 'resubmit', None, '自動重新送審', now_tw()))
            flash('已編輯並重新送出審核')
        else:
            flash('已儲存變更')

        return redirect(url_for('view_application', aid=aid))

    items = q('SELECT * FROM line_items WHERE application_id=?', (aid,))
    return render_template('edit_application.html', user=u, app=a, items=items)


@app.route('/application/<int:aid>/resubmit', methods=['POST'])
def resubmit_application(aid):
    u = me()
    if not u: return redirect(url_for('login'))
    a = q('SELECT * FROM applications WHERE id=?', (aid,), one=True)
    if not a:
        flash('找不到申請'); return redirect(url_for('dashboard'))
    if not (u['role']=='admin' or (u['id']==a['applicant_id'] and a['status']=='rejected')):
        flash('權限不足：僅退回狀態之申請人可重送'); return redirect(url_for('dashboard'))

    next_step = calc_step_on_resubmit(a)
    ex('UPDATE applications SET status=?, current_step=?, updated_at=? WHERE id=?',
       ('submitted', next_step, now_tw(), aid))
    ex('INSERT INTO reviews(application_id, reviewer_id, role, step, decision, amount_approved, comment, created_at) VALUES (?,?,?,?,?,?,?,?)',
       (aid, u['id'], 'applicant', 'resubmit', 'resubmit', None, request.form.get('comment','補繳重送'), now_tw()))
    flash('已補繳重送，進入下一關')
    return redirect(url_for('view_application', aid=aid))

# ===== 審核流程 =====
def can_review(u,a):
    if u['role']=='org_teacher' and a['current_step']=='dept_teacher':
        ta = q('SELECT 1 FROM teacher_assignments WHERE teacher_user_id=? AND organization_id=?',(u['id'],a['org_id']), one=True)
        return bool(ta)
    if u['role']=='parliament_chair' and a['current_step']=='parliament_chair': return True
    if u['role']=='union_president' and a['current_step']=='union_president': return True
    if u['role']=='instructor' and a['current_step']=='instructor': return True
    if u['role']=='admin': return True
    return False

UPLOAD_FOLDER_REIMB = os.path.join('static', 'uploads', 'reimbursements')
os.makedirs(UPLOAD_FOLDER_REIMB, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'jpg','jpeg','png','gif'}

def save_file(file, rid, prefix=''):
    if not file or file.filename == '':
        return None
    if allowed_file(file.filename):
        folder = os.path.join(UPLOAD_FOLDER_REIMB, str(rid))
        os.makedirs(folder, exist_ok=True)
        ext = os.path.splitext(file.filename)[1]
        fname = prefix + '_' + uuid.uuid4().hex + ext
        fpath = os.path.join(folder, fname)
        file.save(fpath)
        return fpath.replace('\\', '/')
    return None


@app.route('/application/<int:aid>/review', methods=['GET','POST'])
def review_application(aid):
    u = me()
    if not u:
        return redirect(url_for('login'))
    if u['role'] not in can_review_roles:
        flash('權限不足：此身分不可審核')
        return redirect(url_for('dashboard'))

    # 撈出完整申請資料（含單位與申請人）
    a = q('''SELECT a.*, o.name AS org_name, u.display_name AS applicant_name
             FROM applications a
             LEFT JOIN organizations o ON o.id=a.org_id
             JOIN users u ON u.id=a.applicant_id
             WHERE a.id=?''', (aid,), one=True)
    if not a:
        flash('找不到申請')
        return redirect(url_for('dashboard'))

    # 確認當前可審核
    if not can_review(u, a):
        flash('您沒有審核此申請的權限或尚未到您這一關')
        return redirect(url_for('view_application', aid=aid))

    if request.method == 'POST':
        decision = request.form['decision']
        comment = request.form.get('comment', '')
        amount_approved = None
        if u['role'] == 'parliament_chair' and decision == 'approve':
            try:
                amount_approved = float(request.form.get('amount_approved', 0))
            except:
                amount_approved = None
            if amount_approved is None:
                flash('議長通過時必須填寫核定金額')
                return redirect(url_for('review_application', aid=aid))

        # 寫入審核紀錄
        ex('''INSERT INTO reviews(application_id, reviewer_id, role, step, decision, amount_approved, comment, created_at)
               VALUES (?,?,?,?,?,?,?,?)''',
           (aid, u['id'], u['role'], a['current_step'], decision, amount_approved, comment, now_tw()))

        # 狀態推進邏輯
        if decision == 'approve':
            if a['type'] == 'org':
                if a['current_step'] == 'dept_teacher':
                    next_step = 'parliament_chair'
                elif a['current_step'] == 'parliament_chair':
                    if amount_approved is not None:
                        ex('UPDATE applications SET amount_approved=? WHERE id=?', (amount_approved, aid))
                    next_step = 'union_president'
                elif a['current_step'] == 'union_president':
                    next_step = 'completed'
                else:
                    next_step = 'completed'
            else:
                if a['current_step'] == 'union_president':
                    next_step = 'instructor'
                elif a['current_step'] == 'instructor':
                    next_step = 'parliament_chair'
                elif a['current_step'] == 'parliament_chair':
                    if amount_approved is not None:
                        ex('UPDATE applications SET amount_approved=? WHERE id=?', (amount_approved, aid))
                    next_step = 'completed'
                else:
                    next_step = 'completed'

            ex('UPDATE applications SET current_step=?, status=?, updated_at=? WHERE id=?',
               (next_step, 'approved' if next_step == 'completed' else 'in_progress', now_tw(), aid))
            flash('審核通過' if next_step != 'completed' else '申請最終通過')
        else:
            # 拒絕
            bypass_teacher = 1 if (a['current_step'] == 'parliament_chair' or a['type'] == 'union') else (row_get(a, 'bypass_teacher', 0) or 0)
            ex('''UPDATE applications SET current_step=?, status=?, last_reject_step=?, bypass_teacher=?, updated_at=?
                   WHERE id=?''',
               ('rejected', 'rejected', a['current_step'], bypass_teacher, now_tw(), aid))
            flash('已退回此申請（請申請人修正後重送）')

        return redirect(url_for('dashboard'))

    # GET 時：顯示完整活動資訊 + 經費明細 + 審核表單
    items = q('SELECT * FROM line_items WHERE application_id=?', (aid,))
    reviews = q('SELECT r.*, u.display_name as reviewer_name FROM reviews r LEFT JOIN users u ON u.id=r.reviewer_id WHERE application_id=? ORDER BY r.created_at', (aid,))
    return render_template('review.html', user=u, app=a, items=items, reviews=reviews)

# ===== 建立核銷 =====
@app.route('/reimburse/<int:aid>/new', methods=['GET','POST'])
def reimburse_new(aid):
    u = me()
    if not u: return redirect(url_for('login'))
    app_row = q('SELECT * FROM applications WHERE id=?',(aid,),one=True)
    if not app_row or app_row['status'] != 'approved':
        flash('僅通過的申請可進行核銷')
        return redirect(url_for('view_application', aid=aid))

    exist = q('SELECT id FROM reimbursements WHERE application_id=?',(aid,),one=True)
    if exist:
        flash('此活動已建立核銷，請前往查看')
        return redirect(url_for('reimburse_view', rid=exist['id']))

    items = q('SELECT name FROM line_items WHERE application_id=?',(aid,))

    if request.method == 'POST':
        # ---- 檔案數量驗證：活動照>=2、回饋單>=1 ----
        act_files = request.files.getlist('activity_photos[]')
        fb = request.files.get('feedback_photo')
        act_count_uploaded = len([f for f in act_files if f and f.filename])
        fb_uploaded = bool(fb and fb.filename)
        if act_count_uploaded < 2 or not fb_uploaded:
            flash('請至少上傳兩張活動照片與一張回饋單')
            return render_template('reimburse_new.html', user=u, app=app_row, items=items)

        # 建立核銷主檔
        rid = ex('INSERT INTO reimbursements(application_id,applicant_id,total_amount,status,current_step,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                 (aid,u['id'],0,'submitted','union_finance',now_tw(),now_tw()))

        # 收據項目
        rec_names = request.form.getlist('rec_name[]')
        rec_purposes = request.form.getlist('rec_purpose[]')
        rec_amounts = request.form.getlist('rec_amount[]')
        rec_files = request.files.getlist('rec_receipt[]')
        total = 0
        for i,(n,p,aamt,f) in enumerate(zip(rec_names,rec_purposes,rec_amounts,rec_files)):
            if n.strip():
                amt = float(aamt or 0)
                total += amt
                path = save_file(f, rid, f'receipt{i}')
                ex('INSERT INTO reimbursement_items(reimbursement_id,item_name,purpose,amount,receipt_path) VALUES(?,?,?,?,?)',(rid,n,p,amt,path))
        # 活動照（已驗證至少兩張）
        for i,f in enumerate([f for f in act_files if f and f.filename]):
            path = save_file(f, rid, f'activity{i}')
            ex('INSERT INTO reimbursement_photos(reimbursement_id,type,path) VALUES(?,?,?)',(rid,'activity',path))
        # 回饋單（至少 1）
        fb_path = save_file(fb, rid, 'feedback')
        if fb_path:
            ex('INSERT INTO reimbursement_photos(reimbursement_id,type,path) VALUES(?,?,?)',(rid,'feedback',fb_path))
        # 檢討事項
        comment = request.form.get('comment','')
        ex('UPDATE reimbursements SET total_amount=?, comment=?, updated_at=? WHERE id=?',(total,comment,now_tw(),rid))
        flash('核銷已建立，進入學生會財務審核')
        return redirect(url_for('reimburse_view', rid=rid))

    return render_template('reimburse_new.html', user=u, app=app_row, items=items)

@app.route('/reimburse/<int:rid>')
def reimburse_view(rid):
    u = me()
    if not u:
        return redirect(url_for('login'))

    r = q('SELECT * FROM reimbursements WHERE id=?',(rid,),one=True)
    if not r:
        flash('找不到核銷紀錄')
        return redirect(url_for('dashboard'))

    # 權限判斷
    is_reviewer = u['role'] in ['union_finance','union_treasurer','union_president','parliament_chair','admin']
    if not (u['id'] == r['applicant_id'] or is_reviewer):
        flash('您沒有權限查看此核銷資料')
        return redirect(url_for('dashboard'))

    # 套用標籤
    r = dict(r)
    r['status_label'] = status_labels.get(r['status'], r['status'])
    r['step_label'] = step_labels.get(r['current_step'], r['current_step'])

    # 取得核銷明細與附檔
    items = q('SELECT * FROM reimbursement_items WHERE reimbursement_id=?',(rid,))
    photos = q('SELECT * FROM reimbursement_photos WHERE reimbursement_id=?',(rid,))

    # 加上原申請單資訊
    app_info = q('''SELECT a.title, a.form_number, o.name as org_name, u.display_name as applicant_name
                    FROM applications a
                    LEFT JOIN organizations o ON o.id=a.org_id
                    JOIN users u ON u.id=a.applicant_id
                    WHERE a.id=?''', (r['application_id'],), one=True)
    
    app_items = q('''SELECT name, purpose, amount
                     FROM line_items
                     WHERE application_id=?''', (r['application_id'],))

    # 加上審核歷程
    reviews = q('''SELECT rr.*, u.display_name
                    FROM reimbursement_reviews rr
                    LEFT JOIN users u ON u.id = rr.reviewer_id
                    WHERE rr.reimbursement_id=?
                    ORDER BY rr.created_at DESC''', (rid,))

    return render_template('reimburse_view.html',
                       user=u, r=r, items=items, photos=photos,
                       app_info=app_info, reviews=reviews)


@app.route('/reimburse/<int:rid>/review', methods=['GET','POST'])
def reimburse_review(rid):
    u = me()
    if not u:
        return redirect(url_for('login'))

    r = q('SELECT * FROM reimbursements WHERE id=?',(rid,),one=True)
    if not r:
        flash('找不到核銷資料')
        return redirect(url_for('dashboard'))

    # 權限：只有特定角色能審核
    if u['role'] not in ['union_finance','union_treasurer','union_president','parliament_chair','admin']:
        flash('您沒有審核此核銷的權限')
        return redirect(url_for('dashboard'))

    # POST → 審核提交
    if request.method == 'POST':
        decision = request.form['decision']
        comment = request.form.get('comment','')
        next_step = r['current_step']
        amount = None

        if decision == 'approve':
            if r['current_step'] == 'union_finance':
                next_step = 'union_treasurer'
            elif r['current_step'] == 'union_treasurer':
                next_step = 'union_president'
            elif r['current_step'] == 'union_president':
                next_step = 'parliament_chair'
            elif r['current_step'] == 'parliament_chair':
                next_step = 'completed'
                try:
                    amount = float(request.form.get('approved_amount', 0))
                except:
                    amount = None
                ex('UPDATE reimbursements SET approved_amount=? WHERE id=?', (amount, rid))
        else:
            next_step = 'rejected'

        # 寫入核銷審核紀錄
        ex('''CREATE TABLE IF NOT EXISTS reimbursement_reviews(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reimbursement_id INTEGER,
                reviewer_id INTEGER,
                decision TEXT,
                comment TEXT,
                created_at TEXT
            )''')
        ex('INSERT INTO reimbursement_reviews(reimbursement_id, reviewer_id, decision, comment, created_at) VALUES (?,?,?,?,?)',
           (rid, u['id'], decision, comment, now_tw()))

        # 更新主表
        if decision == 'reject':
            ex('UPDATE reimbursements SET current_step=?, status=?, updated_at=? WHERE id=?',
                ('rejected', 'rejected', now_tw(), rid))
        else:
            ex('UPDATE reimbursements SET current_step=?, status=?, comment=?, updated_at=? WHERE id=?',
                (next_step, 'approved' if next_step == 'completed' else 'in_progress',
                 comment, now_tw(), rid))

        flash('核銷審核完成')
        return redirect(url_for('dashboard'))

    # GET → 顯示資料
    items = q('SELECT * FROM reimbursement_items WHERE reimbursement_id=?',(rid,))
    photos = q('SELECT * FROM reimbursement_photos WHERE reimbursement_id=?',(rid,))
    app_info = q('''SELECT a.title, a.form_number, o.name as org_name, u.display_name as applicant_name
                    FROM applications a
                    LEFT JOIN organizations o ON o.id = a.org_id
                    JOIN users u ON u.id = a.applicant_id
                    WHERE a.id=?''', (r['application_id'],), one=True)
    # 🔹 取得原申請的經費明細
    app_items = q('''SELECT name, purpose, amount
                     FROM line_items
                     WHERE application_id=?''', (r['application_id'],))


    reviews = q('''SELECT rr.*, u.display_name
                   FROM reimbursement_reviews rr
                   LEFT JOIN users u ON u.id = rr.reviewer_id
                   WHERE rr.reimbursement_id=?
                   ORDER BY rr.created_at DESC''', (rid,))

    r = dict(r)
    r['status_label'] = status_labels.get(r['status'], r['status'])
    r['step_label'] = step_labels.get(r['current_step'], r['current_step'])
    r['app_info'] = app_info

    return render_template('reimburse_review.html', user=u, r=r, items=items, photos=photos, reviews=reviews, app_items=app_items)


# ===== 退回後允許申請人編輯：新增 /reimburse/<rid>/edit =====
@app.route('/reimburse/<int:rid>/edit', methods=['GET','POST'])
def reimburse_edit(rid):
    u = me()
    if not u:
        return redirect(url_for('login'))

    r = q('SELECT * FROM reimbursements WHERE id=?',(rid,),one=True)
    if not r:
        flash('找不到核銷紀錄')
        return redirect(url_for('dashboard'))

    # 只有核銷申請人、且退回狀態才可編輯
    if not (u['id'] == r['applicant_id'] and r['status'] == 'rejected'):
        flash('權限不足：僅退回的核銷申請人可編輯')
        return redirect(url_for('dashboard'))

    items = q('SELECT * FROM reimbursement_items WHERE reimbursement_id=?',(rid,))
    photos = q('SELECT * FROM reimbursement_photos WHERE reimbursement_id=?',(rid,))

    if request.method == 'POST':
        # --- 驗證最終照片數量（考量是否替換） ---
        # 既有數量
        ex_act_count = q('SELECT COUNT(*) AS c FROM reimbursement_photos WHERE reimbursement_id=? AND type=\"activity\"', (rid,), one=True)['c']
        ex_fb_count  = q('SELECT COUNT(*) AS c FROM reimbursement_photos WHERE reimbursement_id=? AND type=\"feedback\"', (rid,), one=True)['c']

        # 新上傳
        act_files = request.files.getlist('activity_photos[]')
        new_act = [f for f in act_files if f and f.filename]
        fb = request.files.get('feedback_photo')
        new_fb = bool(fb and fb.filename)

        # 預估替換後的數量（我們只有在有新檔時才會清掉舊檔）
        final_act_count = len(new_act) if len(new_act) > 0 else ex_act_count
        final_fb_count  = 1 if new_fb else ex_fb_count

        if final_act_count < 2 or final_fb_count < 1:
            flash('請至少保有兩張活動照片與一張回饋單（可不重新上傳，但總數需達標）')
            return render_template('reimburse_edit.html', user=u, r=r, items=items, photos=photos)

        # 重新儲存收據明細
        ex('DELETE FROM reimbursement_items WHERE reimbursement_id=?',(rid,))
        rec_names = request.form.getlist('rec_name[]')
        rec_purposes = request.form.getlist('rec_purpose[]')
        rec_amounts = request.form.getlist('rec_amount[]')
        rec_files = request.files.getlist('rec_receipt[]')
        total = 0
        for i,(n,p,aamt,f) in enumerate(zip(rec_names,rec_purposes,rec_amounts,rec_files)):
            if n.strip():
                amt = float(aamt or 0)
                total += amt
                path = save_file(f, rid, f'receipt{i}') if f and f.filename else None
                ex('INSERT INTO reimbursement_items(reimbursement_id,item_name,purpose,amount,receipt_path) VALUES(?,?,?,?,?)',
                   (rid,n,p,amt,path))

        # 若有上傳新活動照→整批替換
        if len(new_act) > 0:
            ex('DELETE FROM reimbursement_photos WHERE reimbursement_id=? AND type=\"activity\"', (rid,))
            for i,f in enumerate(new_act):
                path = save_file(f, rid, f'activity{i}')
                ex('INSERT INTO reimbursement_photos(reimbursement_id,type,path) VALUES(?,?,?)',(rid,'activity',path))

        # 若有上傳新回饋單→替換
        if new_fb:
            ex('DELETE FROM reimbursement_photos WHERE reimbursement_id=? AND type=\"feedback\"', (rid,))
            fb_path = save_file(fb, rid, 'feedback')
            if fb_path:
                ex('INSERT INTO reimbursement_photos(reimbursement_id,type,path) VALUES(?,?,?)',(rid,'feedback',fb_path))

        # 更新檢討事項 + 重新送審
        comment = request.form.get('comment', r['comment'] or '')
        ex('UPDATE reimbursements SET total_amount=?, comment=?, status=?, current_step=?, updated_at=? WHERE id=?',
           (total, comment if comment.strip() else r['comment'], 'submitted', 'union_finance', now_tw(), rid))
        flash('核銷已重新送出，回到學生會財務審核階段')
        return redirect(url_for('reimburse_view', rid=rid))

    return render_template('reimburse_edit.html', user=u, r=r, items=items, photos=photos)

# ===== Admin 檢視/管理核銷 =====
@app.route('/admin/reimbursements')
def admin_reimbursements():
    r = require('admin')
    if r: return r
    rows = q('''
        SELECT r.*, a.title, a.form_number, o.name AS org_name, u.display_name AS applicant_name
        FROM reimbursements r
        JOIN applications a ON a.id = r.application_id
        LEFT JOIN organizations o ON o.id = a.org_id
        JOIN users u ON u.id = r.applicant_id
        ORDER BY r.updated_at DESC
    ''')
    return render_template('admin_reimbursements.html', user=me(), rows=rows, status_label=status_labels, step_label=step_labels)

@app.route('/admin/reimbursements/<int:rid>/delete', methods=['POST'])
def admin_delete_reimbursement(rid):
    r = require('admin')
    if r: return r
    ex('DELETE FROM reimbursement_items WHERE reimbursement_id=?', (rid,))
    ex('DELETE FROM reimbursement_photos WHERE reimbursement_id=?', (rid,))
    ex('DELETE FROM reimbursements WHERE id=?', (rid,))
    flash('已刪除核銷與其所有明細與附件')
    return redirect(url_for('admin_reimbursements'))

# ===== Admin Panel & 匯出 =====
@app.route('/admin_panel')
def admin_panel():
    u = me()
    if not u:
        return redirect(url_for('login'))
    if u['role'] != 'admin':
        flash('未授權訪問')
        return redirect(url_for('dashboard'))

    org = request.args.get('org') or ''
    status = request.args.get('status') or ''
    step = request.args.get('step') or ''

    base_sql = '''
        SELECT a.*, o.name AS org_name, usr.display_name AS applicant_name
        FROM applications a
        LEFT JOIN users usr ON usr.id = a.applicant_id
        LEFT JOIN organizations o ON o.id = a.org_id
        WHERE 1=1
    '''
    params = []
    if org:
        base_sql += ' AND o.name = ?'
        params.append(org)
    if status:
        base_sql += ' AND a.status = ?'
        params.append(status)
    if step:
        base_sql += ' AND a.current_step = ?'
        params.append(step)

    rows = q(base_sql + ' ORDER BY a.updated_at DESC', tuple(params))
    orgs = q('SELECT DISTINCT name FROM organizations ORDER BY name')
    steps = q('SELECT DISTINCT current_step FROM applications ORDER BY current_step')
    statuses = q('SELECT DISTINCT status FROM applications ORDER BY status')
    return render_template('admin_panel.html', user=u, applications=rows, orgs=orgs, steps=steps, statuses=statuses, org_sel=org, status_sel=status, step_sel=step)

@app.route('/export_csv')
def export_csv():
    u = me()
    if not u:
        return redirect(url_for('login'))
    if u['role'] != 'admin':
        flash('未授權')
        return redirect(url_for('dashboard'))

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['申請單號','單位','活動名稱','申請人','狀態','審核階段','核定金額','總金額','最後更新時間'])

    rows = q('''SELECT a.form_number, o.name AS org_name, a.title, usr.display_name AS applicant_name,
                       a.status, a.current_step, a.amount_approved, a.total_amount, a.updated_at
                FROM applications a
                LEFT JOIN users usr ON usr.id=a.applicant_id
                LEFT JOIN organizations o ON o.id=a.org_id
                ORDER BY a.updated_at DESC''')
    for r in rows:
        writer.writerow([r['form_number'], r['org_name'], r['title'], r['applicant_name'],
                         r['status'], r['current_step'], r['amount_approved'] or '',
                         r['total_amount'] or '', r['updated_at'] or '' ])
    data = output.getvalue().encode('utf-8-sig')  # BOM for Excel
    return Response(data, mimetype='text/csv',
                    headers={'Content-Disposition':'attachment; filename=applications_report.csv'})

@app.route('/export_xlsx')
def export_xlsx():
    u = me()
    if not u or u['role'] != 'admin':
        flash('未授權')
        return redirect(url_for('dashboard'))
    from openpyxl import Workbook
    from io import BytesIO
    wb = Workbook(); ws = wb.active; ws.title = "Applications"
    headers = ['申請單號','單位','活動名稱','申請人','狀態','審核階段','核定金額','總金額','最後更新時間']
    ws.append(headers)
    rows = q('''SELECT a.form_number, o.name AS org_name, a.title, usr.display_name AS applicant_name,
                       a.status, a.current_step, a.amount_approved, a.total_amount, a.updated_at
                FROM applications a
                LEFT JOIN users usr ON usr.id=a.applicant_id
                LEFT JOIN organizations o ON o.id=a.org_id
                ORDER BY a.updated_at DESC''')
    for r in rows:
        ws.append([r['form_number'], r['org_name'], r['title'], r['applicant_name'],
                   r['status'], r['current_step'], r['amount_approved'] or '', r['total_amount'] or '', r['updated_at'] or ''])
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return Response(bio.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':'attachment; filename=applications_report.xlsx'})

@app.route('/export_pdf')
def export_pdf():
    u = me()
    if not u or u['role'] != 'admin':
        flash('未授權')
        return redirect(url_for('dashboard'))
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from io import BytesIO
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x = 2*cm; y = height - 2*cm
    c.setFont("Helvetica-Bold", 12); c.drawString(x, y, "Applications Report"); y -= 1*cm
    headers = ['單號','單位','活動名稱','申請人','狀態','階段','核定','總額','更新']
    c.setFont("Helvetica", 9)
    rows = q('''SELECT a.form_number, o.name AS org_name, a.title, usr.display_name AS applicant_name,
                       a.status, a.current_step, a.amount_approved, a.total_amount, a.updated_at
                FROM applications a
                LEFT JOIN users usr ON usr.id=a.applicant_id
                LEFT JOIN organizations o ON o.id=a.org_id
                ORDER BY a.updated_at DESC''')
    c.drawString(x, y, " | ".join(headers)); y -= 0.6*cm
    for r in rows[:50]:
        line = " | ".join([str(r['form_number']), str(r['org_name']), str(r['title'])[:12], str(r['applicant_name']),
                           str(r['status']), str(r['current_step']), str(r['amount_approved'] or ''),
                           str(r['total_amount'] or ''), str((r['updated_at'] or '')[:16].replace('T',' '))])
        c.drawString(x, y, line); y -= 0.5*cm
        if y < 2*cm:
            c.showPage(); y = height - 2*cm; c.setFont("Helvetica", 9)
    c.showPage(); c.save(); buf.seek(0)
    return Response(buf.getvalue(), mimetype='application/pdf',
                    headers={'Content-Disposition':'attachment; filename=applications_report.pdf'})

# ===== 簡易寄信（可選） =====
import smtplib
from email.mime.text import MIMEText
from email.header import Header

def send_mail(to_email, subject, html):
    try:
        host = os.getenv("SMTP_HOST", getattr(config, "SMTP_HOST", ""))
        port = int(os.getenv("SMTP_PORT", getattr(config, "SMTP_PORT", 587)))
        user = os.getenv("SMTP_USER", getattr(config, "SMTP_USER", ""))
        pwd  = os.getenv("SMTP_PASS", getattr(config, "SMTP_PASS", ""))
        sender = os.getenv("SMTP_SENDER", getattr(config, "SMTP_SENDER", user))

        if not (host and user and pwd and to_email):
            return False

        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = sender
        msg["To"] = to_email

        s = smtplib.SMTP(host, port, timeout=10)
        s.starttls()
        s.login(user, pwd)
        s.sendmail(sender, [to_email], msg.as_string())
        s.quit()
        return True
    except Exception:
        return False

# ===== 啟動 =====
if __name__ == '__main__':
    if not os.path.exists(DB):
        print('fund_app.db 不存在（請先建立或放置於同資料夾）')
    app.run(host='0.0.0.0', port=5000, debug=True)
