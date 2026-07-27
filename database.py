import sqlite3
import json

class VeritabaniYoneticisi:
    def __init__(self, db_adi="otonom_merkez.db"):
        self.db_adi = db_adi
        self.tablolari_olustur()

    def baglan(self):
        # Thread kilitlenmesini engellemek için check_same_thread=False eklendi
        conn = sqlite3.connect(self.db_adi, check_same_thread=False)
        
        # SQLite Turbo Modu (Eşzamanlı okuma/yazma için WAL mimarisi)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        return conn

    def tablolari_olustur(self):
        conn = self.baglan()
        cursor = conn.cursor()
        
        # YENİ: durum eklendi (Event-Driven görev kuyruğu için)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mesailer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                robot TEXT,
                harita TEXT,
                gunler TEXT,
                baslangic TEXT,
                sure_dk INTEGER,
                durum TEXT DEFAULT 'BEKLIYOR'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS saha_haritalari (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                harita_adi TEXT UNIQUE,
                gorsel_data TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rotalar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rota_adi TEXT UNIQUE,
                harita_adi TEXT,
                noktalar_json TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def zamani_dakikaya_cevir(self, saat_str):
        saat, dk = map(int, saat_str.split(':'))
        return saat * 60 + dk

    def cakisma_var_mi(self, robot, gunler_listesi, yeni_baslangic, yeni_sure):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("SELECT gunler, baslangic, sure_dk FROM mesailer WHERE robot = ? AND durum != 'TAMAMLANDI'", (robot,))
        mevcut_mesailer = cursor.fetchall()
        conn.close()

        yeni_bas_dk = self.zamani_dakikaya_cevir(yeni_baslangic)
        yeni_bitis_dk = yeni_bas_dk + int(yeni_sure)

        for kayit in mevcut_mesailer:
            kayitli_gunler = kayit[0].split(',')
            kayit_bas_dk = self.zamani_dakikaya_cevir(kayit[1])
            sure = kayit[2] if kayit[2] is not None else 30
            kayit_bitis_dk = kayit_bas_dk + sure
            
            ortak_gunler = set(gunler_listesi).intersection(set(kayitli_gunler))
            if len(ortak_gunler) > 0:
                if max(yeni_bas_dk, kayit_bas_dk) < min(yeni_bitis_dk, kayit_bitis_dk):
                    return True 
        return False 

    def mesai_ekle(self, robot, harita, gunler, baslangic, sure_dk=30):
        if self.cakisma_var_mi(robot, gunler, baslangic, sure_dk):
            return -1 

        conn = self.baglan()
        cursor = conn.cursor()
        gunler_str = ",".join(gunler)
        cursor.execute("INSERT INTO mesailer (robot, harita, gunler, baslangic, sure_dk, durum) VALUES (?, ?, ?, ?, ?, ?)", 
                       (robot, harita, gunler_str, baslangic, int(sure_dk), 'BEKLIYOR'))
        conn.commit()
        yeni_id = cursor.lastrowid
        conn.close()
        return yeni_id

    def mesai_durum_guncelle(self, mesai_id, yeni_durum):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("UPDATE mesailer SET durum = ? WHERE id = ?", (yeni_durum, mesai_id))
        conn.commit()
        conn.close()

    def mesai_sil(self, mesai_id):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM mesailer WHERE id = ?", (mesai_id,))
        conn.commit()
        conn.close()

    def mesaileri_getir(self):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("SELECT id, robot, harita, gunler, baslangic, sure_dk, durum FROM mesailer")
        kayitlar = cursor.fetchall()
        conn.close()
        
        liste = []
        for row in kayitlar:
            liste.append({
                "id": row[0], "robot": row[1], "harita": row[2],
                "gunler": row[3].split(","), "baslangic": row[4], 
                "sure_dk": row[5], "durum": row[6]
            })
        return liste
        
    def rota_kaydet(self, rota_adi, harita_adi, noktalar):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO rotalar (rota_adi, harita_adi, noktalar_json) VALUES (?, ?, ?)",
                       (rota_adi, harita_adi, json.dumps(noktalar)))
        conn.commit()
        conn.close()

    def rotalari_getir(self):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("SELECT id, rota_adi, harita_adi, noktalar_json FROM rotalar")
        kayitlar = cursor.fetchall()
        conn.close()
        liste = []
        for row in kayitlar:
            liste.append({"id": row[0], "rota_adi": row[1], "harita_adi": row[2], "noktalar": json.loads(row[3])})
        return liste

    def harita_kaydet(self, harita_adi, gorsel_data):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO saha_haritalari (harita_adi, gorsel_data) VALUES (?, ?)", 
                       (harita_adi, gorsel_data))
        conn.commit()
        conn.close()

    def harita_sil(self, harita_adi):
        conn = self.baglan()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM saha_haritalari WHERE harita_adi = ?", (harita_adi,))
        conn.commit()
        conn.close()