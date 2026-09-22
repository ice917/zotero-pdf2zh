import sys
import unittest
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from utils.execute import _decode_pty_utf8
from utils.venv import _engine_from_command


class PtyUtf8DecodeTests(unittest.TestCase):
    def test_multibyte_character_split_across_reads_is_kept(self):
        first, leftover = _decode_pty_utf8("译".encode("utf-8")[:1], b"")
        self.assertEqual(first, "")
        second, leftover = _decode_pty_utf8("译".encode("utf-8")[1:], leftover)
        self.assertEqual(second, "译")
        self.assertEqual(leftover, b"")

    def test_invalid_byte_does_not_swallow_the_rest_of_the_stream(self):
        # A child process that emits a byte outside UTF-8 (here a lone 0x80).
        # The progress line after it must still be parsed.
        text, leftover = _decode_pty_utf8(b"translate 3/17 \x80 done\r", b"")
        self.assertIn("done", text)
        self.assertEqual(leftover, b"")

    def test_stream_keeps_flowing_after_an_invalid_byte(self):
        # Regression: the previous implementation returned ``buf[exc.start:]``
        # as leftover, so offset 0 failed forever and every later read decoded
        # to "" (progress frozen) while leftover grew without bound.
        leftover = b""
        decoded = []
        for chunk in (b"\x80", b"a", b"b", b"c"):
            text, leftover = _decode_pty_utf8(chunk, leftover)
            decoded.append(text)
        self.assertEqual("".join(decoded).replace("\ufffd", ""), "abc")
        self.assertLess(len(leftover), 4)

    def test_leftover_stays_small_over_a_realistic_stream(self):
        chunks = [
            b"\xe8\xaf\x91",  # 译
            b"\x80",
            b"translate 1/17 [",
            b"\xe8\xaf\x91",
            b"\xff\xfe",
            b"translate 17/17 [",
        ]
        leftover = b""
        decoded = []
        for chunk in chunks:
            text, leftover = _decode_pty_utf8(chunk, leftover)
            decoded.append(text)
        self.assertIn("translate 17/17 [", "".join(decoded))
        self.assertLess(len(leftover), 4)


class EngineSelectionTests(unittest.TestCase):
    def test_engine_token_in_a_data_argument_must_not_flip_the_engine(self):
        # The input PDF path and the --output directory both contain
        # "pdf2zh_next"; the executable still says pdf2zh.
        command = [
            "pdf2zh",
            "/data/pdf2zh_next/paper.pdf",
            "--output",
            "/tmp/pdf2zh_next",
        ]
        self.assertEqual(_engine_from_command(command), "pdf2zh")

    def test_engine_is_taken_from_the_executable_position(self):
        self.assertEqual(_engine_from_command(["pdf2zh_next", "a.pdf"]), "pdf2zh_next")
        self.assertEqual(_engine_from_command(["pdf2zh", "a.pdf"]), "pdf2zh")
        self.assertEqual(
            _engine_from_command(["/opt/envs/x/bin/pdf2zh_next", "a.pdf"]),
            "pdf2zh_next",
        )
        self.assertEqual(
            _engine_from_command([r"C:\envs\x\Scripts\pdf2zh.exe", "a.pdf"]),
            "pdf2zh",
        )

    def test_python_dash_m_form_is_resolved_by_module_name(self):
        self.assertEqual(
            _engine_from_command(["python", "-u", "-m", "pdf2zh_next", "a.pdf"]),
            "pdf2zh_next",
        )
        self.assertEqual(
            _engine_from_command(["python3.12", "-u", "-m", "pdf2zh", "a.pdf"]),
            "pdf2zh",
        )
        self.assertEqual(_engine_from_command(["python", "a.pdf"]), "pdf2zh")

    def test_empty_command_defaults_to_pdf2zh(self):
        self.assertEqual(_engine_from_command([]), "pdf2zh")


if __name__ == "__main__":
    unittest.main()
