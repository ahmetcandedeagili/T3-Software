import sqlite3
from datetime import datetime
import os

class KaraKutu:
    def __init__(self, db_adi="farmmax_telemetri.db"):
        self.db_adi = db_adi
        self.tablolari_olustur()

    def baglan(self):
        # Thread kilitlenmesini engellemek için check_same_thread=False eklendi.
        conn = sqlite3.connect(self.db_adi, check_same_thread=False)
        
        # SQLite Turbo Modu (Eşzamanlı okuma/yazma için WAL mimarisi)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        return conn

    def tablolari_olustur(self):
        conn = self.baglan()
        cursor = conn.cursor()
        
        # 1. Metinsel Loglar Tablosu (Hata, Uyarı, Bilgi)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                seviye TEXT,
                modul TEXT,
                mesaj TEXT
            )
        ''')
        
        # 2. Telemetri Tablosu (Koordinat ve Hız verileri)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telemetry_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                durum TEXT,
                rx REAL, ry REAL,
                hx REAL, hy REAL,
                mesafe REAL,
                engel TEXT
            )
        ''')
        
        # UI'dan hızlı filtreleme yapabilmek için index ekliyoruz
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_seviye ON system_logs(seviye)')
        conn.commit()
        conn.close()

    def log_yaz(self, seviye, modul, mesaj):
        """Sistem uyarılarını ve olaylarını SQLite'a kaydeder"""
        zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_logs (tarih, seviye, modul, mesaj) VALUES (?, ?, ?, ?)", 
            (zaman, seviye, modul, mesaj)
        )
        conn.commit()
        conn.close()

    def telemetri_yaz(self, durum, rx, ry, hx, hy, mesafe, engel="Yok"):
        """Fiziksel sürüş verilerini ve koordinatları SQLite'a kaydeder"""
        zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO telemetry_logs (tarih, durum, rx, ry, hx, hy, mesafe, engel) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (zaman, durum, round(rx, 2), round(ry, 2), round(hx, 2) if hx else 0, round(hy, 2) if hy else 0, round(mesafe, 2) if mesafe else 0, engel)
        )
        conn.commit()
        conn.close()

    def log_getir(self, seviye_filtresi="TUMU", limit=50):
        """Arayüz (UI) için geçmiş logları getirir"""
        conn = self.baglan()
        cursor = conn.cursor()
        
        if seviye_filtresi == "TUMU":
            cursor.execute("SELECT tarih, seviye, modul, mesaj FROM system_logs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            cursor.execute("SELECT tarih, seviye, modul, mesaj FROM system_logs WHERE seviye = ? ORDER BY id DESC LIMIT ?", (seviye_filtresi, limit))
        
        sonuclar = cursor.fetchall()
        conn.close()
        
        # JSON formatına uygun sözlük (dict) listesi döndür
        return [{"tarih": row[0], "seviye": row[1], "modul": row[2], "mesaj": row[3]} for row in sonuclar]