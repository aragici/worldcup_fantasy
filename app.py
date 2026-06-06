from flask import Flask, request, jsonify
from flask_cors import CORS
import random
import sqlite3
import os
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(current_dir, 'static') if os.path.exists(os.path.join(current_dir, 'static')) else os.path.join(current_dir, '..', 'static')

# Flask ve CORS Ayarları
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}}, methods=["GET", "POST", "PUT", "DELETE"])

DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
MAX_QUOTA = 2  # Kontenjan Sınırı

def init_db():
    """Veritabanını ve tabloları sıfırdan en güncel şemayla hazırlar."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Oyuncular Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL,
            admin_approved INTEGER DEFAULT 0
        )
    ''')
    
    # Kuponlar Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            group_num INTEGER,
            team_name TEXT,
            updated_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Kura Sıralaması Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS draft_orders (
            user_id INTEGER,
            group_id INTEGER,
            pick_order INTEGER,
            PRIMARY KEY (user_id, group_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Anlık Sıra Takip Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS draft_status (
            id INTEGER PRIMARY KEY,
            current_group_num INTEGER DEFAULT 1,
            current_pick_order INTEGER DEFAULT 1,
            is_started INTEGER DEFAULT 0
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO draft_status (id, current_group_num, current_pick_order, is_started) VALUES (1, 1, 1, 0)")
    
    conn.commit()
    conn.close()

# --------------------------------------------------------------------------
# API ENDPOINT'LERİ
# --------------------------------------------------------------------------

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"status": "error", "message": "Kullanıcı adı ve şifre boş geçilemez!"}), 400

    current_time = datetime.now().strftime("%d.%m.%Y %H:%M")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            return jsonify({"status": "error", "message": "Bu isimle zaten bir oyuncu başvurmuş!"}), 400

        cursor.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)", 
                       (username, password, current_time))
        
        conn.commit()
        conn.close()
        return jsonify({
            "status": "success", 
            "message": f"Kayıt başvurusu alındı!\nTarih: {current_time}\nAdmin 500 TL ödemesini onayladıktan sonra kupon yapabilirsiniz."
        }), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, password, admin_approved FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()

        if not user or user[1] != password:
            return jsonify({"status": "error", "message": "Hatalı kullanıcı adı veya şifre!"}), 401
        
        if user[2] == 0:
            return jsonify({
                "status": "error", 
                "message": "Parayı (500 TL) gönderdiyseniz adminin onaylamasını bekleyin! Girişiniz henüz kilitli."
            }), 403

        return jsonify({"status": "success", "message": "Giriş başarılı!", "user_id": user[0]}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/teams-status', methods=['GET'])
def get_teams_status():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT team_name, COUNT(user_id) FROM user_teams GROUP BY team_name")
        rows = cursor.fetchall()
        conn.close()
        
        status = {row[0]: row[1] for row in rows}
        return jsonify({"status": "success", "counts": status, "max_quota": MAX_QUOTA}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/save-coupon', methods=['POST'])
def save_coupon():
    data = request.json
    user_id = int(data.get('user_id'))
    selections = data.get('selections')
    
    if not selections:
        return jsonify({"status": "error", "message": "Seçim eksik!"}), 400
        
    sel = selections[0]
    g_num = int(sel['group_num'])
    team = sel['team_name']
    
    updated_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Draft canlı mı ve sıra bu adamda mı kontrol et
        cursor.execute("SELECT current_group_num, current_pick_order, is_started FROM draft_status WHERE id = 1")
        status = cursor.fetchone()
        
        if not status or status[2] == 0:
            conn.close()
            return jsonify({"status": "error", "message": "Kura henüz çekilmedi, seçim odası kapalı!"}), 400
            
        c_group, c_pick, is_started = status
        
        if is_started == 2:
            conn.close()
            return jsonify({"status": "error", "message": "Seçim odası kapandı, tüm kuponlar zaten kilitli!"}), 400
            
        cursor.execute("SELECT pick_order FROM draft_orders WHERE user_id = ? AND group_id = ?", (user_id, g_num))
        user_order = cursor.fetchone()
        
        if not user_order or user_order[0] != c_pick or g_num != c_group:
            conn.close()
            return jsonify({"status": "error", "message": "🚨 Seçim sırası sende değil reis, darlık yapma!"}), 400
            
        # 2. Kontenjan Kontrolü (MAX 2)
        cursor.execute("SELECT COUNT(*) FROM user_teams WHERE team_name = ?", (team,))
        current_count = cursor.fetchone()[0]
        if current_count >= MAX_QUOTA:
            conn.close()
            return jsonify({"status": "error", "message": f"Hata: {team} kontenjanı doldu!"}), 400
            
        # 3. Mükerrer seçim kontrolü
        cursor.execute("SELECT id FROM user_teams WHERE user_id = ? AND group_num = ?", (user_id, g_num))
        if cursor.fetchone():
            conn.close()
            return jsonify({"status": "error", "message": "Bu gruptan zaten seçimini yaptın reis!"}), 400

        # 4. Seçimi Kaydet
        cursor.execute("INSERT INTO user_teams (user_id, group_num, team_name, updated_at) VALUES (?, ?, ?, ?)",
                       (user_id, g_num, team, updated_time))
        
        # 5. Sıra Devir Motoru
        if c_pick < 8:
            cursor.execute("UPDATE draft_status SET current_pick_order = ? WHERE id = 1", (c_pick + 1,))
        else:
            if c_group < 10:
                cursor.execute("UPDATE draft_status SET current_group_num = ?, current_pick_order = 1 WHERE id = 1", (c_group + 1,))
            else:
                cursor.execute("UPDATE draft_status SET is_started = 2 WHERE id = 1")
                
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Takım başarıyla kilitlendi, sıra devrediyor!\nZaman Damgası: {updated_time}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/user-coupon/<int:user_id>', methods=['GET'])
def get_user_coupon(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT group_num, team_name FROM user_teams WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        selections = [{"group_num": row[0], "team_name": row[1]} for row in rows]
        return jsonify({"status": "success", "selections": selections}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/user/my-orders/<int:user_id>', methods=['GET'])
def get_user_orders(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, pick_order FROM draft_orders WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        orders = [{"group_id": row[0], "pick_order": row[1]} for row in rows]
        return jsonify(orders), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/approve-user', methods=['POST'])
def approve_user():
    data = request.json
    user_id = data.get('user_id')
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET admin_approved = 1 WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Oyuncunun 500 TL ödemesi onaylandı!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/pending-users', methods=['GET'])
def get_pending_users():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, admin_approved FROM users")
        rows = cursor.fetchall()
        conn.close()
        # Admin panelinin düzgün ayrım yapması için veritabanındaki adı 'admin_approved'ü 'is_active'e mapliyoruz
        users_list = [{"id": row[0], "username": row[1], "is_active": row[2]} for row in rows]
        return jsonify({"users": users_list}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/all-coupons', methods=['GET'])
def get_all_coupons():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.username, ut.group_num, ut.team_name, ut.updated_at 
            FROM user_teams ut
            JOIN users u ON ut.user_id = u.id
            ORDER BY u.username ASC, ut.group_num ASC
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        coupons_dict = {}
        for row in rows:
            username = row[0]
            group_num = row[1]
            team_name = row[2]
            updated_at = row[3] if row[3] else "Bilinmiyor"
            
            if username not in coupons_dict:
                coupons_dict[username] = {"last_update": updated_at, "selections": {}}
            coupons_dict[username]["selections"][group_num] = team_name
            
        return jsonify({"status": "success", "coupons": coupons_dict}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/live-coupons', methods=['GET'])
def get_live_coupons():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.username, ut.group_num, ut.team_name, ut.updated_at 
            FROM user_teams ut
            JOIN users u ON ut.user_id = u.id
            ORDER BY ut.updated_at DESC, u.username ASC
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        coupons_dict = {}
        for row in rows:
            username = row[0]
            group_num = row[1]
            team_name = row[2]
            updated_at = row[3] if row[3] else "Bilinmiyor"
            
            if username not in coupons_dict:
                coupons_dict[username] = {"last_update": updated_at, "selections": {}}
            coupons_dict[username]["selections"][group_num] = team_name
            
        return jsonify({"status": "success", "coupons": coupons_dict}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/clear-coupon/<username>', methods=['DELETE'])
def clear_coupon(username):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return jsonify({"status": "error", "message": "Kullanıcı bulunamadı!"}), 404
            
        user_id = user[0]
        cursor.execute("DELETE FROM user_teams WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"{username} kullanıcısının kuponu sıfırlandı! 🧹"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/delete-user/<string:username>', methods=['DELETE'])
def delete_user(username):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_row = cursor.fetchone()
        
        if not user_row:
            conn.close()
            return jsonify({"status": "error", "message": "Kullanıcı veritabanında bulunamadı!"}), 404
            
        user_id = user_row[0]
        cursor.execute("DELETE FROM draft_orders WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM user_teams WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"{username} arenadan tamamen temizlendi! 🧹"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/draw-kura', methods=['POST'])
def draw_kura():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # admin_approved = 1 olan oyuncuları çekiyoruz
        cursor.execute("SELECT id FROM users WHERE admin_approved = 1")
        players = [row[0] for row in cursor.fetchall()]
        
        if len(players) != 8:
            conn.close()
            return jsonify({"status": "error", "message": f"Kura için tam 8 onaylı oyuncu lazım! Şu anki onaylı: {len(players)}"}), 400
            
        cursor.execute("DELETE FROM draft_orders")
        
        TOTAL_GROUPS = 10 
        past_top_pickers = set()
        
        for g_id in range(1, TOTAL_GROUPS + 1):
            pool = [p for p in players if p not in past_top_pickers]
            punished = [p for p in players if p in past_top_pickers]
            
            random.shuffle(pool)
            random.shuffle(punished)
            current_group_order = pool + punished
            
            for index, p_id in enumerate(current_group_order):
                cursor.execute("INSERT INTO draft_orders (user_id, group_id, pick_order) VALUES (?, ?, ?)", 
                               (p_id, g_id, index + 1))
                
            past_top_pickers.add(current_group_order[0])
            past_top_pickers.add(current_group_order[1])
            
            if len(past_top_pickers) >= 8:
                past_top_pickers.clear()
                
        cursor.execute("UPDATE draft_status SET current_group_num = 1, current_pick_order = 1, is_started = 1 WHERE id = 1")
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Garantili Adalet kurası başarıyla çekildi! Canlı seçim odası aktif. 🎲"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/draft/current-status', methods=['GET'])
def get_draft_status():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT current_group_num, current_pick_order, is_started FROM draft_status WHERE id = 1")
        status_row = cursor.fetchone()
        
        if not status_row or status_row[2] == 0:
            conn.close()
            return jsonify({"is_started": 0, "message": "Adminin kura çekmesi bekleniyor..."}), 200
            
        g_num, p_order, is_started = status_row
        
        cursor.execute("""
            SELECT u.username, u.id FROM draft_orders d
            JOIN users u ON d.user_id = u.id
            WHERE d.group_id = ? AND d.pick_order = ?
        """, (g_num, p_order))
        user_row = cursor.fetchone()
        
        current_username = user_row[0] if user_row else "Bilinmiyor"
        current_user_id = user_row[1] if user_row else 0
        
        conn.close()
        return jsonify({
            "is_started": is_started,
            "current_group_num": g_num,
            "current_pick_order": p_order,
            "current_turn_username": current_username,
            "current_turn_user_id": current_user_id
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/')
def home():
    return app.send_static_file('index.html')

if __name__ == '__main__':
    import mimetypes
    mimetypes.add_type('text/css', '.css')
    mimetypes.add_type('application/javascript', '.js')
    
    port = int(os.environ.get("PORT", 5000))
    init_db()
    app.run(debug=False, host='0.0.0.0', port=port)