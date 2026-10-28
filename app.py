import base64
from datetime import datetime, timedelta, timezone
import io
import math
import os
import sqlite3
import zlib

import pandas as pd
import requests
import streamlit as st

# ======================================================================
# KONFIGURASI UMUM & VARIABEL LINGKUNGAN
# PRD shelterkopi_: Kasir dan Menu Digital Shelter Kopi
# Tim: Widodo Hadi Siswoyo (240121035), Gilang (240121034), Dwi Hadi Wira Utomo (240121113)
# ======================================================================
DB_FILE = "shelterkopi.db"
WIB = timezone(timedelta(hours=7))

# PIN & Notifikasi dari Environment Variable (NF-04)
KASIR_PIN = os.getenv("KASIR_PIN", "1234")
OWNER_PIN = os.getenv("OWNER_PIN", "8888")
DEFAULT_TOPIC = os.getenv("NTFY_TOPIC", "shelterkopi-7391")

NAMA_KEDAI = "Shelter Kopi"
HANDLE = "shelterkopi_"
JAM_BUKA = "16.00 - 00.00"
ALAMAT = "Jl. Pantai Labu, Emplasmen Kualanamu, Kec. Beringin, Deli Serdang, Sumut"
KATEGORI = ["Kopi Susu", "Kopi Hitam", "Bukan Kopi"]
STOK_AWAL = 50

# 17 Menu Awal Sesuai PRD Section 7 & 10 (FR-16)
# Format: (nama, kategori, harga_panas, harga_dingin) - 0 = tidak tersedia
MENU_AWAL = [
    ("Kopi Susu", "Kopi Susu", 13000, 15000),
    ("Cappuccino", "Kopi Susu", 15000, 18000),
    ("Kopster", "Kopi Susu", 0, 17000),
    ("Vietnam Drip", "Kopi Susu", 20000, 0),
    ("Espresso", "Kopi Hitam", 13000, 15000),
    ("Americano", "Kopi Hitam", 13000, 15000),
    ("Kopi Tubruk", "Kopi Hitam", 13000, 0),
    ("Kopi Klasik", "Kopi Hitam", 13000, 0),
    ("V60", "Kopi Hitam", 20000, 20000),
    ("Teh Lemon", "Bukan Kopi", 12000, 12000),
    ("Teh Hijau", "Bukan Kopi", 15000, 15000),
    ("Teh Tarik Om", "Bukan Kopi", 13000, 13000),
    ("Taro", "Bukan Kopi", 15000, 15000),
    ("Redvelvet", "Bukan Kopi", 15000, 15000),
    ("Coklat", "Bukan Kopi", 15000, 15000),
    ("Milo", "Bukan Kopi", 15000, 15000),
    ("Lemsu", "Bukan Kopi", 13000, 13000),
]

st.set_page_config(
    page_title="shelterkopi_ · Kasir & Menu Digital",
    page_icon="☕",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ======================================================================
# INISIALISASI BASIS DATA (FR-16, Section 10)
# ======================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # Tabel menu (FR-16, Section 10)
    c.execute("""
        CREATE TABLE IF NOT EXISTS menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT UNIQUE NOT NULL,
            kategori TEXT NOT NULL,
            harga_panas INTEGER NOT NULL DEFAULT 0,
            harga_dingin INTEGER NOT NULL DEFAULT 0,
            stok INTEGER NOT NULL DEFAULT 0,
            gambar TEXT DEFAULT ''
        )
    """)
    # Tabel pesanan (FR-08, Section 10)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pesanan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pembeli TEXT NOT NULL,
            waktu TEXT NOT NULL,
            metode TEXT NOT NULL,
            total INTEGER NOT NULL,
            diterima INTEGER NOT NULL DEFAULT 0,
            kembalian INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Tabel item (relasi pesanan 1 : 1..*) (FR-08, Section 10)
    c.execute("""
        CREATE TABLE IF NOT EXISTS item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pesanan_id INTEGER NOT NULL,
            menu TEXT NOT NULL,
            ukuran TEXT NOT NULL,
            qty INTEGER NOT NULL,
            harga INTEGER NOT NULL,
            FOREIGN KEY (pesanan_id) REFERENCES pesanan(id) ON DELETE CASCADE
        )
    """)
    # Tabel setelan (FR-15, Section 10)
    c.execute("""
        CREATE TABLE IF NOT EXISTS setelan (
            k TEXT PRIMARY KEY,
            v TEXT
        )
    """)

    # Isi 17 menu awal jika tabel masih kosong (FR-16)
    c.execute("SELECT COUNT(*) FROM menu")
    if c.fetchone()[0] == 0:
        c.executemany(
            """INSERT INTO menu (nama, kategori, harga_panas, harga_dingin, stok, gambar)
               VALUES (?, ?, ?, ?, ?, '')""",
            [(n, k, p, d, STOK_AWAL) for n, k, p, d in MENU_AWAL]
        )

    # Inisialisasi setelan bawaan
    c.execute("INSERT OR IGNORE INTO setelan (k, v) VALUES ('qris_image', '')")
    c.execute("INSERT OR IGNORE INTO setelan (k, v) VALUES ('ntfy_topic', ?)", (DEFAULT_TOPIC,))
    
    conn.commit()
    conn.close()

init_db()

# ======================================================================
# HELPER DATA & PENGATURAN
# ======================================================================
def get_setting(key, default=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT v FROM setelan WHERE k = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["v"] if row and row["v"] is not None else default

def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO setelan (k, v) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def rupiah(x):
    try:
        return "Rp " + f"{int(x):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp 0"

def rb(x):
    try:
        val = int(x)
        return f"{val / 1000:g}rb"
    except (TypeError, ValueError):
        return "0rb"

def angka_menu(x):
    try:
        val = int(x)
        return f"{val / 1000:g}" if val > 0 else "-"
    except (TypeError, ValueError):
        return "-"

def now_wib():
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")

def file_ke_uri(f):
    mime = f.type or "image/png"
    return f"data:{mime};base64," + base64.b64encode(f.getvalue()).decode()

def muat_menu_df():
    conn = get_db()
    df = pd.read_sql("SELECT * FROM menu ORDER BY id", conn)
    conn.close()
    if df.empty:
        return df
    # Urutkan berdasarkan KATEGORI bawaan + kategori kustom baru
    all_kats = KATEGORI + [k for k in df["kategori"].dropna().unique() if k not in KATEGORI]
    df["kategori_sort"] = pd.Categorical(df["kategori"], all_kats, ordered=True)
    return df.sort_values(["kategori_sort", "id"]).drop(columns=["kategori_sort"]).reset_index(drop=True)

# ======================================================================
# ILUSTRASI MINUMAN (SVG Vektor Tajam, Indah & Responsif)
# ======================================================================
LATAR_SVG = {
    "Kopi Susu": ("#FFEFB5", "#F8D45C"),
    "Kopi Hitam": ("#FFE6A3", "#F1BC3A"),
    "Bukan Kopi": ("#FFF4C9", "#F9DE6E"),
}

ES_POS = [
    (146, 146, 50, -12), (208, 134, 52, 10), (160, 214, 54, 8),
    (222, 222, 48, -9), (146, 282, 50, 14), (210, 290, 52, -6), (176, 100, 50, -8)
]

def _spec(nama):
    n = nama.lower()
    if "vietnam" in n:
        return dict(layers=[("#3a1f0e", 1), ("#8a5a34", 1), ("#f1dcb8", 1.6)], extra="phin")
    if "cappuccino" in n:
        return dict(layers=[("#d9b38a", 1.3), ("#a86f43", 1.4), ("#5a3219", 1)], top="art")
    if "kopster" in n:
        return dict(layers=[("#caa27a", 1), ("#8a5530", 1.4), ("#f0dcc0", 1)])
    if "kopi susu" in n:
        return dict(layers=[("#4a2813", 1), ("#a2693a", 1.2), ("#f4e4c8", 1.6)])
    if "espresso" in n:
        return dict(layers=[("#b9773a", .4), ("#3a1d0d", 1.4), ("#2a1408", 1)])
    if "americano" in n:
        return dict(layers=[("#3a1e0e", 1), ("#24120a", 1.5)])
    if "tubruk" in n:
        return dict(layers=[("#2e1709", 1.2), ("#1b0e06", 1.6)], extra="ampas")
    if "klasik" in n:
        return dict(layers=[("#4a2813", 1), ("#2d1609", 1.4)])
    if "v60" in n:
        return dict(layers=[("#6a3b1a", 1), ("#3b200e", 1.4)], extra="v60")
    if "lemsu" in n:
        return dict(layers=[("#fbefb2", 1), ("#f4d86a", 1.5)], extra="lemon")
    if "lemon" in n:
        return dict(layers=[("#f3c24a", 1), ("#e29a2a", 1.6)], extra="lemon")
    if "hijau" in n:
        return dict(layers=[("#b7d56a", 1), ("#8fb83c", 1.6)])
    if "tarik" in n:
        return dict(layers=[("#c58f55", 1.3), ("#a96d3a", 1.2)], top="foam")
    if "taro" in n:
        return dict(layers=[("#cdb4ee", 1), ("#a782d6", 1.5)], top="cream")
    if "redvelvet" in n or "red velvet" in n:
        return dict(layers=[("#f8dfe0", .7), ("#d4333f", 1.2), ("#a31524", 1.4)], top="cream")
    if "coklat" in n or "cokelat" in n:
        return dict(layers=[("#8a5030", 1), ("#4a2412", 1.6)], top="cream")
    if "milo" in n:
        return dict(layers=[("#6b4423", 1), ("#3d2411", 1.6)], top="bubuk")
    return dict(layers=[("#a86f43", 1), ("#5a3219", 1.5)])

def _grad_cair(uid, layers):
    total = sum(w for _, w in layers)
    stops, acc = [], 0.0
    for i, (c, w) in enumerate(layers):
        a, b = acc / total, (acc + w) / total
        acc += w
        pad = min(0.08, (b - a) / 3)
        s = 0 if i == 0 else a + pad
        e = 1 if i == len(layers) - 1 else b - pad
        stops.append(f'<stop offset="{s:.3f}" stop-color="{c}"/><stop offset="{e:.3f}" stop-color="{c}"/>')
    return f'<linearGradient id="l{uid}" x1="0" y1="0" x2="0" y2="1">{"".join(stops)}</linearGradient>'

def _bunga(cx, cy, r, op):
    petals = "".join(
        f'<ellipse cx="0" cy="{-r * .62:.1f}" rx="{r * .16:.1f}" ry="{r * .4:.1f}" fill="#fff" transform="rotate({a})"/>'
        for a in range(0, 360, 45)
    )
    return f'<g transform="translate({cx} {cy})" opacity="{op}">{petals}<circle r="{r * .12:.1f}" fill="#fff"/></g>'

def _lemon(cx, cy, r):
    seg = "".join(
        f'<line x1="{cx}" y1="{cy}" x2="{cx + (r - 7) * math.cos(a * 0.01745):.1f}" '
        f'y2="{cy + (r - 7) * math.sin(a * 0.01745):.1f}" stroke="#fff6c2" stroke-width="2.5"/>'
        for a in range(0, 360, 45)
    )
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#F7D33F" stroke="#E8B923" stroke-width="3"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r - 5}" fill="#FFF0A0"/>{seg}'
        f'<circle cx="{cx}" cy="{cy}" r="4" fill="#fff6c2"/>'
    )

def _phin(rim, uid, cx=200, h=92):
    y0 = rim - h
    return (
        f'<ellipse cx="{cx}" cy="{rim - 6}" rx="104" ry="12" fill="#b7bcc4" stroke="#8d939c" stroke-width="2"/>'
        f'<rect x="{cx - 62}" y="{y0 + 22}" width="124" height="{rim - y0 - 28}" rx="8" fill="url(#mt{uid})"/>'
        f'<rect x="{cx - 70}" y="{y0 + 12}" width="140" height="14" rx="7" fill="#c9ced6"/>'
        f'<rect x="{cx - 52}" y="{y0}" width="104" height="14" rx="7" fill="#aab0b9"/>'
        f'<circle cx="{cx}" cy="{y0 - 6}" r="8" fill="#8d939c"/>'
        f'<ellipse cx="{cx}" cy="{rim + 12}" rx="5" ry="8" fill="#4a2813"/>'
    )

def _v60(rim, uid, cx=200, h=96):
    return (
        f'<path d="M{cx - 88} {rim - h} L{cx + 88} {rim - h} L{cx + 24} {rim - 6} L{cx - 24} {rim - 6} Z" '
        f'fill="#F6F1E4" stroke="#c9bfa8" stroke-width="3" stroke-linejoin="round"/>'
        f'<ellipse cx="{cx}" cy="{rim - h}" rx="88" ry="11" fill="#3b200e" stroke="#c9bfa8" stroke-width="3"/>'
        f'<path d="M{cx - 60} {rim - h + 8} L{cx - 16} {rim - 8} M{cx - 20} {rim - h + 10} L{cx - 6} {rim - 8} '
        f'M{cx + 20} {rim - h + 10} L{cx + 6} {rim - 8} M{cx + 60} {rim - h + 8} L{cx + 16} {rim - 8}" '
        f'stroke="#d8cfba" stroke-width="2.5" fill="none"/>'
        f'<path d="M{cx + 86} {rim - h + 12} Q{cx + 128} {rim - h + 12} {cx + 124} {rim - h + 44}" fill="none" '
        f'stroke="#F6F1E4" stroke-width="12" stroke-linecap="round"/>'
        f'<ellipse cx="{cx}" cy="{rim + 12}" rx="5" ry="8" fill="#4a2813"/>'
    )

def _cream(cx, base, r):
    return (
        f'<ellipse cx="{cx}" cy="{base}" rx="{r}" ry="{r * .3:.1f}" fill="#FFFAF0" stroke="#EADBC0" stroke-width="2"/>'
        f'<ellipse cx="{cx}" cy="{base - r * .32:.1f}" rx="{r * .74:.1f}" ry="{r * .26:.1f}" fill="#FFFAF0" stroke="#EADBC0" stroke-width="2"/>'
        f'<ellipse cx="{cx}" cy="{base - r * .6:.1f}" rx="{r * .46:.1f}" ry="{r * .22:.1f}" fill="#FFFAF0" stroke="#EADBC0" stroke-width="2"/>'
        f'<path d="M{cx - 8} {base - r * .74:.1f} Q{cx} {base - r * 1.08:.1f} {cx + 8} {base - r * .74:.1f} Z" fill="#FFFAF0" stroke="#EADBC0" stroke-width="2"/>'
    )

def _bagian_dingin(uid, sp):
    glass = "M112 84 L288 84 L266 324 Q264 342 246 342 L154 342 Q136 342 134 324 Z"
    top = sp.get("top")
    ext = sp.get("extra", "")
    p = ['<ellipse cx="200" cy="356" rx="108" ry="12" fill="#2B2118" opacity=".16"/>',
         f'<path d="{glass}" fill="#fff" fill-opacity=".32"/>']
    isi = [f'<rect x="100" y="122" width="200" height="230" fill="url(#l{uid})"/>']
    for x, y, s, r in ES_POS:
        isi.append(
            f'<g transform="rotate({r} {x + s / 2} {y + s / 2})">'
            f'<rect x="{x}" y="{y}" width="{s}" height="{s}" rx="9" fill="#fff" fill-opacity=".26" '
            f'stroke="#fff" stroke-opacity=".75" stroke-width="2.5"/>'
            f'<path d="M{x + 9} {y + 14} L{x + 9} {y + s - 16}" stroke="#fff" stroke-opacity=".7" '
            f'stroke-width="3" stroke-linecap="round"/></g>'
        )
    if top in ("foam", "art"):
        isi.append('<ellipse cx="200" cy="122" rx="92" ry="20" fill="#FFF3DF"/>')
        for cx, cy, r in [(150, 118, 9), (176, 112, 11), (206, 116, 10), (236, 112, 11), (258, 120, 8)]:
            isi.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFF8EC"/>')
    if top == "bubuk":
        for cx, cy in [(150, 124), (168, 120), (186, 126), (204, 121), (222, 127), (240, 122), (256, 126), (176, 132), (232, 134)]:
            isi.append(f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="#6b3f1d" opacity=".9"/>')
    if ext == "ampas":
        for cx, cy in [(160, 320), (178, 328), (198, 322), (218, 330), (238, 322), (190, 312), (226, 314), (170, 334)]:
            isi.append(f'<circle cx="{cx}" cy="{cy}" r="3.6" fill="#120a04"/>')
    p.append(f'<g clip-path="url(#k{uid})">{"".join(isi)}</g>')
    if ext not in ("phin", "v60"):
        p.append('<line x1="252" y1="26" x2="214" y2="268" stroke="#F2A93B" stroke-width="12" stroke-linecap="round"/>')
        p.append('<line x1="252" y1="26" x2="214" y2="268" stroke="#fff" stroke-width="12" stroke-dasharray="9 13" opacity=".55"/>')
    p.append(f'<path d="{glass}" fill="none" stroke="#fff" stroke-width="4" stroke-opacity=".95"/>')
    p.append('<path d="M127 112 L142 318" stroke="#fff" stroke-width="8" stroke-linecap="round" opacity=".5"/>')
    p.append('<path d="M268 120 L260 250" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".35"/>')
    p.append('<rect x="106" y="78" width="188" height="11" rx="5.5" fill="#fff" opacity=".96"/>')
    for cx, cy in [(158, 160), (166, 226), (250, 190), (244, 268), (176, 296)]:
        p.append(f'<ellipse cx="{cx}" cy="{cy}" rx="3.4" ry="4.6" fill="#fff" opacity=".55"/>')
    if top == "cream":
        p.append(_cream(200, 82, 78))
    if ext == "phin":
        p.append(_phin(84, uid, h=62))
    if ext == "v60":
        p.append(_v60(84, uid, h=64))
    if ext == "lemon":
        p.append(_lemon(292, 92, 34))
    return "".join(p)

def _bagian_panas(uid, sp, skala=1.0):
    top = sp.get("top")
    ext = sp.get("extra", "")
    permukaan = sp["layers"][0][0]
    badan = "M112 196 L288 196 L278 318 Q275 346 248 346 L152 346 Q125 346 122 318 Z"
    p = ['<ellipse cx="200" cy="366" rx="156" ry="12" fill="#2B2118" opacity=".16"/>',
         '<ellipse cx="200" cy="354" rx="146" ry="20" fill="#FFF6DA" stroke="#E6CF93" stroke-width="3"/>',
         '<ellipse cx="200" cy="350" rx="108" ry="12" fill="#F0DDA6" opacity=".7"/>']
    # Gagang Mug
    p.append(f'<path d="M284 222 Q350 222 344 272 Q338 316 274 312" fill="none" stroke="url(#mg{uid})" stroke-width="20" stroke-linecap="round"/>')
    p.append(f'<path d="{badan}" fill="url(#mg{uid})"/>')
    p.append(f'<clipPath id="k{uid}"><path d="{badan}"/></clipPath>')
    p.append(
        f'<g clip-path="url(#k{uid})"><rect x="100" y="262" width="200" height="32" fill="#2B2118"/>'
        '<text x="200" y="284" text-anchor="middle" font-family="Georgia,serif" font-weight="bold" '
        'font-size="17" letter-spacing="5" fill="#F6D95B">SHELTER</text></g>'
    )
    p.append('<path d="M128 214 L134 318" stroke="#fff" stroke-width="8" stroke-linecap="round" opacity=".7"/>')
    p.append('<ellipse cx="200" cy="196" rx="90" ry="19" fill="#FFF6DA" stroke="#E6CF93" stroke-width="3"/>')
    p.append(f'<ellipse cx="200" cy="198" rx="82" ry="14.5" fill="{permukaan}"/>')
    if len(sp["layers"]) > 1 and top not in ("foam", "art"):
        p.append(f'<ellipse cx="200" cy="199" rx="52" ry="8" fill="{sp["layers"][1][0]}" opacity=".75"/>')
    if top == "art":
        p.append('<ellipse cx="200" cy="198" rx="80" ry="13.5" fill="#F4E2C4"/>')
        p.append('<path d="M200 206 C186 190 170 200 180 208 C186 214 200 220 200 220 C200 220 214 214 220 208 C230 200 214 190 200 206 Z" fill="#8a5530"/>')
    if top == "foam":
        p.append('<ellipse cx="200" cy="197" rx="80" ry="13.5" fill="#FFF3DF"/>')
        for cx, cy, r in [(160, 197, 6), (180, 192, 7), (204, 196, 7), (228, 192, 7), (244, 198, 5)]:
            p.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFF9EE"/>')
    if top == "bubuk":
        for cx, cy in [(156, 198), (172, 192), (190, 200), (208, 194), (226, 200), (244, 196), (200, 206)]:
            p.append(f'<circle cx="{cx}" cy="{cy}" r="3.6" fill="#6b3f1d"/>')
    if ext == "ampas":
        for cx, cy in [(164, 200), (182, 194), (202, 203), (224, 196), (240, 201)]:
            p.append(f'<circle cx="{cx}" cy="{cy}" r="3.2" fill="#120a04"/>')
    # Uap panas
    for dx, ph in [(-34, 0), (6, 12), (44, 4)]:
        x = 200 + dx
        p.append(
            f'<path d="M{x} {170 - ph} Q{x - 20} {140 - ph} {x} {118 - ph} Q{x + 20} {96 - ph} {x} {70 - ph}" '
            'fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" opacity=".8"/>'
        )
    if top == "cream":
        p.append(_cream(200, 194, 62))
    if ext == "phin":
        p.append(_phin(196, uid))
    if ext == "v60":
        p.append(_v60(196, uid))
    if ext == "lemon":
        p.append(_lemon(296, 204, 30))
    isi = "".join(p)
    if skala != 1.0:
        tx, ty = 200 * (1 - skala), 366 * (1 - skala)
        return f'<g transform="translate({tx:.1f} {ty:.1f}) scale({skala})">{isi}</g>'
    return isi

def gambar_svg(nama, suhu="Dingin", kategori="Kopi Susu", lebar=None):
    sp = _spec(nama)
    uid = zlib.crc32(f"{nama}{suhu}".encode()) & 0xFFFFFF
    b1, b2 = LATAR_SVG.get(kategori, LATAR_SVG["Kopi Susu"])
    panas = suhu == "Panas"
    skala = 0.78 if (panas and "espresso" in str(nama).lower()) else 1.0
    glass = "M112 84 L288 84 L266 324 Q264 342 246 342 L154 342 Q136 342 134 324 Z"
    defs = (
        f'<linearGradient id="bg{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{b1}"/>'
        f'<stop offset="1" stop-color="{b2}"/></linearGradient>'
        f'<radialGradient id="gl{uid}" cx=".5" cy=".42" r=".6"><stop offset="0" stop-color="#fff" stop-opacity=".75"/>'
        f'<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="mt{uid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#9aa0a9"/>'
        f'<stop offset=".35" stop-color="#eceff3"/><stop offset=".7" stop-color="#c3c8d0"/>'
        f'<stop offset="1" stop-color="#8b919a"/></linearGradient>'
        f'<linearGradient id="mg{uid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FFFDF2"/>'
        f'<stop offset=".6" stop-color="#FBEFC4"/><stop offset="1" stop-color="#E8D08A"/></linearGradient>'
        f'{_grad_cair(uid, sp["layers"])}'
        f'<clipPath id="k{uid}"><path d="{glass}"/></clipPath>'
    )
    if panas:
        defs = defs.replace(f'<clipPath id="k{uid}"><path d="{glass}"/></clipPath>', "")
    isi = _bagian_panas(uid, sp, skala) if panas else _bagian_dingin(uid, sp)
    badge = "🔥 Panas" if panas else "❄️ Dingin"
    svg = (
        f'<svg viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg" style="display:block;width:100%;height:auto" '
        f'role="img" aria-label="{nama} {suhu}"><defs>{defs}</defs>'
        f'<rect width="400" height="400" fill="url(#bg{uid})"/>'
        f'<circle cx="200" cy="190" r="190" fill="url(#gl{uid})"/>'
        f'{_bunga(46, 52, 34, .55)}{_bunga(356, 340, 40, .45)}{_bunga(362, 62, 20, .5)}{_bunga(40, 350, 22, .4)}'
        f'{isi}'
        '</svg>'
    )
    maxw = f"max-width:{lebar}px;" if lebar else ""
    return f'<div class="foto" style="{maxw}">{svg}<span class="foto-tag">{badge}</span></div>'

def foto_html(m_nama, m_kategori, m_gambar, suhu, lebar=None):
    g = str(m_gambar or "").strip()
    if g.startswith("http") or g.startswith("data:image"):
        maxw = f"max-width:{lebar}px;" if lebar else ""
        badge = "🔥 Panas" if suhu == "Panas" else "❄️ Dingin"
        return f'<div class="foto" style="{maxw}"><img src="{g}" alt="{m_nama}"/><span class="foto-tag">{badge}</span></div>'
    return gambar_svg(m_nama, suhu, m_kategori, lebar)

# ======================================================================
# STRUK TEKS 34 KARAKTER & NOTIFIKASI NTFY.SH (FR-09, FR-10)
# ======================================================================
def buat_struk_teks(pesanan_dict, items_list):
    """
    Menghasilkan struk teks dengan lebar pas 34 karakter per baris.
    Sesuai standar printer thermal 58mm & kebutuhan FR-09.
    """
    W = 34
    garis_ganda = "=" * W
    garis_tunggal = "-" * W

    pid = int(pesanan_dict["id"])
    waktu = str(pesanan_dict["waktu"])
    pembeli = str(pesanan_dict["pembeli"])
    metode = str(pesanan_dict["metode"])
    total = int(pesanan_dict["total"])
    diterima = int(pesanan_dict.get("diterima", 0))
    kembalian = int(pesanan_dict.get("kembalian", 0))

    baris = [
        garis_ganda,
        "S H E L T E R   K O P I".center(W),
        f"@{HANDLE}".center(W),
        f"Buka {JAM_BUKA}".center(W),
        garis_tunggal,
        f"No    : #{pid:05d}",
        f"Waktu : {waktu}",
        f"Nama  : {pembeli[:25]}",
        f"Bayar : {metode[:25]}",
        garis_tunggal,
    ]

    # Baris rincian item (multi-item)
    for it in items_list:
        menu_nama = str(it["menu"])
        varian = str(it["ukuran"])
        qty = int(it["qty"])
        harga = int(it["harga"])
        subtotal = qty * harga

        nama_label = f"{menu_nama} ({varian})"
        if len(nama_label) > W:
            nama_label = nama_label[:W]
        baris.append(nama_label)

        # Baris rincian: " 2 x Rp 15.000          Rp 30.000"
        left_txt = f" {qty} x {rupiah(harga)}"
        right_txt = rupiah(subtotal)
        spasi = max(1, W - len(left_txt) - len(right_txt))
        baris.append(left_txt + (" " * spasi) + right_txt)

    baris.append(garis_tunggal)
    # Total
    tot_val = rupiah(total)
    baris.append("TOTAL".ljust(W - len(tot_val)) + tot_val)

    if metode.startswith("Tunai") and diterima > 0:
        dit_val = rupiah(diterima)
        kem_val = rupiah(kembalian)
        baris.append("DITERIMA".ljust(W - len(dit_val)) + dit_val)
        baris.append("KEMBALI".ljust(W - len(kem_val)) + kem_val)

    baris.extend([
        garis_tunggal,
        "Terima kasih sudah mampir di".center(W),
        "Shelter. Sampai jumpa lagi!".center(W),
        garis_ganda,
    ])

    return "\n".join(baris)

def tampil_struk_ui(pesanan_dict, items_list, key_suffix=""):
    teks_struk = buat_struk_teks(pesanan_dict, items_list)
    st.markdown(f'<div class="struk">{teks_struk}</div>', unsafe_allow_html=True)
    st.download_button(
        label="⬇️ Unduh Struk (.txt)",
        data=teks_struk,
        file_name=f"struk_{int(pesanan_dict['id']):05d}.txt",
        mime="text/plain",
        key=f"dl_struk_{key_suffix}_{pesanan_dict['id']}",
        use_container_width=True
    )

def kirim_notif(topik, judul, pesan):
    """
    Push notification ke HP lewat ntfy.sh (FR-10).
    Tahan kegagalan (NF-05): transaksi tidak batal jika ntfy gagal.
    """
    if not topik or not topik.strip():
        return False, "Topik ntfy kosong"
    judul_clean = judul.encode("ascii", "ignore").decode().strip() or "Shelter Kopi"
    try:
        r = requests.post(
            f"https://ntfy.sh/{topik.strip()}",
            data=pesan.encode("utf-8"),
            headers={
                "Title": judul_clean,
                "Priority": "high",
                "Tags": "coffee,moneybag"
            },
            timeout=5
        )
        if r.ok:
            return True, "Berhasil"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:45]}"

# ======================================================================
# LOGO & GENERATOR CSS DINAMIS (TERANG & GELAP)
# ======================================================================
LOGO_SVG_TERANG = (
    '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" class="logo-svg">'
    '<path d="M18 46 L50 14 L82 46 V90 H18 Z" fill="none" stroke="#2B2118" stroke-width="5.5" stroke-linejoin="round"/>'
    '<path d="M65 41 C65 27 35 27 35 42 C35 57 66 53 66 68 C66 82 37 82 37 68" fill="none" stroke="#2B2118" '
    'stroke-width="8" stroke-linecap="round"/></svg>'
)

LOGO_SVG_GELAP = (
    '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" class="logo-svg">'
    '<path d="M18 46 L50 14 L82 46 V90 H18 Z" fill="none" stroke="#E06D3E" stroke-width="5.5" stroke-linejoin="round"/>'
    '<path d="M65 41 C65 27 35 27 35 42 C35 57 66 53 66 68 C66 82 37 82 37 68" fill="none" stroke="#F5EBE6" '
    'stroke-width="8" stroke-linecap="round"/></svg>'
)

def get_css(tema):
    if tema == "☀️ Terang (Khas Shelter Kopi)":
        return """
<style>
@import url('https://fonts.googleapis.com/css2?family=Gloock&family=Quicksand:wght@500;600;700&display=swap');
:root {
  --kopi: #2B2118;
  --kuning: #F6D95B;
  --emas: #E8B923;
  --krem: #FFFBEA;
  --krem2: #FFF1B8;
  --bata: #D35400;
}
html, body, .stApp {
  font-family: 'Quicksand', system-ui, sans-serif !important;
  background-color: var(--krem) !important;
  color: var(--kopi) !important;
}
header[data-testid="stHeader"] {
  background-color: var(--krem) !important;
  background: var(--krem) !important;
}
.block-container {
  padding-top: 1.2rem;
  max-width: 1280px;
}
[data-testid="stSidebar"] {
  background-color: var(--krem2) !important;
  border-right: 3px solid var(--kopi) !important;
}
[data-testid="stSidebar"] * {
  color: var(--kopi) !important;
}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 {
  font-family: 'Gloock', Georgia, serif !important;
  color: var(--kopi) !important;
}
h1, h2, h3, h4 {
  font-family: 'Gloock', Georgia, serif !important;
  color: var(--kopi) !important;
  letter-spacing: .2px;
}
p, span, label, div, [data-testid="stMarkdownContainer"] p, [data-testid="stWidgetLabel"] label, [data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] span {
  color: var(--kopi) !important;
}
.stCaption, [data-testid="stCaptionContainer"] p {
  color: #6b5a45 !important;
}

/* Header */
.hero {
  border-radius: 22px;
  overflow: hidden;
  box-shadow: 0 12px 30px rgba(43,33,24,.22);
  margin-bottom: 22px;
  background: #fff;
  border: 2px solid var(--kopi);
}
.hero-bar {
  height: 28px;
  background: repeating-linear-gradient(90deg,#2B2118 0 7px,#3a2e22 7px 8px);
}
.hero-body {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 18px 24px;
  flex-wrap: wrap;
  background: radial-gradient(circle at 12% 20%,#FFF1B8 0,#F6D95B 70%);
}
.logo-svg { width: 74px; height: 74px; flex: none; }
.hero-tx { flex: 1; min-width: 220px; }
.brand {
  font-family: 'Gloock', serif;
  font-size: 2.4rem;
  letter-spacing: .4em;
  color: var(--kopi) !important;
  line-height: 1;
}
.handle { font-weight: 700; color: #5a4630 !important; margin-top: 4px; font-size: 1rem; }
.alamat { font-size: .82rem; color: #4a3a28 !important; margin-top: 4px; max-width: 600px; }
.buka {
  background: var(--kopi);
  color: var(--kuning) !important;
  padding: 8px 18px;
  border-radius: 999px;
  font-weight: 700;
  font-size: .92rem;
  white-space: nowrap;
  box-shadow: 0 4px 0 rgba(0,0,0,.18);
}
.buka * { color: var(--kuning) !important; }

/* Kartu & Container */
div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 18px !important;
  border-color: #EBD37A !important;
  background: #ffffff !important;
}
.nm {
  font-family: 'Gloock', serif;
  font-size: 1.15rem;
  color: var(--kopi) !important;
  margin: 2px 0 6px;
}
.chip {
  display: inline-block;
  background: var(--kuning);
  color: var(--kopi) !important;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: .8rem;
  font-weight: 700;
  margin-right: 5px;
  margin-bottom: 5px;
  border: 1.5px solid var(--kopi);
}
.chip-off {
  background: #eee;
  color: #9a9a9a !important;
  border-color: #ccc;
}
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700; }
.badge-ok { background: #dcfce7; color: #166534 !important; border: 1px solid #166534; }
.badge-low { background: #fef3c7; color: #92400e !important; border: 1px solid #B45309; }
.badge-out { background: #fee2e2; color: #991b1b !important; border: 1px solid #991B1B; }

/* Struk */
.struk {
  font-family: 'Courier New', Courier, monospace;
  white-space: pre;
  font-size: .85rem;
  background: #fffef7 !important;
  color: #222222 !important;
  padding: 16px 18px;
  border-radius: 8px;
  border: 1px dashed #bbb;
  box-shadow: 0 4px 14px rgba(0,0,0,.12);
  margin-bottom: 12px;
  overflow-x: auto;
  line-height: 1.35;
}
.struk * { color: #222222 !important; }

/* Metric KPI */
div[data-testid="stMetric"] {
  background: #fff !important;
  border: 2px solid var(--kopi) !important;
  border-radius: 16px !important;
  padding: 14px 18px;
  box-shadow: 5px 5px 0 var(--kuning) !important;
}

/* Tombol */
div.stButton > button[kind="primary"] {
  background: var(--kopi) !important;
  color: var(--kuning) !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 700 !important;
}
div.stButton > button[kind="primary"] * {
  color: var(--kuning) !important;
}
div.stButton > button[kind="primary"]:hover {
  background: #44342a !important;
  color: #fff !important;
}
div.stButton > button[kind="secondary"] {
  border: 2px solid var(--kopi) !important;
  border-radius: 12px !important;
  font-weight: 700 !important;
  color: var(--kopi) !important;
  background: #fff !important;
}
div.stButton > button[kind="secondary"]:hover {
  background: var(--kuning) !important;
}
div[data-testid="stDownloadButton"] > button {
  background: var(--kopi) !important;
  color: var(--kuning) !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 700 !important;
}
div[data-testid="stDownloadButton"] > button * {
  color: var(--kuning) !important;
}

/* Lembar Menu */
.lembar {
  background: #fff;
  border-radius: 22px;
  overflow: hidden;
  box-shadow: 0 12px 30px rgba(43,33,24,.2);
  border: 2px solid var(--kopi);
}
.lembar-top { height: 26px; background: repeating-linear-gradient(90deg,#2B2118 0 7px,#3a2e22 7px 8px); }
.lembar-in { padding: 22px 26px 28px; }
.ljudul { text-align: center; }
.ljudul .big { font-family: 'Gloock', serif; font-size: 3.8rem; line-height: 1; color: var(--kopi) !important; }
.ljudul .al { font-size: .82rem; color: #4a3a28 !important; margin-top: 4px; }
.lgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 18px; }
@media (max-width:800px){ .lgrid { grid-template-columns: 1fr; } .brand { font-size: 1.8rem; } .ljudul .big { font-size: 2.8rem; } }
.panel {
  background: var(--kuning);
  border-radius: 18px;
  padding: 14px;
  border: 2px solid var(--kopi);
}
.mhead {
  display: grid;
  grid-template-columns: 1fr 70px 70px;
  background: var(--kopi);
  color: var(--kuning) !important;
  font-family: 'Gloock', serif;
  border-radius: 999px;
  padding: 6px 16px;
  font-size: .95rem;
  margin-bottom: 8px;
}
.mhead span { color: var(--kuning) !important; }
.mhead span:nth-child(n+2) { text-align: center; }
.mrow {
  display: grid;
  grid-template-columns: 1fr 70px 70px;
  padding: 5px 16px;
  font-weight: 600;
  color: var(--kopi) !important;
  border-bottom: 1px dashed rgba(43,33,24,.25);
}
.mrow:last-child { border-bottom: none; }
.mrow span { color: var(--kopi) !important; }
.mrow span:nth-child(n+2) { text-align: center; font-weight: 700; color: var(--kopi) !important; }
.lpill {
  display: inline-block;
  border: 2px solid var(--kopi);
  background: #fff;
  border-radius: 999px;
  padding: 4px 18px;
  font-family: 'Gloock', serif;
  font-size: .9rem;
  color: var(--kopi) !important;
  margin-bottom: 8px;
}
.lfoot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 20px;
  padding-top: 12px;
  border-top: 1px solid rgba(43,33,24,.2);
}
.lfoot .ig { border: 2px solid var(--kopi); border-radius: 999px; padding: 5px 22px; font-family: 'Gloock', serif; color: var(--kopi) !important; }
</style>
"""
    else:  # Mode Gelap Arang (Night Kedai)
        return """
<style>
@import url('https://fonts.googleapis.com/css2?family=Gloock&family=Quicksand:wght@500;600;700&display=swap');
:root {
  --arang-bg: #18181D;
  --arang-card: #22222A;
  --arang-card-alt: #2A2A34;
  --bata: #D35400;
  --bata-terang: #E06D3E;
  --emas-kopi: #E5A93C;
  --krem-teks: #F5EBE6;
  --abu-muted: #A39B95;
  --garis-border: #383846;
}
html, body, .stApp {
  font-family: 'Quicksand', system-ui, sans-serif !important;
  background-color: var(--arang-bg) !important;
  color: var(--krem-teks) !important;
}
header[data-testid="stHeader"] {
  background-color: var(--arang-bg) !important;
  background: var(--arang-bg) !important;
}
.block-container {
  padding-top: 1.2rem;
  max-width: 1280px;
}
[data-testid="stSidebar"] {
  background-color: var(--arang-card) !important;
  border-right: 2px solid var(--garis-border) !important;
}
/* Paksa SEMUA teks di sidebar jadi terang */
[data-testid="stSidebar"] * {
  color: var(--krem-teks) !important;
}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 {
  font-family: 'Gloock', Georgia, serif !important;
  color: var(--bata-terang) !important;
}
h1, h2, h3, h4 {
  font-family: 'Gloock', Georgia, serif !important;
  color: var(--krem-teks) !important;
  letter-spacing: .5px;
}
p, span, label, div, [data-testid="stMarkdownContainer"] p, [data-testid="stWidgetLabel"] label, [data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] span {
  color: var(--krem-teks) !important;
}
[data-testid="stRadio"] label p, [data-testid="stRadio"] div[role="radiogroup"] label span {
  color: var(--krem-teks) !important;
}
.stCaption, [data-testid="stCaptionContainer"] p {
  color: var(--abu-muted) !important;
}
input, textarea {
  color: #FFFFFF !important;
  background-color: var(--arang-card-alt) !important;
}

/* Header */
.hero {
  border-radius: 18px;
  overflow: hidden;
  box-shadow: 0 10px 25px rgba(0,0,0,.45);
  margin-bottom: 22px;
  background: var(--arang-card);
  border: 1px solid var(--garis-border);
}
.hero-bar {
  height: 6px;
  background: linear-gradient(90deg, var(--bata) 0%, var(--bata-terang) 50%, var(--emas-kopi) 100%);
}
.hero-body {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 18px 24px;
  flex-wrap: wrap;
}
.logo-svg { width: 72px; height: 72px; flex: none; }
.hero-tx { flex: 1; min-width: 220px; }
.brand {
  font-family: 'Gloock', serif;
  font-size: 2.3rem;
  letter-spacing: .35em;
  color: var(--bata-terang) !important;
  line-height: 1.1;
}
.handle { font-weight: 700; color: var(--emas-kopi) !important; font-size: 1rem; margin-top: 3px; }
.alamat { font-size: .82rem; color: var(--abu-muted) !important; margin-top: 4px; max-width: 600px; }
.buka {
  background: var(--arang-card-alt);
  color: var(--emas-kopi) !important;
  padding: 8px 18px;
  border-radius: 999px;
  font-weight: 700;
  font-size: .92rem;
  white-space: nowrap;
  border: 1px solid var(--bata);
}
.buka * { color: var(--emas-kopi) !important; }

/* Kartu & Container */
div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 16px !important;
  border-color: var(--garis-border) !important;
  background: var(--arang-card) !important;
}
.nm {
  font-family: 'Gloock', serif;
  font-size: 1.15rem;
  color: var(--krem-teks) !important;
  margin: 2px 0 6px;
}
.chip {
  display: inline-block;
  background: var(--arang-card-alt);
  color: var(--emas-kopi) !important;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: .8rem;
  font-weight: 700;
  margin-right: 5px;
  margin-bottom: 5px;
  border: 1px solid var(--garis-border);
}
.chip-off { background: #252528; color: #6d6761 !important; border-color: #333339; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700; }
.badge-ok { background: #1B3828; color: #4ADE80 !important; border: 1px solid #166534; }
.badge-low { background: #3D2E16; color: #FBBF24 !important; border: 1px solid #B45309; }
.badge-out { background: #3D1A1A; color: #F87171 !important; border: 1px solid #991B1B; }

/* Struk */
.struk {
  font-family: 'Courier New', Courier, monospace;
  white-space: pre;
  font-size: .85rem;
  background: #151518 !important;
  color: #E6E1DC !important;
  padding: 16px 18px;
  border-radius: 8px;
  border: 1px dashed #5A544F;
  box-shadow: 0 4px 14px rgba(0,0,0,.5);
  margin-bottom: 12px;
  overflow-x: auto;
  line-height: 1.35;
}
.struk * { color: #E6E1DC !important; }

/* Metric KPI */
div[data-testid="stMetric"] {
  background: var(--arang-card) !important;
  border: 1px solid var(--garis-border) !important;
  border-radius: 14px !important;
  padding: 14px 18px;
  box-shadow: 0 4px 12px rgba(0,0,0,.25) !important;
  border-left: 4px solid var(--bata) !important;
}
div[data-testid="stMetric"] * { color: var(--krem-teks) !important; }

/* Tombol */
div.stButton > button[kind="primary"] {
  background: var(--bata) !important;
  color: #fff !important;
  border: none !important;
  border-radius: 10px !important;
  font-weight: 700 !important;
}
div.stButton > button[kind="primary"] * { color: #fff !important; }
div.stButton > button[kind="primary"]:hover {
  background: var(--bata-terang) !important;
}
div.stButton > button[kind="secondary"] {
  background: var(--arang-card-alt) !important;
  border: 1px solid var(--garis-border) !important;
  border-radius: 10px !important;
  font-weight: 600 !important;
  color: var(--krem-teks) !important;
}
div.stButton > button[kind="secondary"]:hover {
  border-color: var(--bata) !important;
  color: var(--bata-terang) !important;
}
div[data-testid="stDownloadButton"] > button {
  background: var(--bata) !important;
  color: #fff !important;
  border: none !important;
  border-radius: 10px !important;
  font-weight: 700 !important;
}
div[data-testid="stDownloadButton"] > button * { color: #fff !important; }

/* Lembar Menu */
.lembar {
  background: var(--arang-card);
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 12px 30px rgba(0,0,0,.45);
  border: 1px solid var(--garis-border);
}
.lembar-top { height: 12px; background: repeating-linear-gradient(90deg, var(--bata) 0 10px, var(--bata-terang) 10px 20px); }
.lembar-in { padding: 24px 28px; }
.ljudul { text-align: center; margin-bottom: 20px; }
.ljudul .big { font-family: 'Gloock', serif; font-size: 3.6rem; color: var(--bata-terang) !important; letter-spacing: .2em; line-height: 1; }
.ljudul .al { font-size: .85rem; color: var(--abu-muted) !important; margin-top: 6px; }
.lgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 800px) { .lgrid { grid-template-columns: 1fr; } .brand { font-size: 1.8rem; } .ljudul .big { font-size: 2.8rem; } }
.panel {
  background: var(--arang-card-alt);
  border-radius: 16px;
  padding: 16px;
  border: 1px solid var(--garis-border);
}
.mhead {
  display: grid;
  grid-template-columns: 1fr 70px 70px;
  background: var(--bata);
  color: #fff !important;
  font-family: 'Gloock', serif;
  border-radius: 999px;
  padding: 6px 16px;
  font-size: .95rem;
  margin-bottom: 8px;
}
.mhead span { color: #fff !important; }
.mhead span:nth-child(n+2) { text-align: center; }
.mrow {
  display: grid;
  grid-template-columns: 1fr 70px 70px;
  padding: 6px 16px;
  font-weight: 600;
  color: var(--krem-teks) !important;
  border-bottom: 1px dashed var(--garis-border);
}
.mrow:last-child { border-bottom: none; }
.mrow span { color: var(--krem-teks) !important; }
.mrow span:nth-child(n+2) { text-align: center; font-weight: 700; color: var(--emas-kopi) !important; }
.lpill {
  display: inline-block;
  background: var(--arang-card);
  border: 1px solid var(--bata);
  border-radius: 999px;
  padding: 4px 16px;
  color: var(--emas-kopi) !important;
  font-size: .85rem;
  font-weight: 700;
  margin-bottom: 10px;
}
.lfoot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 24px;
  padding-top: 14px;
  border-top: 1px solid var(--garis-border);
}
.lfoot .ig { font-family: 'Gloock', serif; color: var(--emas-kopi) !important; font-size: 1.1rem; }
</style>
"""

# ======================================================================
# IN-MEMORY SESSION STATE: KERANJANG, AUTENTIKASI & TEMA
# ======================================================================
if "keranjang" not in st.session_state:
    st.session_state.keranjang = []  # list of dict: {menu_id, nama, kategori, ukuran, harga, qty}
if "kasir_auth" not in st.session_state:
    st.session_state.kasir_auth = False
if "owner_auth" not in st.session_state:
    st.session_state.owner_auth = False
if "uang_diterima" not in st.session_state:
    st.session_state.uang_diterima = 0
if "terakhir_pesanan_id" not in st.session_state:
    st.session_state.terakhir_pesanan_id = None
if "pembeli_input" not in st.session_state:
    st.session_state.pembeli_input = ""

# ======================================================================
# SIDEBAR: PILIHAN TEMA, AUTENTIKASI & NAVIGASI PERAN (FR-01, NF-04)
# ======================================================================
st.sidebar.markdown("### 🎨 Tema Tampilan")
tema_aktif = st.sidebar.radio(
    "Pilihan Tema:",
    ["☀️ Terang (Khas Shelter Kopi)", "🌙 Gelap (Malam Kedai)"],
    index=0,
    key="pilih_tema_app",
    label_visibility="collapsed"
)

# Terapkan CSS sesuai tema
st.markdown(get_css(tema_aktif), unsafe_allow_html=True)

# Tampilkan Banner Header Kedai Sesuai Tema
logo_pilih = LOGO_SVG_TERANG if tema_aktif == "☀️ Terang (Khas Shelter Kopi)" else LOGO_SVG_GELAP
st.markdown(
    f"""
    <div class="hero">
        <div class="hero-bar"></div>
        <div class="hero-body">
            {logo_pilih}
            <div class="hero-tx">
                <div class="brand">SHELTER</div>
                <div class="handle">@{HANDLE} · Kasir &amp; Menu Digital</div>
                <div class="alamat">{ALAMAT}</div>
            </div>
            <div class="buka">☕ Buka {JAM_BUKA} WIB</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

st.sidebar.markdown("---")
st.sidebar.markdown("### 👤 Akses Pengguna")
peran = st.sidebar.radio("Pilih Peran:", ["👨‍🍳 Kasir / Barista", "👑 Pemilik Kedai (Owner)"])

# Logika Autentikasi Kasir
if peran == "👨‍🍳 Kasir / Barista":
    if not st.session_state.kasir_auth:
        st.sidebar.markdown("---")
        st.sidebar.markdown("#### 🔐 Verifikasi PIN Kasir")
        pin_k = st.sidebar.text_input("Masukkan PIN Kasir:", type="password", key="pin_kasir_input")
        if st.sidebar.button("Masuk Kasir", type="primary", use_container_width=True):
            if pin_k == KASIR_PIN:
                st.session_state.kasir_auth = True
                st.sidebar.success("PIN benar ✅")
                st.rerun()
            else:
                st.sidebar.error("❌ PIN Kasir salah!")
        st.sidebar.caption("💡 *PIN default: 1234 (dapat diubah via env KASIR_PIN)*")
    else:
        st.sidebar.markdown("✅ **Status: Terautentikasi sebagai Kasir**")
        halaman_kasir = st.sidebar.selectbox(
            "Pilih Menu Kasir:",
            ["🛒 Kasir & POS", "📋 Menu Digital", "📦 Kelola Menu & Stok"]
        )
        if st.sidebar.button("🔒 Keluar Kasir", use_container_width=True):
            st.session_state.kasir_auth = False
            st.rerun()

# Logika Autentikasi Pemilik (Owner)
else:
    if not st.session_state.owner_auth:
        st.sidebar.markdown("---")
        st.sidebar.markdown("#### 🔐 Verifikasi PIN Pemilik")
        pin_o = st.sidebar.text_input("Masukkan PIN Pemilik:", type="password", key="pin_owner_input")
        if st.sidebar.button("Masuk Pemilik", type="primary", use_container_width=True):
            if pin_o == OWNER_PIN:
                st.session_state.owner_auth = True
                st.sidebar.success("PIN benar ✅")
                st.rerun()
            else:
                st.sidebar.error("❌ PIN Pemilik salah!")
        st.sidebar.caption("💡 *PIN default: 8888 (dapat diubah via env OWNER_PIN)*")
    else:
        st.sidebar.markdown("👑 **Status: Terautentikasi sebagai Pemilik**")
        halaman_owner = st.sidebar.selectbox(
            "Pilih Menu Pemilik:",
            [
                "📊 Laporan Omset & Riwayat",
                "⚙️ Pengaturan & QRIS",
                "🛒 Tampilan Kasir & POS",
                "📋 Tampilan Menu Digital",
                "📦 Kelola Menu & Stok"
            ]
        )
        if st.sidebar.button("🔒 Keluar Pemilik", use_container_width=True):
            st.session_state.owner_auth = False
            st.rerun()

# Sidebar: Notifikasi Cepat
st.sidebar.markdown("---")
st.sidebar.markdown("#### 🔔 Info Notifikasi HP (ntfy.sh)")
curr_topic = get_setting("ntfy_topic", DEFAULT_TOPIC)
st.sidebar.caption(f"Topik aktif: `{curr_topic}`")
if st.sidebar.button("Kirim Notifikasi Tes", key="sb_tes_notif", use_container_width=True):
    ok, pesan = kirim_notif(curr_topic, "Tes Shelter Kopi", "Koneksi notifikasi dari kasir berhasil! ☕🎉")
    if ok:
        st.sidebar.success("Notifikasi terkirim ✅")
    else:
        st.sidebar.error(f"Gagal kirim: {pesan}")

# Footer Akademik Tim (PRD)
st.sidebar.markdown("---")
st.sidebar.caption(
    "**shelterkopi_ v1.0** · 28 Okt 2026\n"
    "Tim Pengembang:\n"
    "- Widodo Hadi Siswoyo (240121035)\n"
    "- Gilang (240121034)\n"
    "- Dwi Hadi Wira Utomo (240121113)\n"
    "*STMIK Pelita Nusantara (IF518)*"
)

# ======================================================================
# FUNGSI LOGIKA KERANJANG MULTI-ITEM (FR-03, FR-04, FR-08)
# ======================================================================
def hitung_stok_di_keranjang(menu_id):
    """Menghitung total porsi suatu menu yang sedang dipesan di keranjang."""
    return sum(item["qty"] for item in st.session_state.keranjang if item["menu_id"] == menu_id)

def tambah_ke_keranjang(menu_id, nama, kategori, ukuran, harga, qty):
    """
    Menambahkan pesanan ke keranjang.
    Jika item dengan menu dan varian sama sudah ada di keranjang, jumlahnya digabung (FR-03).
    """
    for item in st.session_state.keranjang:
        if item["menu_id"] == menu_id and item["ukuran"] == ukuran:
            item["qty"] += qty
            return
    st.session_state.keranjang.append({
        "menu_id": menu_id,
        "nama": nama,
        "kategori": kategori,
        "ukuran": ukuran,
        "harga": harga,
        "qty": qty
    })

def hapus_item_keranjang(index):
    if 0 <= index < len(st.session_state.keranjang):
        st.session_state.keranjang.pop(index)

def sesuaikan_qty_keranjang(index, delta, max_tersedia):
    if 0 <= index < len(st.session_state.keranjang):
        baru = st.session_state.keranjang[index]["qty"] + delta
        if baru <= 0:
            st.session_state.keranjang.pop(index)
        elif baru <= max_tersedia:
            st.session_state.keranjang[index]["qty"] = baru

def total_belanja_keranjang():
    return sum(it["qty"] * it["harga"] for it in st.session_state.keranjang)

def total_gelas_keranjang():
    return sum(it["qty"] for it in st.session_state.keranjang)

def set_nominal_uang(val):
    st.session_state.uang_diterima = int(val)

# ======================================================================
# MODUL 1: KASIR & POS (FR-02 s/d FR-09)
# ======================================================================
def modul_pos():
    st.subheader("🛒 Kasir & Pemesanan Multi-Item")

    df_menu = muat_menu_df()
    if df_menu.empty:
        st.warning("Belum ada menu terdaftar. Silakan tambahkan menu di menu 'Kelola Menu'.")
        return

    kol_katalog, kol_keranjang = st.columns([3, 2], gap="large")

    # ------------------------------------------------------------------
    # KOLOM KIRI: KATALOG MENU (FR-02, FR-03)
    # ------------------------------------------------------------------
    with kol_katalog:
        st.markdown("#### 📋 Katalog Menu")
        list_kat = ["Semua"] + [k for k in KATEGORI if k in df_menu["kategori"].values]
        extra_kats = [k for k in df_menu["kategori"].unique() if k not in list_kat and k != "Semua"]
        pilihan_kategori = st.radio("Kategori Menu:", list_kat + extra_kats, horizontal=True)

        if pilihan_kategori == "Semua":
            df_tampil = df_menu
        else:
            df_tampil = df_menu[df_menu["kategori"] == pilihan_kategori]

        grid_kol = 3
        rows = list(df_tampil.itertuples())

        for i in range(0, len(rows), grid_kol):
            cols = st.columns(grid_kol)
            for col, m in zip(cols, rows[i : i + grid_kol]):
                with col:
                    with st.container(border=True):
                        # Tentukan varian utama untuk preview foto
                        preview_suhu = "Dingin" if m.harga_dingin > 0 else "Panas"
                        st.markdown(foto_html(m.nama, m.kategori, m.gambar, preview_suhu), unsafe_allow_html=True)
                        st.markdown(f'<div class="nm">{m.nama}</div>', unsafe_allow_html=True)

                        # Chip harga Panas / Dingin
                        chips = []
                        if m.harga_panas > 0:
                            chips.append(f'<span class="chip">🔥 {rb(m.harga_panas)}</span>')
                        else:
                            chips.append('<span class="chip chip-off">🔥 -</span>')
                        if m.harga_dingin > 0:
                            chips.append(f'<span class="chip">❄️ {rb(m.harga_dingin)}</span>')
                        else:
                            chips.append('<span class="chip chip-off">❄️ -</span>')
                        st.markdown("".join(chips), unsafe_allow_html=True)

                        # Lencana Stok & Peringatan Stok (FR-02)
                        stok_dipesan = hitung_stok_di_keranjang(m.id)
                        sisa_stok = max(0, m.stok - stok_dipesan)

                        if m.stok <= 0:
                            badge = '<span class="badge badge-out">Habis</span>'
                        elif sisa_stok <= 0:
                            badge = f'<span class="badge badge-low">Di keranjang ({stok_dipesan})</span>'
                        elif sisa_stok <= 5:
                            badge = f'<span class="badge badge-low">Sisa {sisa_stok}</span>'
                        else:
                            badge = f'<span class="badge badge-ok">Stok {sisa_stok}</span>'
                        st.markdown(badge, unsafe_allow_html=True)

                        # Opsi Varian & Jumlah yang valid (FR-03)
                        varian_tersedia = []
                        if m.harga_panas > 0:
                            varian_tersedia.append("Panas")
                        if m.harga_dingin > 0:
                            varian_tersedia.append("Dingin")

                        if not varian_tersedia:
                            st.caption("Menu belum memiliki harga")
                            continue

                        # Pemilihan Varian (Radio jika > 1 varian)
                        varian_pilih = varian_tersedia[0]
                        if len(varian_tersedia) > 1:
                            varian_pilih = st.radio(
                                "Varian",
                                varian_tersedia,
                                key=f"var_{m.id}",
                                horizontal=True,
                                format_func=lambda v: "🔥 Panas" if v == "Panas" else "❄️ Dingin",
                                label_visibility="collapsed"
                            )

                        harga_terpilih = m.harga_panas if varian_pilih == "Panas" else m.harga_dingin

                        # Input jumlah (max sisa stok setelah dikurangi isi keranjang)
                        bisa_pesan = sisa_stok > 0
                        qty_input = st.number_input(
                            "Jumlah",
                            min_value=1,
                            max_value=max(1, sisa_stok),
                            value=1,
                            step=1,
                            key=f"qty_in_{m.id}",
                            disabled=not bisa_pesan,
                            label_visibility="collapsed"
                        )

                        # Tombol Tambah ke Keranjang
                        if st.button(
                            "➕ Tambah" if bisa_pesan else "❌ Habis",
                            key=f"btn_add_{m.id}",
                            type="primary" if bisa_pesan else "secondary",
                            use_container_width=True,
                            disabled=not bisa_pesan
                        ):
                            tambah_ke_keranjang(
                                menu_id=m.id,
                                nama=m.nama,
                                kategori=m.kategori,
                                ukuran=varian_pilih,
                                harga=harga_terpilih,
                                qty=int(qty_input)
                            )
                            st.toast(f"✅ Ditambah: {qty_input}x {m.nama} ({varian_pilih})", icon="🛒")
                            st.rerun()

    # ------------------------------------------------------------------
    # KOLOM KANAN: KERANJANG PESANAN & CHECKOUT (FR-04 s/d FR-09)
    # ------------------------------------------------------------------
    with kol_keranjang:
        with st.container(border=True):
            st.markdown(f"### 🛒 Keranjang Pesanan ({len(st.session_state.keranjang)} item)")

            if not st.session_state.keranjang:
                st.info("Keranjang masih kosong. Pilih menu di sebelah kiri untuk menambahkan pesanan multi-item.")
            else:
                # Tampilkan daftar item di keranjang (FR-04)
                for idx, it in enumerate(list(st.session_state.keranjang)):
                    subtotal = it["qty"] * it["harga"]
                    ikon_suhu = "🔥" if it["ukuran"] == "Panas" else "❄️"
                    c_info, c_qty, c_sub, c_del = st.columns([3, 2, 2, 1])
                    with c_info:
                        st.markdown(f"**{it['nama']}**")
                        st.caption(f"{ikon_suhu} {it['ukuran']} @ {rupiah(it['harga'])}")
                    with c_qty:
                        q_min, q_val, q_plus = st.columns([1, 1, 1])
                        if q_min.button("➖", key=f"q_min_{idx}"):
                            # Cek sisa stok database
                            row_m = df_menu[df_menu["id"] == it["menu_id"]].iloc[0]
                            sesuaikan_qty_keranjang(idx, -1, row_m["stok"])
                            st.rerun()
                        q_val.markdown(f"<div style='text-align:center;font-weight:700;padding-top:6px'>{it['qty']}</div>", unsafe_allow_html=True)
                        if q_plus.button("➕", key=f"q_plus_{idx}"):
                            row_m = df_menu[df_menu["id"] == it["menu_id"]].iloc[0]
                            stok_sisa_db = row_m["stok"] - hitung_stok_di_keranjang(it["menu_id"])
                            if stok_sisa_db > 0:
                                sesuaikan_qty_keranjang(idx, +1, row_m["stok"])
                                st.rerun()
                            else:
                                st.warning("Stok menu sudah habis!")
                    with c_sub:
                        st.markdown(f"<div style='text-align:right;font-weight:700;padding-top:6px'>{rupiah(subtotal)}</div>", unsafe_allow_html=True)
                    with c_del:
                        if st.button("🗑️", key=f"del_cart_{idx}"):
                            hapus_item_keranjang(idx)
                            st.rerun()
                    st.markdown("<hr style='margin:4px 0;opacity:0.25;'>", unsafe_allow_html=True)

                if st.button("Kosongkan Keranjang", type="secondary", use_container_width=True):
                    st.session_state.keranjang = []
                    st.session_state.uang_diterima = 0
                    st.rerun()

            total_bayar = total_belanja_keranjang()
            total_gelas = total_gelas_keranjang()

            warna_box_bg = "#FFF1B8" if tema_aktif == "☀️ Terang (Khas Shelter Kopi)" else "#1C1512"
            warna_box_border = "#2B2118" if tema_aktif == "☀️ Terang (Khas Shelter Kopi)" else "#D35400"
            warna_box_total = "#2B2118" if tema_aktif == "☀️ Terang (Khas Shelter Kopi)" else "#E06D3E"

            st.markdown(
                f"""
                <div style='background:{warna_box_bg};border:2px solid {warna_box_border};border-radius:12px;padding:12px 16px;margin:14px 0;'>
                    <div style='display:flex;justify-content:space-between;font-size:.9rem;'>
                        <span>Total Gelas: <b>{total_gelas} porsi</b></span>
                        <span>Multi-Item Checkout</span>
                    </div>
                    <div style='display:flex;justify-content:space-between;align-items:center;margin-top:6px;'>
                        <span style='font-size:1.1rem;font-weight:700;'>TOTAL BAYAR:</span>
                        <span style='font-size:1.6rem;font-weight:700;color:{warna_box_total};'>{rupiah(total_bayar)}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            # Form Data Checkout & Pembayaran (FR-05, FR-06, FR-07)
            st.markdown("#### 💳 Pembayaran & Checkout")

            # Input nama pembeli / no meja wajib diisi (FR-05)
            nama_pembeli = st.text_input(
                "Nama Pembeli / No. Meja *",
                value=st.session_state.pembeli_input,
                key="input_nama_pembeli",
                placeholder="Contoh: Mas Widodo / Meja 04"
            )

            metode_bayar = st.selectbox(
                "Metode Pembayaran",
                ["Tunai (Cash)", "QRIS"],
                key="metode_bayar_pos"
            )

            kembalian = 0
            if metode_bayar == "Tunai (Cash)":
                st.markdown("##### 💵 Input Tunai")
                st.number_input(
                    "Uang Diterima (Rp)",
                    min_value=0,
                    step=1000,
                    value=st.session_state.uang_diterima,
                    key="uang_diterima_input",
                    on_change=lambda: set_nominal_uang(st.session_state.uang_diterima_input)
                )

                # Tombol Cepat Uang Diterima (FR-06)
                b1, b2, b3, b4 = st.columns(4)
                if b1.button("Pas", use_container_width=True):
                    set_nominal_uang(total_bayar)
                    st.rerun()
                if b2.button("20rb", use_container_width=True):
                    set_nominal_uang(20000)
                    st.rerun()
                if b3.button("50rb", use_container_width=True):
                    set_nominal_uang(50000)
                    st.rerun()
                if b4.button("100rb", use_container_width=True):
                    set_nominal_uang(100000)
                    st.rerun()

                uang_now = st.session_state.uang_diterima
                if total_bayar > 0:
                    if uang_now >= total_bayar:
                        kembalian = uang_now - total_bayar
                        st.success(f"Kembalian: **{rupiah(kembalian)}** 💵")
                    elif uang_now > 0:
                        st.warning(f"⚠️ Uang diterima kurang {rupiah(total_bayar - uang_now)}")
            else:
                # Pembayaran QRIS (FR-07)
                st.markdown("##### 📱 Pembayaran QRIS")
                qris_img = get_setting("qris_image", "")
                if qris_img:
                    st.image(qris_img, caption="Scan QRIS Kedai untuk pembayaran pas", use_container_width=True)
                else:
                    st.warning("⚠️ Gambar QRIS kedai belum diunggah oleh Pemilik. (Dapat diunggah di menu Pengaturan & QRIS).")
                st.info(f"Pembayaran QRIS dicatat PAS: **{rupiah(total_bayar)}**")
                set_nominal_uang(total_bayar)
                kembalian = 0

            # Tombol Proses Pembayaran (FR-08)
            tombol_bayar = st.button(
                "🚀 Proses Pembayaran & Simpan Transaksi",
                type="primary",
                use_container_width=True,
                disabled=len(st.session_state.keranjang) == 0
            )

            if tombol_bayar:
                # Validasi FR-05 & FR-06
                nama_bersih = nama_pembeli.strip()
                if not nama_bersih:
                    st.error("❌ Nama Pembeli atau Nomor Meja wajib diisi sebelum pembayaran!")
                    return

                if len(st.session_state.keranjang) == 0:
                    st.error("❌ Keranjang masih kosong!")
                    return

                if metode_bayar == "Tunai (Cash)" and st.session_state.uang_diterima < total_bayar:
                    kekurangan = total_bayar - st.session_state.uang_diterima
                    st.error(f"❌ Pembayaran tunai ditolak: Uang kurang {rupiah(kekurangan)}!")
                    return

                # Eksekusi Transaksi Atomik (FR-08)
                conn = get_db()
                try:
                    cur = conn.cursor()
                    # 1. Validasi ulang stok seluruh item di database
                    rekap_kebutuhan_stok = {}
                    for it in st.session_state.keranjang:
                        rekap_kebutuhan_stok[it["menu_id"]] = rekap_kebutuhan_stok.get(it["menu_id"], 0) + it["qty"]

                    for mid, butuh_qty in rekap_kebutuhan_stok.items():
                        cur.execute("SELECT nama, stok FROM menu WHERE id = ?", (mid,))
                        row_stok = cur.fetchone()
                        if not row_stok:
                            st.error(f"❌ Menu ID {mid} tidak ditemukan di database!")
                            conn.rollback()
                            conn.close()
                            return
                        if row_stok["stok"] < butuh_qty:
                            st.error(f"❌ Stok '{row_stok['nama']}' tidak cukup! Tersisa {row_stok['stok']}, diminta {butuh_qty}.")
                            conn.rollback()
                            conn.close()
                            return

                    # 2. Kurangi stok tiap menu secara atomik (FR-08)
                    for mid, butuh_qty in rekap_kebutuhan_stok.items():
                        cur.execute("UPDATE menu SET stok = stok - ? WHERE id = ?", (butuh_qty, mid))

                    # 3. Simpan satu transaksi pesanan (FR-08)
                    waktu_skrg = now_wib()
                    uang_final = st.session_state.uang_diterima if metode_bayar == "Tunai (Cash)" else total_bayar
                    cur.execute(
                        """INSERT INTO pesanan (pembeli, waktu, metode, total, diterima, kembalian)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (nama_bersih, waktu_skrg, metode_bayar, total_bayar, uang_final, kembalian)
                    )
                    pesanan_id = cur.lastrowid

                    # 4. Simpan seluruh item ke tabel item (FR-08)
                    for it in st.session_state.keranjang:
                        cur.execute(
                            """INSERT INTO item (pesanan_id, menu, ukuran, qty, harga)
                               VALUES (?, ?, ?, ?, ?)""",
                            (pesanan_id, it["nama"], it["ukuran"], it["qty"], it["harga"])
                        )

                    conn.commit()
                    conn.close()

                    # 5. Notifikasi ntfy.sh (FR-10, NF-05)
                    rincian_notif = []
                    for it in st.session_state.keranjang:
                        rincian_notif.append(f"• {it['qty']}x {it['nama']} ({it['ukuran']}) - {rupiah(it['qty'] * it['harga'])}")
                    teks_notif = (
                        f"☕ Pesanan Baru #{pesanan_id:05d}\n"
                        f"Nama: {nama_bersih}\n"
                        f"------------------------\n"
                        + "\n".join(rincian_notif)
                        + f"\n------------------------\n"
                        f"Total: {rupiah(total_bayar)} ({metode_bayar})"
                    )
                    topik = get_setting("ntfy_topic", DEFAULT_TOPIC)
                    ok_notif, pesan_notif = kirim_notif(topik, f"Pesanan Baru #{pesanan_id:05d}", teks_notif)

                    # Update session state & tampilkan struk
                    st.session_state.terakhir_pesanan_id = pesanan_id
                    st.session_state.keranjang = []
                    st.session_state.uang_diterima = 0
                    st.session_state.pembeli_input = ""

                    sukses_msg = f"🎉 Pesanan #{pesanan_id:05d} atas nama '{nama_bersih}' berhasil disimpan!"
                    if metode_bayar == "Tunai (Cash)":
                        sukses_msg += f" (Kembalian: {rupiah(kembalian)})"
                    if ok_notif:
                        sukses_msg += " 📲 Notifikasi HP terkirim."
                    else:
                        sukses_msg += f" ⚠️ (Notifikasi ntfy gagal: {pesan_notif})"

                    st.success(sukses_msg)
                    st.rerun()

                except Exception as e:
                    conn.rollback()
                    conn.close()
                    st.error(f"❌ Gagal menyimpan transaksi: {str(e)}")

        # Tampilkan Struk Pesanan Terakhir Jika Ada (FR-09)
        if st.session_state.terakhir_pesanan_id:
            st.markdown("---")
            st.markdown("#### 🧾 Struk Transaksi Terakhir")
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM pesanan WHERE id = ?", (st.session_state.terakhir_pesanan_id,))
            p_row = c.fetchone()
            if p_row:
                c.execute("SELECT * FROM item WHERE pesanan_id = ?", (st.session_state.terakhir_pesanan_id,))
                it_rows = c.fetchall()
                conn.close()
                tampil_struk_ui(dict(p_row), [dict(r) for r in it_rows], key_suffix="pos")
            else:
                conn.close()

# ======================================================================
# MODUL 2: MENU DIGITAL (FR-11)
# ======================================================================
def modul_menu_digital():
    st.subheader("📋 Menu Digital Shelter Kopi")
    df_menu = muat_menu_df()

    tab_lembar, tab_galeri = st.tabs(["📋 Lembar Menu Cetak", "🖼️ Galeri Foto Minuman"])

    # Tab 1: Replika Lembar Menu Cetak (FR-11)
    with tab_lembar:
        def render_panel_kategori(df, kat_judul):
            sub = df[df["kategori"] == kat_judul]
            baris_html = []
            for r in sub.itertuples():
                baris_html.append(
                    f'<div class="mrow">'
                    f'<span>• {r.nama}</span>'
                    f'<span>{angka_menu(r.harga_panas)}</span>'
                    f'<span>{angka_menu(r.harga_dingin)}</span>'
                    f'</div>'
                )
            return (
                f'<div class="panel">'
                f'<div class="mhead"><span>{kat_judul}</span><span>Panas</span><span>Dingin</span></div>'
                f'{"".join(baris_html)}'
                f'</div>'
            )

        kiri_html = render_panel_kategori(df_menu, "Kopi Susu") + "<br>" + render_panel_kategori(df_menu, "Kopi Hitam")
        kanan_html = render_panel_kategori(df_menu, "Bukan Kopi")

        # Tambahkan kategori kustom tambahan jika ada
        kategori_extra = [k for k in df_menu["kategori"].unique() if k not in KATEGORI]
        for k_ext in kategori_extra:
            kanan_html += "<br>" + render_panel_kategori(df_menu, k_ext)

        st.markdown(
            f"""
            <div class="lembar">
                <div class="lembar-top"></div>
                <div class="lembar-in">
                    <div class="ljudul">
                        <div class="big">MENU</div>
                        <div class="al">Shelter Kopi · {ALAMAT}</div>
                    </div>
                    <div class="lgrid">
                        <div>{kiri_html}</div>
                        <div>
                            <div class="lpill">🕓 Buka {JAM_BUKA} WIB</div>
                            {kanan_html}
                        </div>
                    </div>
                    <div class="lfoot">
                        <div class="ig">@{HANDLE}</div>
                        {LOGO_SVG_TERANG if tema_aktif == "☀️ Terang (Khas Shelter Kopi)" else LOGO_SVG_GELAP}
                    </div>
                </div>
            </div>
            <div style="text-align:center;font-size:.82rem;margin-top:10px;opacity:0.8;">
                * Angka harga dalam ribuan rupiah (contoh: 15 = Rp 15.000). Tanda '-' berarti varian tidak tersedia.
            </div>
            """,
            unsafe_allow_html=True
        )

    # Tab 2: Galeri Foto Minuman (FR-11)
    with tab_galeri:
        opsi_suhu = st.radio(
            "Tampilkan versi minuman:",
            ["Dingin", "Panas"],
            horizontal=True,
            format_func=lambda s: "❄️ Versi Dingin" if s == "Dingin" else "🔥 Versi Panas"
        )
        kol_harga = "harga_dingin" if opsi_suhu == "Dingin" else "harga_panas"
        df_galeri = df_menu[df_menu[kol_harga] > 0]

        if df_galeri.empty:
            st.info(f"Belum ada menu yang memiliki versi {opsi_suhu}.")
        else:
            cols_gal = 3
            rows_g = list(df_galeri.itertuples())
            for i in range(0, len(rows_g), cols_gal):
                cols = st.columns(cols_gal)
                for col, m in zip(cols, rows_g[i : i + cols_gal]):
                    with col:
                        with st.container(border=True):
                            st.markdown(foto_html(m.nama, m.kategori, m.gambar, opsi_suhu), unsafe_allow_html=True)
                            st.markdown(f'<div class="nm">{m.nama}</div>', unsafe_allow_html=True)
                            st.caption(f"Kategori: {m.kategori}")
                            harga_val = getattr(m, kol_harga)
                            st.markdown(f'<span class="chip" style="background:#D35400;color:#fff !important;">{rupiah(harga_val)}</span>', unsafe_allow_html=True)

# ======================================================================
# MODUL 3: KELOLA MENU & STOK (FR-12)
# ======================================================================
def modul_kelola_menu():
    st.subheader("📦 Kelola Menu Minuman & Pemantauan Stok")

    tab_tambah, tab_ubah = st.tabs(["➕ Tambah Menu Baru", "🔧 Ubah Harga & Stok Menu"])

    # Tab Tambah Menu Baru (FR-12)
    with tab_tambah:
        with st.form("form_tambah_menu", clear_on_submit=True):
            st.markdown("##### ➕ Tambahkan Menu Baru ke Katalog")
            c1, c2 = st.columns(2)
            nama_baru = c1.text_input("Nama Menu (Unik) *")
            kat_pilihan = c2.selectbox("Kategori Menu", KATEGORI + ["Kategori Baru..."])
            kat_baru = kat_pilihan
            if kat_pilihan == "Kategori Baru...":
                kat_baru = st.text_input("Nama Kategori Baru:")

            c3, c4, c5 = st.columns(3)
            h_panas = c3.number_input("Harga Panas (Rp)", min_value=0, step=500, value=0, help="0 jika tidak ada versi panas")
            h_dingin = c4.number_input("Harga Dingin (Rp)", min_value=0, step=500, value=0, help="0 jika tidak ada versi dingin")
            stok_awal = c5.number_input("Stok Awal Porsi", min_value=0, value=STOK_AWAL)

            up_foto = st.file_uploader("Upload Foto Minuman (Opsional, JPG/PNG)", type=["jpg", "jpeg", "png", "webp"])
            url_foto = st.text_input("atau Masukkan URL Gambar (Opsional):", placeholder="https://...")

            btn_simpan = st.form_submit_button("Simpan Menu Baru", type="primary")

            if btn_simpan:
                nama_bersih = nama_baru.strip()
                kategori_bersih = kat_baru.strip() if kat_baru else "Kopi Susu"

                # Validasi FR-12: tolak nama kosong atau kedua harga 0
                if not nama_bersih:
                    st.error("❌ Nama menu tidak boleh kosong!")
                elif int(h_panas) <= 0 and int(h_dingin) <= 0:
                    st.error("❌ Minimal salah satu harga (Panas atau Dingin) harus lebih besar dari Rp 0!")
                else:
                    conn = get_db()
                    c = conn.cursor()
                    c.execute("SELECT id FROM menu WHERE LOWER(nama) = LOWER(?)", (nama_bersih,))
                    if c.fetchone():
                        st.error(f"❌ Menu dengan nama '{nama_bersih}' sudah ada!")
                        conn.close()
                    else:
                        gambar_str = ""
                        if up_foto:
                            gambar_str = file_ke_uri(up_foto)
                        elif url_foto.strip():
                            gambar_str = url_foto.strip()

                        c.execute(
                            """INSERT INTO menu (nama, kategori, harga_panas, harga_dingin, stok, gambar)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (nama_bersih, kategori_bersih, int(h_panas), int(h_dingin), int(stok_awal), gambar_str)
                        )
                        conn.commit()
                        conn.close()
                        st.success(f"✅ Menu '{nama_bersih}' berhasil ditambahkan ke katalog!")
                        st.rerun()

    # Tab Ubah Harga, Tambah Stok & Hapus Menu (FR-12)
    with tab_ubah:
        df_menu = muat_menu_df()
        if df_menu.empty:
            st.info("Belum ada menu yang dapat diubah.")
            return

        st.markdown("##### 📋 Daftar Menu & Stok Terkini")
        tabel_view = df_menu[["id", "nama", "kategori", "harga_panas", "harga_dingin", "stok"]].copy()
        tabel_view.columns = ["ID", "Nama Menu", "Kategori", "Harga Panas (Rp)", "Harga Dingin (Rp)", "Sisa Stok"]
        st.dataframe(tabel_view, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("##### ⚙️ Edit Menu Minuman")

        dict_opsi = {f"#{r.id} - {r.nama} (Stok: {r.stok})": r.id for r in df_menu.itertuples()}
        label_pilihan = st.selectbox("Pilih Menu:", list(dict_opsi.keys()))
        menu_id_dipilih = dict_opsi[label_pilihan]
        m_row = df_menu[df_menu["id"] == menu_id_dipilih].iloc[0]

        kol_restock, kol_edit = st.columns([1, 2], gap="large")

        # Restock Cepat
        with kol_restock:
            with st.container(border=True):
                st.markdown("###### ➕ Tambah Stok")
                st.metric("Stok Sekarang", f"{m_row['stok']} porsi")
                tambah_jml = st.number_input("Jumlah Tambahan Stok", min_value=1, value=10, step=5, key=f"tambah_stok_{menu_id_dipilih}")
                if st.button("➕ Tambah Stok Sekarang", type="primary", use_container_width=True):
                    conn = get_db()
                    c = conn.cursor()
                    c.execute("UPDATE menu SET stok = stok + ? WHERE id = ?", (int(tambah_jml), menu_id_dipilih))
                    conn.commit()
                    conn.close()
                    st.toast(f"✅ Stok {m_row['nama']} ditambah {tambah_jml} porsi", icon="📦")
                    st.rerun()

        # Ubah Harga, Foto, dan Hapus Menu (FR-12)
        with kol_edit:
            with st.container(border=True):
                st.markdown("###### 🔧 Ubah Rincian Menu")
                e1, e2 = st.columns(2)
                np_edit = e1.number_input("Harga Panas (Rp)", min_value=0, step=500, value=int(m_row["harga_panas"]), key=f"edit_p_{menu_id_dipilih}")
                nd_edit = e2.number_input("Harga Dingin (Rp)", min_value=0, step=500, value=int(m_row["harga_dingin"]), key=f"edit_d_{menu_id_dipilih}")

                up_foto_edit = st.file_uploader("Ganti Foto (JPG/PNG)", type=["jpg", "jpeg", "png", "webp"], key=f"up_edit_{menu_id_dipilih}")
                kembalikan_svg = st.checkbox("Kembalikan ke ilustrasi bawaan (hapus foto asli)", key=f"rst_img_{menu_id_dipilih}")

                c_simpan, c_hapus = st.columns(2)
                with c_simpan:
                    if st.button("💾 Simpan Perubahan", type="primary", use_container_width=True, key=f"btn_sv_{menu_id_dipilih}"):
                        if int(np_edit) <= 0 and int(nd_edit) <= 0:
                            st.error("Minimal satu harga (Panas atau Dingin) harus > 0.")
                        else:
                            gambar_baru = m_row["gambar"]
                            if kembalikan_svg:
                                gambar_baru = ""
                            elif up_foto_edit:
                                gambar_baru = file_ke_uri(up_foto_edit)

                            conn = get_db()
                            c = conn.cursor()
                            c.execute(
                                "UPDATE menu SET harga_panas = ?, harga_dingin = ?, gambar = ? WHERE id = ?",
                                (int(np_edit), int(nd_edit), gambar_baru, menu_id_dipilih)
                            )
                            conn.commit()
                            conn.close()
                            st.toast(f"✅ Data menu {m_row['nama']} berhasil diperbarui", icon="💾")
                            st.rerun()

                with c_hapus:
                    with st.popover("🗑️ Hapus Menu", use_container_width=True):
                        st.warning(f"Apakah Anda yakin ingin menghapus menu **{m_row['nama']}**?")
                        st.caption("Penghapusan menu tidak akan menghapus riwayat item transaksi yang sudah lampau.")
                        yakin_hapus = st.checkbox("Ya, saya yakin ingin menghapus", key=f"chk_del_m_{menu_id_dipilih}")
                        if st.button("Konfirmasi Hapus Menu", type="primary", disabled=not yakin_hapus, key=f"btn_del_m_{menu_id_dipilih}"):
                            conn = get_db()
                            c = conn.cursor()
                            c.execute("DELETE FROM menu WHERE id = ?", (menu_id_dipilih,))
                            conn.commit()
                            conn.close()
                            st.toast(f"Menu {m_row['nama']} berhasil dihapus", icon="🗑️")
                            st.rerun()

# ======================================================================
# MODUL 4: LAPORAN OMSET & RIWAYAT (FR-13, FR-14) (Pemilik Saja)
# ======================================================================
def modul_laporan_owner():
    st.subheader("📊 Laporan Penjualan & Riwayat Transaksi")

    conn = get_db()
    df_pesanan = pd.read_sql("SELECT * FROM pesanan ORDER BY id DESC", conn)
    df_item = pd.read_sql("SELECT * FROM item", conn)
    conn.close()

    if df_pesanan.empty:
        st.info("Belum ada data pesanan tercatat di kedai.")
        return

    # Kalkulasi Metrik Utama (FR-13)
    total_omset = int(df_pesanan["total"].sum())
    hari_ini = datetime.now(WIB).strftime("%Y-%m-%d")
    df_hari_ini = df_pesanan[df_pesanan["waktu"].str.startswith(hari_ini)]
    omset_hari_ini = int(df_hari_ini["total"].sum()) if not df_hari_ini.empty else 0
    total_transaksi = len(df_pesanan)
    total_gelas = int(df_item["qty"].sum()) if not df_item.empty else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Omset", rupiah(total_omset))
    m2.metric("Omset Hari Ini", rupiah(omset_hari_ini))
    m3.metric("Jumlah Transaksi", f"{total_transaksi} pesanan")
    m4.metric("Gelas Terjual", f"{total_gelas} porsi")

    st.markdown("---")
    st.markdown("#### 📈 Analisis Visual Penjualan")

    # Tiga Grafik Penjualan (FR-13)
    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown("##### 🏆 Omset per Menu Minuman")
        if not df_item.empty:
            df_item["subtotal"] = df_item["qty"] * df_item["harga"]
            omset_per_menu = df_item.groupby("menu")["subtotal"].sum().sort_values(ascending=False)
            st.bar_chart(omset_per_menu)
        else:
            st.caption("Data belum ada")

    with g2:
        st.markdown("##### 🔥❄️ Panas vs Dingin (Gelas)")
        if not df_item.empty:
            gelas_per_varian = df_item.groupby("ukuran")["qty"].sum()
            st.bar_chart(gelas_per_varian)
        else:
            st.caption("Data belum ada")

    with g3:
        st.markdown("##### 💳 Metode Pembayaran")
        omset_per_metode = df_pesanan.groupby("metode")["total"].sum()
        st.bar_chart(omset_per_metode)

    st.markdown("---")
    st.markdown("#### 📋 Tabel Riwayat Transaksi")

    # Gabungkan ringkasan item per pesanan untuk tampilan tabel
    ringkasan_items = {}
    if not df_item.empty:
        for it in df_item.itertuples():
            ringkasan_items.setdefault(it.pesanan_id, []).append(f"{it.qty}x {it.menu} ({it.ukuran})")

    tabel_riwayat = df_pesanan.copy()
    tabel_riwayat["Rincian Item"] = tabel_riwayat["id"].apply(lambda pid: ", ".join(ringkasan_items.get(pid, [])))
    tabel_tampil = tabel_riwayat[["id", "waktu", "pembeli", "Rincian Item", "total", "metode", "diterima", "kembalian"]].copy()
    tabel_tampil.columns = ["ID", "Waktu (WIB)", "Nama / Meja", "Rincian Pesanan", "Total", "Metode", "Diterima", "Kembalian"]
    tabel_tampil["Total"] = tabel_tampil["Total"].apply(rupiah)
    tabel_tampil["Diterima"] = tabel_tampil["Diterima"].apply(rupiah)
    tabel_tampil["Kembalian"] = tabel_tampil["Kembalian"].apply(rupiah)
    st.dataframe(tabel_tampil, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### 🧾 Lihat Struk & 🗑️ Koreksi / Hapus Transaksi (FR-14)")

    opsi_trx = {
        f"#{int(r.id):05d} · {r.pembeli} · {rupiah(r.total)} ({r.waktu})": int(r.id)
        for r in df_pesanan.itertuples()
    }
    trx_label = st.selectbox("Pilih Transaksi untuk Diperiksa:", list(opsi_trx.keys()))
    trx_id = opsi_trx[trx_label]

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM pesanan WHERE id = ?", (trx_id,))
    trx_row = c.fetchone()
    c.execute("SELECT * FROM item WHERE pesanan_id = ?", (trx_id,))
    trx_items = c.fetchall()
    conn.close()

    c_struk, c_aksi = st.columns([3, 2], gap="large")

    with c_struk:
        st.markdown("##### 📄 Tampilan Struk")
        if trx_row:
            tampil_struk_ui(dict(trx_row), [dict(it) for it in trx_items], key_suffix=f"owner_{trx_id}")

    with c_aksi:
        with st.container(border=True):
            st.markdown("##### ⚠️ Hapus Transaksi & Koreksi")
            st.write(f"Pesanan: **#{trx_id:05d}**")
            st.write(f"Atas nama: **{trx_row['pembeli']}**")
            st.write(f"Total: **{rupiah(trx_row['total'])}**")
            st.caption("Gunakan fitur ini jika terjadi pembatalan pesanan atau salah input oleh barista.")

            kembalikan_stok = st.checkbox("Kembalikan stok menu yang dipesan (re-stock)", value=True, key=f"chk_rst_{trx_id}")
            yakin_hapus = st.checkbox("Saya yakin ingin menghapus transaksi ini", key=f"chk_cf_{trx_id}")

            if st.button("🗑️ Hapus Pesanan & Struk", type="primary", disabled=not yakin_hapus, use_container_width=True):
                conn = get_db()
                try:
                    c = conn.cursor()
                    if kembalikan_stok:
                        for it in trx_items:
                            c.execute("UPDATE menu SET stok = stok + ? WHERE nama = ?", (int(it["qty"]), it["menu"]))
                    c.execute("DELETE FROM item WHERE pesanan_id = ?", (trx_id,))
                    c.execute("DELETE FROM pesanan WHERE id = ?", (trx_id,))
                    conn.commit()
                    conn.close()

                    if st.session_state.terakhir_pesanan_id == trx_id:
                        st.session_state.terakhir_pesanan_id = None

                    st.toast(f"✅ Transaksi #{trx_id:05d} berhasil dihapus!", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    conn.rollback()
                    conn.close()
                    st.error(f"❌ Gagal menghapus transaksi: {str(e)}")

# ======================================================================
# MODUL 5: PENGATURAN & QRIS (FR-15, NF-07) (Pemilik Saja)
# ======================================================================
def modul_pengaturan():
    st.subheader("⚙️ Pengaturan Kedai, QRIS & Cadangan Data")

    tab_qris, tab_notif, tab_db = st.tabs(["📱 Pengaturan QRIS", "🔔 Pengaturan Notifikasi HP", "💾 Pencadangan Basis Data"])

    # Tab 1: Pengaturan Gambar QRIS Kedai (FR-15)
    with tab_qris:
        st.markdown("##### 📱 Gambar QRIS Resmi Kedai")
        st.write("Unggah gambar QRIS statis milik Shelter Kopi. Gambar ini akan ditampilkan saat kasir memilih metode pembayaran QRIS.")

        qris_saat_ini = get_setting("qris_image", "")
        if qris_saat_ini:
            st.image(qris_saat_ini, caption="Gambar QRIS Kedai Saat Ini", width=280)
            if st.button("Hapus QRIS Saat Ini", type="secondary"):
                set_setting("qris_image", "")
                st.toast("Gambar QRIS berhasil dihapus", icon="🗑️")
                st.rerun()
        else:
            st.warning("⚠️ Belum ada gambar QRIS yang diunggah.")

        st.markdown("---")
        up_qris = st.file_uploader("Unggah Gambar QRIS Baru (JPG/PNG)", type=["jpg", "jpeg", "png", "webp"], key="upload_qris_setting")
        if up_qris and st.button("💾 Simpan Gambar QRIS", type="primary"):
            uri_qris = file_ke_uri(up_qris)
            set_setting("qris_image", uri_qris)
            st.success("✅ Gambar QRIS kedai berhasil diperbarui!")
            st.rerun()

    # Tab 2: Pengaturan Topik Notifikasi ntfy.sh (FR-15)
    with tab_notif:
        st.markdown("##### 🔔 Push Notifikasi Pesanan ke HP (ntfy.sh)")
        st.write(
            "Aplikasi menggunakan layanan notifikasi gratis dan tanpa akun **ntfy.sh**. "
            "Setiap ada pesanan baru di kasir, ringkasan transaksi akan langsung dikirim ke ponsel Pemilik."
        )

        topik_saat_ini = get_setting("ntfy_topic", DEFAULT_TOPIC)
        topik_input = st.text_input("Nama Topik Notifikasi (Unik):", value=topik_saat_ini)

        c_sv_notif, c_test_notif = st.columns(2)
        with c_sv_notif:
            if st.button("💾 Simpan Topik Notifikasi", type="primary", use_container_width=True):
                set_setting("ntfy_topic", topik_input.strip())
                st.success("Topik notifikasi berhasil disimpan!")
                st.rerun()

        with c_test_notif:
            if st.button("📲 Kirim Notifikasi Tes ke HP", use_container_width=True):
                ok, res = kirim_notif(topik_input.strip(), "Tes Notifikasi", "Koneksi notifikasi dari Shelter Kopi sukses! 🎉")
                if ok:
                    st.success("Notifikasi tes terkirim ke ntfy.sh! Periksa ponsel Anda.")
                else:
                    st.error(f"Gagal mengirim notifikasi: {res}")

        st.info(
            "**Cara Menghubungkan ke Ponsel Pemilik:**\n"
            "1. Unduh aplikasi **ntfy** di Google Play Store atau Apple App Store (gratis).\n"
            f"2. Buka aplikasi ntfy, tekan tombol **+**, lalu masukkan nama topik: `{topik_input.strip()}`.\n"
            "3. Notifikasi pesanan baru akan berbunyi otomatis setiap transaksi kasir berhasil disimpan!"
        )

    # Tab 3: Pencadangan Basis Data SQLite (NF-07)
    with tab_db:
        st.markdown("##### 💾 Integritas & Pencadangan Basis Data (shelterkopi.db)")
        st.write(
            "Seluruh data menu, harga, pesanan, item, dan konfigurasi tersimpan secara lokal dan aman di berkas `shelterkopi.db`."
        )

        if os.path.exists(DB_FILE):
            ukuran_kb = os.path.getsize(DB_FILE) / 1024
            st.success(f"Lokasi Berkas: `{os.path.abspath(DB_FILE)}` (Ukuran: {ukuran_kb:.1f} KB)")
            with open(DB_FILE, "rb") as f_db:
                st.download_button(
                    label="⬇️ Unduh Cadangan Basis Data (shelterkopi.db)",
                    data=f_db.read(),
                    file_name=f"backup_shelterkopi_{datetime.now(WIB).strftime('%Y%m%d_%H%M%S')}.db",
                    mime="application/x-sqlite3",
                    type="primary"
                )
        else:
            st.error("Berkas database belum ditemukan.")

        st.markdown(
            """
            > [!TIP]
            > **Panduan Pencadangan Berkala untuk Pemilik Kedai:**
            > 1. Unduh berkas cadangan database setiap malam setelah tutup kedai (pukul 00.00).
            > 2. Simpan salinan di Google Drive atau flashdisk untuk mengantisipasi kerusakan perangkat keras.
            > 3. Untuk memulihkan data di komputer baru, cukup letakkan kembali berkas `shelterkopi.db` di folder aplikasi.
            """
        )

# ======================================================================
# ROUTING UTAMA BERDASARKAN PERAN & HALAMAN
# ======================================================================
if peran == "👨‍🍳 Kasir / Barista":
    if not st.session_state.kasir_auth:
        st.info("🔐 Silakan masukkan PIN Kasir di bilah sisi (sidebar) kiri untuk mengakses modul kasir.")
        # Tampilkan Menu Digital untuk umum/pelanggan yang melihat layar kasir saat terkunci (FR-11)
        modul_menu_digital()
    else:
        if halaman_kasir == "🛒 Kasir & POS":
            modul_pos()
        elif halaman_kasir == "📋 Menu Digital":
            modul_menu_digital()
        elif halaman_kasir == "📦 Kelola Menu & Stok":
            modul_kelola_menu()

else:  # Pemilik Kedai (Owner)
    if not st.session_state.owner_auth:
        st.warning("🔐 Akses Halaman Pemilik Kedai terlindungi PIN. Silakan masukkan PIN Pemilik di bilah sisi (sidebar) kiri.")
    else:
        if halaman_owner == "📊 Laporan Omset & Riwayat":
            modul_laporan_owner()
        elif halaman_owner == "⚙️ Pengaturan & QRIS":
            modul_pengaturan()
        elif halaman_owner == "🛒 Tampilan Kasir & POS":
            modul_pos()
        elif halaman_owner == "📋 Tampilan Menu Digital":
            modul_menu_digital()
        elif halaman_owner == "📦 Kelola Menu & Stok":
            modul_kelola_menu()
