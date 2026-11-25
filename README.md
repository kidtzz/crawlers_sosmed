# Social Media Group & Resource Crawlers

Scraper Playwright untuk mencari grup WhatsApp, Google Drive, dan referensi belajar dari **Threads** dan **Twitter (X)** lengkap dengan filter anti-spam, filter relevansi topik, validasi link grup WhatsApp aktif + nama grup, dan ekspor rapi ke CSV & Markdown.

## Struktur Folder

```text
crawlers/
├── README.md
├── requirements.txt
├── .gitignore
├── shared/                     # Utilitas bersama (ekstraksi link, filter kualitas, ekspor)
│   ├── __init__.py
│   └── crawler_utils.py
├── threads/                    # Scraper khusus Threads
│   ├── __init__.py
│   ├── scraper.py
│   ├── .env.example
│   ├── .env.credentials.example
│   └── output/                 # Hasil CSV & Markdown Threads (diabaikan Git)
├── twitter/                    # Scraper khusus Twitter (X)
│   ├── __init__.py
│   ├── scraper.py
│   ├── .env.example
│   ├── .env.credentials.example
│   └── output/                 # Hasil CSV & Markdown X (diabaikan Git)
└── tests/                      # Unit test terpusat
    ├── __init__.py
    ├── test_threads_scraper.py
    └── test_x_scraper.py
```

## Instalasi

```powershell
pip install -r requirements.txt
playwright install chromium
```

## Konfigurasi & Cara Menjalankan

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
python -m unittest discover -s tests -v
```
