// ==========================================
// 1. BAĞLANTI VE GLOBAL DEĞİŞKENLER
// ==========================================
const socket = new WebSocket("ws://127.0.0.1:8765");

// Tesis harita ebatları (Kamera veya harita seçildiğinde dolar)
let HATTIN_UZUNLUGU_METRE = 0; 
let HATTIN_GENISLIGI_METRE = 0;  

// Rota (Teach) modülü için koordinat hafızası
let currentDrawnRoute = [];

// ==========================================
// 2. KARA KUTU VE LOG TERMİNALİ MODÜLÜ
// ==========================================
window.appendLogToTerminal = function(time, level, message) {
    const container = document.getElementById("log-container");
    if (!container) return;

    const logRow = document.createElement("div");
    logRow.className = `log-row ${level}`;
    logRow.innerText = `[${time}] [${level}] -> ${message}`;

    container.appendChild(logRow);
    container.scrollTop = container.scrollHeight; // Otomatik aşağı kaydır
};

window.clearLogs = function() {
    const container = document.getElementById("log-container");
    if (container) container.innerHTML = "";
};

window.addTask = function(taskName) {
    const time = new Date().toLocaleTimeString('tr-TR', { hour12: false });
    
    // Mesaj içeriğine göre hata seviyesini belirle
    let level = "INFO";
    if (taskName.includes("Hata") || taskName.includes("ACİL") || taskName.includes("Kritik")) level = "ERROR";
    else if (taskName.includes("Uyarı") || taskName.includes("İptal") || taskName.includes("Temizlendi")) level = "WARN";

    appendLogToTerminal(time, level, taskName);
};

// Veritabanındaki geçmiş logları Python'dan talep eder
window.fetchGecmisLogs = function() {
    if(socket && socket.readyState === WebSocket.OPEN) {
        clearLogs();
        appendLogToTerminal(new Date().toLocaleTimeString('tr-TR'), "INFO", "Veritabanından geçmiş loglar çekiliyor...");
        socket.send(JSON.stringify({ komut: "GECMIS_LOGLARI_ISTE", seviye: "TUMU" }));
    } else {
        alert("Bağlantı koptuğu için loglar çekilemiyor.");
    }
};

// ==========================================
// 3. WEBSOCKET OLAYLARI (EVENTS)
// ==========================================

// Bağlantı Kurulduğunda
socket.onopen = () => {
    console.log("FarmMax Backend'ine Bağlanıldı!");
    document.getElementById("conn-status").innerText = "Sistem Aktif";
    document.getElementById("conn-status").style.color = "#4caf50";
    
    // Açılışta kayıtlı verileri (Mesai ve Rotalar) çek
    socket.send(JSON.stringify({ komut: "MESAI_LISTESI_ISTE" }));
    socket.send(JSON.stringify({ komut: "ROTALARI_ISTE" }));
};

// Bağlantı Koptuğunda
socket.onclose = () => {
    console.log("Bağlantı Koptu!");
    document.getElementById("conn-status").innerText = "Bağlantı Yok";
    document.getElementById("conn-status").style.color = "#f44336";
};

// Sunucudan Veri Geldiğinde (Ana Yönlendirici)
socket.onmessage = (event) => {
    try {
        const data = JSON.parse(event.data);
        
        // A. Canlı Log Akışı
        if (data.tip === "LOG_EVENT") {
            appendLogToTerminal(data.timestamp, data.level, data.message);
        }

        // B. Veritabanından Gelen Geçmiş Loglar
        if (data.tip === "GECMIS_LOGLAR") {
            clearLogs();
            if (data.liste && data.liste.length > 0) {
                data.liste.reverse().forEach(log => {
                    let saat = log.tarih.split(" ")[1] || log.tarih; 
                    appendLogToTerminal(saat, log.seviye, log.mesaj);
                });
                appendLogToTerminal("---", "INFO", "Geçmiş log dökümü tamamlandı.");
            } else {
                appendLogToTerminal("---", "WARN", "Veritabanında kayıtlı log bulunamadı.");
            }
        }
        
        // C. ÇOKLU ROBOT FİLO TELEMETRİSİ 
        if (data.tip === "TELEMETRI_FILO") {
            const mapScrollArea = document.getElementById("map-scroll");
            const mapContainer = document.getElementById("stitched-map-container");
            const selectedRobot = document.getElementById("ui-active-robot") ? document.getElementById("ui-active-robot").value : "FarmMax - Alfa";

            // ÇÖZÜM 3: Sıfıra bölme güvenliği (HATTIN_GENISLIGI_METRE > 0 eklendi)
            if (mapScrollArea && mapContainer.offsetWidth > 0 && HATTIN_UZUNLUGU_METRE > 0 && HATTIN_GENISLIGI_METRE > 0) {
                
                // Alfa Robotunu Yürüt
                const alfaData = data.robotlar["FarmMax - Alfa"];
                const alfaMarker = document.getElementById("robot-marker-alfa");
                if (alfaData && alfaMarker) {
                    let axPct = Math.min(Math.max(alfaData.y / HATTIN_UZUNLUGU_METRE, 0), 1); 
                    let ayPct = Math.min(Math.max(alfaData.x / HATTIN_GENISLIGI_METRE, 0), 1);
                    alfaMarker.style.left = (axPct * mapContainer.offsetWidth) + "px";
                    alfaMarker.style.top = (ayPct * mapContainer.offsetHeight) + "px";
                    alfaMarker.style.display = "block";
                }

                // Beta Robotunu Yürüt
                const betaData = data.robotlar["FarmMax - Beta"];
                const betaMarker = document.getElementById("robot-marker-beta");
                if (betaData && betaMarker) {
                    let bxPct = Math.min(Math.max(betaData.y / HATTIN_UZUNLUGU_METRE, 0), 1); 
                    let byPct = Math.min(Math.max(betaData.x / HATTIN_GENISLIGI_METRE, 0), 1);
                    betaMarker.style.left = (bxPct * mapContainer.offsetWidth) + "px";
                    betaMarker.style.top = (byPct * mapContainer.offsetHeight) + "px";
                    betaMarker.style.display = "block";
                }
                
                // SOL PANEL BİLGİLERİNİ SADECE SEÇİLİ ROBOTA GÖRE GÜNCELLE
                const currentData = data.robotlar[selectedRobot];
                if (currentData) {
                    document.getElementById("pos-x").innerText = currentData.x.toFixed(2);
                    document.getElementById("pos-y").innerText = currentData.y.toFixed(2);
                    
                    const locSpan = document.getElementById("aruco-location");
                    locSpan.innerText = currentData.bolge;
                    if(currentData.bolge.includes("Kamera")) {
                        locSpan.style.color = "var(--accent-green)";
                    } else {
                        locSpan.style.color = "var(--accent-orange)";
                    }
                    
                    document.getElementById("battery-level").innerText = `%${currentData.pil}`;
                }
            }
        }
        
        // D. ArUco Kamera Konum Algılama
        // DÜZELTME: Bu blok var olmayan "active-robot" elementine erişiyordu (DOM'da
        // sadece "ui-active-robot" seçimi var) ve backend zaten bu bilgiyi
        // TELEMETRI_FILO mesajı içinde gönderiyor. Backend ileride ayrıca bir
        // ARUCO_KONUM olayı yayınlamaya başlarsa, güvenli/mevcut elementleri
        // kullanacak şekilde burada yeniden eklenebilir.
        if (data.tip === "ARUCO_KONUM") {
            const locSpan = document.getElementById("aruco-location");
            if (locSpan) {
                locSpan.innerText = data.bolge;
                locSpan.style.color = "var(--accent-green)";
            }
            addTask(`📍 FarmMax Konum Güncellendi: ${data.robot} -> ${data.bolge} bölgesinde.`);
        }

        // E. Mesai/Görev Listesi Güncelleme
        if (data.tip === "MESAI_LISTESI_GUNCEL") {
            renderSchedules(data.liste);
        }

        // F. Rota Listesi Güncelleme (Form Select Doldurma)
        if (data.tip === "ROTA_LISTESI_GUNCEL") {
            const routeSelect = document.getElementById("sch-route");
            if (routeSelect) {
                routeSelect.innerHTML = "";
                if(data.liste && data.liste.length > 0) {
                    data.liste.forEach(r => {
                        let opt = document.createElement("option");
                        opt.value = r.rota_adi;
                        opt.innerText = `📍 ${r.rota_adi} (${r.harita_adi})`;
                        routeSelect.appendChild(opt);
                    });
                } else {
                    routeSelect.innerHTML = `<option value="">Önce bir rota çizin...</option>`;
                }
            }
        }
        
        // G. Görev/Çakışma Hataları
        if (data.tip === "GOREV_HATASI") {
            alert(`⚠️ FarmMax UYARISI!\n\nRobot: ${data.robot || 'Bilinmiyor'}\nHata: ${data.mesaj}`);
            addTask(`❌ HATA: ${data.mesaj}`);
        }
    } catch (err) {
        console.error("WebSocket Mesajı İşlenirken Hata Oluştu:", err);
    }
};

// ==========================================
// 4. ARAYÜZ (UI) GÜNCELLEME MODÜLLERİ
// ==========================================

// Mesaileri Ekrana Çizme (Sağ Panel ve Modal İçi)
window.renderSchedules = function(schedules) {
    const schListModal = document.getElementById('sch-list'); 
    const schListMain = document.getElementById('sch-list-main'); 
    
    let modalHtml = '';
    let mainHtml = '';

    if (!schedules || schedules.length === 0) {
        let bosMesaj = '<div style="color:#888; text-align:center; padding:10px; font-size:12px;">Görev kuyruğu boş. Planlanmış mesai yok.</div>';
        if (schListModal) schListModal.innerHTML = '<li class="sch-empty">Veritabanında kayıtlı mesai yok.</li>';
        if (schListMain) schListMain.innerHTML = bosMesaj;
        return;
    }

    // Rotalara göre gruplama yap
    const klasorler = {};
    schedules.forEach(sch => {
        let haritaAdi = sch.harita || "Tesis Geneli"; 
        if (!klasorler[haritaAdi]) {
            klasorler[haritaAdi] = [];
        }
        klasorler[haritaAdi].push(sch);
    });

    for (const [haritaAdi, mesailer] of Object.entries(klasorler)) {
        mainHtml += `
            <div style="color: #ffb300; margin-top: 8px; font-weight: bold;">
                <span style="margin-right: 5px;">📍</span> ${haritaAdi}
            </div>
            <div style="margin-left: 10px; border-left: 1px dashed #555; padding-left: 12px; margin-top: 4px;">
        `;

        mesailer.forEach(sch => {
            let gunlerYazi = Array.isArray(sch.gunler) ? sch.gunler.join(",") : sch.gunler;
            let sure = sch.sure_dk ? sch.sure_dk : 30; // Varsayılan süre kontrolü
            let durum = sch.durum || "BEKLIYOR";
            let statusColor = durum === "GOREVDE" ? "#10b981" : "#f59e0b";
            
            // Ana Görev Paneli Görünümü
            mainHtml += `
                <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.03); padding: 4px 6px; margin-bottom: 3px; border-radius: 4px;">
                    <div style="color: #ddd;">
                        <span style="color: #666;">└─</span> 🤖 <strong style="color: #4caf50;">${sch.robot}</strong> 
                        <span style="color: #999; font-size: 11px; margin-left: 4px;">(${sch.baslangic} | ${sure}dk)</span>
                        <span style="font-size:10px; padding:2px 4px; border-radius:3px; background:${statusColor}; color:#000; margin-left:5px;">${durum}</span>
                    </div>
                    <button onclick="silMesai(${sch.id})" style="background: transparent; border: 1px solid #f44336; color: #f44336; border-radius: 3px; font-size: 10px; padding: 1px 5px; cursor: pointer;">Sil</button>
                </div>
            `;

            // Modal İçi Detaylı Görünüm
            modalHtml += `
                <li class="sch-item" style="display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.05); margin-bottom: 8px; padding: 10px; border-radius: 6px; border-left: 4px solid #4caf50;">
                    <div style="font-size: 13px; line-height: 1.5;">
                        <div>
                            <strong style="color: #fff;">${sch.robot}</strong> 
                            <span style="color: #4caf50;">(Rota: ${sch.harita})</span>
                            <span style="font-size:10px; padding:2px 4px; border-radius:3px; background:${statusColor}; color:#000; margin-left:5px;">${durum}</span>
                        </div>
                        <div style="color: #aaa;">📅 ${gunlerYazi} | ⏰ Başlama: ${sch.baslangic} (${sure}dk)</div>
                    </div>
                    <button onclick="silMesai(${sch.id})" class="sch-del-btn">İptal Et</button>
                </li>
            `;
        });
        mainHtml += `</div>`; 
    }

    if (schListModal) schListModal.innerHTML = modalHtml;
    if (schListMain) schListMain.innerHTML = mainHtml;
};

// ==========================================
// 5. DOM YÜKLENDİĞİNDE ÇALIŞACAK ETKİLEŞİMLER
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
    
    // Mesai Günleri Checkbox Toplu Seçim Mantığı
    const chkAll = document.getElementById('sch-all-days');
    const chkDays = document.querySelectorAll('.sch-day');

    if (chkAll) {
        chkAll.addEventListener('change', function() {
            chkDays.forEach(cb => cb.checked = this.checked);
        });
    }

    chkDays.forEach(cb => {
        cb.addEventListener('change', function() {
            if (!this.checked) chkAll.checked = false;
            else chkAll.checked = Array.from(chkDays).every(c => c.checked);
        });
    });

    // Donanım Güvenlik: Acil Stop Butonu
    document.getElementById("btn-estop").addEventListener("click", () => {
        alert("ACİL DURUM! FarmMax MOTORLARI KİLİTLENDİ!");
        clearLogs(); 
        addTask("FarmMax SİSTEMİ ACİL OLARAK DURDURULDU!");
        document.getElementById("btn-estop").style.backgroundColor = "#b71c1c";
        
        if(socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ komut: "ACIL_STOP" }));
        }
    });

    // YENİ: Acil Stop Kaldırma Butonu (ESP32 tarafındaki kalıcı kilidi açar)
    const btnEstopClear = document.getElementById("btn-estop-clear");
    if (btnEstopClear) {
        btnEstopClear.addEventListener("click", () => {
            addTask("FarmMax Acil Stop kilidi kaldırıldı, sistem normale döndü.");
            document.getElementById("btn-estop").style.backgroundColor = "";
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ komut: "ESTOP_KALDIR" }));
            }
        });
    }

    // Operasyon: Seçili Robotu Şarj İstasyonuna Döndür (RTH)
    document.getElementById("btn-rth").addEventListener("click", () => {
        const selectedRobot = document.getElementById("ui-active-robot") ? document.getElementById("ui-active-robot").value : "FarmMax - Alfa";
        addTask(`${selectedRobot} için Şarj İstasyonuna Dönüş (RTH) Başlatıldı`);
        if(socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ komut: "EVE_DON", robot: selectedRobot }));
        }
    });

    // UI: Modalların Açılıp Kapanması
    const toggleModal = (btnId, modalId, closeId) => {
        const btn = document.getElementById(btnId);
        const modal = document.getElementById(modalId);
        const close = document.getElementById(closeId);
        if(btn && modal && close) {
            btn.addEventListener('click', () => modal.classList.remove('hidden'));
            close.addEventListener('click', () => modal.classList.add('hidden'));
        }
    };

    toggleModal('btnSettings', 'settingsModal', 'closeSettings');
    toggleModal('btnCameraSetup', 'cameraModal', 'closeCameraModal');
    toggleModal('btnSchedule', 'scheduleModal', 'closeScheduleModal');

    // Rota Çizim Modalı Özel Davranışları
    const btnRouteDraw = document.getElementById('btnRouteDraw');
    const routeDrawModal = document.getElementById('routeDrawModal');
    const closeRouteDraw = document.getElementById('closeRouteDraw');
    if(btnRouteDraw && routeDrawModal) {
        btnRouteDraw.addEventListener('click', () => {
            routeDrawModal.classList.remove('hidden');
            currentDrawnRoute = []; // Menü açıldığında çizimleri temizle
            drawRouteLines(); 
        });
        closeRouteDraw.addEventListener('click', () => routeDrawModal.classList.add('hidden'));
    }

    // Tesis Krokisi Üzerine Tıklama ile Rota Oluşturma
    const drawCanvas = document.getElementById("draw-canvas-container");
    if(drawCanvas) {
        drawCanvas.addEventListener('click', function(e) {
            const rect = drawCanvas.getBoundingClientRect();
            // X-Y piksellerini görüntüye oranla yüzdelik hesapla (Responsive için)
            const xPct = (e.clientX - rect.left) / rect.width;
            const yPct = (e.clientY - rect.top) / rect.height;
            
            currentDrawnRoute.push({ x: xPct, y: yPct });
            document.getElementById("draw-points-count").innerText = `${currentDrawnRoute.length} Nokta Çizildi`;
            drawRouteLines();
        });
    }

    // Hat (Bölge) Seçimi Değiştiğinde Ölçekleme Güncellemesi
    const hatSelector = document.getElementById("hat-selector");
    if (hatSelector) {
        hatSelector.addEventListener("change", function() {
            const seciliOption = this.options[this.selectedIndex];
            const hatAdi = seciliOption.value;
            HATTIN_UZUNLUGU_METRE = parseFloat(seciliOption.getAttribute('data-length'));
            HATTIN_GENISLIGI_METRE = parseFloat(seciliOption.getAttribute('data-width'));
            if (hatAdi !== "") {
                kamerayiAc(hatAdi, HATTIN_UZUNLUGU_METRE, HATTIN_GENISLIGI_METRE);
            }
        });
    }

    // Harita / Kroki Yükleme
    const mapUploadInput = document.getElementById('mapUploadInput');
    const btnMapUploadTrigger = document.getElementById('btnMapUploadTrigger');
    const btnMapUploadConfirm = document.getElementById('btnMapUploadConfirm');
    const btnMapRemove = document.getElementById('btnMapRemove'); 
    const customMapName = document.getElementById('customMapName'); 
    const mapUploadStatus = document.getElementById('map-upload-status');
    const stitchedMapImg = document.getElementById('stitched-map-img');

    if (btnMapUploadTrigger && mapUploadInput) {
        btnMapUploadTrigger.addEventListener('click', () => mapUploadInput.click());

        mapUploadInput.addEventListener('change', function() {
            if (this.files && this.files.length > 0) {
                let fileName = this.files[0].name;
                if(fileName.length > 15) fileName = fileName.substring(0, 15) + "...";
                btnMapUploadTrigger.innerText = `📄 ${fileName}`;
                btnMapUploadTrigger.style.backgroundColor = "var(--t3-blue, #005B9F)";
            }
        });

        btnMapUploadConfirm.addEventListener('click', () => {
            const file = mapUploadInput.files[0];
            const haritaAdi = customMapName ? customMapName.value.trim() : "TesisGenel";

            if (customMapName && !haritaAdi) {
                alert("Lütfen Tesis/Ahır Krokisine bir isim verin!");
                return;
            }

            if (file) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const base64Image = e.target.result;
                    stitchedMapImg.src = base64Image;
                    stitchedMapImg.style.display = "block";
                    mapUploadStatus.style.display = "block";
                    
                    setTimeout(() => { 
                        mapUploadStatus.style.display = "none"; 
                        btnMapUploadTrigger.innerText = "📁 Kroki Seç";
                        mapUploadInput.value = ""; 
                    }, 3000);

                    if(typeof socket !== 'undefined' && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({ 
                            komut: "HARITA_KAYDET", 
                            harita_adi: haritaAdi,
                            gorsel_data: base64Image
                        }));
                    }
                };
                reader.readAsDataURL(file);
            } else {
                alert("Lütfen önce yüklemek için bir tesis krokisi seçin!");
            }
        });
    }

    // Tesis Haritası Silme İşlemi
    if (btnMapRemove) {
        btnMapRemove.addEventListener('click', () => {
            const haritaAdi = customMapName ? customMapName.value.trim() : "";
            if (customMapName && !haritaAdi) {
                alert("Silmek istediğiniz tesis haritasının adını kutuya yazın!");
                return;
            }
            if(socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ komut: "HARITA_SIL", harita_adi: haritaAdi }));
            }
            if (stitchedMapImg) {
                stitchedMapImg.src = "";
                stitchedMapImg.style.display = "none";
            }
            if (customMapName) customMapName.value = "";
            alert("Yüklenen tesis haritası başarıyla silindi!");
        });
    }
});

// ==========================================
// 6. ROTA ÇİZİM (TEACH & REPEAT) MODÜLÜ
// ==========================================

// Tıklanan koordinatları SVG üzerinde birleştirir
function drawRouteLines() {
    const svg = document.getElementById("draw-svg");
    const container = document.getElementById("draw-canvas-container");
    if(!svg || !container) return;
    
    svg.innerHTML = ""; // Önceki çizimleri temizle
    const width = container.clientWidth;
    const height = container.clientHeight;

    if(currentDrawnRoute.length === 0) return;

    // Aradaki çizgileri (Polyline) oluştur
    if(currentDrawnRoute.length > 1) {
        let pointsString = currentDrawnRoute.map(p => `${p.x * width},${p.y * height}`).join(" ");
        let polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        polyline.setAttribute("points", pointsString);
        polyline.setAttribute("fill", "none");
        polyline.setAttribute("stroke", "#10b981"); // FarmMax Yeşili
        polyline.setAttribute("stroke-width", "3");
        polyline.setAttribute("stroke-dasharray", "5,5"); 
        svg.appendChild(polyline);
    }

    // Hedef noktalarını (Düğüm/Circle) oluştur
    currentDrawnRoute.forEach((p, index) => {
        let circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", p.x * width);
        circle.setAttribute("cy", p.y * height);
        circle.setAttribute("r", "5");
        circle.setAttribute("fill", index === 0 ? "#ffcc00" : "#E30613"); // İlk nokta sarı
        svg.appendChild(circle);
    });
}

// Çizilen rotayı iptal et
window.clearDrawnRoute = function() {
    currentDrawnRoute = [];
    document.getElementById("draw-points-count").innerText = `0 Nokta Çizildi`;
    drawRouteLines();
};

// Çizilen rotayı isimle veritabanına kaydet
window.saveDrawnRoute = function() {
    const routeName = document.getElementById("draw-route-name").value.trim();
    const mapName = document.getElementById("draw-route-map").value;

    if(!routeName) return alert("Lütfen rotaya bir isim verin. (Örn: Ahır-1 Yem Rotası)");
    if(currentDrawnRoute.length < 2) return alert("Geçerli bir rota için haritaya en az 2 nokta çizmelisiniz.");

    if(socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            komut: "ROTA_KAYDET",
            rota_adi: routeName,
            harita_adi: mapName,
            noktalar: currentDrawnRoute
        }));
        
        addTask(`Sistem: '${routeName}' rotası başarıyla veritabanına işlendi.`);
        alert("Rota Başarıyla Kaydedildi! Görev Yöneticisinden atama yapabilirsiniz.");
        
        document.getElementById('routeDrawModal').classList.add('hidden');
        clearDrawnRoute();
        document.getElementById("draw-route-name").value = "";
    }
};

// ==========================================
// 7. KAMERA VE FİLO MESAİ YÖNETİMİ
// ==========================================

// Sisteme manuel IP Kamera Ekleme
window.kameraEkle = function() {
    let ahirSecim = document.getElementById('ahirSecim').value;
    let ipInput = document.getElementById('ipInput');
    let ip = ipInput.value.trim();
    
    if (ip !== "") {
        let liste = document.getElementById('kameraListesi');
        let yeniKamera = document.createElement('li');
        yeniKamera.innerHTML = `<strong>[${ahirSecim}]</strong> 📷 IP: ${ip}`;
        yeniKamera.style.backgroundColor = "rgba(255,255,255,0.05)";
        yeniKamera.style.padding = "10px";
        yeniKamera.style.marginTop = "8px";
        yeniKamera.style.borderRadius = "4px";
        yeniKamera.style.borderLeft = "4px solid var(--t3-blue)"; // DÜZELTME: --accent-blue tanımsızdı
        
        liste.appendChild(yeniKamera);
        ipInput.value = ''; 
        
        if(socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ komut: "YENI_KAMERA_EKLE", ahir: ahirSecim, ip: ip }));
        }
        addTask(`Tesis Kamerası Eklendi: ${ip} -> ${ahirSecim}`); 
    } else {
        alert("Geçerli bir IP girmelisiniz!");
    }
};

// Seçilen tesis bölgesinin panoramik görüntüsünü sunucudan talep etme
window.kamerayiAc = function(hedefHat, uzunluk, genislik) {
    let mesaj = document.getElementById('cam-feed-text');
    let lokalEkran = document.getElementById('kameraEkrani');
    let container = document.getElementById('video-container');
    
    mesaj.style.display = "block";
    mesaj.style.color = "#4caf50"; 
    mesaj.innerHTML = `⚙️ <strong>${hedefHat}</strong> Kameralarına Bağlanılıyor ve Tesis Görünümü Oluşturuluyor...`;
    lokalEkran.style.display = "none";
    container.style.backgroundColor = "#111";
    addTask(`Kamera İsteği: ${hedefHat} yayını talep edildi.`);

    if(socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ 
            komut: "YAYIN_BASLAT", 
            hat: hedefHat,
            uzunluk: uzunluk,
            genislik: genislik
        }));
    }

    const stitchedImg = document.getElementById('stitched-map-img');
    const scaleInfo = document.getElementById('map-scale-info');
    
    scaleInfo.innerText = `Aktif Hat: ${hedefHat.replace(/-/g, ' ')} | Boyut: ${uzunluk}m x ${genislik}m`;
    const scrollContainerHeight = 200; 
    
    // Görüntüyü gerçek dünya metriklerine (en/boy oranına) göre ölçekle
    if (genislik > 0) {
        const calculatedWidth = (scrollContainerHeight / genislik) * uzunluk;
        stitchedImg.style.width = calculatedWidth + "px";
    }
    
    // 1.5 Saniye sonra görüntülerin yüklendiğini varsayıp DOM'u güncelle
    setTimeout(() => {
        // ÇÖZÜM 2: YENİ HANDOFF AKTİF KAMERA URL'Sİ 
        lokalEkran.src = "http://localhost:8080/active_camera_feed"; 
        lokalEkran.style.display = "block";
        mesaj.style.display = "none";
        container.style.backgroundColor = "#0a1a0a";
        
        stitchedImg.src = "http://localhost:8080/stitched_panorama";
        stitchedImg.style.display = "block";
        
        // ÇÖZÜM 1: ROTA MODALINDAKİ HARİTAYI CANLANDIR
        const drawLiveMap = document.getElementById("draw-live-map");
        const drawPlaceholder = document.getElementById("draw-placeholder-text");
        if(drawLiveMap) {
            drawLiveMap.src = "http://localhost:8080/stitched_panorama";
            drawLiveMap.style.display = "block";
            if(drawPlaceholder) drawPlaceholder.style.display = "none";
        }
    }, 1500); 
};

window.kameraHatasi = function() {
    let mesaj = document.getElementById('cam-feed-text');
    let lokalEkran = document.getElementById('kameraEkrani');
    let container = document.getElementById('video-container');
    
    lokalEkran.style.display = "none"; 
    mesaj.style.display = "block";
    mesaj.style.color = "#f44336"; 
    mesaj.innerHTML = "Tesis Görüntüsü Oluşturulamadı<br><span style='font-size:14px; color:#ccc;'>Kamera bağlantılarını kontrol edin.</span>";
    container.style.backgroundColor = "#1a0505";
    addTask("Hata: Görüntü işleme işlemi başarısız oldu.");
};

// Robotlara otonom çalışma takvimi ve süre sınırı ekleme
window.mesaiProgramla = function() {
    const robot = document.getElementById('sch-robot').value;
    const rotaAdi = document.getElementById('sch-route').value; 
    const baslangic = document.getElementById('sch-start').value;
    const sureDk = document.getElementById('sch-duration').value; 
    
    const checkboxes = document.querySelectorAll('.sch-day');
    let secilenGunler = [];
    checkboxes.forEach(cb => {
        if(cb.checked) secilenGunler.push(cb.value);
    });

    if(!baslangic || !sureDk || secilenGunler.length === 0 || !rotaAdi) {
        alert("Lütfen FarmMax'in tesiste çalışacağı rotayı, günleri, saati ve TAHMİNİ SÜREYİ eksiksiz seçin!");
        return;
    }

    if(socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            komut: "MESAI_EKLE",
            robot: robot,
            harita: rotaAdi, 
            gunler: secilenGunler,
            baslangic: baslangic,
            sure_dk: parseInt(sureDk)
        }));
    }

    // Modal içerisindeki verileri sıfırla
    checkboxes.forEach(cb => cb.checked = false);
    document.getElementById('sch-all-days').checked = false; 
    document.getElementById('sch-start').value = "";
    document.getElementById('sch-duration').value = "30";
};

// Veritabanından görev silme
window.silMesai = function(id) {
    if(socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ komut: "MESAI_SIL", id: id }));
    }
};