# Social Media Group & Resource Crawlers

Scraper Playwright untuk mencari grup WhatsApp, Google Drive, dan referensi belajar dari **Threads** dan **Twitter (X)** lengkap dengan filter anti-spam, filter relevansi topik, validasi link grup WhatsApp aktif + nama grup, dan ekspor rapi ke CSV & Markdown.

## Struktur Folder

```text
crawlers/
├── README.md
├── requirements.txt
├── .gitignore
├── crawler_utils.py            # Utilitas bersama (filter, validasi WA, ekspor)
├── threads/                    # Semua file Threads dalam 1 folder
│   ├── __init__.py
│   ├── scraper.py
│   ├── test_scraper.py
│   ├── .env.example
│   └── .env.credentials.example
└── twitter/                    # Semua file Twitter (X) dalam 1 folder
    ├── __init__.py
    ├── scraper.py
    ├── test_scraper.py
    ├── .env.example
    └── .env.credentials.example
```

## Instalasi

```powershell
pip install -r requirements.txt
playwright install chromium
```

## Cara Menjalankan

### 1. Threads
1. Salin `threads/.env.credentials.example` menjadi `threads/.env.credentials` dan isi kredensial akun cadangan.
2. Salin `threads/.env.example` menjadi `threads/.env` dan sesuaikan keyword serta filter topik/spam.
3. Jalankan:
   ```powershell
   python threads/scraper.py
   ```

### 2. Twitter (X)
1. Salin `twitter/.env.credentials.example` menjadi `twitter/.env.credentials` dan isi kredensial akun cadangan X.
2. Salin `twitter/.env.example` menjadi `twitter/.env` dan sesuaikan keyword serta filter topik/spam.
3. Jalankan:
   ```powershell
   python twitter/scraper.py
   ```

## Menjalankan Unit Test

```powershell
python -m unittest discover -s threads -v
python -m unittest discover -s twitter -v
```
