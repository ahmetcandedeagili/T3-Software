import asyncio
import websockets
import json
import threading
import math
import time
from datetime import datetime
import aiohttp
from aiohttp import web

# ==========================================
# GÖRÜNTÜ İŞLEME VE ROS KÜTÜPHANELERİ
# ==========================================
import cv2
import cv2.aruco as aruco
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, PoseStamped, Point
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image
from std_msgs.msg import String
from cv_bridge import CvBridge

from database import VeritabaniYoneticisi
from kara_kutu import KaraKutu 

db = VeritabaniYoneticisi("otonom_merkez.db")
kk = KaraKutu("farmmax_telemetri.db")

connected_clients = set()
ESP32_IP = "http://127.0.0.1:5000"  
main_loop = None  

# YENİ: ESP32 ve API istekleri için kalıcı (Global) bağlantı yöneticisi
global_http_session = None

filo_durumu = {
    "FarmMax - Alfa": {"x": 0.0, "y": 0.0, "durum": "BEKLEMEDE", "pil": 85, "bolge": "Odometri"},
    "FarmMax - Beta": {"x": 2.5, "y": 2.5, "durum": "BEKLEMEDE", "pil": 8, "bolge": "Odometri"} 
}

# =========================================================================
#  STITCHER & AKILLI YETKİ DEVRİ (HANDOFF) MOTORU
# =========================================================================
class CameraStream:
    def __init__(self, src):
        self.src = src
        self.stream = None
        self.ret = False
        self.frame = None
        self.stopped = False
        self.lock = threading.Lock()
        
    def start_stream(self):
        # YENİ: Başlangıçta ana döngüyü kilitlememesi için ayrı çağrılır
        self.stream = cv2.VideoCapture(self.src)
        self.ret, self.frame = self.stream.read()
        threading.Thread(target=self.update, daemon=True).start()

    def update(self):
        while not self.stopped:
            if self.stream is None or not self.stream.isOpened():
                self.stream = cv2.VideoCapture(self.src)
                time.sleep(1)
                continue
            ret, frame = self.stream.read()
            with self.lock:
                self.ret = ret
                self.frame = frame

    def read(self):
        with self.lock:
            return self.ret, self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.stopped = True
        if self.stream: self.stream.release()

class MultiCameraStitcher:
    def __init__(self, overlap_pixels=100):
        self.cameras = []
        self.overlap = overlap_pixels
        
        # --- YETKİ DEVRİ İÇİN HAFİF ARUCO ---
        self.aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        self.aruco_params = aruco.DetectorParameters()
        self.active_cam_idx = 0
        self.active_frame = None
        
        # --- UZAMSAL KOORDİNAT DÖNÜŞÜM VERİLERİ ---
        self.hat_uzunluk_m = 100.0  
        self.hat_genislik_m = 5.0   
        self.alfa_x_metre = 0.0
        self.alfa_y_metre = 0.0
        self.robot_gorunuyor_mu = False
        self.last_w, self.last_h = 640, 480

    def add_camera_sync(self, src):
        """Bu fonksiyon arka planda çalıştırılacak (Kilitlemeyi Önler)"""
        if str(src).isdigit(): src = int(src)
        cam = CameraStream(src)
        cam.start_stream()
        self.cameras.append(cam)
        print(f"📷 Stitcher: Yeni Tesis Kamerası Eklendi -> {src}")

    def update_active_camera_handoff(self, frames):
        """
        Senin istediğin 'Yetki Devri' mantığı:
        1. kamera 2. kameranın gördüğünü teyit etmeden yetkiyi bırakmaz!
        """
        if not frames: return
        if self.active_cam_idx >= len(frames): self.active_cam_idx = 0
            
        current_frame = frames[self.active_cam_idx]
        gray = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = aruco.detectMarkers(gray, self.aruco_dict, parameters=self.aruco_params)
        
        if ids is not None:
            # Robotu bulduk, konumuna bakalım
            cx = int((corners[0][0][0][0] + corners[0][0][2][0]) / 2)
            w = current_frame.shape[1]
            
            # Sağ sınıra (Overlap bölgesine) girdi mi? Sonraki kameraya sor!
            if cx > (w - self.overlap - 50) and self.active_cam_idx < len(frames) - 1:
                next_frame = frames[self.active_cam_idx + 1]
                next_gray = cv2.cvtColor(next_frame, cv2.COLOR_BGR2GRAY)
                next_corners, next_ids, _ = aruco.detectMarkers(next_gray, self.aruco_dict, parameters=self.aruco_params)
                
                if next_ids is not None:  # Kamera 2 gördü, YETKİYİ DEVRET!
                    print(f"🔄 Handoff (Sağa Geçiş): Kamera {self.active_cam_idx} -> Kamera {self.active_cam_idx + 1}")
                    self.active_cam_idx += 1
                    
            # Sol sınıra girdi mi? Önceki kameraya sor!
            elif cx < (self.overlap + 50) and self.active_cam_idx > 0:
                prev_frame = frames[self.active_cam_idx - 1]
                prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
                prev_corners, prev_ids, _ = aruco.detectMarkers(prev_gray, self.aruco_dict, parameters=self.aruco_params)
                
                if prev_ids is not None:  # Kamera 1 tekrar gördü, YETKİYİ GERİ VER!
                    print(f"🔄 Handoff (Sola Geçiş): Kamera {self.active_cam_idx} -> Kamera {self.active_cam_idx - 1}")
                    self.active_cam_idx -= 1
        else:
            # Kameralardan tamamen koptuysa, hepsine hızlıca bak (Kurtarma)
            for i, f in enumerate(frames):
                if i == self.active_cam_idx: continue
                g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                c, i_ds, _ = aruco.detectMarkers(g, self.aruco_dict, parameters=self.aruco_params)
                if i_ds is not None:
                    print(f"🔍 Kurtarma: Robot Kamera {i}'de bulundu. Yetki atandı.")
                    self.active_cam_idx = i
                    break

    def get_frames_and_stitch(self):
        if not self.cameras:
            h, w = 480, 640
            img = np.zeros((h, w, 3), dtype=np.uint8)
            cv2.putText(img, "KAMERA BEKLENIYOR", (30, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
            self.active_frame = img
            return img
        
        frames = []
        for cam in self.cameras:
            ret, frame = cam.read()
            if not ret or frame is None:
                img = np.zeros((480, 640, 3), dtype=np.uint8)
                frames.append(img)
            else:
                frame = cv2.resize(frame, (640, 480))
                frames.append(frame)

        # 1. AKILLI YETKİ DEVRİNİ ÇALIŞTIR VE AKTİF KAMERAYI BELİRLE
        self.update_active_camera_handoff(frames)
        self.active_frame = frames[self.active_cam_idx].copy() # UI Alt Panel İçin

        # 2. UI ÜST PANEL VE OTONOM İÇİN PANORAMAYI DİK
        if len(frames) == 1:
            stitched = frames[0]
        else:
            stitched = frames[0]
            for i in range(1, len(frames)):
                h, w, _ = frames[i].shape
                if w > self.overlap:
                    cropped = frames[i][:, self.overlap:]
                    stitched = np.hstack((stitched, cropped))
                else:
                    stitched = np.hstack((stitched, frames[i]))
                    
        self.last_h, self.last_w, _ = stitched.shape
        return stitched

tesis_stitcher = MultiCameraStitcher(overlap_pixels=100)

# =========================================================================
# HTTP MJPEG YAYINLARI (UI PANLLERİ İÇİN ÇİFT YAYIN)
# =========================================================================

async def mjpeg_handler_panorama(request):
    """ UI Üst Panel: Tüm rotayı çizmek için birleşmiş tesis haritası """
    boundary = "frame"
    response = web.StreamResponse(status=200, reason='OK', headers={
        'Content-Type': f'multipart/x-mixed-replace;boundary={boundary}', 'Access-Control-Allow-Origin': '*'})
    await response.prepare(request)
    
    while True:
        frame = tesis_stitcher.get_frames_and_stitch() # Hem birleştirir hem aktif kamerayı hesaplar
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ret:
            try: await response.write(b'--' + boundary.encode() + b'\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            except: break 
        await asyncio.sleep(0.05) 
    return response

async def mjpeg_handler_active(request):
    """ UI Alt Panel: Sadece robotun yetkisinde olduğu aktif kameranın yayını """
    boundary = "frame"
    response = web.StreamResponse(status=200, reason='OK', headers={
        'Content-Type': f'multipart/x-mixed-replace;boundary={boundary}', 'Access-Control-Allow-Origin': '*'})
    await response.prepare(request)
    
    while True:
        frame = tesis_stitcher.active_frame
        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ret:
                try: await response.write(b'--' + boundary.encode() + b'\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                except: break 
        await asyncio.sleep(0.05) 
    return response

async def start_camera_server():
    app = web.Application()
    app.router.add_get('/stitched_panorama', mjpeg_handler_panorama)
    app.router.add_get('/active_camera_feed', mjpeg_handler_active)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("📷 Tesis Panorama ve Aktif Kamera Sunucusu Başlatıldı (HTTP Port: 8080)")

# =========================================================================
# ROS2 VE MEVCUT HABERLEŞME MERKEZİ
# =========================================================================

async def send_log_to_ui(level: str, message: str):
    kk.log_yaz(level, "FMS_CORE", message)
    if not connected_clients: return
    log_payload = json.dumps({"tip": "LOG_EVENT", "timestamp": datetime.now().strftime("%H:%M:%S"), "level": level, "message": message})
    await asyncio.gather(*[ws.send(log_payload) for ws in connected_clients])

def twist_to_tank(linear_x: float, angular_z: float, max_linear=1.0, max_angular=1.0) -> tuple:
    norm_x = max(-max_linear, min(max_linear, linear_x)) / max_linear
    norm_z = max(-max_angular, min(max_angular, angular_z)) / max_angular
    left, right = norm_x - norm_z, norm_x + norm_z
    return max(-100, min(100, int(left * 100))), max(-100, min(100, int(right * 100)))

async def send_drive_to_esp32(linear_x: float, angular_z: float):
    # YENİ: Performans için kalıcı (Global) HTTP Session kullanılıyor
    left, right = twist_to_tank(linear_x, angular_z)
    try:
        if global_http_session:
            await global_http_session.get(f"{ESP32_IP}/api/drive?left={left}&right={right}", timeout=0.5)
    except Exception: pass

async def send_estop_to_esp32():
    try:
        if global_http_session:
            await global_http_session.get(f"{ESP32_IP}/api/estop", timeout=0.5)
    except Exception: pass

async def send_clear_estop_to_esp32():
    # YENİ: ACIL_STOP sonrası motorların tekrar hareket edebilmesi için
    # donanım tarafındaki kilidi açan çağrı. Önceden hiçbir yerden çağrılmıyordu.
    try:
        if global_http_session:
            await global_http_session.get(f"{ESP32_IP}/api/clear_estop", timeout=0.5)
    except Exception: pass

async def esp32_heartbeat_loop():
    while True:
        try:
            if global_http_session:
                await global_http_session.get(f"{ESP32_IP}/api/heartbeat", timeout=0.5)
        except Exception: pass
        await asyncio.sleep(1.0)

class RobotBridgeNode(Node):
    def __init__(self):
        super().__init__('robot_bridge_node')
        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.goal_pub = self.create_publisher(PoseStamped, '/goal_pose', 10)
        
        # Otonoma Rota Takibi İçin Panorama'yı Yayınla
        self.image_pub = self.create_publisher(Image, '/panorama/image_raw', 10)
        self.cv_bridge = CvBridge()
        self.create_timer(0.1, self.publish_image) 
        
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.cmd_vel_sub = self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)
        self.pose_sub = self.create_subscription(Point, '/alfa/pixel_pose', self.pixel_pose_callback, 10)
        self.log_sub = self.create_subscription(String, '/kara_kutu_log', self.kara_kutu_callback, 10)
        
        self.current_x = 0.0
        self.current_y = 0.0
        self.last_aruco_time = 0.0

    def publish_image(self):
        # Panorama görselini al ve ROS ağına bas (Otonom zeka için)
        frame = tesis_stitcher.get_frames_and_stitch()
        if frame is not None:
            msg = self.cv_bridge.cv2_to_imgmsg(frame, "bgr8")
            self.image_pub.publish(msg)

    def pixel_pose_callback(self, msg):
        h, w = tesis_stitcher.last_h, tesis_stitcher.last_w
        if h > 0 and w > 0:
            tesis_stitcher.alfa_y_metre = (msg.x / w) * tesis_stitcher.hat_uzunluk_m
            tesis_stitcher.alfa_x_metre = (msg.y / h) * tesis_stitcher.hat_genislik_m
            tesis_stitcher.robot_gorunuyor_mu = True
            self.last_aruco_time = time.time()

    def kara_kutu_callback(self, msg):
        try:
            data = json.loads(msg.data)
            kk.log_yaz(durum=data.get("durum"), rx=data.get("rx"), ry=data.get("ry"), hx=data.get("hx"), hy=data.get("hy"), mesafe=data.get("mesafe"), engel=data.get("engel"))
        except Exception: pass

    def odom_callback(self, msg):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y

    def cmd_vel_callback(self, msg):
        if main_loop is not None:
            asyncio.run_coroutine_threadsafe(send_drive_to_esp32(msg.linear.x, msg.angular.z), main_loop)

    def send_emergency_stop(self):
        msg = Twist()
        self.cmd_vel_pub.publish(msg)

    def send_goal(self, x, y):
        msg = PoseStamped()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.position.x, msg.pose.position.y, msg.pose.orientation.w = float(x), float(y), 1.0 
        self.goal_pub.publish(msg)

ros_node = None

async def robot_handler(websocket):
    print("✅ Musteri Arayuzu Baglandi!")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            komut = data.get("komut")
            
            # --- UI KOMUTLARI ---
            if komut == "MESAI_LISTESI_ISTE":
                await websocket.send(json.dumps({"tip": "MESAI_LISTESI_GUNCEL", "liste": db.mesaileri_getir()}))
            elif komut == "HEDEF_GIT":
                if ros_node: ros_node.send_goal(data.get("x"), data.get("y"))
            elif komut == "ROTA_TEMIZLE":
                await send_log_to_ui("WARN", "Manuel rota temizlendi.")
                if ros_node: ros_node.send_goal(-1.0, -1.0)
            elif komut == "ACIL_STOP":
                await send_log_to_ui("ERROR", "Kritik: AGV ACİL STOP Sistem Tarafından Tetiklendi!")
                if ros_node:
                    ros_node.send_emergency_stop()
                    # YENİ: Rota temizlenmezse autonomous.py bir sonraki karede eski
                    # hedefe gitmek için tekrar Twist yayınlar ve estop'u ezer.
                    ros_node.send_goal(-1.0, -1.0)
                await send_estop_to_esp32()
            elif komut == "ESTOP_KALDIR":
                await send_log_to_ui("WARN", "Acil stop kaldırıldı. Sistem normale döndü.")
                await send_clear_estop_to_esp32()
            elif komut == "YENI_KAMERA_EKLE":
                # YENİ: Sistemi Dondurmadan Asenkron Kamera Ekleme (Thread ile)
                ip = data.get("ip")
                await asyncio.to_thread(tesis_stitcher.add_camera_sync, ip)
                await send_log_to_ui("INFO", f"Stitcher: Tesis Kamerası Eklendi -> {ip}")

            # --- YENİ EKLENEN KOMUTLAR (Önceden UI'dan gönderilip hiç işlenmiyordu) ---

            elif komut == "ROTALARI_ISTE":
                await websocket.send(json.dumps({"tip": "ROTA_LISTESI_GUNCEL", "liste": db.rotalari_getir()}))

            elif komut == "ROTA_KAYDET":
                rota_adi = data.get("rota_adi")
                harita_adi = data.get("harita_adi")
                noktalar = data.get("noktalar")
                db.rota_kaydet(rota_adi, harita_adi, noktalar)
                await send_log_to_ui("INFO", f"Rota veritabanına kaydedildi: {rota_adi} ({harita_adi})")
                # Tüm bağlı istemcilerin mesai programlama ekranındaki rota listesi güncellensin
                guncel_liste = db.rotalari_getir()
                payload = json.dumps({"tip": "ROTA_LISTESI_GUNCEL", "liste": guncel_liste})
                await asyncio.gather(*[ws.send(payload) for ws in connected_clients], return_exceptions=True)

            elif komut == "MESAI_EKLE":
                robot = data.get("robot")
                harita = data.get("harita")
                gunler = data.get("gunler", [])
                baslangic = data.get("baslangic")
                sure_dk = data.get("sure_dk", 30)
                yeni_id = db.mesai_ekle(robot, harita, gunler, baslangic, sure_dk)

                if yeni_id == -1:
                    await websocket.send(json.dumps({
                        "tip": "GOREV_HATASI",
                        "robot": robot,
                        "mesaj": f"{robot} için seçilen gün/saat aralığında zaten planlanmış bir mesai var. Çakışma tespit edildi."
                    }))
                    await send_log_to_ui("WARN", f"Mesai çakışması nedeniyle reddedildi: {robot} / {harita}")
                else:
                    await send_log_to_ui("INFO", f"Yeni mesai kaydedildi: {robot} -> {harita} ({baslangic})")
                    guncel_liste = db.mesaileri_getir()
                    payload = json.dumps({"tip": "MESAI_LISTESI_GUNCEL", "liste": guncel_liste})
                    await asyncio.gather(*[ws.send(payload) for ws in connected_clients], return_exceptions=True)

            elif komut == "MESAI_SIL":
                mesai_id = data.get("id")
                db.mesai_sil(mesai_id)
                await send_log_to_ui("WARN", f"Mesai silindi: ID {mesai_id}")
                guncel_liste = db.mesaileri_getir()
                payload = json.dumps({"tip": "MESAI_LISTESI_GUNCEL", "liste": guncel_liste})
                await asyncio.gather(*[ws.send(payload) for ws in connected_clients], return_exceptions=True)

            elif komut == "HARITA_KAYDET":
                harita_adi = data.get("harita_adi")
                gorsel_data = data.get("gorsel_data")
                db.harita_kaydet(harita_adi, gorsel_data)
                await send_log_to_ui("INFO", f"Tesis krokisi veritabanına kaydedildi: {harita_adi}")

            elif komut == "HARITA_SIL":
                harita_adi = data.get("harita_adi")
                db.harita_sil(harita_adi)
                await send_log_to_ui("WARN", f"Tesis krokisi silindi: {harita_adi}")

            elif komut == "GECMIS_LOGLARI_ISTE":
                seviye = data.get("seviye", "TUMU")
                gecmis_loglar = kk.log_getir(seviye_filtresi=seviye, limit=100)
                await websocket.send(json.dumps({"tip": "GECMIS_LOGLAR", "liste": gecmis_loglar}))

            elif komut == "EVE_DON":
                robot = data.get("robot", "FarmMax - Alfa")
                await send_log_to_ui("WARN", f"{robot} için manuel Eve Dönüş (RTH) tetiklendi.")
                if ros_node:
                    # NOT: Şu an sadece mevcut rotayı temizliyor; autonomous.py'nin kendi
                    # route_history tersine çevirme mantığı yalnızca rota kendiliğinden
                    # bittiğinde devreye giriyor. Gerçek "zorla eve dön" davranışı için
                    # autonomous.py'ye ayrı bir RTH komutu/topic'i eklenmesi önerilir.
                    ros_node.send_goal(-1.0, -1.0)

            elif komut == "YAYIN_BASLAT":
                hat = data.get("hat")
                uzunluk = data.get("uzunluk", 100.0)
                genislik = data.get("genislik", 5.0)
                # YENİ: Önceden bu değerler hiç güncellenmiyordu, stitcher hep
                # sabit 100m x 5m varsayıyordu. Artık seçilen hatta göre ayarlanıyor.
                try:
                    tesis_stitcher.hat_uzunluk_m = float(uzunluk)
                    tesis_stitcher.hat_genislik_m = float(genislik)
                except (TypeError, ValueError):
                    pass
                await send_log_to_ui("INFO", f"Yayın hattı ayarlandı: {hat} ({uzunluk}m x {genislik}m)")
    except websockets.exceptions.ConnectionClosed: pass
    finally: connected_clients.remove(websocket)

async def task_dispatcher_loop():
    while True: await asyncio.sleep(10) # Görev yöneticisi aynı kalıyor

async def broadcast_telemetry():
    sayac = 0
    while True:
        if connected_clients:
            if ros_node and (time.time() - ros_node.last_aruco_time < 1.5):
                tesis_stitcher.robot_gorunuyor_mu = True
            else:
                tesis_stitcher.robot_gorunuyor_mu = False

            if tesis_stitcher.robot_gorunuyor_mu:
                filo_durumu["FarmMax - Alfa"]["x"] = tesis_stitcher.alfa_x_metre
                filo_durumu["FarmMax - Alfa"]["y"] = tesis_stitcher.alfa_y_metre
                filo_durumu["FarmMax - Alfa"]["bolge"] = f"Kamera {tesis_stitcher.active_cam_idx}" # UI'da hangi kamerada olduğunu da basar
            elif ros_node:
                filo_durumu["FarmMax - Alfa"]["x"] = ros_node.current_x
                filo_durumu["FarmMax - Alfa"]["y"] = ros_node.current_y
                filo_durumu["FarmMax - Alfa"]["bolge"] = "Odometri (Gözden Kayboldu)"
            
            message = json.dumps({"tip": "TELEMETRI_FILO", "robotlar": filo_durumu})
            for client in list(connected_clients):
                try: await client.send(message)
                except: pass
            
            if sayac % 2 == 0 and ros_node:
                # YENİ: ArUco/kamera pozisyonu görünür durumdaysa onu logla,
                # değilse odometriye düş. Önceden hep odometri yazılıyordu
                # ve bu, kamera görüyorken bile kara kutuyu yanıltıyordu.
                if tesis_stitcher.robot_gorunuyor_mu:
                    kk.telemetri_yaz("Seyir (Kamera)", tesis_stitcher.alfa_x_metre, tesis_stitcher.alfa_y_metre, 0, 0, 0)
                else:
                    kk.telemetri_yaz("Seyir (Odometri)", ros_node.current_x, ros_node.current_y, 0, 0, 0)
            sayac += 1
        await asyncio.sleep(0.5)

def ros_spin_thread(): rclpy.spin(ros_node)

async def main():
    global main_loop, global_http_session
    main_loop = asyncio.get_running_loop() 
    print("🚀 FMS WebSocket Koprusu Baslatildi (Port: 8765)...")
    
    # HTTP İstekleri İçin Kalıcı Oturum Başlatılıyor
    global_http_session = aiohttp.ClientSession()
    
    asyncio.create_task(start_camera_server())
    asyncio.create_task(broadcast_telemetry())
    asyncio.create_task(esp32_heartbeat_loop()) 
    asyncio.create_task(task_dispatcher_loop()) 
    
    async with websockets.serve(robot_handler, "0.0.0.0", 8765):
        await asyncio.Future()

if __name__ == "__main__":
    rclpy.init()
    ros_node = RobotBridgeNode()
    threading.Thread(target=ros_spin_thread, daemon=True).start()
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    finally:
        ros_node.destroy_node()
        rclpy.shutdown()
        # YENİ: Uygulama kapanırken kalıcı oturumu güvenli şekilde kapat
        if global_http_session:
            asyncio.run(global_http_session.close())