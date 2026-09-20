# -*- coding: utf-8 -*-
"""v28.9 侧车按文档归档单元测试 (converter 写入端 + 与 adopt 认领端同一口径)

锁的是**身份口径**, 不是写盘循环本身:
  ① 没给 P2Z_DOC_PDF -> 只有 latest.jsonl (行为与 v28.8 前一致, 不阻断翻译)
  ② 给了 -> 归档件 pdf-<md5[:16]>.jsonl, 且与 adopt.pdf_md5_16 逐字符一致
     (两端口径一旦漂移, export --pdf 会"找不到归档件"而拒绝执行 = 整条管线卡死)
  ③ 身份取 PDF **内容**而非文件名: 改名/重下同内容仍是同一篇
  ④ 内容不同 -> 不同归档件 (不能互相覆盖)
  ⑤ 源路径拿不到(不存在/是目录/无权限) -> 退化为 None, 不抛错
  ⑥ 每进程只算一次并缓存: 算完再改环境变量不改结果 (防同进程内键漂移)
  ⑦ 目录自动创建 (首次跑时 segflow 目录可能还不存在)
  ⑧ 往返: converter 认领的路径, adopt.archived_sidecar 认得出同一份

隔离关键: 在导入 pdf2zh 前重定向 USERPROFILE 到临时目录, 使 expanduser("~")
指向测试沙盒 —— 绝不触碰真实 ~/.cache/pdf2zh/segflow/latest.jsonl。

运行: venv python test_segflow_archive.py, 退出码 0=全过
"""
import sys
import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="pdf2zh_test_")
os.environ["USERPROFILE"] = _SANDBOX

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from pdf2zh.converter import TranslateConverter  # noqa: E402
import adopt as AD  # noqa: E402

SEGFLOW = os.path.join(_SANDBOX, ".cache", "pdf2zh", "segflow")


def new_host():
    """跳过 __init__ 直接建实例: _segflow_paths 只吃 self._segflow_doc_path。"""
    return object.__new__(TranslateConverter)


def make_pdf(name, body):
    p = os.path.join(_SANDBOX, name)
    with open(p, "wb") as f:
        f.write(body)
    return p


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    pdf_a = make_pdf("a.pdf", b"%PDF-1.4 alpha body\n" * 100)
    pdf_b = make_pdf("b.pdf", b"%PDF-1.4 beta body\n" * 100)

    # ① 没给源路径 -> 只有 latest.jsonl
    os.environ.pop("P2Z_DOC_PDF", None)
    latest, doc = new_host()._segflow_paths()
    check("① 无 P2Z_DOC_PDF -> 归档件为 None", doc is None, repr(doc))
    check("① latest.jsonl 仍在", latest == os.path.join(SEGFLOW, "latest.jsonl"), latest)

    # ② 给了 -> 归档件路径 + 与 adopt 口径一致
    os.environ["P2Z_DOC_PDF"] = pdf_a
    latest2, doc_a = new_host()._segflow_paths()
    want = os.path.join(SEGFLOW, "pdf-%s.jsonl" % AD.pdf_md5_16(pdf_a))
    check("② 归档件路径形态", doc_a == want, "%r != %r" % (doc_a, want))
    check("② 与 adopt.pdf_md5_16 同口径", os.path.basename(doc_a) ==
          "pdf-%s.jsonl" % AD.pdf_md5_16(pdf_a))
    check("② 目录自动创建", os.path.isdir(SEGFLOW), SEGFLOW)

    # ③ 身份取内容不取文件名: 同内容换名 -> 同一归档件
    pdf_a2 = make_pdf("renamed copy.pdf", b"%PDF-1.4 alpha body\n" * 100)
    os.environ["P2Z_DOC_PDF"] = pdf_a2
    _, doc_a2 = new_host()._segflow_paths()
    check("③ 同内容异名 -> 同一归档件", doc_a2 == doc_a, "%r != %r" % (doc_a2, doc_a))

    # ④ 内容不同 -> 不同归档件 (不互相覆盖)
    os.environ["P2Z_DOC_PDF"] = pdf_b
    _, doc_b = new_host()._segflow_paths()
    check("④ 异内容 -> 异归档件", doc_b is not None and doc_b != doc_a, repr(doc_b))

    # ⑤ 源路径拿不到 -> 退化 None 且不抛
    os.environ["P2Z_DOC_PDF"] = os.path.join(_SANDBOX, "nope.pdf")
    try:
        _, doc_missing = new_host()._segflow_paths()
        ok = doc_missing is None
    except Exception as e:                                  # noqa: BLE001
        ok, doc_missing = False, "raised %r" % (e,)
    check("⑤ 源文件不存在 -> 退化 None 不抛错", ok, repr(doc_missing))

    os.environ["P2Z_DOC_PDF"] = _SANDBOX             # 目录: open() 会 OSError
    try:
        _, doc_dir = new_host()._segflow_paths()
        ok = doc_dir is None
    except Exception as e:                                  # noqa: BLE001
        ok, doc_dir = False, "raised %r" % (e,)
    check("⑤ 源为目录 -> 退化 None 不抛错", ok, repr(doc_dir))

    # ⑥ 每进程只算一次 (算完改环境变量不改结果)
    host = new_host()
    os.environ["P2Z_DOC_PDF"] = pdf_a
    _, first = host._segflow_paths()
    os.environ["P2Z_DOC_PDF"] = pdf_b
    _, second = host._segflow_paths()
    check("⑥ 结果缓存: 改环境变量不漂移", first == second == doc_a, "%r / %r" % (first, second))

    # ⑦ 往返: 按 converter 的路径落一份, adopt 认领端认得出同一份
    os.environ["P2Z_DOC_PDF"] = pdf_a
    _, arch = new_host()._segflow_paths()
    with open(arch, "w", encoding="utf-8") as f:
        f.write('{"page": 1, "segs": [], "vars": {}}\n')
    check("⑦ adopt.archived_sidecar 认出同一份", AD.archived_sidecar(pdf_a) == arch,
          repr(AD.archived_sidecar(pdf_a)))
    check("⑦ adopt.archived_sidecar 对无归档件的篇返回 None",
          AD.archived_sidecar(pdf_b) is None, repr(AD.archived_sidecar(pdf_b)))

    os.environ.pop("P2Z_DOC_PDF", None)
    print("\n侧车按文档归档单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
