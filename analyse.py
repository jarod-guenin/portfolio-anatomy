#!/usr/bin/env python3
"""
analyse.py — Analyse les dessins du dossier `dessins/` et génère le site dans `docs/`.

Pour chaque dessin :
  - calcule ~30 métriques (fichier, couleur, lumière, texture, composition, portfolio) ;
  - lit les calques des fichiers .tif / .psd (fenêtre « Calques » du site) ;
  - crée une version web filigranée (WebP) + une miniature, avec copyright dans les métadonnées ;
  - écrit tout dans docs/data.json, lu par le site.

Usage :
    python analyse.py           # analyse uniquement les dessins nouveaux ou modifiés
    python analyse.py --force   # recalcule tout (à faire si tu changes le filigrane ou config.json)
    python analyse.py --inspect dessins/Griffith/griffith.tif   # montre les calques détectés dans un fichier
"""
from __future__ import annotations

import argparse
import colorsys
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

import calques

Image.MAX_IMAGE_PIXELS = 400_000_000   # grands formats d'impression acceptés

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "dessins"
OUT = ROOT / "docs"
CACHE_FILE = ROOT / ".cache" / "stats.json"
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".psd", ".bmp", ".gif"}
PRIORITE = [".png", ".webp", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".psd"]  # image affichée
A_CALQUES = {".tif", ".tiff", ".psd"}                  # fichiers pouvant contenir des calques
STATS_VERSION = 3          # à incrémenter si on change les calculs
WORK_SIDE = 1200           # taille de travail pour les calculs lourds (px, plus grand côté)

BIT_DEPTH = {"1": 1, "L": 8, "P": 8, "RGB": 8, "RGBA": 8, "LA": 8, "PA": 8, "CMYK": 8,
             "YCbCr": 8, "I;16": 16, "I;16B": 16, "I;16L": 16, "I": 32, "F": 32}
COMPRESSION = {"PNG": "Deflate (sans perte)", "JPEG": "DCT (avec perte)", "WEBP": "VP8 / VP8L",
               "GIF": "LZW (sans perte)", "BMP": "Aucune", "MPO": "DCT (avec perte)", "PSD": "RLE / PackBits"}
TIFF_COMPRESSION = {"raw": "Aucune", "tiff_lzw": "LZW (sans perte)", "tiff_adobe_deflate": "ZIP / Deflate (sans perte)",
                    "tiff_deflate": "ZIP / Deflate (sans perte)", "packbits": "PackBits (sans perte)",
                    "jpeg": "JPEG (avec perte)", "tiff_jpeg": "JPEG (avec perte)", "webp": "WebP",
                    "tiff_ccitt": "CCITT (fax)", "group3": "CCITT G3", "group4": "CCITT G4", "zstd": "Zstandard"}


# ───────────────────────────── outils ─────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*[int(round(min(255, max(0, v)))) for v in rgb])


def norm100(values) -> list[int]:
    a = np.asarray(values, dtype=float)
    m = a.max() if a.size else 0
    return [int(round(v)) for v in (a / m * 100 if m > 0 else a)]


def resize_max(img: np.ndarray, side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = side / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def circ_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def hue_name(h: float) -> str:
    table = [(15, "rouge"), (45, "orange"), (70, "jaune"), (90, "vert-jaune"), (150, "vert"),
             (195, "cyan"), (255, "bleu"), (290, "violet"), (330, "magenta"), (345, "rose"), (361, "rouge")]
    for limit, name in table:
        if h < limit:
            return name
    return "rouge"


def ratio_name(w: int, h: int) -> str:
    r = max(w, h) / min(w, h)
    for value, name in [(1, "carré"), (math.sqrt(2), "format A"), (1.5, "3:2"), (4 / 3, "4:3"),
                        (16 / 9, "16:9"), (1.25, "5:4")]:
        if abs(r - value) < 0.01:
            return name
    return ""


# ───────────────────────────── ouverture ─────────────────────────────

def open_image(path: Path):
    im = Image.open(path)
    im.load()
    fmt, info, mode, bands = im.format, dict(im.info), im.mode, im.getbands()
    im = ImageOps.exif_transpose(im) or im
    return im, fmt, info, mode, bands


def to_rgb_array(im: Image.Image) -> np.ndarray:
    """Image → tableau RGB uint8, transparence aplatie sur fond blanc."""
    if im.mode.startswith("I") or im.mode == "F":
        a = np.asarray(im).astype(np.float64)
        a = (a - a.min()) / (np.ptp(a) or 1) * 255
        im = Image.fromarray(a.astype(np.uint8), "L")
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return np.asarray(Image.alpha_composite(bg, rgba).convert("RGB"))


# ───────────────────────────── A. fichier ─────────────────────────────

def file_stats(path, im, fmt, info, mode, bands, sha) -> dict:
    w, h = im.size
    depth = BIT_DEPTH.get(mode, 8)
    size = path.stat().st_size
    raw = w * h * len(bands) * depth / 8
    fmt = fmt or path.suffix[1:].upper()
    comp = COMPRESSION.get(fmt, "inconnue")
    if fmt == "TIFF":
        c = str(info.get("compression", "inconnue"))
        comp = TIFF_COMPRESSION.get(c, c)

    icc = info.get("icc_profile")
    if icc:
        try:
            profil = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
        except Exception:
            profil = "Profil ICC illisible"
    else:
        profil = "Aucun (sRGB supposé)"

    dpi = info.get("dpi")
    dpi = f"{round(float(dpi[0]))} dpi" if dpi else "non renseigné"

    has_alpha = "A" in bands or "transparency" in info
    transp = 0.0
    if has_alpha:
        alpha = np.asarray(im.convert("RGBA").getchannel("A"))
        transp = float((alpha < 250).mean())   # 250 : ignore les arrondis (certains logiciels écrivent 254)

    return {
        "largeur": w, "hauteur": h, "megapixels": round(w * h / 1e6, 2),
        "ratio": round(max(w, h) / min(w, h), 3), "ratio_nom": ratio_name(w, h),
        "orientation": "portrait" if h > w else ("paysage" if w > h else "carré"),
        "format": fmt, "poids": size, "bpp": round(size * 8 / (w * h), 3),
        "brut": int(raw), "taux_compression": round(raw / size, 2) if size else None,
        "compression": comp, "profondeur": depth, "canaux": "".join(bands), "mode": mode,
        "profil": profil, "dpi": dpi, "transparence": round(transp, 4), "sha256": sha,
    }


# ───────────────────────────── B. couleur ─────────────────────────────

def kmeans_palette(work: np.ndarray, k: int = 8) -> list[dict]:
    lab = cv2.cvtColor(work.astype(np.float32) / 255, cv2.COLOR_RGB2Lab).reshape(-1, 3)
    rng = np.random.default_rng(0)
    idx = rng.choice(len(lab), size=min(len(lab), 80_000), replace=False)
    sample = np.ascontiguousarray(lab[idx])
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.2)
    _, labels, centers = cv2.kmeans(sample, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k).astype(float)
    rgb = cv2.cvtColor(centers.reshape(1, -1, 3).astype(np.float32), cv2.COLOR_Lab2RGB).reshape(-1, 3) * 255
    merged: dict[str, float] = {}
    for i in np.argsort(-counts):
        if counts[i] > 0:
            hx = to_hex(rgb[i])
            merged[hx] = merged.get(hx, 0) + counts[i]
    total = sum(merged.values())
    return [{"hex": hx, "pct": round(c / total * 100, 1)} for hx, c in sorted(merged.items(), key=lambda x: -x[1])]


def harmony(palette: list[dict]) -> dict:
    chroma = []
    for p in palette:
        r, g, b = (int(p["hex"][i:i + 2], 16) / 255 for i in (1, 3, 5))
        hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
        if ss > 0.25 and vv > 0.2 and p["pct"] >= 2:
            chroma.append((hh * 360, p["pct"], p["hex"]))
    if not chroma:
        return {"type": "Achromatique", "detail": "noir, blanc et gris dominent", "groupes": []}

    clusters: list[dict] = []
    for hue, pct, hx in sorted(chroma):
        for c in clusters:
            if circ_dist(hue, c["h"]) <= 30:
                c["m"].append(hx)
                c["w"] += pct
                break
        else:
            clusters.append({"h": hue, "w": pct, "m": [hx]})
    hues = [c["h"] for c in clusters]
    k = len(hues)
    pair = [circ_dist(a, b) for i, a in enumerate(hues) for b in hues[i + 1:]]
    s = sorted(hues)
    gaps = [(s[(i + 1) % k] - s[i]) % 360 for i in range(k)] if k > 1 else [360]
    span = 360 - max(gaps)

    if k == 1:
        t = "Monochromatique"
    elif k == 2:
        d = pair[0]
        t = "Analogue" if d <= 60 else ("Complémentaire" if d >= 150 else "Contraste de teintes")
    elif k == 3:
        if span <= 90:
            t = "Analogue"
        elif all(90 <= d <= 150 for d in pair):
            t = "Triadique"
        elif max(pair) >= 150:
            t = "Complémentaire divisé"
        else:
            t = "Polychrome"
    else:
        t = "Analogue étendu" if span <= 120 else "Polychrome"

    order = sorted(clusters, key=lambda c: -c["w"])
    detail = " + ".join(f"{hue_name(c['h'])}s" for c in order)
    return {"type": t, "detail": detail, "groupes": [c["m"] for c in order]}


def color_temperature(work: np.ndarray):
    c = work.reshape(-1, 3).astype(np.float64) / 255
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).mean(axis=0)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    x_, y_, z_ = m @ lin
    s = x_ + y_ + z_
    if s <= 0:
        return None, "indéterminée"
    x, y = x_ / s, y_ / s
    n = (x - 0.3320) / (0.1858 - y)
    cct = 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33
    cct = float(min(20000, max(1000, cct)))
    label = "très chaud" if cct < 3000 else "chaud" if cct < 4500 else "neutre" if cct < 6500 else "froid"
    return round(cct / 50) * 50, label


def colorfulness(work: np.ndarray):
    r, g, b = (work[..., i].astype(np.float64) for i in range(3))
    rg, yb = r - g, 0.5 * (r + g) - b
    cf = math.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    labels = [(15, "pas coloré"), (33, "légèrement coloré"), (45, "modérément coloré"),
              (59, "moyennement coloré"), (82, "assez coloré"), (109, "très coloré")]
    label = next((lab for lim, lab in labels if cf < lim), "extrêmement coloré")
    return round(cf, 1), label


def color_stats(full_rgb: np.ndarray, work: np.ndarray) -> dict:
    packed = (full_rgb[..., 0].astype(np.uint32) << 16) | (full_rgb[..., 1].astype(np.uint32) << 8) | full_rgb[..., 2]
    uniques = int(np.unique(packed).size)

    hsv = cv2.cvtColor(work, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    mask = (sat > 0.15) & (val > 0.15)
    colored = hue[mask]
    hist24 = np.histogram(colored, bins=24, range=(0, 360))[0]
    if colored.size:
        dom = float(np.argmax(hist24) * 15 + 7.5)
        around = float(np.mean([circ_dist(h, dom) <= 30 for h in colored[:: max(1, colored.size // 200_000)]]))
    else:
        dom, around = None, 0.0
    red = float((((hue >= 340) | (hue < 20)) & mask).mean())

    palette = kmeans_palette(work)
    cf, cf_label = colorfulness(work)
    temp, temp_label = color_temperature(work)
    flat = work.reshape(-1, 3)
    return {
        "palette": palette,
        "couleurs_uniques": uniques,
        "saturation_moy": round(float(sat.mean()), 4),
        "saturation_std": round(float(sat.std()), 4),
        "colorfulness": cf, "colorfulness_label": cf_label,
        "teintes": norm100(hist24),
        "part_chromatique": round(float(mask.mean()), 4),
        "teinte_dominante": dom, "teinte_nom": hue_name(dom) if dom is not None else "aucune",
        "part_autour_dominante": round(around, 4),
        "part_rouge": round(red, 4),
        "temperature": temp, "temperature_label": temp_label,
        "harmonie": harmony(palette),
        "moyenne": to_hex(flat.mean(axis=0)),
        "mediane": to_hex(np.median(flat, axis=0)),
    }


# ───────────────────────────── C. lumière ─────────────────────────────

def light_stats(work: np.ndarray) -> dict:
    f = work.astype(np.float64) / 255
    lum = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    p1, p99 = np.percentile(lum, [1, 99])
    mean = float(lum.mean())
    return {
        "histo": norm100(np.histogram(lum, bins=32, range=(0, 1))[0]),
        "rgb": {c: norm100(np.histogram(work[..., i], bins=32, range=(0, 256))[0]) for i, c in enumerate("rgb")},
        "moyenne": round(mean, 4), "mediane": round(float(np.median(lum)), 4),
        "ecart_type": round(float(lum.std()), 4), "contraste_rms": round(float(lum.std()), 4),
        "p1": round(float(p1), 4), "p99": round(float(p99), 4), "plage": round(float(p99 - p1), 4),
        "noirs": round(float((lum <= 0.02).mean()), 4), "blancs": round(float((lum >= 0.98).mean()), 4),
        "cle": "low-key" if mean < 0.35 else ("high-key" if mean > 0.65 else "mid-key"),
    }


# ───────────────────────────── D. texture ─────────────────────────────

def fractal_dimension(edges: np.ndarray) -> float:
    z = edges > 0
    if z.sum() < 50:
        return 0.0
    p = min(z.shape)
    sizes = [2 ** i for i in range(1, int(math.log2(p)) - 1)]
    counts = []
    for s in sizes:
        hh, ww = z.shape[0] // s * s, z.shape[1] // s * s
        blocks = z[:hh, :ww].reshape(hh // s, s, ww // s, s).any(axis=(1, 3))
        counts.append(max(1, int(blocks.sum())))
    slope = np.polyfit(np.log(sizes), np.log(counts), 1)[0]
    return round(float(-slope), 3)


def orientation_name(a: float) -> str:
    if a < 22.5 or a >= 157.5:
        return "horizontale"
    if a < 67.5:
        return "diagonale montante ( / )"
    if a < 112.5:
        return "verticale"
    return "diagonale descendante ( \\ )"


def texture_stats(work: np.ndarray):
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    otsu, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hi = max(40.0, otsu)
    edges = cv2.Canny(blur, hi * 0.5, hi)

    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    p = hist[hist > 0] / hist.sum()
    entropy = float(-(p * np.log2(p)).sum())

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    sel = mag > max(1e-6, float(np.percentile(mag, 80)))
    # angle du trait (perpendiculaire au gradient), 0° = horizontal, 90° = vertical
    ang = (90 - np.degrees(np.arctan2(gy[sel], gx[sel]))) % 180
    rose = np.histogram(ang, bins=18, range=(0, 180), weights=mag[sel])[0]
    dom = float(np.argmax(rose) * 10 + 5) if rose.sum() else None

    return {
        "densite_contours": round(float((edges > 0).mean()), 4),
        "nettete": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "entropie": round(entropy, 3),
        "orientation": norm100(rose),
        "orientation_dom": dom,
        "orientation_nom": orientation_name(dom) if dom is not None else "aucune",
        "anisotropie": round(float(rose.max() / rose.mean()), 2) if rose.sum() else 0,
        "fractale": fractal_dimension(edges),
    }


# ───────────────────────────── E. composition ─────────────────────────────

def spectral_saliency(gray: np.ndarray) -> np.ndarray:
    small = resize_max(gray, 96).astype(np.float64) / 255
    spec = np.fft.fft2(small)
    log_amp = np.log(np.abs(spec) + 1e-9)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spec)))) ** 2
    sal = cv2.GaussianBlur(sal, (0, 0), sigmaX=max(small.shape) * 0.04)
    return (sal - sal.min()) / (np.ptp(sal) + 1e-12)


def composition_stats(work: np.ndarray) -> dict:
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
    sal = spectral_saliency(gray)
    h, w = sal.shape
    ys, xs = np.mgrid[0:h, 0:w]
    tot = sal.sum() or 1
    cx, cy = float((sal * xs).sum() / tot / (w - 1 or 1)), float((sal * ys).sum() / tot / (h - 1 or 1))
    thirds = [(a, b) for a in (1 / 3, 2 / 3) for b in (1 / 3, 2 / 3)]
    ecart = min(math.hypot(cx - a, cy - b) for a, b in thirds)

    q = [sal[:h // 2, :w // 2].sum(), sal[:h // 2, w // 2:].sum(), sal[h // 2:, :w // 2].sum(), sal[h // 2:, w // 2:].sum()]
    q = [round(float(v / (sum(q) or 1)), 4) for v in q]

    py, px = np.unravel_index(np.argmax(sal), sal.shape)
    row = ["haut", "centre", "bas"][min(2, int(py / h * 3))]
    col = ["gauche", "centre", "droite"][min(2, int(px / w * 3))]
    zone = "centre" if row == col == "centre" else (row if col == "centre" else (col if row == "centre" else f"{row}-{col}"))

    gw = 24
    gh = int(min(48, max(8, round(gw * h / w))))
    grid = cv2.resize(sal.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
    grid = (grid / (grid.max() or 1) * 100).round().astype(int)

    g = resize_max(gray, 256).astype(np.float64)
    if g.std() < 1e-6:
        sym = 1.0
    else:
        sym = float(np.corrcoef(g.ravel(), np.fliplr(g).ravel())[0, 1])
    return {
        "centre": [round(cx, 4), round(cy, 4)],
        "ecart_tiers": round(ecart, 4),
        "saillance": {"l": gw, "h": gh, "grille": grid.ravel().tolist()},
        "pic_zone": zone,
        "quadrants": q,
        "symetrie": round(max(0.0, sym), 3),
    }


# ───────────────────────────── analyse complète ─────────────────────────────

def analyse_calques(path: Path, im: Image.Image, mode: str, id_: str, cfg: dict):
    """Lit les calques, écrit le sprite de vignettes ; retourne le bloc « calques » pour data.json."""
    res = calques.read_layers(path, im.size, mode)
    if not res:
        return None
    info, liste, thumbs = res
    out = dict(info)
    out["toile"] = list(im.size)
    out["fichier"] = path.name
    out["liste"] = liste
    if cfg.get("calques_vignettes", True):
        (OUT / "calques").mkdir(exist_ok=True)
        sprite = calques.save_sprite(thumbs, OUT / "calques" / f"{id_}.webp")
        if sprite:
            sprite["url"] = f"calques/{id_}.webp"
            out["sprite"] = sprite
            for i, it in enumerate(liste):
                if thumbs[i] is not None:
                    it["vignette"] = i
    return out


def analyse(path, im, fmt, info, mode, bands, sha):
    full = to_rgb_array(im)
    work = resize_max(full, WORK_SIDE)
    stats = {
        "fichier": file_stats(path, im, fmt, info, mode, bands, sha),
        "couleur": color_stats(full, work),
        "lumiere": light_stats(work),
        "texture": texture_stats(work),
        "composition": composition_stats(work),
    }
    return stats


# ───────────────────────────── images web ─────────────────────────────

def get_font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def watermark(img: Image.Image, text: str, opacity: float, style: str = "discret") -> Image.Image:
    """Filigrane. Styles : « discret » (quelques mentions espacées), « dense » (mosaïque), « coin » (signature)."""
    img = img.convert("RGBA")
    w, h = img.size
    a = int(255 * opacity)
    if style == "coin":
        size = max(12, int(min(w, h) / 40))
        font = get_font(size)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        tw = draw.textlength(text, font=font)
        m = size
        draw.text((w - tw - m, h - size * 1.6 - m), text, font=font, fill=(255, 255, 255, a),
                  stroke_width=max(1, size // 14), stroke_fill=(0, 0, 0, a // 2))
        return Image.alpha_composite(img, layer)

    dense = style == "dense"
    size = max(12, int(min(w, h) / (16 if dense else 34)))
    font = get_font(size)
    diag = int(math.hypot(w, h)) + 2
    layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    tw = draw.textlength(text, font=font)
    step_x = int(tw + size * (3 if dense else 9))
    step_y = int(size * (4.5 if dense else 11))
    for row, y in enumerate(range(0, diag, step_y)):
        offset = (row % 2) * step_x // 2
        for x in range(-step_x + offset, diag, step_x):
            draw.text((x, y), text, font=font, fill=(255, 255, 255, a),
                      stroke_width=max(1, size // 16), stroke_fill=(0, 0, 0, a // (1 if dense else 2)))
    layer = layer.rotate(30, resample=Image.BICUBIC)
    left, top = (diag - w) // 2, (diag - h) // 2
    return Image.alpha_composite(img, layer.crop((left, top, left + w, top + h)))


def make_exif(cfg: dict, titre: str, annee: str) -> bytes:
    exif = Image.Exif()
    exif[0x013B] = cfg["auteur"]                                                  # Artist
    exif[0x8298] = f"(c) {annee} {cfg['auteur']} - Tous droits reserves"          # Copyright
    exif[0x010E] = f"{titre} - {cfg.get('url_site', '')}"                         # ImageDescription
    return exif.tobytes()


def make_web_images(im: Image.Image, has_alpha: bool, dest: Path, thumb: Path, cfg: dict, titre: str, annee: str):
    base = im.convert("RGBA") if has_alpha else im.convert("RGB")
    exif = make_exif(cfg, titre, annee)

    web = base.copy()
    web.thumbnail((cfg["taille_web"], cfg["taille_web"]), Image.LANCZOS)
    if cfg.get("filigrane"):
        web = watermark(web, cfg["filigrane"], cfg.get("filigrane_opacite", 0.09), cfg.get("filigrane_style", "discret"))
    if not has_alpha:
        web = web.convert("RGB")
    web.save(dest, "WEBP", quality=cfg.get("qualite_webp", 82), method=6, exif=exif)

    th = base.copy()
    th.thumbnail((360, 360), Image.LANCZOS)
    th.save(thumb, "WEBP", quality=72, method=6, exif=exif)


# ───────────────────────────── page d'accueil ─────────────────────────────

def build_accueil(cfg: dict, force: bool):
    """Image de la page d'accueil (non filigranée) → docs/accueil.webp. Retourne le bloc pour data.json."""
    src = cfg.get("accueil")
    if not src:
        return None
    path = (ROOT / src)
    if not path.exists():
        # sinon : première image trouvée dans le dossier accueil/
        found = sorted(f for f in (ROOT / "accueil").glob("*") if f.suffix.lower() in EXTS) if (ROOT / "accueil").exists() else []
        if not found:
            print(f"  ⚠ page d'accueil : « {src} » introuvable → pas de page d'accueil")
            return None
        path = found[0]
    sha = sha256_file(path)[:10]
    dest = OUT / "accueil.webp"
    stamp = ROOT / ".cache" / "accueil.sha"
    if force or not dest.exists() or not stamp.exists() or stamp.read_text() != sha:
        im = ImageOps.exif_transpose(Image.open(path))
        has_alpha = "A" in im.getbands()
        im = im.convert("RGBA" if has_alpha else "RGB")
        im.thumbnail((cfg.get("accueil_taille", 2560),) * 2, Image.LANCZOS)
        im.save(dest, "WEBP", quality=86, method=6,
                exif=make_exif(cfg, "Accueil", str(dt.date.today().year)))
        stamp.write_text(sha)
        print(f"  accueil   {path.name} → docs/accueil.webp")
    with Image.open(dest) as im:
        size = list(im.size)
    return {"img": f"accueil.webp?v={sha}", "taille": size,
            "ajustement": cfg.get("accueil_ajustement", "cover"), "fond": cfg.get("accueil_fond", "#111111"),
            "texte": cfg.get("accueil_texte", "Attrape un bord et tire pour déchirer")}


# ───────────────────────────── infos du dessin ─────────────────────────────

def read_info(folder: Path, src: Path) -> dict:
    p = folder / "info.json"
    if not p.exists():
        info = {
            "titre": folder.name,
            "description": "",
            "tags": [],
            "categorie": "",
            "date": dt.date.fromtimestamp(src.stat().st_mtime).isoformat(),
        }
        p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    → info.json créé pour « {folder.name} » (à compléter)")
    info = json.loads(p.read_text(encoding="utf-8"))
    info.setdefault("titre", folder.name)
    info.setdefault("description", "")
    info.setdefault("tags", [])
    info.setdefault("categorie", "")
    info.setdefault("date", dt.date.fromtimestamp(src.stat().st_mtime).isoformat())
    return info


# ───────────────────────────── F. portfolio ─────────────────────────────

RANKS = [
    (lambda s: s["lumiere"]["moyenne"], False, "plus sombre"),
    (lambda s: s["lumiere"]["moyenne"], True, "plus clair"),
    (lambda s: s["lumiere"]["contraste_rms"], True, "plus contrasté"),
    (lambda s: s["couleur"]["saturation_moy"], True, "plus saturé"),
    (lambda s: s["couleur"]["colorfulness"], True, "plus coloré"),
    (lambda s: s["couleur"]["part_rouge"], True, "plus rouge"),
    (lambda s: s["texture"]["densite_contours"], True, "plus détaillé"),
    (lambda s: s["texture"]["fractale"], True, "plus complexe"),
    (lambda s: s["composition"]["symetrie"], True, "plus symétrique"),
]


def palette_lab(stats: dict):
    pal = stats["couleur"]["palette"]
    rgb = np.array([[int(p["hex"][i:i + 2], 16) for i in (1, 3, 5)] for p in pal], dtype=np.float32) / 255
    lab = cv2.cvtColor(rgb.reshape(1, -1, 3), cv2.COLOR_RGB2Lab).reshape(-1, 3)
    w = np.array([p["pct"] for p in pal], dtype=np.float64)
    return lab, w / w.sum()


def palette_similarity(a: dict, b: dict) -> float:
    """Similarité 0–1 entre deux palettes : distance ΔE moyenne pondérée, dans les deux sens."""
    la, wa = palette_lab(a)
    lb, wb = palette_lab(b)
    d = np.linalg.norm(la[:, None, :] - lb[None, :, :], axis=2)
    mean_de = 0.5 * ((wa * d.min(axis=1)).sum() + (wb * d.min(axis=0)).sum())
    return float(math.exp(-mean_de / 20))


def portfolio_stats(items: list[dict]):
    n = len(items)
    for it in items:
        it["rangs"], it["proches"] = [], []
    if n < 2:
        return
    per_item = {it["id"]: [] for it in items}
    for key, desc, label in RANKS:
        ordered = sorted(items, key=lambda it: key(it["stats"]), reverse=desc)
        for r, it in enumerate(ordered, 1):
            per_item[it["id"]].append((r, label))
    for it in items:
        best = sorted(per_item[it["id"]])
        chosen, used = [], set()
        for r, label in best:
            family = "lum" if label in ("plus sombre", "plus clair") else label
            if family in used:
                continue
            used.add(family)
            chosen.append({"rang": r, "libelle": label, "total": n})
            if len(chosen) == 4:
                break
        it["rangs"] = chosen

        scores = []
        for other in items:
            if other["id"] != it["id"]:
                scores.append((palette_similarity(it["stats"], other["stats"]), other))
        scores.sort(key=lambda x: -x[0])
        it["proches"] = [{"id": o["id"], "titre": o["titre"], "score": round(s, 3)} for s, o in scores[:3]]


# ───────────────────────────── main ─────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyse les dessins et génère docs/data.json")
    parser.add_argument("--force", action="store_true", help="recalcule tout, images web comprises")
    parser.add_argument("--inspect", metavar="FICHIER", help="affiche les calques détectés dans un fichier et s'arrête")
    args = parser.parse_args()
    if args.inspect:
        calques.inspect(Path(args.inspect))
        return

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if not SRC.exists():
        SRC.mkdir()
        print("Dossier dessins/ créé : ajoute un sous-dossier par dessin, puis relance le script.")
        return
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    (OUT / "thumb").mkdir(parents=True, exist_ok=True)
    CACHE_FILE.parent.mkdir(exist_ok=True)
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}

    items = []
    used_keys = set()
    folders = sorted(p for p in SRC.iterdir() if p.is_dir() and not p.name.startswith("."))
    print(f"{len(folders)} dossier(s) trouvé(s) dans dessins/")
    for folder in folders:
        images = sorted((f for f in folder.iterdir() if f.suffix.lower() in EXTS),
                        key=lambda f: (PRIORITE.index(f.suffix.lower()), f.name.lower()))
        if not images:
            print(f"  ⚠ « {folder.name} » : aucune image, ignoré")
            continue
        # image affichée : de préférence un export (.png/.jpg…) ; calques : le .tif/.psd du dossier
        src = images[0]
        layer_src = next((f for f in images if f.suffix.lower() in A_CALQUES), None)
        ignored = [f.name for f in images if f not in (src, layer_src)]
        if ignored:
            print(f"  ⚠ « {folder.name} » : fichiers ignorés : {', '.join(ignored)}")
        info = read_info(folder, src)
        sha = sha256_file(src)
        id_ = sha[:12]
        lsha = sha256_file(layer_src) if layer_src and layer_src != src else ""
        key = sha + (":" + lsha if lsha else "")
        used_keys.add(key)
        annee = str(info["date"])[:4]
        opened = None

        def layers_of():
            if not layer_src:
                return None
            if layer_src == src:
                return analyse_calques(src, opened[0], opened[3], id_, cfg)
            with Image.open(layer_src) as lim:
                return analyse_calques(layer_src, lim, lim.mode, id_, cfg)

        entry = cache.get(key)
        if args.force or not entry or entry.get("v") != STATS_VERSION:
            print(f"  analyse   {folder.name}" + (f"  (calques : {layer_src.name})" if lsha else ""))
            opened = open_image(src)
            entry = cache[key] = {"v": STATS_VERSION, "stats": analyse(src, *opened, sha)}
            entry["calques"] = layers_of()
        else:
            print(f"  en cache  {folder.name}")
            sprite = (entry.get("calques") or {}).get("sprite")
            if sprite and not (OUT / sprite["url"]).exists():
                opened = open_image(src)
                entry["calques"] = layers_of()
        lay = entry.get("calques")
        if lay:
            if layer_src == src:
                entry["stats"]["fichier"]["poids_calques"] = lay.get("poids_bloc", 0)
            print(f"            {lay['nb_calques']} calque(s), {lay['nb_groupes']} groupe(s) — {lay['source']}")

        web, thumb = OUT / "img" / f"{id_}.webp", OUT / "thumb" / f"{id_}.webp"
        if args.force or not web.exists() or not thumb.exists():
            opened = opened or open_image(src)
            im, _, info_img, _, bands = opened
            has_alpha = "A" in bands or "transparency" in info_img
            make_web_images(im, has_alpha, web, thumb, cfg, info["titre"], annee)
        with Image.open(web) as wi:
            web_size = list(wi.size)

        items.append({
            "id": id_, "dossier": folder.name, "titre": info["titre"],
            "description": info["description"], "tags": info["tags"],
            "categorie": info["categorie"], "date": str(info["date"]),
            "fichier": src.name, "img": f"img/{id_}.webp", "thumb": f"thumb/{id_}.webp",
            "web": web_size, "stats": entry["stats"], "calques": entry.get("calques"),
        })

    portfolio_stats(items)
    items.sort(key=lambda it: it["date"], reverse=True)

    accueil = build_accueil(cfg, args.force)
    data = {
        "accueil": accueil,
        "auteur": cfg["auteur"], "titre_site": cfg.get("titre_site", "Portfolio"),
        "contact": cfg.get("contact", ""), "genere_le": dt.datetime.now().isoformat(timespec="seconds"),
        "dessins": items,
    }
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    keep = {it["id"] for it in items}
    for sub in ("img", "thumb", "calques"):
        for f in (OUT / sub).glob("*.webp"):
            if f.stem not in keep:
                f.unlink()
    cache = {k: v for k, v in cache.items() if k in used_keys}
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    print(f"✓ {len(items)} dessin(s) → docs/data.json")


if __name__ == "__main__":
    main()
