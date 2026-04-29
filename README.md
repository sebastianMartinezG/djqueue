# 🎵 DJ Queue – Deploy en Railway

## Archivos del proyecto
djqueue/
├── app.py              ← Backend Flask
├── requirements.txt    ← Flask + Gunicorn
├── Procfile            ← Comando de arranque
├── railway.json        ← Config de Railway
└── templates/
    ├── client.html
    ├── admin.html
    └── admin_login.html

## 🚀 Deploy en Railway (5 minutos)

1. Sube esta carpeta a un repo de GitHub (github.com → New repo → sube los archivos)
2. Ve a railway.app → "New Project" → "Deploy from GitHub repo"
3. Selecciona tu repo → Railway detecta Python automáticamente
4. En Variables agrega: ADMIN_PASSWORD=TuContraseña y SECRET_KEY=cualquiertextoaletorio
5. Railway te da una URL pública → esa va en el QR

## 📱 QR
Con tu URL pública ve a qr.io, pega la URL y descarga el QR.

## ⚙️ Ajustes en app.py
MAX_SONGS_PER_USER = 3   (límite por persona)
PENALTY_OFFSET = 5        (posiciones de castigo)
