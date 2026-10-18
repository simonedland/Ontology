"""Extractors: turn raw media into graph nodes, each as a logged prov:Activity.

Every extractor degrades gracefully: it uses the best open-source tool that is
installed (pdqhash, pypdf, faster-whisper, rapidocr, phonenumbers) and falls
back to a pure-Python/Pillow implementation otherwise.  The ModelAgent node
records which tool actually ran, its version and licences, so the provenance
is honest about what produced each fact.

Deliberately absent: face recognition / clustering, speaker matching, the
sticker-set-ID creator heuristic (GDPR Art. 9, EU AI Act Art. 5).
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import identifiers as ids
from . import keys as K
from . import media as M
from .graph import Graph

# --------------------------------------------------------------------- hashing


def sha256_file(path: str | Path) -> str:
    """Streaming sha256 of a file (hex)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class PHash:
    """A perceptual hash with its algorithm, bit width, quality and near-duplicate threshold."""
    algo: str      # pdq | phash64 | dhash64
    hex: str
    bits: int
    quality: int | None
    threshold: int  # max Hamming distance treated as near-duplicate


def perceptual_hash(path: str | Path) -> PHash | None:
    """PDQ when ``pdqhash`` is installed, else DCT pHash (numpy) or dHash."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(path)
        img.seek(0)
        img = img.convert("RGB")
    except Exception:
        return None
    try:
        import numpy as np
        import pdqhash  # type: ignore

        vec, quality = pdqhash.compute(np.asarray(img))
        bits = "".join("1" if b else "0" for b in vec)
        return PHash("pdq", f"{int(bits, 2):064x}", 256, int(quality), 31)
    except ImportError:
        pass
    gray = img.convert("L")
    try:
        import numpy as np

        a = np.asarray(gray.resize((32, 32), Image.LANCZOS), dtype=float)
        n = 32
        k = np.arange(n)
        dct = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
        coeffs = dct @ a @ dct.T
        low = coeffs[:8, :8].flatten()
        med = np.median(low[1:])
        bits = "".join("1" if v > med else "0" for v in low)
        return PHash("phash64", f"{int(bits, 2):016x}", 64, None, 10)
    except ImportError:
        px = list(gray.resize((9, 8), Image.LANCZOS).getdata())
        bits = "".join("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
        return PHash("dhash64", f"{int(bits, 2):016x}", 64, None, 10)


def hamming(a: str, b: str) -> int:
    """Hamming distance between two equal-length hex hashes."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def near_pairs(hashes: dict[str, PHash]):
    """Candidate pairs via multi-index hashing: split each hash into threshold+1 bands;
    by pigeonhole any pair within the threshold shares at least one band exactly.
    Avoids O(n^2) at archive scale (production: Faiss IndexBinaryFlat / range search)."""
    from collections import defaultdict

    by_algo: dict[str, list[str]] = defaultdict(list)
    for k, h in hashes.items():
        by_algo[h.algo].append(k)
    seen = set()
    for algo, ks in by_algo.items():
        h0 = hashes[ks[0]]
        nb = h0.threshold + 1
        bounds = [round(i * h0.bits / nb) for i in range(nb + 1)]
        buckets: dict[tuple, list[str]] = defaultdict(list)
        for k in sorted(ks):
            v = int(hashes[k].hex, 16)
            for bi in range(nb):
                lo, hi = bounds[bi], bounds[bi + 1]
                buckets[(bi, (v >> lo) & ((1 << (hi - lo)) - 1))].append(k)
        for members in buckets.values():
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if (a, b) not in seen:
                        seen.add((a, b))
                        yield a, b


# ------------------------------------------------------------------------ EXIF
_GPS = 0x8825
_EXIF = 0x8769


def _ratio(v) -> float:
    try:
        return float(v)
    except TypeError:
        return v[0] / v[1]


def exif_extract(path: str | Path) -> dict:
    """Read EXIF from a byte-preserved (document-mode) image."""
    try:
        from PIL import Image
    except ImportError:
        return {}
    try:
        ex = Image.open(path).getexif()
    except Exception:
        return {}
    out = {}
    for tag, name in ((0x010F, "make"), (0x0110, "model"), (0x0131, "software"), (0x0132, "dateTime")):
        if ex.get(tag):
            out[name] = str(ex.get(tag)).strip("\x00 ")
    sub = ex.get_ifd(_EXIF)
    for tag, name in ((0xA431, "bodySerial"), (0x9003, "dateTimeOriginal"), (0xA434, "lensModel")):
        if sub.get(tag):
            out[name] = str(sub.get(tag)).strip("\x00 ")
    gps = ex.get_ifd(_GPS)
    if gps.get(2) and gps.get(4):
        def deg(t):
            d, m, s = (_ratio(x) for x in t)
            return d + m / 60 + s / 3600
        lat, lon = deg(gps[2]), deg(gps[4])
        if gps.get(1) in ("S", b"S"):
            lat = -lat
        if gps.get(3) in ("W", b"W"):
            lon = -lon
        out["gps"] = (round(lat, 6), round(lon, 6))
    return out


# ------------------------------------------------------------- documents/PDF
def pdf_extract(path: str | Path) -> dict:
    """Text + metadata.  pypdf if installed, else a minimal parser for simple PDFs."""
    data = Path(path).read_bytes()
    out: dict = {"meta": {}, "text": "", "tool": "tgo-minipdf", "version": "0.1"}
    m = re.search(rb"xmpMM:DocumentID(?:>|=\")([^<\"]+)", data)
    if m:
        out["meta"]["xmpDocumentID"] = m.group(1).decode("latin-1")
    if b"/Encrypt" in data:
        out["encrypted"] = True
        return out
    try:
        import pypdf  # type: ignore

        r = pypdf.PdfReader(str(path))
        info = r.metadata or {}
        for k in ("/Author", "/Producer", "/Creator", "/Title", "/CreationDate"):
            if info.get(k):
                out["meta"][k.strip("/").lower()] = str(info.get(k))
        out["text"] = "\n".join((p.extract_text() or "") for p in r.pages)
        out["tool"], out["version"] = "pypdf", pypdf.__version__
        return out
    except ImportError:
        pass
    for k in ("Author", "Producer", "Creator", "Title", "CreationDate"):
        mm = re.search(rb"/" + k.encode() + rb"\s*\(((?:\\.|[^\\)])*)\)", data)
        if mm:
            out["meta"][k.lower()] = mm.group(1).decode("latin-1")
    texts = []
    for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        body = sm.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        for tm in re.finditer(rb"\(((?:\\.|[^\\)])*)\)\s*Tj|\[(.*?)\]\s*TJ", body, re.S):
            if tm.group(1) is not None:
                texts.append(tm.group(1))
            else:
                texts.append(b"".join(re.findall(rb"\(((?:\\.|[^\\)])*)\)", tm.group(2))))
    out["text"] = "\n".join(t.decode("latin-1").replace("\\(", "(").replace("\\)", ")") for t in texts)
    return out


def office_extract(path: str | Path) -> dict:
    """OOXML (docx/xlsx/pptx): docProps are file-declared metadata."""
    out: dict = {"meta": {}, "text": "", "tool": "tgo-ooxml", "version": "0.1"}
    try:
        with zipfile.ZipFile(path) as z:
            if any(i.flag_bits & 0x1 for i in z.infolist()):
                out["encrypted"] = True  # recorded, never opened (no password guessing)
                return out
            names = set(z.namelist())
            if "docProps/core.xml" in names:
                core = z.read("docProps/core.xml").decode("utf-8", "replace")
                for tag, name in (("dc:creator", "author"), ("cp:lastModifiedBy", "lastModifiedBy"),
                                  ("dcterms:created", "created"), ("dc:title", "title")):
                    mm = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", core, re.S)
                    if mm:
                        out["meta"][name] = mm.group(1)
            if "docProps/app.xml" in names:
                mm = re.search(r"<Application>(.*?)</Application>", z.read("docProps/app.xml").decode("utf-8", "replace"))
                if mm:
                    out["meta"]["producer"] = mm.group(1)
            body = [n for n in names if re.match(r"(word/document|xl/sharedStrings|ppt/slides/slide\d+)\.xml$", n)]
            out["text"] = "\n".join(re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "replace")) for n in sorted(body))
    except zipfile.BadZipFile:
        pass
    return out


# ------------------------------------------------------------------ ASR / OCR
def transcribe(path: str | Path) -> dict | None:
    """faster-whisper if installed *and* a local model dir is configured, else a sidecar.

    Offline by design: the model is loaded from ``TGO_WHISPER_MODEL`` (a local
    CTranslate2 directory, e.g. large-v3-turbo); nothing is downloaded at runtime.
    """
    import os

    model_dir = os.environ.get("TGO_WHISPER_MODEL")
    try:
        # A non-dir value would be read as an HF repo id, and a dir without tokenizer.json
        # makes faster-whisper fetch openai/whisper-tiny's tokenizer: both hit the Hub.
        if not model_dir or not (Path(model_dir) / "tokenizer.json").is_file():
            raise ImportError("no complete local whisper model configured")
        from faster_whisper import WhisperModel  # type: ignore
        import faster_whisper

        model = WhisperModel(model_dir, compute_type="int8", local_files_only=True)
        segs, info = model.transcribe(str(path), vad_filter=True)
        return {"language": info.language, "language_probability": info.language_probability,
                "segments": [{"start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob,
                              "no_speech_prob": s.no_speech_prob} for s in segs],
                "tool": "faster-whisper", "version": faster_whisper.__version__, "model": Path(model_dir).name,
                "codeLicense": "MIT", "weightsLicense": "MIT"}
    except ImportError:
        side = Path(str(path) + ".transcript.json")
        if side.exists():
            d = json.loads(side.read_text(encoding="utf-8"))
            d.update(tool="sidecar-transcript", version="demo", model="stand-in for faster-whisper",
                     codeLicense="n/a", weightsLicense="n/a")
            return d
    return None


def _ocr_model_paths() -> dict | None:
    """Explicit PP-OCR model files from ``TGO_OCR_MODELS`` (``rapidocr download_models`` output)."""
    import os

    d = os.environ.get("TGO_OCR_MODELS")
    if not d or not Path(d).is_dir():
        return None
    onnx = sorted(Path(d).rglob("*.onnx"))
    pick = {k: next((str(p) for p in onnx if k in p.name.lower()), None) for k in ("det", "cls", "rec")}
    if not all(pick.values()):
        return None
    params = {"Det.model_path": pick["det"], "Cls.model_path": pick["cls"], "Rec.model_path": pick["rec"]}
    keys = next((p for p in sorted(Path(d).rglob("*.txt")) if "key" in p.name.lower() or "dict" in p.name.lower()), None)
    if keys:
        params["Rec.rec_keys_path"] = str(keys)
    return params


def ocr(path: str | Path) -> dict | None:
    """RapidOCR (PP-OCR, Apache-2.0) or Tesseract if installed, else ``.ocr.txt`` sidecar.

    Offline by design: ``rapidocr`` >= 3 fetches models from a CDN unless given
    explicit paths, so it only runs when ``TGO_OCR_MODELS`` holds them; the legacy
    ``rapidocr_onnxruntime`` wheel bundles its models.
    """
    params = _ocr_model_paths()
    if params:
        try:
            from rapidocr import RapidOCR  # type: ignore
            import rapidocr

            res = RapidOCR(params=params)(str(path))
            return {"text": "\n".join(res.txts or ()), "tool": "rapidocr", "version": rapidocr.__version__,
                    "codeLicense": "Apache-2.0", "weightsLicense": "Apache-2.0"}
        except Exception:
            pass
    try:
        from rapidocr_onnxruntime import RapidOCR as BundledRapidOCR  # type: ignore

        res, _ = BundledRapidOCR()(str(path))
        return {"text": "\n".join(r[1] for r in res or []), "tool": "rapidocr", "version": "onnx",
                "codeLicense": "Apache-2.0", "weightsLicense": "Apache-2.0"}
    except Exception:
        pass
    try:
        import pytesseract  # type: ignore
        from PIL import Image

        return {"text": pytesseract.image_to_string(Image.open(path)), "tool": "tesseract",
                "version": str(pytesseract.get_tesseract_version()), "codeLicense": "Apache-2.0",
                "weightsLicense": "Apache-2.0"}
    except Exception:
        pass
    side = Path(str(path) + ".ocr.txt")
    if side.exists():
        return {"text": side.read_text(encoding="utf-8"), "tool": "sidecar-ocr", "version": "demo",
                "codeLicense": "n/a", "weightsLicense": "n/a"}
    return None


# Tiny demo gazetteer; production uses spaCy / Stanza / GLiNER (see report).
GAZETTEER = {
    "chisinau": "LOC", "chișinău": "LOC", "balti": "LOC", "bălți": "LOC", "cahul": "LOC",
    "tiraspol": "LOC", "comrat": "LOC", "odesa": "LOC", "kyiv": "LOC", "moscow": "LOC",
    "oslo": "LOC", "bergen": "LOC", "tromsø": "LOC", "kirkenes": "LOC", "riga": "LOC",
}


def load_gazetteer(path) -> dict[str, str]:
    """``{"surface form": "LOC|ORG|PER"}`` from JSON, lower-cased; the demo GAZETTEER if ``path`` is None."""
    if not path:
        return GAZETTEER
    return {k.lower(): v for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}


def entities(text: str, gazetteer: dict[str, str] | None = None) -> list[tuple[str, str, int, int]]:
    """Named entities as ``(surface, label, start, end)``: spaCy if a model is installed, else gazetteer."""
    try:
        import spacy  # type: ignore

        nlp = spacy.load("xx_ent_wiki_sm")
        return [(e.text, e.label_, e.start_char, e.end_char) for e in nlp(text).ents]
    except Exception:
        out = []
        for m in re.finditer(r"[\wÀ-ɏ]+", text):
            lab = (gazetteer or GAZETTEER).get(m.group(0).lower())
            if lab:
                out.append((m.group(0), lab, m.start(), m.end()))
        return out


# =========================================================== graph application
def link_identifiers(g: Graph, source_key: str, text: str, *, evidence: str, base_status: str = "inferred") -> int:
    """Write CONTAINS_IDENTIFIER edges (TextPositionSelector offsets) for ``text``."""
    n = 0
    for f in ids.extract(text):
        ikey = K.identifier(f.kind, f.value)
        props = {"value": f.value, "kind": f.kind}
        if f.extra:
            props["detail"] = list(f.extra)
        g.node(ikey, f.label, **props)
        if f.label == "Url" and f.extra:
            dkey = K.identifier("domain", f.extra[0])
            g.node(dkey, "Domain", value=f.extra[0], kind="domain")
            g.edge("CONTAINS_IDENTIFIER", source_key, dkey, status=base_status, conf=f.conf,
                   method="url-canonicalize", evidence=[evidence])
        g.edge("CONTAINS_IDENTIFIER", source_key, ikey, status=base_status, conf=f.conf,
               method=f"regex+{f.validation}", evidence=[evidence], validation=f.validation,
               selector=f"TextPositionSelector:{f.start}-{f.end}", raw=f.raw)
        n += 1
    return n


def _media_nodes(g: Graph):
    for n in list(g.nodes.values()):
        if n.props.get("localPath") and ("Media" in n.labels):
            yield n


def run_all(g: Graph, root: Path, gazetteer: dict[str, str] | None = None,
            phash_threshold: int | None = None) -> dict:
    """Run every extractor over every downloaded media node (and all message text) in ``g``.

    ``gazetteer`` replaces the demo place list; ``phash_threshold`` overrides the near-duplicate
    Hamming threshold.  Returns per-extractor counts.
    """
    counts = {"phash": 0, "exif": 0, "documents": 0, "transcripts": 0, "ocr": 0, "identifiers": 0}
    media = list(_media_nodes(g))
    images = [n for n in media if n.props.get("mime", "").startswith("image/") or "Photo" in n.labels
              or "Sticker" in n.labels]

    # 1. Perceptual fingerprints + near-duplicates --------------------------
    hashes: dict[str, PHash] = {}
    for n in images:
        ph = perceptual_hash(root / n.props["localPath"])
        if ph and phash_threshold is not None:
            ph.threshold = phash_threshold
        if ph:
            hashes[n.key] = ph
    if hashes:
        algo = next(iter(hashes.values())).algo
        agent = {"tool": "pdqhash" if algo == "pdq" else f"tgo-{algo}", "version": "0.2.8" if algo == "pdq" else "0.1",
                 "codeLicense": "MIT/BSD" if algo == "pdq" else "MIT", "weightsLicense": "n/a"}
        with g.activity("DetectionRun", f"phash:{algo}", agent=agent, threshold=next(iter(hashes.values())).threshold):
            fp_degree: dict[str, int] = {}
            for key, ph in hashes.items():
                fk = K.fingerprint(ph.algo, ph.hex)
                g.node(fk, "MediaFingerprint", algorithm=ph.algo, hashHex=ph.hex, quality=ph.quality)
                g.edge("FINGERPRINT_OF", fk, key, method=ph.algo)
                fp_degree[fk] = fp_degree.get(fk, 0) + 1
                counts["phash"] += 1
            for a, b in near_pairs(hashes):
                    ha, hb = hashes[a], hashes[b]
                    d = hamming(ha.hex, hb.hex)
                    # d == 0 is already expressed by the shared MediaFingerprint node
                    if 0 < d <= ha.threshold:
                        conf = round(1.0 - 0.5 * d / max(ha.threshold, 1), 3)
                        g.edge("NEAR_DUPLICATE_OF", a, b, conf=conf, method=ha.algo,
                               evidence=[K.fingerprint(ha.algo, ha.hex), K.fingerprint(hb.algo, hb.hex)],
                               metric="hamming", distance=d, threshold=ha.threshold)

    # 2. EXIF on document-mode images only ----------------------------------
    doc_images = [n for n in images if n.props.get("sendMode") == M.DOCUMENT_MODE]
    if doc_images:
        with g.activity("DetectionRun", "exif", agent={"tool": "pillow-exif", "version": _pil_version(),
                                                       "codeLicense": "MIT-CMU", "weightsLicense": "n/a"}):
            for n in doc_images:
                ex = exif_extract(root / n.props["localPath"])
                if not ex:
                    continue
                counts["exif"] += 1
                n.props.update({f"exif_{k}": v for k, v in ex.items() if k != "gps"})
                if ex.get("bodySerial"):
                    dk = K.device(ex["bodySerial"], ex.get("make", ""))
                    g.node(dk, "Device", bodySerial=ex["bodySerial"], make=ex.get("make"), model=ex.get("model"))
                    g.edge("CAPTURED_WITH", n.key, dk, method="exif:BodySerialNumber", evidence=[n.key])
                if ex.get("gps"):
                    lat, lon = ex["gps"]
                    gk = K.geo(lat, lon)
                    g.node(gk, "GeoPoint", lat=lat, lon=lon)
                    g.edge("HAS_EXIF_GPS", n.key, gk, method="exif:GPS", evidence=[n.key], spoofable=True)

    # 3. Documents: text + file-declared metadata ---------------------------
    docs = [n for n in media if n.props.get("mime") in (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation")]
    for n in docs:
        path = root / n.props["localPath"]
        res = pdf_extract(path) if n.props["mime"] == "application/pdf" else office_extract(path)
        with g.activity("DetectionRun", f"{res['tool']}:{n.key}", agent={
                "tool": res["tool"], "version": res["version"], "codeLicense": "Apache-2.0 (Tika in prod)",
                "weightsLicense": "n/a"}):
            counts["documents"] += 1
            if res.get("encrypted"):
                n.props["isEncrypted"] = True
                continue
            meta = res["meta"]
            if meta:
                mk = f"meta:{n.key}"  # file-declared claims are per file, never merged across files
                g.node(mk, "DocumentMetadata", **meta)
                g.edge("HAS_METADATA", n.key, mk, status="file_declared", conf=0.5,
                       method=res["tool"], evidence=[n.key])
            if res["text"].strip():
                tk = f"text:{n.key}"
                g.node(tk, "ExtractedText", text=res["text"][:20000], chars=len(res["text"]))
                g.edge("HAS_TEXT", n.key, tk, method=res["tool"])
                counts["identifiers"] += link_identifiers(g, n.key, res["text"], evidence=tk)
                _link_entities(g, n.key, res["text"], tk, gazetteer)
    # shared xmpMM:DocumentID across different sha256 => same document lineage
    by_xmp: dict[str, list[str]] = {}
    for e in g.by_type("HAS_METADATA"):
        xmp = g.nodes[e.dst].props.get("xmpDocumentID")
        if xmp:
            by_xmp.setdefault(xmp, []).append(e.src)
    if any(len(f) > 1 for f in by_xmp.values()):
        with g.activity("DetectionRun", "xmp-lineage", agent={"tool": "tgo-xmp-lineage", "version": "0.1",
                                                               "codeLicense": "MIT", "weightsLicense": "n/a"}):
            for mk, files in by_xmp.items():
                for a in files:
                    for b in files:
                        if a < b:
                            g.edge("SAME_DOCUMENT_LINEAGE", a, b, status="file_declared", conf=0.7,
                                   method="xmpMM:DocumentID", evidence=[f"meta:{a}", f"meta:{b}"],
                                   xmpDocumentID=mk)

    # 4. Speech-to-text on voice notes / round videos ----------------------
    for n in media:
        if not ({"VoiceNote", "RoundVideo"} & n.labels):
            continue
        tr = transcribe(root / n.props["localPath"])
        if not tr:
            n.props["fileStatus"] = "asr_not_available"
            continue
        with g.activity("DetectionRun", f"asr:{n.key}", agent={
                "tool": tr["tool"], "version": tr["version"], "modelId": tr.get("model"),
                "codeLicense": tr.get("codeLicense"), "weightsLicense": tr.get("weightsLicense")}):
            counts["transcripts"] += 1
            tk = f"transcript:{n.key}"
            full = " ".join(s["text"].strip() for s in tr["segments"])
            g.node(tk, "Transcript", detectedLanguage=tr.get("language"),
                   languageProbability=tr.get("language_probability"), text=full)
            g.edge("HAS_TRANSCRIPT", n.key, tk, conf=tr.get("language_probability") or 0.8, method=tr["tool"],
                   evidence=[n.key])
            for i, s in enumerate(tr["segments"]):
                sk = f"{tk}/seg:{i:04d}"
                g.node(sk, "TranscriptSegment", startSec=s["start"], endSec=s["end"], text=s["text"].strip(),
                       turnLabel=s.get("speaker", "SPEAKER_00"),
                       mediaFragment=f"#t={s['start']:.2f},{s['end']:.2f}")
                g.edge("HAS_SEGMENT", tk, sk, conf=0.9, method=tr["tool"], evidence=[n.key],
                       startSec=s["start"], endSec=s["end"])
            counts["identifiers"] += link_identifiers(g, n.key, full, evidence=tk)
            _link_entities(g, n.key, full, tk, gazetteer)

    # 5. Gated OCR (screenshot-like images) -------------------------------
    for n in images:
        res = ocr(root / n.props["localPath"])
        if not res or not res["text"].strip():
            continue
        with g.activity("DetectionRun", f"ocr:{n.key}", agent={
                "tool": res["tool"], "version": res["version"], "codeLicense": res["codeLicense"],
                "weightsLicense": res["weightsLicense"]}):
            counts["ocr"] += 1
            tk = f"ocr:{n.key}"
            g.node(tk, "ExtractedText", text=res["text"], method="ocr")
            g.edge("HAS_TEXT", n.key, tk, status="inferred", conf=0.85, method=res["tool"], evidence=[n.key])
            counts["identifiers"] += link_identifiers(g, n.key, res["text"], evidence=tk)

    # 6. Identifiers in message text (observed text, validated pattern) ----
    with g.activity("DetectionRun", "identifiers:messages", agent={
            "tool": "tgo-identifiers", "version": "0.1", "codeLicense": "MIT", "weightsLicense": "n/a"}):
        for n in g.by_label("Message"):
            if n.props.get("text"):
                counts["identifiers"] += link_identifiers(g, n.key, n.props["text"], evidence=n.key)
    return counts


def _link_entities(g: Graph, src: str, text: str, evidence: str, gazetteer=None) -> None:
    for surface, label, s, e in entities(text, gazetteer):
        ek = f"entity:{label}:{surface.lower()}"
        g.node(ek, "ExtractedEntity", name=surface, entityType=label)
        g.edge("MENTIONS_ENTITY", src, ek, conf=0.7, method="ner:gazetteer", evidence=[evidence],
               selector=f"TextPositionSelector:{s}-{e}")


def _pil_version() -> str:
    try:
        import PIL

        return PIL.__version__
    except ImportError:
        return "n/a"
