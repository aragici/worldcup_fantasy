from flask import Flask, request, jsonify
from flask_cors import CORS
import random
import sqlite3
import os
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(current_dir, 'static') if os.path.exists(os.path.join(current_dir, 'static')) else os.path.join(current_dir, '..', 'static')

# Dosyalar GitHub'da direkt dışarıda olduğu için Flask'a ana dizini 'static' olarak hedef gösteriyoruz:
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}}, methods=["GET", "POST", "PUT", "DELETE"])

DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
MAX_QUOTA = 2  # Kontenjan Sınırı: 4

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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS draft_orders (
            user_id INTEGER,
            group_id INTEGER, -- 1, 2, 3... (Toplam kaç grup varsa)
            pick_order INTEGER, -- 1'den 8'e kadar seçim sırası
            PRIMARY KEY (user_id, group_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS draft_orders (
            user_id INTEGER,
            group_id INTEGER,
            pick_order INTEGER,
            PRIMARY KEY (user_id, group_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # 🚨 ANLIK SIRA TAKİP TABLOSU (Şu an hangi grup ve kaçıncı sıra seçiyor?)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS draft_status (
            id INTEGER PRIMARY KEY,
            current_group_num INTEGER DEFAULT 1,
            current_pick_order INTEGER DEFAULT 1,
            is_started INTEGER DEFAULT 0 -- 0: Kura çekilmedi, 1: Draft Canlı!
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
    """Yeni oyuncu kaydı alır (Dakika hassasiyetli)."""
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
    """Oyuncu girişi ve 500 TL ödeme kontrolü."""
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
    """Takımların anlık kaç kez seçildiğini döndürür."""
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
    """Oyuncunun kuponunu veritabanına kaydeder/kilitler ve saniyeli zaman damgası vurur."""
    data = request.json
    user_id = data.get('user_id')
    selections = data.get('selections')

    # PARÇA PARÇA SEÇİME İZİN VERMEK İÇİN "len(selections) != 10" ZORUNLULUĞUNU KALDIRDIK
    if not user_id or selections is None:
        return jsonify({"status": "error", "message": "Eksik parametre! Seçim verisi bulunamadı!"}), 400

    updated_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Önce bu adamın eski kuponunu temizle
        cursor.execute("DELETE FROM user_teams WHERE user_id = ?", (user_id,))
        
        # Seçimleri tek tek dönüp kontenjanı kontrol ederek kaydet
        for s in selections:
            cursor.execute("SELECT COUNT(user_id) FROM user_teams WHERE team_name = ?", (s['team_name'],))
            current_count = cursor.fetchone()[0]
            
            if current_count >= MAX_QUOTA:
                conn.rollback()
                conn.close()
                return jsonify({"status": "error", "message": f"Hata: {s['team_name']} kontenjanı doldu! Sayfayı yenileyin."}), 400
                
            cursor.execute("INSERT INTO user_teams (user_id, group_num, team_name, updated_at) VALUES (?, ?, ?, ?)",
                           (user_id, s['group_num'], s['team_name'], updated_time))
            
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Kuponun başarıyla sisteme kilitlendi!\nZaman Damgası: {updated_time}"}), 200
    except Exception as e:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("ALTER TABLE user_teams ADD COLUMN updated_at TEXT")
            conn.commit()
            conn.close()
            return save_coupon()
        except:
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/user-coupon/<int:user_id>', methods=['GET'])
def get_user_coupon(user_id):
    """Oyuncunun eski seçimlerini geri yüklemek için çeker."""
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


@app.route('/api/admin/pending-users', methods=['GET'])
def get_pending_users():
    """Onay bekleyenleri listeler."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, created_at FROM users WHERE admin_approved = 0 ORDER BY created_at ASC")
        rows = cursor.fetchall()
        conn.close()
        
        users = [{"id": row[0], "username": row[1], "created_at": row[2]} for row in rows]
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/approve-user', methods=['POST'])
def approve_user():
    """Kullanıcı onay kilidini açar."""
    data = request.json
    user_id = data.get('user_id')
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET admin_approved = 1 WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Oyuncunun 500 TL ödemesi onaylandı! Giriş hakkı aktif."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/all-coupons', methods=['GET'])
def get_all_coupons():
    """Tüm oyuncuların kilitli kuponlarını ve kilitlenme saniyelerini admin için çeker."""
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
    """Tüm oyuncuların yaptığı kupon seçimlerini herkesin görmesi için döndürür."""
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
    """Kullanıcı adına göre kilitli kuponu sıfırlar (user_teams tablosundan siler)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Önce bu kullanıcı adının benzersiz ID'sini çekiyoruz
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return jsonify({"status": "error", "message": "Kullanıcı bulunamadı!"}), 404
            
        user_id = user[0]
        
        # 2. Gerçek kupon tablosu olan user_teams'den bu kullanıcının tüm seçimlerini kazıyoruz
        cursor.execute("DELETE FROM user_teams WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "message": f"{username} isimli oyuncunun kuponu sıfırlandı, tüm kontenjanlar boşa düştü! 🧹"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Kupon silinemedi: {str(e)}"}), 500


@app.route('/api/admin/draw-kura', methods=['POST'])
def draw_kura():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Sadece onaylı (aktif) olan 8 oyuncuyu çekiyoruz
    cursor.execute("SELECT id FROM users WHERE is_active = 1")
    players = [row[0] for row in cursor.fetchall()]
    
    if len(players) != 8:
        return jsonify({"message": f"Kura için tam 8 onaylı oyuncu lazım! Şu anki onaylı: {len(players)}"}), 400
        
    # Eski kura kayıtları varsa temizle
    cursor.execute("DELETE FROM draft_orders")
    
    # Toplam grup sayın kaçsa (Örn: 8 grup olsun)
    TOTAL_GROUPS = 8 
    
    # Kimlerin hangi grupta ilk 2'den seçtiğini takip eden küme
    past_top_pickers = set()
    
    for g_id in range(1, TOTAL_GROUPS + 1):
        # Bu grup için sırayı belirleyeceğimiz liste
        current_group_order = []
        
        # Henüz avantaja kavuşmamış oyuncular
        pool = [p for p in players if p not in past_top_pickers]
        # Daha önce ilk 2'den seçmiş olanlar (Cezalılar/Sona atılacaklar)
        punished = [p for p in players if p in past_top_pickers]
        
        # 1. Havuzu (hiç seçmeyenleri) kendi içinde karıştır
        random.shuffle(pool)
        # 2. Havuzu (daha önce seçenleri) kendi içinde karıştır
        random.shuffle(punished)
        
        # İkisini birleştir: Önce hiç seçmeyenler avantaja gelir, seçenler arkaya kayar
        current_group_order = pool + punished
        
        # Veritabanına bu grubun sıralamasını kaydet
        for index, p_id in enumerate(current_group_order):
            pick_order = index + 1 # 1-indexed sıralama (1. sıra, 2. sıra...)
            cursor.execute("""
                INSERT INTO draft_orders (user_id, group_id, pick_order) 
                VALUES (?, ?, ?)
            """, (p_id, g_id, pick_order))
            
        # Bu grubun ilk 2 seçenini "daha önce seçenler" listesine ekle
        past_top_pickers.add(current_group_order[0])
        past_top_pickers.add(current_group_order[1])
        
        # Eğer herkes en az bir kez ilk 2'den seçtiyse havuzu sıfırla ki döngü adilce devam etsin
        if len(past_top_pickers) >= 8:
            past_top_pickers.clear()
            
    conn.commit()
    conn.close()
    return jsonify({"message": "Garantili Adalet kurası başarıyla çekildi, tüm gruplar kilitlendi! 🎲"}), 200

@app.route('/api/admin/delete-user/<string:username>', methods=['DELETE'])
def delete_user(username):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # 1. Önce kullanıcının ID'sini bulalım
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_row = cursor.fetchone()
        
        if not user_row:
            return jsonify({"message": "Böyle bir kullanıcı veritabanında bulunamadı!"}), 404
            
        user_id = user_row[0]
        
        # 2. İlişkili tüm kura sıralamalarını sil (draft_orders)
        cursor.execute("DELETE FROM draft_orders WHERE user_id = ?", (user_id,))
        
        # 3. Kullanıcının yaptığı kupon seçimlerini sil (coupons tablonun adı her neyse)
        # Senin kupon tablonun adı muhtemelen 'selections' veya 'coupons'. Ona göre temizliyoruz:
        cursor.execute("DELETE FROM coupons WHERE user_id = ?", (user_id,))
        
        # 4. En son ana kullanıcı hesabını sil
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        
        conn.commit()
        return jsonify({"message": f"{username} arenadan tamamen diskalifiye edildi, tüm verileri temizlendi! 🧹"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"message": f"Silme esnasında SQL hatası çıktı: {str(e)}"}), 500
    finally:
        conn.close()
@app.route('/api/admin/pending-users', methods=['GET'])
def get_pending_users():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, is_active FROM users")
    rows = cursor.fetchall()
    conn.close()
    users_list = [{"id": row[0], "username": row[1], "is_active": row[2]} for row in rows]
    return jsonify({"users": users_list}), 200

@app.route('/api/admin/delete-user/<string:username>', methods=['DELETE'])
def delete_user(username):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_row = cursor.fetchone()
        if not user_row:
            return jsonify({"message": "Kullanıcı bulunamadı!"}), 404
        user_id = user_row[0]
        cursor.execute("DELETE FROM draft_orders WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM coupons WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return jsonify({"message": f"{username} turnuvadan tamamen silindi! 🧹"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"message": str(e)}), 500
    finally:
        conn.close()

# Oyuncunun ekranına anlık draft durumunu basan servis
@app.route('/api/draft/current-status', methods=['GET'])
def get_draft_status():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT current_group_num, current_pick_order, is_started FROM draft_status WHERE id = 1")
    status_row = cursor.fetchone()
    
    if not status_row or status_row[2] == 0:
        conn.close()
        return jsonify({"is_started": 0, "message": "Adminin kura çekmesi bekleniyor..."}), 200
        
    g_num, p_order, is_started = status_row
    
    # Şu an seçme sırası olan oyuncunun ismini bulalım
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
        "is_started": 1,
        "current_group_num": g_num,
        "current_pick_order": p_order,
        "current_turn_username": current_username,
        "current_turn_user_id": current_user_id
    }), 200

# Sıralı Takım Seçme Servisi
@app.route('/api/save-coupon', methods=['POST'])
def save_coupon():
    data = request.json
    user_id = int(data.get('user_id'))
    selections = data.get('selections') # Örn: [{"group_num": 1, "team_name": "Fransa"}]
    
    if not selections:
        return jsonify({"message": "Seçim eksik!"}), 400
        
    # Sadece o anki tek bir seçimi işliyoruz (Çünkü adım adım gidiyorlar)
    sel = selections[0]
    g_num = int(sel['group_num'])
    team = sel['team_name']
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # 1. Draft Canlı mı ve Sıra Gerçekten Bu Adamda mı Kontrol Et
    cursor.execute("SELECT current_group_num, current_pick_order, is_started FROM draft_status WHERE id = 1")
    status = cursor.fetchone()
    
    if not status or status[2] == 0:
        conn.close()
        return jsonify({"message": "Kura henüz çekilmedi, seçim odası kapalı!"}), 400
        
    c_group, c_pick, _ = status
    
    # Bu grupta bu adamın sırasını doğrula
    cursor.execute("SELECT pick_order FROM draft_orders WHERE user_id = ? AND group_id = ?", (user_id, g_num))
    user_order = cursor.fetchone()
    
    if not user_order or user_order[0] != c_pick or g_num != c_group:
        conn.close()
        return jsonify({"message": "🚨 Dur orada reis! Seçim sırası sende değil, darlık yapma!"}), 400
        
    # 2. Kontenjan Kontrolü (MAX 2)
    cursor.execute("SELECT COUNT(*) FROM coupons WHERE team_name = ?", (team,))
    team_count = cursor.fetchone()[0]
    if team_count >= 2:
        conn.close()
        return jsonify({"message": f"Bu takım [ {team} ] çoktan 2 kez seçilmiş, kontenjanı dolu!"}), 400
        
    # 3. Daha önce bu gruptan takım seçmiş mi kontrolü
    cursor.execute("SELECT id FROM coupons WHERE user_id = ? AND group_num = ?", (user_id, g_num))
    if cursor.fetchone():
        conn.close()
        return jsonify({"message": "Bu gruptan zaten seçimini yaptın reis!"}), 400

    # 4. Seçimi Kaydet
    cursor.execute("INSERT INTO coupons (user_id, group_num, team_name) VALUES (?, ?, ?)", (user_id, g_num, team))
    
    # 5. 🚀 AKILLI SIRA DEVİR MOTORU: Sırayı bir sonraki oyuncuya geçir
    if c_pick < 8:
        # Aynı grupta bir sonraki sıraya geç (1. sıradan 2. sıraya vb.)
        cursor.execute("UPDATE draft_status SET current_pick_order = ? WHERE id = 1", (c_pick + 1,))
    else:
        # Gruptaki 8 kişi de seçtiyse, bir sonraki gruba geç ve sırayı 1'e sıfırla!
        if c_group < 10:
            cursor.execute("UPDATE draft_status SET current_group_num = ?, current_pick_order = 1 WHERE id = 1", (c_group + 1,))
        else:
            # 10 grup da tamamen bittiyse draftı sonlandır
            cursor.execute("UPDATE draft_status SET is_started = 2 WHERE id = 1")
            
    conn.commit()
    conn.close()
    return jsonify({"message": "Takım başarıyla kilitlendi, sıra bir sonraki oyuncuya geçti reisim! 🚀"}), 200

@app.route('/')
def home():
    """Kullanıcı ana Ngrok linkine tıkladığında doğrudan index.html'e fırlatır."""
    return app.send_static_file('index.html')
if __name__ == '__main__':
    # Tarayıcının CSS dosyalarını kesinlikle stil dosyası olarak okumasını zorunlu kılıyoruz:
    import mimetypes
    mimetypes.add_type('text/css', '.css')
    mimetypes.add_type('application/javascript', '.js')
    
    port = int(os.environ.get("PORT", 5000))
    init_db()
    app.run(debug=False, host='0.0.0.0', port=port)