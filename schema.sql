PRAGMA foreign_keys = ON;

-- Status tiap sumber (untuk panel "status sumber" di terminal)
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  nama TEXT NOT NULL UNIQUE,
  tipe TEXT NOT NULL CHECK (tipe IN ('gdelt','rss','factcheck_api')),
  url TEXT,
  terakhir_sukses TEXT,
  error_terakhir TEXT
);

-- Satu baris = satu artikel/laporan. Waktu selalu UTC: 2026-09-30T13:05:00Z
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL UNIQUE,
  judul TEXT NOT NULL,
  domain TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  hash_teks TEXT,
  raw TEXT,      -- JSON kecil dari sumber
  emosi TEXT     -- sengaja kosong dulu; diisi di fase berikutnya
);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published_at);

-- Klaim dikurasi manusia lewat claims.json; kolom vonis TIDAK pernah diisi otomatis
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  teks TEXT NOT NULL,
  first_seen_item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
  first_seen_at TEXT,      -- kemunculan paling awal yang terdeteksi, bukan asal pasti
  vonis TEXT CHECK (vonis IN ('benar','setengah','salah','belum_terbukti')),
  keyakinan_asal TEXT,
  catatan TEXT,
  kata_kunci TEXT          -- JSON array frasa untuk mencocokkan judul
);

CREATE TABLE IF NOT EXISTS claim_items (
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  peran TEXT NOT NULL DEFAULT 'amplifikasi' CHECK (peran IN ('asal','amplifikasi','bantahan')),
  PRIMARY KEY (claim_id, item_id)
);

CREATE TABLE IF NOT EXISTS hourly_counts (
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  jam TEXT NOT NULL,       -- awal jam UTC: 2026-09-30T13:00:00Z
  jumlah INTEGER NOT NULL,
  PRIMARY KEY (claim_id, jam)
);
