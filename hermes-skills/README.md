# Skill Hermes: `tiktok-pack`

Kirim foto produk + deskripsi seller ke bot Telegram Hermes, terus balasannya berisi:
- hero image 9:16 (produk asli dari foto seller ditempel ke background AI z.ai)
- prompt Google Flow (2 klip)
- caption + hashtag, teks layar, naskah VO 15 detik
- checklist posting

Videonya kamu bikin sendiri di Flow. Upload ke TikTok dan pasang link produk juga manual.

## Setup (sekali)
1. Clone repo ini di PC tempat Hermes jalan:
   `git clone https://github.com/AmarJSB/jsb.git`
2. Install `uv`. `uv` bawaan Hermes itu internal dan gak masuk PATH, jadi harus install sendiri:
   - Linux/WSL/Mac: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

   Habis itu buka terminal baru.
3. Buka `~/.hermes/config.yaml` (Windows: `%LOCALAPPDATA%\hermes\config.yaml`). Di situ biasanya
   udah ada baris `skills:`. Tambahin `external_dirs` **di bawah baris itu** (indent 2 spasi), jangan
   bikin blok `skills:` kedua (config-nya jadi gak kebaca):
   ```yaml
   skills:
     # ...setting lama biarin aja...
     external_dirs:
       - /path/ke/jsb/hermes-skills
   ```
   Pastikan juga `GLM_API_KEY=...` ada di `~/.hermes/.env` (biasanya udah ada dari setup provider
   z.ai) dan saldo z.ai pay-as-you-go-nya ada isinya. Terus restart Hermes.
4. Warm-up (gratis, gak manggil API). Pakai foto produk apa aja sebagai `contoh.jpg`:
   ```
   cd jsb/hermes-skills/tiktok-pack/scripts
   uv run test_hero.py
   uv run hero.py contoh.jpg --backdrop contoh.jpg
   ```
   Run pertama download sekitar 300 MB (library + model u2net), jadi bisa makan beberapa menit.
   Kalau berhasil, baris terakhirnya `HERO: <path>`. Run berikutnya cuma beberapa detik.
5. Tes di Telegram:
   - Kirim `jalankan: uv --version` buat mastiin Hermes nemu `uv`.
   - Kirim 1 foto produk dengan caption deskripsi dari seller.
   - Ketik `/tiktok-pack`.

## Pemakaian
- Kirim 1–3 foto sebagai foto biasa (pilih HD, jangan "kirim sebagai file", jangan HEIC). Captionnya
  isi deskripsi seller, lalu ketik `/tiktok-pack`.
- Biaya sekitar $0.015 per background, maksimal 2 kali per paket. Video pakai kredit Flow.
- Hasil tersimpan di `~/.hermes/cache/tiktok-pack/`. Mau ganti produk dengan background yang sama
  tanpa bayar lagi:
  `uv run hero.py FOTO --backdrop ~/.hermes/cache/tiktok-pack/<folder>/backdrop.jpg`

## Kalau ada masalah
- `ERROR: ...`: pesannya udah jelasin penyebabnya (saldo habis, key ditolak, filter konten, rate limit).
- Gambar gak kekirim di Telegram: pastikan balasan Hermes punya baris `MEDIA:<path>`. Kalau kamu
  nyalain pembatasan folder media, tambahin folder `cache/tiktok-pack` ke allow-list.
- `git pull` konflik karena Hermes sempat ngedit skill ini:
  `git checkout -- hermes-skills && git pull`
