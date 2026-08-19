"""Audio metadata extraction with a stdlib wave fallback."""

from __future__ import annotations

import math
import wave
from pathlib import Path


def analyze_audio(path: str | Path) -> dict[str, float | str | None]:
    path = Path(path)
    try:
        import soundfile as sf
        import numpy as np
        samples, sample_rate = sf.read(path, always_2d=False)
        data = np.asarray(samples, dtype=float)
        if data.ndim > 1:
            data = data.mean(axis=1)
        duration = len(data) / sample_rate if sample_rate else 0
        rms = float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0
        peak = float(np.max(np.abs(data))) if len(data) else 0
        silence_ratio = float(np.mean(np.abs(data) < 0.01)) if len(data) else 1
        loudness = 20 * math.log10(max(rms, 1e-9))
        quality = "Clipping Detected" if peak >= 0.99 else ("Low Volume / Noisy" if loudness < -35 or silence_ratio > 0.65 else "Good Quality")
        bitrate = path.stat().st_size * 8 / max(duration, 0.001) / 1000
        return {"duration_seconds": round(duration, 2), "sample_rate_khz": round(sample_rate / 1000, 2),
                "bitrate_kbps": round(bitrate, 2), "loudness_dbfs": round(loudness, 2), "quality_status": quality}
    except Exception:
        try:
            from pydub import AudioSegment
            import numpy as np
            segment = AudioSegment.from_file(path)
            values = np.array(segment.get_array_of_samples(), dtype=float)
            if segment.channels > 1:
                values = values.reshape((-1, segment.channels)).mean(axis=1)
            scale = float(1 << (8 * segment.sample_width - 1))
            normalized = values / scale
            duration = len(segment) / 1000
            rms = float(np.sqrt(np.mean(np.square(normalized)))) if len(normalized) else 0
            peak = float(np.max(np.abs(normalized))) if len(normalized) else 0
            silence_ratio = float(np.mean(np.abs(normalized) < 0.01)) if len(normalized) else 1
            loudness = 20 * math.log10(max(rms, 1e-9))
            quality = "Clipping Detected" if peak >= 0.99 else ("Low Volume / Noisy" if loudness < -35 or silence_ratio > 0.65 else "Good Quality")
            return {"duration_seconds": round(duration, 2), "sample_rate_khz": round(segment.frame_rate / 1000, 2),
                    "bitrate_kbps": round(path.stat().st_size * 8 / max(duration, 0.001) / 1000, 2),
                    "loudness_dbfs": round(loudness, 2), "quality_status": quality}
        except Exception:
            pass
        with wave.open(str(path), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            duration = frames / rate if rate else 0
            raw = audio.readframes(frames)
            width = audio.getsampwidth()
            values = [int.from_bytes(raw[i:i + width], "little", signed=True) for i in range(0, len(raw), width)] if width else []
            scale = float(2 ** (8 * width - 1)) if width else 1
            rms = math.sqrt(sum((v / scale) ** 2 for v in values) / max(len(values), 1))
            loudness = 20 * math.log10(max(rms, 1e-9))
            return {"duration_seconds": round(duration, 2), "sample_rate_khz": round(rate / 1000, 2),
                    "bitrate_kbps": round(path.stat().st_size * 8 / max(duration, 0.001) / 1000, 2),
                    "loudness_dbfs": round(loudness, 2),
                    "quality_status": "Low Volume / Noisy" if loudness < -35 else "Good Quality"}