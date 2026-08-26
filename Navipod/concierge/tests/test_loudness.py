"""Tests for the ffmpeg-based loudness measurement module."""

import subprocess
from unittest.mock import patch

import pytest

import loudness


# Realistic ffmpeg loudnorm stderr output (from a 440 Hz sine wave).
# This lets us test the JSON parser without running ffmpeg.
_SAMPLE_STDERR = """
{
    "input_i" : "-21.75",
    "input_tp" : "-0.02",
    "input_lra" : "0.00",
    "input_thresh" : "-31.75",
    "output_i" : "-14.00",
    "output_tp" : "-1.00",
    "output_lra" : "0.00",
    "output_thresh" : "-24.00",
    "normalization_type" : "linear",
    "target_offset" : "0.00"
}
"""


class TestParseLoudnormJson:
    def test_parses_valid_output(self):
        result = loudness._parse_loudnorm_json(_SAMPLE_STDERR)
        assert result is not None
        # -14 target - (-21.75) = +7.75 dB gain
        assert result.gain_db == pytest.approx(7.75, abs=0.01)
        # -0.02 dBFS -> 10^(-0.02/20) ≈ 0.9977 linear
        assert result.peak == pytest.approx(0.9977, abs=0.01)

    def test_returns_none_on_empty(self):
        assert loudness._parse_loudnorm_json("") is None

    def test_returns_none_on_no_json(self):
        assert loudness._parse_loudnorm_json("just some text") is None

    def test_returns_none_on_malformed_json(self):
        assert loudness._parse_loudnorm_json("{broken json}") is None

    def test_returns_none_on_missing_input_i(self):
        stderr = '{"input_tp": "-1.0"}'
        assert loudness._parse_loudnorm_json(stderr) is None

    def test_handles_quiet_track(self):
        """A quiet track at -30 LUFS needs +16 dB gain."""
        stderr = '{"input_i": "-30.00", "input_tp": "-6.0"}'
        result = loudness._parse_loudnorm_json(stderr)
        assert result is not None
        assert result.gain_db == pytest.approx(16.0, abs=0.01)

    def test_handles_loud_track(self):
        """A loud track at -6 LUFS needs -8 dB gain (quieter)."""
        stderr = '{"input_i": "-6.00", "input_tp": "0.5"}'
        result = loudness._parse_loudnorm_json(stderr)
        assert result is not None
        assert result.gain_db == pytest.approx(-8.0, abs=0.01)
        # 0.5 dBFS -> ~1.059 linear, clamped to 1.0
        assert result.peak == 1.0

    def test_handles_missing_peak(self):
        stderr = '{"input_i": "-14.00"}'
        result = loudness._parse_loudnorm_json(stderr)
        assert result is not None
        assert result.gain_db == pytest.approx(0.0, abs=0.01)
        assert result.peak == 1.0


class TestMeasureLoudness:
    def test_returns_none_when_ffmpeg_missing(self):
        with patch("loudness.shutil.which", return_value=None):
            result = loudness.measure_loudness("/tmp/nonexistent.mp3")
            assert result is None

    def test_returns_none_when_file_missing(self):
        with patch("loudness.shutil.which", return_value="/usr/bin/ffmpeg"):
            result = loudness.measure_loudness("/tmp/definitely_does_not_exist_xyz.mp3")
            assert result is None

    def test_parses_ffmpeg_output(self, tmp_path):
        """Integration test: generate a real file and measure it."""
        ffmpeg = loudness._find_ffmpeg()
        if not ffmpeg:
            pytest.skip("ffmpeg not installed")

        test_file = tmp_path / "test.mp3"
        proc = subprocess.run(
            [
                ffmpeg,
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:a", "libmp3lame", "-q:a", "2",
                str(test_file), "-y",
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            pytest.skip("ffmpeg could not generate test file")

        result = loudness.measure_loudness(test_file)
        assert result is not None
        # A pure 440 Hz sine at full scale is ~-21 LUFS, so gain should
        # be strongly positive (around +7-8 dB to reach -14).
        assert result.gain_db > 5.0
        assert 0.0 < result.peak <= 1.0


class TestLoudnessResult:
    def test_dataclass_fields(self):
        r = loudness.LoudnessResult(gain_db=-3.5, peak=0.89)
        assert r.gain_db == -3.5
        assert r.peak == 0.89
