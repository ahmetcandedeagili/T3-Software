from flask import Flask, request, jsonify
import time

app = Flask(__name__)

# --- TERMİNAL RENKLERİ ---
C_GREEN = '\033[92m'
C_RED = '\033[91m'
C_YELLOW = '\033[93m'
C_BLUE = '\033[94m'
C_RESET = '\033[0m'

# YENİ: Estop kalıcı bir kilit değildi; /api/estop çağrılsa bile /api/drive
# hemen ardından gelen bir komutla motorları yeniden çalıştırabiliyordu.
# Artık gerçek donanımdaki gibi bir "kilit" bayrağı tutuluyor.
estop_active = False

@app.route('/')
def index():
    return jsonify({
        "status": "FarmMax ESP32 Mock Server Active", 
        "version": "1.0",
        "developer": "Ahmet Can Dedeagili"
    })

@app.route('/api/drive')
def drive():
    # URL'den gelen 'left' ve 'right' parametrelerini yakala
    left = request.args.get('left', default=0, type=int)
    right = request.args.get('right', default=0, type=int)

    # YENİ: Estop aktifken gelen sürüş komutlarını reddet.
    # Önceden bu kontrol yoktu; estop'tan hemen sonra gelen bir /cmd_vel
    # yayını (örn. autonomous.py'nin rotası temizlenmemişse) motorları
    # tekrar çalıştırabiliyordu.
    if estop_active:
        print(f"{C_RED}[MOTOR] Komut reddedildi -> ESTOP AKTİF, önce /api/clear_estop çağrılmalı.{C_RESET}")
        return jsonify({"status": "blocked_by_estop", "left": 0, "right": 0}), 423

    # Ekrana donanım logu bas (Motorlara PWM gidiyormuş gibi)
    if left == 0 and right == 0:
        print(f"{C_YELLOW}[MOTOR] Durduruldu (Fren).{C_RESET}")
    else:
        print(f"{C_GREEN}[MOTOR] Güç Verildi -> Sol Palet: %{left} | Sağ Palet: %{right}{C_RESET}")
        
    return jsonify({"status": "ok", "left": left, "right": right})

@app.route('/api/heartbeat')
def heartbeat():
    # Canlılık sinyali
    print(f"{C_BLUE}[HEARTBEAT] Bağlantı canlı (Deadman devrede).{C_RESET}")
    return "ok"

@app.route('/api/estop')
def estop():
    # Yazılımsal Acil Stop
    global estop_active
    estop_active = True
    print(f"{C_RED}!!! [ESTOP] YAZILIM ACİL STOP AKTİF! MOTORLAR KİLİTLENDİ !!!{C_RESET}")
    return jsonify({"status": "estop_active"})

@app.route('/api/clear_estop')
def clear_estop():
    # Acil Stop Kaldırma
    global estop_active
    estop_active = False
    print(f"{C_YELLOW}[ESTOP] Acil stop kaldırıldı. Sistem normale döndü.{C_RESET}")
    return jsonify({"status": "estop_cleared"})

@app.route('/api/status')
def status():
    # Sahte sensör verileri
    return jsonify({
        "distance_cm": 150, # Sahte mesafe sensörü
        "estop_active": estop_active,
        "max_speed_pct": 80
    })

if __name__ == '__main__':
    print(f"{C_GREEN}=========================================={C_RESET}")
    print(f"{C_GREEN} FarmMax ESP32 Sahte Sunucusu Başlatıldı{C_RESET}")
    print(f"{C_GREEN}=========================================={C_RESET}")
    print("Kart bekleniyor... Bu esnada testler localhost'ta!\n")
    
    # Bütün IP'lerden gelecek isteklere açık, 5000 portunda çalışıyor
    app.run(host='0.0.0.0', port=5000, debug=False)