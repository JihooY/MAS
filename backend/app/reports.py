import re

import numpy as np
from .i18n import translate


def evidence(tracks, analyses, start, end):
    result = []
    for track in tracks:
        data = analyses.get(track["id"])
        if not data:
            continue
        offset = track.get("offset", 0)
        series = data["series"]
        ts = np.asarray(series["time"]) - offset
        mask = (ts >= start) & (ts <= end)
        pt = np.asarray(series["pitch_time"]) - offset
        pmask = (pt >= start) & (pt <= end)
        hz = np.array([np.nan if v is None else v for v in series["pitch_hz"]])[pmask]
        midi = np.array([np.nan if v is None else v for v in series["pitch_midi"]])[
            pmask
        ]
        rms = np.asarray(series["rms_db"])[mask]
        if not len(rms):
            continue
        valid = midi[np.isfinite(midi)]
        result.append(
            {
                "track_id": track["id"],
                "source_id": track.get("source_id"),
                "name": track["name"],
                "offset_seconds": offset,
                "range_seconds": [start, end],
                "source_range_seconds": [
                    max(0, start + offset),
                    min(track["duration"], end + offset),
                ],
                "whole_file": data["summary"],
                "region": {
                    "median_pitch_hz": round(float(np.nanmedian(hz)), 2)
                    if len(valid)
                    else None,
                    "note_center_deviation_cents": round(
                        float(np.median(np.abs(valid - np.round(valid))) * 100), 2
                    )
                    if len(valid)
                    else None,
                    "mean_rms_db": round(float(np.mean(rms)), 2),
                    "valid_pitch_frames": len(valid),
                    "total_pitch_frames": len(midi),
                },
                "vibrato_candidates": [
                    v
                    for v in data["vibrato_candidates"]
                    if v["end"] - offset >= start and v["start"] - offset <= end
                ],
            }
        )
    return result


def measured_report(items, start, end, language="ko"):
    def t(message, **values):
        return translate(message, language, **values)
    lines = [t("## 보컬 측정 리포트"), t("**분석 구간:** {start}–{end}초", start=f"{start:.2f}", end=f"{end:.2f}"), "",
             t("| 보컬 | 중앙 음높이 | 평균 RMS | 유효 음정 프레임 |"),
             "| :--- | ---: | ---: | ---: |"]
    for item in items:
        s = item["region"]
        pitch = (
            f"{s['median_pitch_hz']:.1f} Hz" if s["median_pitch_hz"] else t("추정 불가")
        )
        name = re.sub(r"([\\`*_{}\[\]()#+.!|<>-])", r"\\\1", item["name"].replace("\n", " "))
        lines.append(f"| **{name}** | {pitch} | {s['mean_rms_db']:.1f} dBFS | {s['valid_pitch_frames']}/{s['total_pitch_frames']} |")
    if len(items) >= 2:
        a, b = items[:2]
        diff = a["region"]["mean_rms_db"] - b["region"]["mean_rms_db"]
        lines.extend(
            [
                "",
                t("첫 두 트랙의 평균 프레임 RMS 차이는 {difference} dB입니다. 개별 재생 음량을 조절하며 차이를 다시 들어보세요.", difference=f"{diff:+.1f}"),
            ]
        )
    lines.extend(
        [
            "",
            t("### 평가 해석"),
            t("음 중심 편차는 악보 대비 정확도가 아닙니다. 포르타멘토, 튜닝 기준, 비브라토도 수치에 영향을 줍니다."),
            t("성구·창법·가창 순위는 판정하지 않습니다. 시간 오프셋은 수동 정렬이며 음악적 구간 정렬을 보장하지 않습니다."),
        ]
    )
    return "\n".join(lines)


def ai_report(settings, items, language="ko", *, audio_analyses, on_stage=lambda *_: None):
    from .music_evaluation import evaluate
    return evaluate(settings, items, audio_analyses, language, on_stage)
