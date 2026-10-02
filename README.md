# Jejak Klaim MVP (nol biaya)

Alur: **GitHub Actions (tiap jam)** → `ingest.py` → **SQLite** (disimpan di cache Actions) → `data/latest.json` → **GitHub Pages** (tampilan terminal `index.html`).

## Pasang (6 langkah)
1. Buat repo GitHub **publik** (Pages di plan Free hanya untuk repo publik), unggah semua file, termasuk folder `.github`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Tab Actions → `hourly` → **Run workflow** (uji manual pertama).
4. Buka URL Pages setelah run selesai; panel terisi dari `latest.json`.
5. Opsional: simpan `FACTCHECK_API_KEY` di Settings → Secrets → Actions. Cek dulu kuota dan biaya di Google Cloud Console.
6. Tambahkan feed RSS 2.0 yang sudah kamu cek sendiri ke `sources.json` (Atom belum didukung).

## Mengubah vonis
Edit `claims.json` di GitHub, lalu commit. Vonis hanya berasal dari file ini dan **tidak pernah diisi otomatis**.

## Rumus hotspot
- `n` = jumlah item yang memuat topik itu pada jam terakhir yang sudah lengkap (UTC).
- `rata-rata` = (jumlah item topik itu pada 24 jam sebelumnya) ÷ 24; jam tanpa item dihitung 0.
- `skor` = `n` ÷ `rata-rata` jika rata-rata > 0, selain itu `skor` = `n`.
- Topik = pasangan kata berurutan (bigram) dari judul, setelah stopword dibuang.
- Ambang awal: `n` ≥ 3 dan `skor` ≥ 3. Ini asumsi awal, bukan nilai baku; atur di `sources.json`.

## Batas dan risiko
- **Belum diuji ke GDELT dan Fact Check API sungguhan.** Uji dilakukan offline dengan respons palsu: dua kali jalan tidak menggandakan data, kegagalan sumber tercatat di panel status, dan tampilan melolosi uji escaping judul berbahaya.
- GDELT membatasi 1 permintaan per 5 detik per IP, dan runner GitHub berbagi IP dengan banyak pengguna, jadi HTTP 429 bisa muncul. Skrip mencoba ulang (jeda 15, 30, 60 detik) dan memakai 1 kueri saja. Dua feed Google News RSS (tidak resmi, tanpa dokumentasi formal; belum diuji dari sini) menjaga feed tetap terisi bila GDELT menolak.
- Workflow terjadwal di repo publik dinonaktifkan GitHub bila 60 hari tanpa aktivitas repo; `hourly.yml` membuat commit kosong tanggal 1 dan 16 untuk mencegahnya. Jadwal juga bisa tertunda.
- Cache Actions bukan penyimpanan permanen. Jika cache hilang, baseline 24 jam dibangun ulang (skor hotspot kurang akurat beberapa jam). Vonis tetap aman karena ada di `claims.json`.
- Cek versi action (`checkout@v4`, `cache@v4`, `upload-pages-artifact@v3`, `deploy-pages@v4`) sebelum dipakai lama.
- Hanya judul, URL, dan domain yang disimpan. Patuhi ToS tiap sumber dan jangan menampilkan akun individu sebagai tuduhan.
- **25 jam pertama panel Topik Naik sengaja kosong** (baseline belum lengkap; tanpanya setiap topik tampak naik). Feed dan watchlist langsung terisi. Jika cache hilang, masa tunggu ini diulang.
- `first_seen_at` = kemunculan paling awal yang terdeteksi sistem ini, bukan asal pasti.
- Pencocokan klaim memakai substring judul, sehingga bisa salah cocok. Ganti dengan pengelompokan setelah ada cukup kartu berlabel.
