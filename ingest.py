#!/usr/bin/env python3
"""Jejak Klaim MVP: dijalankan sekali per jam.
Ambil data -> simpan ke SQLite -> hitung sinyal hotspot -> tulis data/latest.json.
Hanya pustaka standar Python 3.9+ (tanpa pip)."""
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
DB, OUT = ROOT / "data" / "jejak.db", ROOT / "data" / "latest.json"
STOP = set("""yang dan di ke dari untuk dengan pada ini itu adalah akan juga atau ada tidak bisa sudah telah
oleh dalam para kata saat usai setelah hingga sebagai antara lebih karena agar jadi kini soal benarkah ternyata""".split())


def now():
    return datetime.now(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hour_of(s):
    return s[:13] + ":00:00Z"


def norm(s):
    """Rapikan tanggal apa pun ke format UTC standar; jika gagal, pakai waktu sekarang."""
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return iso(d if d.tzinfo else d.replace(tzinfo=timezone.utc))
    except Exception:
        return iso(now())


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "jejak-klaim-mvp/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------- database ----------
def connect():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    return con


def source_id(con, nama, tipe, url):
    con.execute("INSERT OR IGNORE INTO sources(nama,tipe,url) VALUES(?,?,?)", (nama, tipe, url))
    return con.execute("SELECT id FROM sources WHERE nama=?", (nama,)).fetchone()[0]


def mark(con, sid, error=None):
    if error:
        con.execute("UPDATE sources SET error_terakhir=? WHERE id=?", (error[:300], sid))
    else:
        con.execute("UPDATE sources SET terakhir_sukses=?, error_terakhir=NULL WHERE id=?", (iso(now()), sid))


def add_item(con, sid, url, judul, published, domain, raw):
    judul = re.sub(r"\s+", " ", judul or "").strip()
    if not url or not judul:
        return 0
    cur = con.execute(
        "INSERT OR IGNORE INTO items(source_id,url,judul,domain,published_at,fetched_at,hash_teks,raw) VALUES(?,?,?,?,?,?,?,?)",
        (sid, url, judul, domain, published, iso(now()),
         hashlib.sha1(judul.lower().encode()).hexdigest(), json.dumps(raw, ensure_ascii=False)))
    return cur.rowcount


# ---------- sumber ----------
def gdelt_get(url, tries=4):
    """GDELT membatasi 1 permintaan per 5 detik per IP, dan IP runner GitHub dipakai bersama
    banyak orang, sehingga 429 sering terjadi. Coba lagi dengan jeda 15, 30, 60 detik."""
    err = ""
    for i in range(tries):
        try:
            body = http_get(url).strip()
            if not body:
                return {}
            if body[0] in "{[":
                return json.loads(body)
            err = body[:120]  # teks biasa = pembatasan atau kueri ditolak
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            err = f"HTTP {e.code}"
        if i < tries - 1:
            time.sleep(15 * 2 ** i)
    raise RuntimeError(f"GDELT menolak setelah {tries} percobaan: {err}")


def fetch_gdelt(con, cfg):
    sid = source_id(con, "GDELT", "gdelt", "https://api.gdeltproject.org/api/v2/doc/doc")
    queries = cfg.get("gdelt_queries", [])
    n, ok, last_err = 0, 0, None
    time.sleep(random.uniform(0, 15))  # acak sedikit agar tidak serempak dengan pengguna lain
    for k, q in enumerate(queries):
        if k:
            time.sleep(10)
        try:
            url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode({
                "query": q, "mode": "artlist", "format": "json", "maxrecords": 250,
                "timespan": cfg.get("gdelt_timespan", "2h"), "sort": "datedesc"})
            for a in gdelt_get(url).get("articles", []):
                pub = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
                n += add_item(con, sid, a.get("url"), a.get("title"), pub, a.get("domain"), {"lang": a.get("language")})
            ok += 1
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    if ok or not queries:
        mark(con, sid)
    if last_err:
        mark(con, sid, ("sebagian kueri gagal: " if ok else "") + last_err)
    return n


def fetch_rss(con, feed):
    sid = source_id(con, feed["nama"], "rss", feed["url"])
    n = 0
    try:
        for it in ET.fromstring(http_get(feed["url"])).iter("item"):
            link = (it.findtext("link") or "").strip()
            title, src = it.findtext("title") or "", (it.findtext("source") or "").strip()
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]  # Google News menambahkan " - NamaMedia" di judul
            pd = it.findtext("pubDate")
            pub = iso(parsedate_to_datetime(pd)) if pd else iso(now())
            n += add_item(con, sid, link, title, pub, src or urllib.parse.urlparse(link).netloc, {})
        mark(con, sid)
    except Exception as e:
        mark(con, sid, f"{type(e).__name__}: {e}")
    return n


def fetch_factcheck(con, cfg):
    """Opsional: aktif hanya jika variabel lingkungan FACTCHECK_API_KEY diisi."""
    key = os.environ.get("FACTCHECK_API_KEY")
    if not key or not cfg.get("factcheck_queries"):
        return 0
    base = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
    sid = source_id(con, "Google Fact Check", "factcheck_api", base)
    n = 0
    try:
        for q in cfg["factcheck_queries"]:
            url = base + "?" + urllib.parse.urlencode(
                {"query": q, "languageCode": "id", "maxAgeDays": 2, "pageSize": 50, "key": key})
            for c in json.loads(http_get(url)).get("claims", []):
                for r in c.get("claimReview", []):
                    n += add_item(con, sid, r.get("url"), r.get("title") or c.get("text"),
                                  norm(r.get("reviewDate")), (r.get("publisher") or {}).get("site"),
                                  {"rating": r.get("textualRating"), "klaim": c.get("text")})
        mark(con, sid)
    except Exception as e:
        mark(con, sid, f"{type(e).__name__}: {e}")
    return n


# ---------- klaim ----------
def load_claims(con):
    for c in json.loads((ROOT / "claims.json").read_text(encoding="utf-8")):
        con.execute(
            """INSERT INTO claims(id,teks,vonis,keyakinan_asal,catatan,kata_kunci,first_seen_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET teks=excluded.teks, vonis=excluded.vonis,
                 keyakinan_asal=excluded.keyakinan_asal, catatan=excluded.catatan,
                 kata_kunci=excluded.kata_kunci,
                 first_seen_at=COALESCE(excluded.first_seen_at, claims.first_seen_at)""",
            (c["id"], c["teks"], c.get("vonis"), c.get("keyakinan_asal"), c.get("catatan"),
             json.dumps(c.get("kata_kunci", []), ensure_ascii=False), c.get("first_seen_at")))


def match_claims(con, ref):
    """Cocokkan judul item (48 jam terakhir) dengan kata kunci klaim, lalu hitung per jam."""
    since = iso(ref - timedelta(hours=48))
    for c in con.execute("SELECT id, kata_kunci FROM claims").fetchall():
        kws = [k.lower() for k in json.loads(c["kata_kunci"] or "[]") if k.strip()]
        if not kws:
            continue
        cond = " OR ".join("lower(judul) LIKE ?" for _ in kws)
        rows = con.execute(f"SELECT id, published_at FROM items WHERE published_at >= ? AND ({cond})",
                           [since] + [f"%{k}%" for k in kws]).fetchall()
        per = defaultdict(int)
        for r in rows:
            per[hour_of(r["published_at"])] += 1
            con.execute("INSERT OR IGNORE INTO claim_items(claim_id,item_id,peran) VALUES(?,?,'amplifikasi')",
                        (c["id"], r["id"]))
        for jam, n in per.items():
            con.execute("INSERT OR REPLACE INTO hourly_counts(claim_id,jam,jumlah) VALUES(?,?,?)", (c["id"], jam, n))
        if rows:
            first = min(rows, key=lambda r: r["published_at"])
            con.execute("""UPDATE claims SET first_seen_at=COALESCE(first_seen_at,?),
                           first_seen_item_id=COALESCE(first_seen_item_id,?) WHERE id=?""",
                        (first["published_at"], first["id"], c["id"]))


# ---------- sinyal hotspot ----------
def rate(get, cur):
    """n = jumlah pada jam acuan; base = rata-rata 24 jam sebelumnya (jam kosong = 0).
    skor = n / base jika base > 0, selain itu skor = n."""
    n = get(iso(cur))
    base = sum(get(iso(cur - timedelta(hours=h))) for h in range(1, 25)) / 24
    return n, base, (n / base if base > 0 else float(n))


def tokens(judul):
    return [t for t in re.findall(r"[a-z0-9]+", judul.lower()) if t not in STOP and len(t) > 2]


WARMUP_JAM = 25  # jam acuan + 24 jam baseline harus sudah terpantau


def warmup_left(con, ref):
    """Jam tersisa sampai baseline lengkap, dihitung dari pengambilan data pertama."""
    first = con.execute("SELECT MIN(fetched_at) FROM items").fetchone()[0]
    if not first:
        return WARMUP_JAM
    done = max(0.0, (ref - datetime.fromisoformat(first.replace("Z", "+00:00"))).total_seconds() / 3600)
    return math.ceil(max(0.0, WARMUP_JAM - done))


def hotspots(con, cfg, ref):
    if warmup_left(con, ref) > 0:
        return []  # tanpa baseline, setiap topik tampak "naik" (rata-rata 0 membuat skor = n)
    cur = ref - timedelta(hours=1)  # jam terakhir yang sudah lengkap
    rows = con.execute("SELECT id,judul,url,domain,published_at FROM items WHERE published_at >= ? AND published_at < ?",
                       (iso(cur - timedelta(hours=24)), iso(ref))).fetchall()
    per, ex = defaultdict(lambda: defaultdict(set)), defaultdict(list)
    for r in rows:
        w, jam = tokens(r["judul"]), hour_of(r["published_at"])
        for term in {f"{a} {b}" for a, b in zip(w, w[1:])}:
            per[term][jam].add(r["id"])
            if jam == iso(cur) and len(ex[term]) < 3:
                ex[term].append({"judul": r["judul"], "url": r["url"], "domain": r["domain"]})
    out = []
    for term, byhour in per.items():
        n, base, skor = rate(lambda j: len(byhour.get(j, ())), cur)
        if n >= cfg.get("hotspot_min_n", 3) and skor >= cfg.get("hotspot_min_skor", 3):
            out.append({"topik": term, "n": n, "rata_rata_24j": round(base, 2), "skor": round(skor, 2), "contoh": ex[term]})
    return sorted(out, key=lambda x: (-x["skor"], -x["n"]))[:15]


# ---------- ekspor ----------
def export(con, cfg, ref):
    cur = ref - timedelta(hours=1)
    claims = []
    for c in con.execute("SELECT * FROM claims ORDER BY id").fetchall():
        counts = {r["jam"]: r["jumlah"] for r in con.execute("SELECT jam,jumlah FROM hourly_counts WHERE claim_id=?", (c["id"],))}
        n, base, skor = rate(lambda j: counts.get(j, 0), cur)
        claims.append({"id": c["id"], "teks": c["teks"], "vonis": c["vonis"], "keyakinan_asal": c["keyakinan_asal"],
                       "catatan": c["catatan"], "first_seen_at": c["first_seen_at"],
                       "n": n, "rata_rata_24j": round(base, 2), "skor": round(skor, 2)})
    feed = [dict(r) for r in con.execute(
        "SELECT judul,url,domain,published_at FROM items ORDER BY published_at DESC LIMIT 40")]
    sources = [dict(r) for r in con.execute("SELECT nama,tipe,terakhir_sukses,error_terakhir FROM sources ORDER BY nama")]
    payload = {"generated_at": iso(now()), "jam_acuan": iso(cur),
               "rumus": "skor = n / rata-rata 24 jam sebelumnya (jika rata-rata 0: skor = n)",
               "warmup_jam": warmup_left(con, ref),
               "hotspots": hotspots(con, cfg, ref), "claims": claims, "feed": feed, "sources": sources}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    cfg = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    con = connect()
    ref = now().replace(minute=0, second=0, microsecond=0)
    load_claims(con)
    new = fetch_gdelt(con, cfg) + fetch_factcheck(con, cfg) + sum(fetch_rss(con, f) for f in cfg.get("rss", []))
    con.execute("DELETE FROM items WHERE published_at < ?", (iso(ref - timedelta(days=cfg.get("retention_days", 3))),))
    match_claims(con, ref)
    export(con, cfg, ref)
    con.commit()
    print(f"item baru: {new} | {OUT}")


if __name__ == "__main__":
    main()
