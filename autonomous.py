import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from geometry_msgs.msg import Twist, PoseStamped, Point
from std_msgs.msg import String
from cv_bridge import CvBridge
import cv2
import cv2.aruco as aruco
import math
import json
from ultralytics import YOLO

class Autonomous(Node):
    def __init__(self):
        super().__init__('autonomous_merkez')
        
        # Loglama için anlık durumları tutacağımız değişkenler
        self.guncel_engel_var = False
        self.guncel_engel_ismi = ""
        self.guncel_durum = "BEKLEMEDE"
        
        self.log_timer = self.create_timer(1.0, self.log_kaydet)
        self.bridge = CvBridge()
        
        # MERKEZ KÖPRÜDEN GELEN PANORAMAYI (KAMERAYI) DİNLER
        self.subscription = self.create_subscription(
            Image, '/panorama/image_raw', self.image_callback, 10)
        
        # ARAYÜZDEN GELEN HEDEFLERİ DİNLER
        self.goal_subscription = self.create_subscription(
            PoseStamped, '/goal_pose', self.hedef_callback, 10)
            
        # MOTOR SÜRÜŞÜ İÇİN YAYINCI
        self.cmd_publisher = self.create_publisher(Twist, '/cmd_vel', 10)
        
        # YENİ: MERKEZ (BRIDGE) İLE HABERLEŞME YAYINCILARI (ArUco & Veritabanı)
        self.pose_publisher = self.create_publisher(Point, '/alfa/pixel_pose', 10)
        self.kutu_publisher = self.create_publisher(String, '/kara_kutu_log', 10)
        
        # ArUco Kurulumu
        self.aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        self.aruco_params = aruco.DetectorParameters()
        self.detector = aruco.ArucoDetector(self.aruco_dict, self.aruco_params)

        # YENİ: Bu node'un hangi robota (Alfa) ait olduğunu ArUco ID'si ile sabitliyoruz.
        # Önceden ilk görülen marker doğrudan "robot" kabul ediliyordu; Beta devreye
        # girdiğinde (farklı ID'li marker) bu, iki robotun karışmasına yol açardı.
        # Beta için ayrı bir node/parametre başlatılırken bu ID değiştirilmelidir
        # (örn. ROS parametresi olarak dışarıdan verilebilir).
        self.robot_aruco_id = 0
        
        self.get_logger().info("YOLOv8 Yukleniyor... (Engel Tespiti)")
        self.yolo_model = YOLO('yolov8n.pt') 
        
        # PROFESYONEL ROTA VE EVE DÖNÜŞ (RTH) DEĞİŞKENLERİ
        self.waypoints = []
        self.route_history = []
        self.returning_home = False
        
        # YENİ: Tolerans payı 10'dan 25'e çıkarıldı (Kamera Handoff Sıçramaları İçin Debounce)
        self.hedefe_varis_mesafesi = 25 
        self.guvenlik_yaricapi_px = 120 
        
        self.guncel_robot_x = None
        self.guncel_robot_y = None
        self.robot_theta = 0.0
        
        self.window_name = "T3 Yazilim - Otonom Kontrol Merkezi"
        cv2.namedWindow(self.window_name)
        cv2.setMouseCallback(self.window_name, self.mouse_callback)
        self.get_logger().info("Sistem Hazir! Arayuzden veya Sol Tik ile hedef bekleniyor...")

    def hedef_callback(self, msg):
        hedef_x = int(msg.pose.position.x)
        hedef_y = int(msg.pose.position.y)
        
        # BOMBA 4 ÇÖZÜLDÜ: KÖPRÜDEN GELEN (-1, -1) SİNYALİ ROTAYI SİLER!
        if hedef_x == -1 and hedef_y == -1:
            self.waypoints.clear()
            self.route_history.clear()
            self.returning_home = False
            self.get_logger().info("MERKEZDEN EMIR GELDI: ROTA TEMIZLENDI! Motorlar durdurulacak.")
            return # Hedef olarak eklemeden çık
        
        if len(self.waypoints) == 0 and self.guncel_robot_x is not None:
            base_pt = (self.guncel_robot_x, self.guncel_robot_y)
            self.waypoints.append(base_pt)
            self.route_history.append(base_pt)
            
        hedef_pt = (hedef_x, hedef_y)
        self.waypoints.append(hedef_pt)
        self.route_history.append(hedef_pt)
        self.returning_home = False 
        self.get_logger().info(f"UI'dan Hedef Geldi -> X:{hedef_x}, Y:{hedef_y}. Kalan Nokta: {len(self.waypoints)}")

    def aciyi_normalize_et(self, aci):
        while aci > math.pi: aci -= 2.0 * math.pi
        while aci < -math.pi: aci += 2.0 * math.pi
        return aci

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if len(self.waypoints) == 0 and self.guncel_robot_x is not None:
                base_pt = (self.guncel_robot_x, self.guncel_robot_y)
                self.waypoints.append(base_pt)
                self.route_history.append(base_pt)
            
            hedef_pt = (x, y)
            self.waypoints.append(hedef_pt)
            self.route_history.append(hedef_pt)
            self.returning_home = False 
            self.get_logger().info(f"Lokal Hedef Eklendi. Toplam Nokta: {len(self.waypoints)}")
            
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.waypoints.clear()
            self.route_history.clear()
            self.returning_home = False
            self.get_logger().info("ROTA TEMIZLENDI! Motorlar durdurulacak.")

    def image_callback(self, msg):
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # --- A: ARUCO KONUM TESPİTİ VE MERKEZE BİLDİRİM ---
            corners, ids, _ = self.detector.detectMarkers(gray)
            robot_x, robot_y, robot_theta = None, None, None
            
            if ids is not None:
                aruco.drawDetectedMarkers(frame, corners, ids)

                # YENİ: İlk görülen marker'ı değil, ID'si bu robota (self.robot_aruco_id)
                # ait olan marker'ı seç. Çoklu robot (Alfa/Beta) sahada aynı anda
                # görünürken karışmayı önler.
                hedef_idx = None
                for i, marker_id in enumerate(ids.flatten()):
                    if int(marker_id) == self.robot_aruco_id:
                        hedef_idx = i
                        break

                if hedef_idx is not None:
                    kose = corners[hedef_idx][0]
                    robot_x = int((kose[0][0] + kose[2][0]) / 2.0)
                    robot_y = int((kose[0][1] + kose[2][1]) / 2.0)

                    self.guncel_robot_x, self.guncel_robot_y = robot_x, robot_y
                    dx = kose[1][0] - kose[0][0]
                    dy = kose[1][1] - kose[0][1]
                    robot_theta = math.atan2(dy, dx)
                    self.robot_theta = robot_theta

                    # YENİ: ARUCO VERİSİNİ KÖPRÜYE YOLLA (Tek Gerçeklik Kaynağı)
                    pose_msg = Point()
                    pose_msg.x = float(robot_x)
                    pose_msg.y = float(robot_y)
                    pose_msg.z = float(robot_theta)
                    self.pose_publisher.publish(pose_msg)

                    cv2.circle(frame, (robot_x, robot_y), self.guvenlik_yaricapi_px, (255, 0, 0), 2)
                else:
                    # Sahnede marker var ama bu robota ait değil (ör. sadece Beta görünüyor)
                    self.guncel_robot_x, self.guncel_robot_y = None, None
            else:
                self.guncel_robot_x, self.guncel_robot_y = None, None

            # --- B: YOLO NESNE TESPİTİ VE ÇARPIŞMA ÖNLEYİCİ ---
            results = self.yolo_model(frame, verbose=False)
            frame = results[0].plot() 
            
            self.guncel_engel_var = False
            self.guncel_engel_ismi = ""
            
            if robot_x is not None:
                for box in results[0].boxes:
                    class_id = int(box.cls[0])
                    if class_id in [0, 15, 16, 17, 19] and float(box.conf[0]) > 0.50:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        engel_x, engel_y = int((x1 + x2) / 2), y2 
                        
                        if math.hypot(engel_x - robot_x, engel_y - robot_y) < self.guvenlik_yaricapi_px:
                            self.guncel_engel_var = True
                            self.guncel_engel_ismi = self.yolo_model.names[class_id]
                            cv2.line(frame, (robot_x, robot_y), (engel_x, engel_y), (0, 0, 255), 3)
                            break 

            # --- C: OTONOM SÜRÜŞ VE EVE DÖNÜŞ (RTH) MANTIĞI ---
            twist_msg = Twist()
            hedef_var_mi = len(self.waypoints) > 0
            
            if self.guncel_engel_var:
                cv2.putText(frame, f"ACIL FREN! {self.guncel_engel_ismi.upper()}!", (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            elif robot_x is None:
                cv2.putText(frame, "DIKKAT: ARUCO KAYIP! FREN!", (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            elif robot_x is not None and hedef_var_mi:
                hedef_x, hedef_y = self.waypoints[0]
                mesafe = math.hypot(hedef_x - robot_x, hedef_y - robot_y)
                
                if mesafe < self.hedefe_varis_mesafesi:
                    self.waypoints.pop(0)
                    
                    if len(self.waypoints) == 0:
                        if not self.returning_home and len(self.route_history) > 1:
                            self.get_logger().info("Gorev Bitti! Base'e Donuluyor...")
                            self.returning_home = True
                            self.waypoints = list(reversed(self.route_history))[1:]
                        elif self.returning_home:
                            self.get_logger().info("Base'e Varildi! Operasyon Tamamlandi.")
                            self.returning_home = False
                            self.route_history.clear()
                else:
                    hedef_acisi = math.atan2(hedef_y - robot_y, hedef_x - robot_x)
                    aci_farki = self.aciyi_normalize_et(hedef_acisi - robot_theta)
                    
                    if abs(aci_farki) > 0.15: 
                        twist_msg.linear.x = 0.0  
                        twist_msg.angular.z = 0.4 if aci_farki > 0 else -0.4 
                    else: 
                        twist_msg.linear.x = 0.2  
                        twist_msg.angular.z = aci_farki * 0.8 
                    
                    cv2.line(frame, (robot_x, robot_y), (hedef_x, hedef_y), (0, 255, 255), 2)
            
            self.cmd_publisher.publish(twist_msg)
            
            # --- D: GEOMETRİK ROTA GÖRSELLEŞTİRME ---
            for i in range(1, len(self.waypoints)): 
                cv2.line(frame, self.waypoints[i-1], self.waypoints[i], (0, 255, 0), 2)
            for wp in self.waypoints: 
                cv2.circle(frame, wp, 6, (0, 0, 255), -1)
                
            if self.returning_home:
                cv2.putText(frame, "MOD: EVE DONUS (RTH)", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
                
            cv2.imshow(self.window_name, frame)
            cv2.waitKey(1)
            
        except Exception as e:
            self.get_logger().error(f"Goruntu isleme hatasi: {e}")

    def log_kaydet(self):
        if self.guncel_robot_x is None:
            return

        hedef_x, hedef_y, mesafe = 0, 0, 0
        
        if len(self.waypoints) > 0:
            hedef_x, hedef_y = self.waypoints[0]
            mesafe = math.hypot(hedef_x - self.guncel_robot_x, hedef_y - self.guncel_robot_y)
            
            if self.guncel_engel_var:
                self.guncel_durum = "ACIL FREN"
            elif self.returning_home:
                self.guncel_durum = "EVE DONUS"
            else:
                self.guncel_durum = "SEYIR HALINDE"
        else:
            self.guncel_durum = "BEKLEMEDE"

        engel_metni = self.guncel_engel_ismi.upper() if self.guncel_engel_var else "Yok"

        # BOMBA 3 ÇÖZÜLDÜ: Veriyi lokal SQLite yerine MQTT/ROS üzerinden Köprüye (Bridge) salla!
        log_json = json.dumps({
            "durum": self.guncel_durum,
            "rx": self.guncel_robot_x,
            "ry": self.guncel_robot_y,
            "hx": hedef_x,
            "hy": hedef_y,
            "mesafe": mesafe,
            "engel": engel_metni
        })
        
        kutu_msg = String()
        kutu_msg.data = log_json
        self.kutu_publisher.publish(kutu_msg)

def main(args=None):
    rclpy.init(args=args)
    node = Autonomous()
    try: 
        rclpy.spin(node)
    except KeyboardInterrupt: 
        pass
    finally:
        node.cmd_publisher.publish(Twist()) # Dur
        node.destroy_node()
        cv2.destroyAllWindows()
        rclpy.shutdown()

if __name__ == '__main__':
    main()