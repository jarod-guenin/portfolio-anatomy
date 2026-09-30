"""
calques.py — Lecture des calques d'un fichier de dessin (sans dépendance lourde).

Formats pris en charge :
  - TIFF multicalque SketchBook (Autodesk / Alias) : un calque par sous-image (SubIFD) ;
  - TIFF « Photoshop » : calques stockés dans le tag 37724 (ImageSourceData), écrit par
    Photoshop, Krita, Affinity, Clip Studio, Photopea… (big et little endian) ;
  - PSD ;
  - TIFF multipage : chaque page est traitée comme un calque ;
  - sinon : une seule image aplatie.

Résultat : liste des calques du haut vers le bas (comme dans le panneau du logiciel),
avec nom, type, niveau d'imbrication, visibilité, opacité, mode de fusion, masque,
écrêtage, verrou, boîte englobante, couverture, couleur moyenne, + vignettes.
"""
from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

BLEND_FR = {
    "norm": "Normal", "diss": "Fondu", "dark": "Obscurcir", "mul ": "Produit", "idiv": "Densité couleur +",
    "lbrn": "Densité linéaire +", "dkCl": "Couleur plus foncée", "lite": "Éclaircir", "scrn": "Superposition",
    "div ": "Densité couleur −", "lddg": "Densité linéaire − (Ajout)", "lgCl": "Couleur plus claire",
    "over": "Incrustation", "sLit": "Lumière tamisée", "hLit": "Lumière crue", "vLit": "Lumière vive",
    "lLit": "Lumière linéaire", "pLit": "Lumière ponctuelle", "hMix": "Mélange maximal", "diff": "Différence",
    "smud": "Exclusion", "fsub": "Soustraction", "fdiv": "Division", "hue ": "Teinte", "sat ": "Saturation",
    "colr": "Couleur", "lum ": "Luminosité", "pass": "Transfert",
}
# Noms utilisés par FireAlpaca / MediBang (FR), plus parlants pour un dessinateur
BLEND_FR["mul "] = "Multiplier"
BLEND_FR["scrn"] = "Écran"
BLEND_FR["over"] = "Incrustation"

ALPHA_MIN = 8       # en dessous (≈ 3 %), un pixel est considéré comme vide (voile de brosse, bruit)
THUMB_BOX = 128     # taille d'une vignette dans le sprite (px)
SPRITE_COLS = 8


# ───────────────────────────── lecture binaire ─────────────────────────────

class Reader:
    def __init__(self, data: bytes, le: bool = False, pos: int = 0):
        self.d, self.le, self.p = data, le, pos

    def _u(self, fmt: str, n: int):
        v = struct.unpack_from(("<" if self.le else ">") + fmt, self.d, self.p)[0]
        self.p += n
        return v

    def u8(self): return self._u("B", 1)
    def u16(self): return self._u("H", 2)
    def i16(self): return self._u("h", 2)
    def u32(self): return self._u("I", 4)
    def i32(self): return self._u("i", 4)
    def u64(self): return self._u("Q", 8)

    def skip(self, n: int):
        self.p += n

    def raw(self, n: int) -> bytes:
        b = self.d[self.p:self.p + n]
        if len(b) < n:
            raise ValueError("données tronquées")
        self.p += n
        return b

    def sig(self) -> str:
        """Code 4 caractères, retourné à l'endroit si le fichier est little endian (ex. 'MIB8' → '8BIM')."""
        b = self.raw(4)
        return (b[::-1] if self.le else b).decode("latin-1")


SIGS = (b"8BIM", b"MIB8", b"8B64", b"46B8")


def align_sig(r: Reader, end: int) -> bool:
    """Saute l'éventuel bourrage (0 à 3 octets) avant la prochaine signature '8BIM'."""
    for k in range(4):
        if r.p + k + 4 <= end and r.d[r.p + k:r.p + k + 4] in SIGS:
            r.p += k
            return True
    return False


# ───────────────────────────── modèle ─────────────────────────────

@dataclass
class Channel:
    id: int
    length: int


@dataclass
class Layer:
    nom: str = ""
    top: int = 0
    left: int = 0
    bottom: int = 0
    right: int = 0
    channels: list = field(default_factory=list)
    blend: str = "norm"
    opacite: int = 255
    ecretage: bool = False
    flags: int = 0
    fond: int | None = None             # opacité de remplissage (iOpa)
    section: int = 0                    # 0 calque, 1/2 groupe ouvert/fermé, 3 fin de groupe
    masque: tuple | None = None         # (top, left, bottom, right, couleur par défaut)
    special: str = ""                   # texte, remplissage, réglage…
    data: dict = field(default_factory=dict)   # canal → bytes compressés

    @property
    def visible(self) -> bool:
        return not (self.flags & 2)

    @property
    def verrou_alpha(self) -> bool:
        return bool(self.flags & 1)

    @property
    def w(self): return max(0, self.right - self.left)

    @property
    def h(self): return max(0, self.bottom - self.top)


SPECIAL_KEYS = {
    "TySh": "texte", "tySh": "texte", "SoCo": "remplissage couleur", "GdFl": "dégradé", "PtFl": "motif",
    "brit": "réglage", "levl": "réglage", "curv": "réglage", "expA": "réglage", "vibA": "réglage",
    "hue ": "réglage", "hue2": "réglage", "blnc": "réglage", "blwh": "réglage", "phfl": "réglage",
    "mixr": "réglage", "clrL": "réglage", "nvrt": "réglage", "post": "réglage", "thrs": "réglage",
    "grdm": "réglage", "selc": "réglage", "SoLd": "objet dynamique", "PlLd": "objet dynamique",
}


# ───────────────────────────── bloc « layer info » ─────────────────────────────

def parse_layer_info(r: Reader, end: int, version: int = 1) -> tuple[list[Layer], bool]:
    """Lit la liste des calques (ordre du fichier : du bas vers le haut) + données des canaux."""
    count = r.i16()
    merged_alpha = count < 0
    count = abs(count)
    layers: list[Layer] = []
    for _ in range(count):
        L = Layer()
        L.top, L.left, L.bottom, L.right = r.i32(), r.i32(), r.i32(), r.i32()
        nch = r.u16()
        for _ in range(nch):
            cid = r.i16()
            clen = r.u64() if version == 2 else r.u32()
            L.channels.append(Channel(cid, clen))
        r.sig()  # '8BIM'
        L.blend = r.sig()
        L.opacite, clip, L.flags, _ = r.u8(), r.u8(), r.u8(), r.u8()
        L.ecretage = clip == 1
        extra = r.u32()
        ext_end = r.p + extra

        mlen = r.u32()
        mend = r.p + mlen
        if mlen >= 20:
            t, l_, b, rr = r.i32(), r.i32(), r.i32(), r.i32()
            color = r.u8()
            L.masque = (t, l_, b, rr, color)
        r.p = mend

        blen = r.u32()
        r.p += blen

        nlen = r.u8()
        name = r.raw(nlen)
        L.nom = name.decode("latin-1", errors="replace")
        pad = (4 - (1 + nlen) % 4) % 4
        r.p += pad

        while r.p + 12 <= ext_end and align_sig(r, ext_end):
            r.sig()
            key = r.sig()
            big = version == 2 and key in ("LMsk", "Lr16", "Lr32", "Layr", "Mt16", "Mt32", "Mtrn", "Alph",
                                           "FMsk", "lnk2", "FEid", "FXid", "PxSD")
            ln = r.u64() if big else r.u32()
            start = r.p
            try:
                if key == "luni":
                    n = Reader(r.d, r.le, start).u32()
                    raw = r.d[start + 4:start + 4 + 2 * n]
                    L.nom = raw.decode("utf-16-le" if r.le else "utf-16-be", errors="replace").rstrip("\x00")
                elif key in ("lsct", "lsdk"):
                    L.section = Reader(r.d, r.le, start).u32()
                    if ln >= 12:
                        rr2 = Reader(r.d, r.le, start + 4)
                        if rr2.sig() == "8BIM":
                            L.blend = rr2.sig()
                elif key == "iOpa":
                    L.fond = r.d[start]
                elif key in SPECIAL_KEYS and not L.special:
                    L.special = SPECIAL_KEYS[key]
            except (struct.error, IndexError):
                pass
            r.p = start + ln
        r.p = ext_end
        layers.append(L)

    # données image des canaux, dans le même ordre
    for L in layers:
        for ch in L.channels:
            L.data[ch.id] = r.d[r.p:r.p + ch.length]
            r.p += ch.length
    return layers, merged_alpha


# ───────────────────────────── décodage des pixels ─────────────────────────────

def decode_channel(blob: bytes, w: int, h: int, depth: int, le: bool, version: int = 1) -> np.ndarray | None:
    if w <= 0 or h <= 0 or len(blob) < 2:
        return None
    comp = struct.unpack_from("<H" if le else ">H", blob, 0)[0]
    body = blob[2:]
    bps = depth // 8
    try:
        if comp == 0:
            raw = body[:w * h * bps]
        elif comp == 1:
            if depth != 8:
                return None
            skip = h * (4 if version == 2 else 2)
            raw = Image.frombytes("L", (w, h), body[skip:], "packbits", "L").tobytes()
        elif comp in (2, 3):
            raw = zlib.decompress(body)
        else:
            return None
    except Exception:
        return None
    if depth == 8:
        a = np.frombuffer(raw, dtype=np.uint8, count=w * h).reshape(h, w)
        if comp == 3:
            a = np.cumsum(a, axis=1, dtype=np.uint8)
        return a
    if depth == 16:
        dt = np.dtype("<u2" if le else ">u2")
        a = np.frombuffer(raw, dtype=dt, count=w * h).reshape(h, w)
        if comp == 3:
            a = np.cumsum(a.astype(np.uint16), axis=1, dtype=np.uint16)
        return (a >> 8).astype(np.uint8)
    if depth == 32:
        if comp == 3:
            return None
        a = np.frombuffer(raw, dtype=np.dtype("<f4" if le else ">f4"), count=w * h).reshape(h, w)
        return (np.clip(a, 0, 1) * 255).astype(np.uint8)
    return None


def layer_rgba(L: Layer, depth: int, le: bool, nb_couleurs: int, version: int = 1) -> np.ndarray | None:
    """Pixels RGBA du calque (taille de sa boîte englobante)."""
    w, h = L.w, L.h
    if not w or not h:
        return None
    chans = {cid: decode_channel(L.data.get(cid, b""), w, h, depth, le, version)
             for cid in [c.id for c in L.channels] if cid >= -1}
    if nb_couleurs == 1:
        g = chans.get(0)
        if g is None:
            return None
        rgb = [g, g, g]
    elif nb_couleurs == 4:
        c, m, y, k = (chans.get(i) for i in range(4))
        if any(x is None for x in (c, m, y, k)):
            return None
        # Photoshop stocke le CMJN inversé (255 = pas d'encre)
        rgb = [(x.astype(np.uint16) * k // 255).astype(np.uint8) for x in (c, m, y)]
    else:
        rgb = [chans.get(i) for i in range(3)]
        if any(x is None for x in rgb):
            return None
    a = chans.get(-1)
    if a is None:
        a = np.full((h, w), 255, np.uint8)
    return np.dstack(rgb + [a])


# ───────────────────────────── sources ─────────────────────────────

def _tiff_tag_bytes(path: Path, tag_id: int) -> tuple[bytes | None, bool]:
    """Lit un tag du premier IFD d'un TIFF (classique ou BigTIFF). Retourne (octets, little_endian)."""
    with open(path, "rb") as f:
        head = f.read(16)
        if head[:2] not in (b"II", b"MM"):
            return None, False
        le = head[:2] == b"II"
        e = "<" if le else ">"
        magic = struct.unpack(e + "H", head[2:4])[0]
        if magic == 42:
            off = struct.unpack(e + "I", head[4:8])[0]
            f.seek(off)
            n = struct.unpack(e + "H", f.read(2))[0]
            ent, fmt = 12, "HHII"
        elif magic == 43:
            off = struct.unpack(e + "Q", head[8:16])[0]
            f.seek(off)
            n = struct.unpack(e + "Q", f.read(8))[0]
            ent, fmt = 20, "HHQQ"
        else:
            return None, le
        table = f.read(n * ent)
        for i in range(n):
            tag, typ, count, val = struct.unpack_from(e + fmt, table, i * ent)
            if tag == tag_id:
                size = count * {1: 1, 2: 1, 7: 1}.get(typ, 1)
                if size <= (4 if magic == 42 else 8):
                    return table[i * ent + ent - (4 if magic == 42 else 8):][:size], le
                f.seek(val)
                return f.read(size), le
    return None, le


def from_tiff_photoshop(path: Path):
    blob, le = _tiff_tag_bytes(path, 37724)
    if not blob:
        return None
    marker = b"Adobe Photoshop Document Data Block\x00"
    if not blob.startswith(marker):
        return None
    pos = len(marker)
    # l'ordre des octets du bloc suit en général celui du TIFF ; on le vérifie sur la signature
    if blob[pos:pos + 4] == b"MIB8":
        le = True
    elif blob[pos:pos + 4] == b"8BIM":
        le = False
    r = Reader(blob, le, pos)
    found = {}
    while r.p + 12 <= len(blob) and align_sig(r, len(blob)):
        r.sig()
        key = r.sig()
        ln = r.u32()
        found[key] = (r.p, ln)
        r.p += ln
    for key, depth in (("Layr", 8), ("Lr16", 16), ("Lr32", 32)):
        if key in found:
            start, ln = found[key]
            if ln < 2:
                continue
            layers, _ = parse_layer_info(Reader(blob, le, start), start + ln)
            if layers:
                return layers, depth, le, 1, len(blob)
    return None


def from_psd(path: Path):
    data = path.read_bytes()
    if data[:4] != b"8BPS":
        return None
    r = Reader(data, False, 4)
    version = r.u16()
    r.p += 6
    nch = r.u16()
    r.u32(), r.u32()
    depth = r.u16()
    mode = r.u16()
    r.skip(r.u32())               # color mode data
    r.skip(r.u32())               # image resources
    lm_len = r.u64() if version == 2 else r.u32()
    lm_end = r.p + lm_len
    if lm_len == 0:
        return [], depth, False, version, 0
    li_len = r.u64() if version == 2 else r.u32()
    li_len += li_len % 2
    li_start = r.p
    layers = []
    if li_len:
        layers, _ = parse_layer_info(r, li_start + li_len, version)
    r.p = li_start + li_len
    if not layers and r.p + 4 <= lm_end:
        # 16/32 bits : les calques sont dans un bloc additionnel Lr16 / Lr32
        r.skip(r.u32())                     # masque global
        while r.p + 12 <= lm_end and align_sig(r, lm_end):
            r.sig()
            key = r.sig()
            ln = r.u64() if version == 2 and key in ("Lr16", "Lr32", "Layr") else r.u32()
            if key in ("Lr16", "Lr32", "Layr") and ln > 2:
                layers, _ = parse_layer_info(Reader(data, False, r.p), r.p + ln, version)
                break
            r.p += ln
    nb = {1: 1, 3: 3, 4: 4}.get(mode, 3)
    return layers, depth, False, version, lm_len, nb


# ───────────────────────────── TIFF à sous-images (SketchBook) ─────────────────────────────

_TSIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
_TFMT = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d", 13: "I", 16: "Q", 17: "q", 18: "Q"}
# modes de fusion SketchBook : seuls ceux vérifiés sur un vrai fichier sont nommés
SKETCHBOOK_BLEND = {0: "Normal", 1: "Multiplier"}


def _read_ifd(f, off: int, le: bool, big: bool) -> dict:
    e = "<" if le else ">"
    f.seek(off)
    n = struct.unpack(e + ("Q" if big else "H"), f.read(8 if big else 2))[0]
    ent, inline, voff = (20, 8, 12) if big else (12, 4, 8)
    table = f.read(n * ent)
    tags = {}
    for i in range(n):
        b = i * ent
        tag, typ = struct.unpack_from(e + "HH", table, b)
        count = struct.unpack_from(e + ("Q" if big else "I"), table, b + 4)[0]
        size = _TSIZE.get(typ, 1) * count
        if size <= inline:
            raw = table[b + voff:b + voff + size]
        else:
            ptr = struct.unpack_from(e + ("Q" if big else "I"), table, b + voff)[0]
            pos = f.tell()
            f.seek(ptr)
            raw = f.read(size)
            f.seek(pos)
        if typ == 2:
            val = raw.split(b"\0")[0].decode("latin-1")
        elif typ in (1, 7):
            val = raw
        elif typ in (5, 10):
            v = struct.unpack(e + ("I" if typ == 5 else "i") * (2 * count), raw)
            val = [v[k] / v[k + 1] if v[k + 1] else 0.0 for k in range(0, len(v), 2)]
        elif typ in _TFMT:
            val = list(struct.unpack(e + _TFMT[typ] * count, raw))
        else:
            val = raw
        tags[tag] = val
    return tags


def _decode_ifd(path: Path, tags: dict) -> np.ndarray:
    """Recopie une sous-image dans un petit TIFF autonome, décodé par Pillow (LZW, ZIP, prédicteur…)."""
    w, h = tags[256][0], tags[257][0]
    with open(path, "rb") as f:
        strips = []
        for o, c in zip(tags[273], tags[279]):
            f.seek(o)
            strips.append(f.read(c))
    keep = {256: (4, [w]), 257: (4, [h]), 258: (3, tags.get(258, [8])), 259: (3, tags.get(259, [1])),
            262: (3, tags.get(262, [2])), 277: (3, tags.get(277, [1])), 278: (4, tags.get(278, [h])),
            284: (3, tags.get(284, [1])), 317: (3, tags.get(317, [1]))}
    entries = sorted(list(keep) + [273, 279])
    start = 8 + 2 + len(entries) * 12 + 4
    extra = bytearray()
    ext_off = {}
    for k, (typ, vals) in keep.items():
        if _TSIZE[typ] * len(vals) > 4:
            ext_off[k] = start + len(extra)
            extra += struct.pack("<" + _TFMT[typ] * len(vals), *vals)
    so_off = start + len(extra)
    extra += b"\0" * (4 * len(strips))
    sc_off = start + len(extra)
    extra += struct.pack("<" + "I" * len(strips), *[len(x) for x in strips])
    pix = start + len(extra)
    pos, cur = [], pix
    for x in strips:
        pos.append(cur)
        cur += len(x)
    struct.pack_into("<" + "I" * len(strips), extra, so_off - start, *pos)
    out = bytearray(b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", len(entries)))
    for k in entries:
        if k == 273:
            out += struct.pack("<HHII", 273, 4, len(strips), pos[0] if len(strips) == 1 else so_off)
        elif k == 279:
            out += struct.pack("<HHII", 279, 4, len(strips), len(strips[0]) if len(strips) == 1 else sc_off)
        else:
            typ, vals = keep[k]
            if k in ext_off:
                out += struct.pack("<HHII", k, typ, len(vals), ext_off[k])
            else:
                out += struct.pack("<HHI", k, typ, len(vals)) + struct.pack("<" + _TFMT[typ] * len(vals), *vals).ljust(4, b"\0")
    out += b"\0\0\0\0" + extra + b"".join(strips)
    im = Image.open(io.BytesIO(bytes(out)))
    im.load()
    if im.mode not in ("RGBA", "RGB", "L", "LA"):
        im = im.convert("RGBA")
    a = np.asarray(im)
    if a.ndim == 2:
        a = np.dstack([a, a, a, np.full(a.shape, 255, np.uint8)])
    elif a.shape[2] == 2:
        a = np.dstack([a[..., 0], a[..., 0], a[..., 0], a[..., 1]])
    elif a.shape[2] == 3:
        a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
    return a


def from_tiff_subifd(path: Path, canvas):
    """TIFF où chaque calque est une sous-image (tag 330) — format « Alias MultiLayer TIFF » de SketchBook."""
    with open(path, "rb") as f:
        head = f.read(16)
        if head[:2] not in (b"II", b"MM"):
            return None
        le = head[:2] == b"II"
        e = "<" if le else ">"
        big = struct.unpack(e + "H", head[2:4])[0] == 43
        off = struct.unpack(e + "Q", head[8:16])[0] if big else struct.unpack(e + "I", head[4:8])[0]
        main = _read_ifd(f, off, le, big)
        subs = main.get(330)
        if not subs:
            return None
        software = str(main.get(305, "")) + " " + str(main.get(50790, ""))
        sketchbook = "Alias" in software or "Sketchbook" in software or 50784 in main
        W, H = canvas
        raw_layers = []
        for so in subs:
            tg = _read_ifd(f, so, le, big)
            name = tg.get(285, "")
            if (tg.get(254, [0])[0] & 1) or name == "Thumbnail" or 273 not in tg:
                continue          # vignettes : on ne garde que les vrais calques
            raw_layers.append(tg)
    if not raw_layers:
        return None

    items, thumbs = [], []
    for tg in raw_layers:           # ordre du fichier : du bas vers le haut
        a = _decode_ifd(path, tg)
        orient = tg.get(274, [1])[0]
        if orient in (4, 3):
            a = a[::-1]
        if orient in (2, 3):
            a = a[:, ::-1]
        if sketchbook:
            a = a[..., [2, 1, 0, 3]]           # SketchBook range les calques en BGR
        a = np.ascontiguousarray(a)
        h, w = a.shape[:2]
        xres = tg.get(282, [1.0])[0] or 1.0
        yres = tg.get(283, [1.0])[0] or 1.0
        x = round(tg.get(286, [0.0])[0] * (xres if 282 in tg else 1))
        y = round(tg.get(287, [0.0])[0] * (yres if 283 in tg else 1))
        top = H - y - h if orient in (4, 3) else y      # origine en bas pour les images « bas-gauche »
        name = tg.get(285, "")
        u = tg.get(50788)
        if isinstance(u, (bytes, bytearray)):
            name = u.split(b"\0")[0].decode("utf-8", errors="replace") or name
        elif isinstance(u, list):
            name = bytes(u).split(b"\0")[0].decode("utf-8", errors="replace") or name
        opacite, visible, fusion = 100, True, "Normal"
        md = tg.get(50784)
        if isinstance(md, str):
            parts = [p.strip() for p in md.split(",")]
            try:
                opacite = round(float(parts[0]) * 100)
                visible = parts[2] != "0"
                mode = int(parts[7])
                fusion = SKETCHBOOK_BLEND.get(mode, f"Mode n°{mode}")
            except (ValueError, IndexError):
                pass
        items.append(describe(name, "calque", 0, visible, opacite, fusion, False, False, None,
                              (x, top, x + w, top + h), a, canvas, ""))
        items[-1]["visible_effectif"] = visible
        thumbs.append(make_thumb(a, x, top, canvas))
    items.reverse()
    thumbs.reverse()
    src = "SketchBook (TIFF multicalque)" if sketchbook else "TIFF à sous-images (SubIFD)"
    soft = main.get(50790) or main.get(305)
    info = summary(items, src, 0)
    if soft:
        info["logiciel"] = str(soft).replace("V1_Windows_", "").replace("_", " ")
    return info, items, thumbs


# ───────────────────────────── API principale ─────────────────────────────

BLEND_KEYS = set(BLEND_FR)


def _flatten_tree(layers: list[Layer]) -> list[tuple[Layer, int]]:
    """Ordre du haut vers le bas avec niveau d'imbrication ; retire les marqueurs de fin de groupe."""
    out, depth = [], 0
    for L in reversed(layers):
        if L.section == 3:
            depth = max(0, depth - 1)
            continue
        out.append((L, depth))
        if L.section in (1, 2):
            depth += 1
    return out


def read_layers(path: Path, canvas: tuple[int, int], mode: str = "RGB"):
    """Retourne (infos, liste de calques, vignettes RGBA) ; None si aucun calque exploitable."""
    ext = path.suffix.lower()
    res = None
    try:
        if ext in (".tif", ".tiff"):
            res = from_tiff_photoshop(path)
            src = "Photoshop (tag 37724)"
        elif ext == ".psd":
            res = from_psd(path)
            src = "PSD"
    except Exception as exc:          # fichier inattendu : on n'empêche pas le reste de l'analyse
        print(f"    ⚠ calques illisibles ({exc})")
        res = None

    if res:
        layers, depth, le, version, block_size, *rest = res
        nb = rest[0] if rest else ({"L": 1, "LA": 1, "CMYK": 4}.get(mode, 3))
        return build(layers, depth, le, version, nb, canvas, src, block_size)

    if ext in (".tif", ".tiff"):
        try:
            sub = from_tiff_subifd(path, canvas)
        except Exception as exc:
            print(f"    ⚠ sous-images illisibles ({exc})")
            sub = None
        return sub or from_pages(path, canvas)
    return None


def from_pages(path: Path, canvas):
    try:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            if n < 2:
                return None
            items, thumbs = [], []
            for i in range(n):
                im.seek(i)
                page = im.convert("RGBA")
                arr = np.asarray(page)
                items.append(describe(f"Page {i + 1}", "calque", 0, True, 100, "Normal", False, False, None,
                                      (0, 0, page.width, page.height), arr, canvas, ""))
                thumbs.append(make_thumb(arr, 0, 0, canvas))
    except Exception:
        return None
    items.reverse()
    thumbs.reverse()
    return summary(items, "TIFF multipage (1 page = 1 calque)", 0), items, thumbs


def describe(nom, typ, niveau, visible, opacite, fusion, ecretage, verrou, masque, bbox, arr, canvas, special,
             fond=None, enfants=0):
    W, H = canvas
    d = {"nom": nom or "(sans nom)", "type": typ, "niveau": niveau, "visible": visible, "opacite": opacite,
         "fusion": fusion, "ecretage": ecretage, "verrou": verrou, "masque": masque}
    if fond is not None and fond != 100:
        d["fond"] = fond
    if special:
        d["special"] = special
    if typ == "groupe":
        d["enfants"] = enfants
        return d
    l, t, r, b = bbox
    d["bbox"] = [l, t, r, b]
    d["taille"] = [max(0, r - l), max(0, b - t)]
    if arr is not None:
        a = np.where(arr[..., 3] >= ALPHA_MIN, arr[..., 3], 0)
        # partie du calque réellement dans le canevas
        x0, y0, x1, y1 = max(0, -l), max(0, -t), min(arr.shape[1], W - l), min(arr.shape[0], H - t)
        inside = a[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else a[:0, :0]
        px = int((inside > 0).sum())
        d["pixels"] = px
        d["couverture"] = round(px / (W * H), 5) if W * H else 0
        if px:
            wgt = a.astype(np.float64)
            mean = (arr[..., :3].astype(np.float64) * wgt[..., None]).sum(axis=(0, 1)) / wgt.sum()
            d["couleur"] = "#{:02X}{:02X}{:02X}".format(*[int(round(v)) for v in mean])
            ys, xs = np.nonzero(a)
            d["contenu"] = [int(l + xs.min()), int(t + ys.min()), int(l + xs.max() + 1), int(t + ys.max() + 1)]
        else:
            d["vide"] = True
    return d


def make_thumb(arr: np.ndarray | None, left: int, top: int, canvas) -> Image.Image:
    """Vignette recadrée sur le contenu du calque, centrée dans une case au format de la toile."""
    W, H = canvas
    s = THUMB_BOX / max(W, H)
    tw, th = max(1, round(W * s)), max(1, round(H * s))
    frame = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    if arr is None or not arr.size:
        return frame
    ys, xs = np.nonzero(arr[..., 3] >= ALPHA_MIN)
    if not len(xs):
        return frame
    crop = arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    im = Image.fromarray(np.ascontiguousarray(crop), "RGBA")
    pad = 4
    k = min((tw - 2 * pad) / im.width, (th - 2 * pad) / im.height, 1.0)
    nw, nh = max(1, round(im.width * k)), max(1, round(im.height * k))
    im = im.resize((nw, nh), Image.LANCZOS)
    if k < 0.25:
        # à petite échelle les traits fins s'effacent : on renforce l'alpha
        a = np.asarray(im.getchannel("A")).astype(np.float32)
        im.putalpha(Image.fromarray(np.clip(a * 1.8, 0, 255).astype(np.uint8)))
    frame.alpha_composite(im, ((tw - nw) // 2, (th - nh) // 2))
    return frame


def build(layers, depth, le, version, nb, canvas, src, block_size):
    items, thumbs = [], []
    tree = _flatten_tree(layers)
    # nombre d'enfants directs de chaque groupe
    for i, (L, lvl) in enumerate(tree):
        typ = "groupe" if L.section in (1, 2) else "calque"
        fusion = BLEND_FR.get(L.blend, L.blend.strip())
        masque = None
        if L.masque:
            masque = True
        enfants = 0
        if typ == "groupe":
            for L2, lvl2 in tree[i + 1:]:
                if lvl2 <= lvl:
                    break
                if lvl2 == lvl + 1:
                    enfants += 1
        arr = None
        if typ == "calque":
            arr = layer_rgba(L, depth, le, nb, version)
        items.append(describe(L.nom, typ, lvl, L.visible, round(L.opacite / 255 * 100), fusion, L.ecretage,
                              L.verrou_alpha, masque, (L.left, L.top, L.right, L.bottom), arr, canvas, L.special,
                              round(L.fond / 255 * 100) if L.fond is not None else None, enfants))
        thumbs.append(None if typ == "groupe" else make_thumb(arr, L.left, L.top, canvas))
    # visibilité effective : un calque dans un groupe masqué est masqué aussi
    hidden_stack: list[int] = []
    for it in items:
        while hidden_stack and it["niveau"] <= hidden_stack[-1]:
            hidden_stack.pop()
        it["visible_effectif"] = it["visible"] and not hidden_stack
        if it["type"] == "groupe" and not it["visible"]:
            hidden_stack.append(it["niveau"])
    return summary(items, src, block_size, depth), items, thumbs


def summary(items, src, block_size, depth=8):
    calques = [i for i in items if i["type"] == "calque"]
    modes: dict[str, int] = {}
    for i in calques:
        modes[i["fusion"]] = modes.get(i["fusion"], 0) + 1
    top = max(calques, key=lambda i: i.get("couverture", 0), default=None)
    return {
        "source": src,
        "profondeur": depth,
        "poids_bloc": block_size,
        "nb_calques": len(calques),
        "nb_groupes": sum(1 for i in items if i["type"] == "groupe"),
        "nb_masques": sum(1 for i in calques if not i["visible_effectif" if "visible_effectif" in i else "visible"]),
        "nb_vides": sum(1 for i in calques if i.get("vide")),
        "nb_ecretage": sum(1 for i in calques if i["ecretage"]),
        "nb_opacite_reduite": sum(1 for i in calques if i["opacite"] < 100),
        "nb_avec_masque": sum(1 for i in calques if i.get("masque")),
        "profondeur_max": max((i["niveau"] for i in items), default=0),
        "modes": dict(sorted(modes.items(), key=lambda x: -x[1])),
        "plus_couvrant": top["nom"] if top and top.get("couverture") else None,
        "couverture_moy": round(float(np.mean([i.get("couverture", 0) for i in calques])), 5) if calques else 0,
    }


def save_sprite(thumbs: list, dest: Path) -> dict | None:
    """Assemble les vignettes en une seule image (grille) pour limiter le nombre de fichiers."""
    ok = [t for t in thumbs if t is not None]
    if not ok:
        return None
    tw, th = ok[0].size
    n = len(thumbs)
    cols = min(SPRITE_COLS, n)
    rows = -(-n // cols)
    sheet = Image.new("RGBA", (cols * tw, rows * th), (0, 0, 0, 0))
    for i, t in enumerate(thumbs):
        if t is not None:
            sheet.paste(t, ((i % cols) * tw, (i // cols) * th))
    sheet.save(dest, "WEBP", quality=80, method=6)
    return {"l": tw, "h": th, "cols": cols, "rows": rows}


def inspect(path: Path):
    """Affiche ce qui est détecté dans un fichier (pour le diagnostic)."""
    with Image.open(path) as im:
        canvas, mode = im.size, im.mode
        print(f"{path.name} : {im.format} {canvas[0]}×{canvas[1]} {mode}, {getattr(im, 'n_frames', 1)} page(s)")
        if im.format == "TIFF":
            tags = sorted(im.tag_v2.keys())
            print(f"  tags TIFF : {tags}")
            print(f"  compression : {im.info.get('compression')}")
    res = read_layers(path, canvas, mode)
    if not res:
        print("  → aucun calque trouvé : le fichier est aplati (une seule image).")
        return
    info, items, _ = res
    print(f"  → {info['nb_calques']} calque(s), {info['nb_groupes']} groupe(s) — source : {info['source']}"
          + (f" ({info['logiciel']})" if info.get("logiciel") else ""))
    for it in items:
        eye = "👁" if it["visible"] else "  "
        extra = []
        if it["fusion"] != "Normal":
            extra.append(it["fusion"])
        if it["opacite"] < 100:
            extra.append(f"{it['opacite']} %")
        if it.get("ecretage"):
            extra.append("écrêtage")
        if it.get("vide"):
            extra.append("vide")
        pre = "  " * it["niveau"] + ("📁 " if it["type"] == "groupe" else "")
        print(f"   {eye} {pre}{it['nom']}" + (f"  [{', '.join(extra)}]" if extra else ""))
