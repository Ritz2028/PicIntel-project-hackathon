import os
import json
import math
import random
import sqlite3
import hashlib
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytesseract
from PIL import Image, ExifTags

# Windows uses the installed Tesseract-OCR executable.
# In Linux/Docker, tesseract is installed on PATH automatically.
if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    flash,
    redirect,
    url_for,
    send_from_directory,
)
from flask_wtf import FlaskForm, CSRFProtect
from flask_wtf.file import FileField, FileRequired, FileAllowed
from werkzeug.utils import secure_filename

try:
    import imagehash
except ImportError:
    imagehash = None

try:
    from serpapi import GoogleSearch
except ImportError:
    GoogleSearch = None


app = Flask(__name__)
app.config.from_object("config.Config")
csrf = CSRFProtect(app)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Keep the same demo database used by the original project.
MOCK_SEARCH_DATABASE = {
    "general_images": [
        {
            "url": "https://news.example.com/breaking-news-article",
            "domain": "news.example.com",
            "title": "Breaking News: Major Event Coverage",
            "match_score": 0.92,
            "context": "News Article",
        },
        {
            "url": "https://socialmedia.com/viral-post/12345",
            "domain": "socialmedia.com",
            "title": "Viral Social Media Post",
            "match_score": 0.86,
            "context": "Social Media",
        },
        {
            "url": "https://blog.photography.net/portfolio/stunning-shots",
            "domain": "photography.net",
            "title": "Professional Photography Portfolio",
            "match_score": 0.79,
            "context": "Photography Blog",
        },
    ]
}


class UploadForm(FlaskForm):
    """Secure file upload form with validation."""

    file = FileField(
        "Image File",
        validators=[
            FileRequired(message="Please select a file to upload"),
            FileAllowed(
                ["png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp"],
                "Only image files are allowed (PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP)",
            ),
        ],
    )


def get_db():
    db_path = os.path.join(app.root_path, "picintel_index.sqlite")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            url TEXT,
            title TEXT,
            domain TEXT,
            phash TEXT,
            added_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def compute_phash(file_path):
    try:
        with Image.open(file_path) as im:
            im = im.convert("RGB")
            if imagehash is not None:
                return str(imagehash.phash(im))

            # Fallback if imagehash is not installed.
            gray = np.asarray(im.convert("L").resize((32, 32)), dtype=np.float32)
            small = cv2.resize(gray, (8, 8))
            median = np.median(small)
            bits = (small > median).flatten()
            value = 0
            for bit in bits:
                value = (value << 1) | int(bit)
            return f"{value:016x}"
    except Exception:
        return None


def hamming_distance(hash_a, hash_b):
    try:
        if imagehash is not None:
            return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)

        a = int(hash_a, 16)
        b = int(hash_b, 16)
        return (a ^ b).bit_count()
    except Exception:
        return 64


def index_local_image(file_path, filename, source_url=None, title=None, domain=None):
    ph = compute_phash(file_path)
    if not ph:
        return None

    conn = get_db()
    conn.execute(
        """
        INSERT INTO images
        (filename, url, title, domain, phash, added_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            source_url or "",
            title or filename,
            domain or "Local Index",
            ph,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def search_local_similar(file_path, top_k=8):
    query_ph = compute_phash(file_path)
    if not query_ph:
        return []

    conn = get_db()
    cur = conn.execute(
        """
        SELECT filename, url, title, domain, phash, added_at
        FROM images
        """
    )
    rows = cur.fetchall()
    conn.close()

    results = []

    for fn, url, title, domain, ph, added_at in rows:
        dist = hamming_distance(query_ph, ph)
        score = max(0.0, 1.0 - (dist / 64.0))

        results.append(
            {
                "filename": fn,
                "url": url,
                "title": title or fn,
                "domain": domain or "Local Index",
                "match_score": round(score, 3),
                "first_found": added_at,
                "confidence": (
                    "High" if score > 0.9 else
                    "Medium" if score > 0.8 else
                    "Low"
                ),
                "context": "Local pHash Index",
            }
        )

    results.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return results[:top_k]


def validate_file_type(file_path):
    try:
        with Image.open(file_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def extract_exif_data(exif_data):
    exif_info = {}

    for tag_id, value in exif_data.items():
        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
        try:
            if isinstance(value, bytes):
                value = value.decode(errors="ignore")
            exif_info[tag_name] = value
        except Exception:
            exif_info[tag_name] = str(value)

    return exif_info


def convert_gps_coordinate_fixed(coord):
    try:
        degrees = float(coord[0])
        minutes = float(coord[1])
        seconds = float(coord[2])
        return degrees + (minutes / 60.0) + (seconds / 3600.0)
    except Exception:
        return None


def extract_gps_data_fixed(gps_info):
    gps_data = {}

    try:
        gps_tags = {
            key: ExifTags.GPSTAGS.get(key, str(key))
            for key in gps_info
        }

        gps_items = {
            gps_tags[key]: value
            for key, value in gps_info.items()
        }

        lat = gps_items.get("GPSLatitude")
        lon = gps_items.get("GPSLongitude")

        if lat and lon:
            latitude = convert_gps_coordinate_fixed(lat)
            longitude = convert_gps_coordinate_fixed(lon)

            if gps_items.get("GPSLatitudeRef") == "S":
                latitude = -latitude
            if gps_items.get("GPSLongitudeRef") == "W":
                longitude = -longitude

            gps_data["latitude"] = latitude
            gps_data["longitude"] = longitude

    except Exception:
        pass

    return gps_data


def extract_metadata(file_path):
    metadata = {
        "basic_info": {},
        "exif": {},
        "gps": {},
        "ocr_text": "",
    }

    try:
        image = Image.open(file_path)

        metadata["basic_info"] = {
            "filename": os.path.basename(file_path),
            "format": image.format,
            "mode": image.mode,
            "dimensions": f"{image.width}x{image.height}",
            "file_size": os.path.getsize(file_path),
        }

        exif_data = image.getexif()

        if exif_data:
            metadata["exif"] = extract_exif_data(exif_data)

            gps_ifd = exif_data.get_ifd(ExifTags.IFD.GPSInfo)
            if gps_ifd:
                metadata["gps"] = extract_gps_data_fixed(gps_ifd)

    except Exception:
        pass

    metadata["ocr_text"] = extract_text_ocr(file_path)
    return metadata


def extract_text_ocr(file_path):
    try:
        image = cv2.imread(file_path)
        if image is None:
            return ""

        text = pytesseract.image_to_string(image)
        cleaned_text = " ".join(text.split())
        return cleaned_text
    except Exception:
        return ""


def analyze_noise_patterns(gray):
    try:
        blur_kernel = cv2.GaussianBlur(gray, (5, 5), 0)
        noise = gray.astype(np.float32) - blur_kernel.astype(np.float32)
        std = float(np.std(noise))

        return {
            "noise_level": round(std, 4),
            "ai_indicator": float(np.clip(1.0 - std / 30.0, 0, 1)),
        }
    except Exception:
        return {"noise_level": 0.0, "ai_indicator": 0.5}


def analyze_frequency_domain(gray):
    try:
        fft = np.fft.fft2(gray)
        fft_shift = np.fft.fftshift(fft)
        magnitude_spectrum = np.log1p(np.abs(fft_shift))

        h, w = gray.shape
        center = magnitude_spectrum[h // 2, w // 2]
        high_frequency = magnitude_spectrum.mean()

        freq_ratio = float(high_frequency / (center + 1e-6))

        return {
            "frequency_ratio": round(freq_ratio, 4),
            "ai_indicator": float(np.clip(freq_ratio / 2.0, 0, 1)),
        }
    except Exception:
        return {"frequency_ratio": 0.0, "ai_indicator": 0.5}


def detect_ai_artifacts(rgb_img):
    try:
        artifact_score = 0.0

        if rgb_img.shape[0] % 8 == 0 and rgb_img.shape[1] % 8 == 0:
            artifact_score += 0.15

        # Very smooth images can be a weak AI indicator.
        gray = cv2.cvtColor(rgb_img, cv2.COLOR_RGB2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()

        if laplacian_var < 50:
            artifact_score += 0.35

        return {
            "artifact_score": round(min(1.0, artifact_score), 4),
            "ai_indicator": round(min(1.0, artifact_score), 4),
        }
    except Exception:
        return {"artifact_score": 0.0, "ai_indicator": 0.5}


def calculate_pixel_correlation(gray, direction=1):
    try:
        if direction == 1:
            a = gray[:, :-1].astype(np.float32).flatten()
            b = gray[:, 1:].astype(np.float32).flatten()
        else:
            a = gray[:-1, :].astype(np.float32).flatten()
            b = gray[1:, :].astype(np.float32).flatten()

        if len(a) < 2:
            return 0.0

        return float(np.corrcoef(a, b)[0, 1])
    except Exception:
        return 0.0


def analyze_pixel_patterns(img):
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        horizontal = calculate_pixel_correlation(gray, 1)
        vertical = calculate_pixel_correlation(gray, 2)
        avg_correlation = (abs(horizontal) + abs(vertical)) / 2.0

        return {
            "horizontal_correlation": round(horizontal, 4),
            "vertical_correlation": round(vertical, 4),
            "average_correlation": round(avg_correlation, 4),
            "ai_indicator": float(np.clip(avg_correlation, 0, 1)),
        }
    except Exception:
        return {
            "horizontal_correlation": 0.0,
            "vertical_correlation": 0.0,
            "average_correlation": 0.0,
            "ai_indicator": 0.5,
        }


def analyze_compression_artifacts(file_path, gray):
    try:
        file_size = os.path.getsize(file_path)
        pixel_count = max(1, gray.shape[0] * gray.shape[1])
        compression_ratio = file_size / pixel_count

        return {
            "file_size_bytes": file_size,
            "compression_ratio": round(compression_ratio, 4),
            "ai_indicator": float(
                np.clip(1.0 - min(compression_ratio / 10.0, 1.0), 0, 1)
            ),
        }
    except Exception:
        return {
            "file_size_bytes": 0,
            "compression_ratio": 0.0,
            "ai_indicator": 0.5,
        }


def extract_advanced_features(img, gray, hsv, lab):
    return {
        "mean_brightness": round(float(np.mean(gray)), 3),
        "contrast": round(float(np.std(gray)), 3),
        "mean_saturation": round(float(np.mean(hsv[:, :, 1])), 3),
        "mean_lab_a": round(float(np.mean(lab[:, :, 1])), 3),
        "mean_lab_b": round(float(np.mean(lab[:, :, 2])), 3),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 3),
    }


def generate_detailed_indicators(
    noise_analysis,
    frequency_analysis,
    artifact_analysis,
    pixel_analysis,
    compression_analysis,
    ai_score,
):
    indicators = []

    if noise_analysis["ai_indicator"] > 0.65:
        indicators.append("Unusual noise distribution")

    if frequency_analysis["ai_indicator"] > 0.65:
        indicators.append("Unusual frequency-domain characteristics")

    if artifact_analysis["ai_indicator"] > 0.65:
        indicators.append("Potential synthetic image artifacts")

    if pixel_analysis["ai_indicator"] > 0.75:
        indicators.append("High pixel correlation")

    if compression_analysis["ai_indicator"] > 0.7:
        indicators.append("Unusual compression characteristics")

    if ai_score >= 0.8:
        indicators.append("Multiple AI-generation indicators detected")

    return indicators


def combine_ai_analyses(
    features,
    noise_analysis,
    frequency_analysis,
    artifact_analysis,
    pixel_analysis,
    compression_analysis,
    image_analysis,
):
    ai_score = (
        noise_analysis["ai_indicator"] * 0.15
        + frequency_analysis["ai_indicator"] * 0.20
        + artifact_analysis["ai_indicator"] * 0.30
        + pixel_analysis["ai_indicator"] * 0.25
        + compression_analysis["ai_indicator"] * 0.10
    )

    if image_analysis and "dimensions" in image_analysis:
        try:
            width, height = map(
                int, image_analysis["dimensions"].split("x")
            )

            if width == height and width in (256, 512, 1024, 2048):
                ai_score += 0.15
            elif width % 8 == 0 and height % 8 == 0:
                ai_score += 0.05
        except Exception:
            pass

    ai_score = min(1.0, ai_score)

    indicators = generate_detailed_indicators(
        noise_analysis,
        frequency_analysis,
        artifact_analysis,
        pixel_analysis,
        compression_analysis,
        ai_score,
    )

    if ai_score >= 0.8:
        assessment = "Likely AI-generated"
        risk_level = "High"
        confidence = "High"
    elif ai_score >= 0.6:
        assessment = "Possibly AI-generated"
        risk_level = "Medium"
        confidence = "Medium"
    else:
        assessment = "Likely authentic"
        risk_level = "Low"
        confidence = "Medium"

    return {
        "ai_score": round(ai_score, 3),
        "assessment": assessment,
        "confidence": confidence,
        "risk_level": risk_level,
        "indicators": indicators,
        "features": features,
        "noise_analysis": noise_analysis,
        "frequency_analysis": frequency_analysis,
        "artifact_analysis": artifact_analysis,
        "pixel_analysis": pixel_analysis,
        "compression_analysis": compression_analysis,
    }


def generate_fallback_ai_detection():
    return {
        "ai_score": 0.5,
        "assessment": "Unable to determine",
        "confidence": "Low",
        "risk_level": "Unknown",
        "indicators": [],
    }


def detect_ai_generated_image(file_path, image_analysis):
    try:
        img = cv2.imread(file_path)

        if img is None:
            return generate_fallback_ai_detection()

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)

        features = extract_advanced_features(img, gray, hsv, lab)
        noise_analysis = analyze_noise_patterns(gray)
        frequency_analysis = analyze_frequency_domain(gray)
        artifact_analysis = detect_ai_artifacts(rgb_img)
        pixel_analysis = analyze_pixel_patterns(img)
        compression_analysis = analyze_compression_artifacts(
            file_path, gray
        )

        return combine_ai_analyses(
            features,
            noise_analysis,
            frequency_analysis,
            artifact_analysis,
            pixel_analysis,
            compression_analysis,
            image_analysis,
        )

    except Exception:
        return generate_fallback_ai_detection()


def analyze_saturation_patterns(hsv):
    saturation = hsv[:, :, 1]
    return {
        "mean_saturation": float(np.mean(saturation)),
        "std_saturation": float(np.std(saturation)),
    }


def calculate_texture_energy(gray):
    return float(np.mean(np.square(gray.astype(np.float32))))


def analyze_edge_coherence(gray):
    edges = cv2.Canny(gray, 100, 200)
    return float(np.mean(edges > 0))


def analyze_local_binary_patterns(gray):
    return {
        "mean": float(np.mean(gray)),
        "std": float(np.std(gray)),
    }


def analyze_fft_characteristics(gray):
    fft = np.fft.fftshift(np.fft.fft2(gray))
    magnitude = np.abs(fft)
    return {
        "mean_frequency_magnitude": float(np.mean(magnitude)),
        "max_frequency_magnitude": float(np.max(magnitude)),
    }


def extract_wavelet_features(gray):
    # Lightweight substitute that keeps the original function interface.
    return {
        "low_frequency_energy": float(
            np.mean(cv2.GaussianBlur(gray, (9, 9), 0))
        )
    }


def analyze_noise_distribution(noise):
    return {
        "mean": float(np.mean(noise)),
        "std": float(np.std(noise)),
    }


def detect_periodic_noise(noise):
    fft_noise = np.fft.fftshift(np.fft.fft2(noise))
    return {
        "periodic_energy": float(np.mean(np.abs(fft_noise)))
    }


def detect_color_bleeding(rgb_img):
    return {
        "channel_difference": float(
            np.mean(
                np.abs(
                    rgb_img[:, :, 0].astype(float)
                    - rgb_img[:, :, 1].astype(float)
                )
            )
        )
    }


def detect_unnatural_smoothness(rgb_img):
    gray = cv2.cvtColor(rgb_img, cv2.COLOR_RGB2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    return {
        "laplacian_variance": float(laplacian_var),
        "unnaturally_smooth": bool(laplacian_var < 50),
    }


def detect_checkerboard_patterns(rgb_img):
    gray = cv2.cvtColor(rgb_img, cv2.COLOR_RGB2GRAY)
    return {
        "detected": bool(
            gray.shape[0] % 8 == 0 and gray.shape[1] % 8 == 0
        )
    }


def detect_artificial_symmetry(rgb_img):
    flipped = cv2.flip(rgb_img, 1)
    return {
        "symmetry_score": float(
            1.0
            - np.mean(
                np.abs(
                    rgb_img.astype(np.float32)
                    - flipped.astype(np.float32)
                )
            )
            / 255.0
        )
    }


def analyze_image_characteristics(file_path):
    try:
        img = cv2.imread(file_path)

        if img is None:
            return generate_fallback_analysis()

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        orb = cv2.ORB_create(nfeatures=1000)
        keypoints, descriptors = orb.detectAndCompute(gray, None)

        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))

        analysis = {
            "dimensions": f"{img.shape[1]}x{img.shape[0]}",
            "file_size_kb": os.path.getsize(file_path) // 1024,
            "feature_points": len(keypoints) if keypoints else 0,
            "brightness_level": round(brightness, 2),
            "contrast_level": round(contrast, 2),
            "color_channels": img.shape[2] if len(img.shape) > 2 else 1,
            "uniqueness_score": calculate_uniqueness_score(
                brightness,
                contrast,
                len(keypoints) if keypoints else 0,
            ),
            "complexity_level": assess_image_complexity(gray),
        }

        return analysis

    except Exception:
        return generate_fallback_analysis()


def generate_fallback_analysis():
    return {
        "dimensions": "Unknown",
        "file_size_kb": 0,
        "feature_points": 0,
        "brightness_level": 0,
        "contrast_level": 0,
        "color_channels": 0,
        "uniqueness_score": 0.5,
        "complexity_level": "Unknown",
    }


def calculate_uniqueness_score(brightness, contrast, feature_count):
    brightness_factor = min(1.0, abs(brightness - 128) / 128)
    contrast_factor = min(1.0, contrast / 80)
    feature_factor = min(1.0, feature_count / 1000)

    uniqueness = (
        brightness_factor * 0.2
        + contrast_factor * 0.3
        + feature_factor * 0.5
    )

    return round(min(1.0, uniqueness), 3)


def assess_image_complexity(gray_image):
    edges = cv2.Canny(gray_image, 100, 200)
    edge_density = float(np.mean(edges > 0))

    if edge_density > 0.25:
        return "High"
    if edge_density > 0.10:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# SerpApi reverse image search
# ---------------------------------------------------------------------------

def serpapi_reverse_search(image_url):
    """
    Perform a real Google reverse-image search through SerpApi.

    SerpApi requires image_url to be publicly accessible. For local
    development/deployment, the uploaded image can later be exposed through
    a public URL such as ngrok or a deployed Flask server.
    """
    key = os.getenv("SERPAPI_KEY")

    if not key or GoogleSearch is None:
        return []

    params = {
        "engine": "google_reverse_image",
        "image_url": image_url,
        "api_key": key,
    }

    data = GoogleSearch(params).get_dict()

    return [
        {
            "title": r.get("title"),
            "url": r.get("link"),
            "domain": r.get("source"),
            "context": "Web (SerpApi)",
        }
        for r in data.get("image_results", [])[:10]
    ]


def perform_reverse_search(file_path, filename):
    image_analysis = analyze_image_characteristics(file_path)

    # Existing local pHash search.
    local_results = search_local_similar(file_path, top_k=8)

    # Real SerpApi results are used when the API key and a public base URL
    # are configured. For now, the GitHub version can safely remain
    # unconfigured and simply return local results.
    serpapi_results = []

    public_base_url = os.getenv("PUBLIC_BASE_URL")

    if public_base_url and os.getenv("SERPAPI_KEY"):
        image_url = f"{public_base_url.rstrip('/')}/uploads/{filename}"

        try:
            serpapi_results = serpapi_reverse_search(image_url)
        except Exception as e:
            print(f"SerpApi reverse search failed: {e}")

    # Only use demo results when no SerpApi key is configured.
    # This keeps demo/fallback data clearly separate from real web results.
    fallback_needed = len(local_results) < 2

    if serpapi_results:
        combined = local_results + serpapi_results
        search_engines = ["Local pHash Index", "SerpApi Google Reverse Image"]
    elif os.getenv("SERPAPI_KEY"):
        combined = local_results
        search_engines = ["Local pHash Index"]
    else:
        mock_results = generate_mock_results(image_analysis) if fallback_needed else []
        combined = local_results + mock_results
        search_engines = ["Local pHash Index"]
        if mock_results:
            search_engines.append("Demo Mock")

    combined.sort(
        key=lambda x: x.get("match_score", 0),
        reverse=True,
    )

    return {
        "matches_found": len(combined),
        "total_indexed": len(local_results),
        "search_engines": search_engines,
        "results": combined,
        "similarity_analysis": image_analysis,
        "authenticity_indicators": assess_authenticity_indicators(
            image_analysis
        ),
        "ai_detection": detect_ai_generated_image(
            file_path, image_analysis
        ),
        "search_timestamp": datetime.now().isoformat(),
        "search_duration": (
            f"{random.uniform(0.2, 0.6):.1f} seconds"
            if serpapi_results or not fallback_needed
            else f"{random.uniform(0.8, 1.6):.1f} seconds"
        ),
    }


def generate_mock_results(image_analysis):
    results = []
    uniqueness = image_analysis.get("uniqueness_score", 0.5)

    num_results = (
        random.randint(2, 6)
        if uniqueness > 0.6
        else random.randint(4, 8)
    )

    base_results = random.sample(
        MOCK_SEARCH_DATABASE["general_images"],
        min(num_results, len(MOCK_SEARCH_DATABASE["general_images"])),
    )

    for result in base_results:
        customized_result = result.copy()

        base_score = result["match_score"]
        uniqueness_modifier = (1 - uniqueness) * 0.1

        customized_result["match_score"] = min(
            0.99, base_score + uniqueness_modifier
        )

        days_ago = random.randint(30, 800)
        found_date = (
            datetime.now() - timedelta(days=days_ago)
        ).strftime("%Y-%m-%d")

        customized_result["first_found"] = found_date
        customized_result["confidence"] = (
            "High"
            if customized_result["match_score"] > 0.9
            else "Medium"
            if customized_result["match_score"] > 0.8
            else "Low"
        )

        customized_result["context"] = "Demo Mock"
        results.append(customized_result)

    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results


def assess_authenticity_indicators(image_analysis):
    uniqueness = image_analysis.get("uniqueness_score", 0.5)
    complexity = image_analysis.get("complexity_level", "Unknown")
    feature_count = image_analysis.get("feature_points", 0)

    indicators = []

    if uniqueness > 0.7:
        indicators.append("Image has relatively distinctive visual characteristics.")

    if complexity == "High":
        indicators.append("High visual complexity detected.")

    if feature_count > 500:
        indicators.append("Strong local feature response detected.")

    if not indicators:
        indicators.append("No strong uniqueness indicators detected.")

    return {
        "uniqueness_score": uniqueness,
        "complexity": complexity,
        "feature_points": feature_count,
        "indicators": indicators,
    }


def format_file_size(size_bytes):
    size_names = ["B", "KB", "MB", "GB"]

    if size_bytes <= 0:
        return "0 B"

    i = int(math.floor(math.log(size_bytes, 1024)))
    i = min(i, len(size_names) - 1)

    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)

    return f"{s} {size_names[i]}"


@app.route("/")
def index():
    form = UploadForm()
    return render_template("index.html", form=form)


@app.route("/upload", methods=["POST"])
def upload_file():
    form = UploadForm()

    if form.validate_on_submit():
        file = form.file.data
        original_filename = secure_filename(file.filename)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{original_filename}"

        file_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            filename,
        )

        try:
            file.save(file_path)

            if not validate_file_type(file_path):
                os.remove(file_path)
                return jsonify(
                    {
                        "success": False,
                        "error": (
                            "Invalid file type detected. "
                            "Only genuine image files are allowed."
                        ),
                    }
                ), 400

            metadata = extract_metadata(file_path)

            index_local_image(
                file_path,
                filename,
            )

            reverse_search_results = perform_reverse_search(
                file_path,
                filename,
            )

            analysis_data = {
                "metadata": metadata,
                "reverse_search": reverse_search_results,
                "filename": filename,
                "analysis_complete": True,
                "upload_time": datetime.now().isoformat(),
            }

            analysis_path = (
                file_path.replace(".jpg", "_analysis.json")
                .replace(".png", "_analysis.json")
                .replace(".jpeg", "_analysis.json")
            )

            with open(analysis_path, "w") as f:
                json.dump(analysis_data, f, indent=2)

            return jsonify(
                {
                    "success": True,
                    "filename": filename,
                    "message": "File uploaded and analyzed successfully!",
                }
            )

        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)

            return jsonify(
                {
                    "success": False,
                    "error": f"Upload failed: {e}",
                }
            ), 500

    errors = []

    for field, field_errors in form.errors.items():
        for error in field_errors:
            errors.append(f"{field}: {error}")

    return jsonify(
        {
            "success": False,
            "error": "Form validation failed: " + "; ".join(errors),
        }
    ), 400


@app.route("/analysis/<filename>")
def analysis(filename):
    file_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        filename,
    )

    if not os.path.exists(file_path):
        flash("File not found", "error")
        return redirect(url_for("index"))

    analysis_path = (
        file_path.replace(".jpg", "_analysis.json")
        .replace(".png", "_analysis.json")
        .replace(".jpeg", "_analysis.json")
    )

    analysis_data = {}

    if os.path.exists(analysis_path):
        with open(analysis_path, "r") as f:
            analysis_data = json.load(f)

        if (
            "metadata" in analysis_data
            and "basic_info" in analysis_data["metadata"]
            and "file_size" in analysis_data["metadata"]["basic_info"]
        ):
            analysis_data["metadata"]["basic_info"]["file_size_formatted"] = (
                format_file_size(
                    analysis_data["metadata"]["basic_info"]["file_size"]
                )
            )

    return render_template(
        "analysis.html",
        filename=filename,
        analysis=analysis_data,
    )


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename,
    )


@app.errorhandler(413)
def too_large(e):
    return jsonify(
        {
            "success": False,
            "error": "File too large. Maximum size is 16MB.",
        }
    ), 413


@app.errorhandler(400)
def bad_request(e):
    return jsonify(
        {
            "success": False,
            "error": "Bad request. Please check your file and try again.",
        }
    ), 400


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=True,
    )
