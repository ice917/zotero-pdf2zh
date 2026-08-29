# -*- coding: utf-8 -*-
"""
将翻译后的中文 Markdown 转为 PDF(pandoc + xelatex)
用法:
    python md2pdf.py <输入.md> [输出.pdf] [--template-pdf <原PDF>]
依赖:
    pandoc 已安装(用户已装 3.8)
    MiKTeX(提供 xelatex)已安装
    pymupdf/fitz(仅 --template-pdf 时需要, zotero-pdf2zh 环境已带)

--template-pdf: 对照原 PDF 的页面尺寸/页边距/字号, 让输出 PDF 版式参数与原文件一致。
"""
import argparse
import os
import shutil
import subprocess
import sys

FALLBACK_CJK_FONTS = [
    "SimSun",            # 宋体
    "Microsoft YaHei",   # 微软雅黑
    "SimHei",            # 黑体
    "Noto Sans CJK SC",
    "Source Han Sans SC",
]

# MiKTeX 常见安装路径(自动探测 xelatex)
MIKTEX_XELATEX_CANDIDATES = [
    r"C:\Users\97638\AppData\Local\Programs\MiKTeX\miktex\bin\x64\xelatex.exe",
    r"C:\Program Files\MiKTeX\miktex\bin\x64\xelatex.exe",
]

_LATEX_FONT_SIZES = (10, 11, 12)


def find_xelatex():
    """优先用 PATH 里的 xelatex,否则探测 MiKTeX 常见安装路径"""
    found = shutil.which("xelatex")
    if found:
        return found
    for cand in MIKTEX_XELATEX_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def find_cjk_font():
    """探测系统中可用的中文字体(Windows 字体目录)"""
    patterns = [
        r"C:\Windows\Fonts\simsun.ttc",       # 宋体
        r"C:\Windows\Fonts\msyh.ttc",         # 微软雅黑
        r"C:\Windows\Fonts\simhei.ttf",       # 黑体
        r"C:\Windows\Fonts\Deng.ttf",         # 等线
        r"C:\Windows\Fonts\msyhbd.ttc",
    ]
    for p in patterns:
        if os.path.exists(p):
            name = os.path.splitext(os.path.basename(p))[0]
            # 映射回常见字体名
            fontmap = {"simsun": "SimSun", "msyh": "Microsoft YaHei",
                       "msyhbd": "Microsoft YaHei", "simhei": "SimHei",
                       "deng": "DengXian"}
            return fontmap.get(name.lower(), name)
    return None


def read_template_pdf(pdf_path):
    """读取原 PDF 版式参数, 返回 (paperwidth_pt, paperheight_pt, margins_pt, fontsize_pt)
    页面尺寸取自首页; 边距用正文页(跳过封面/版权页)文本块的最小左/最大右/最小上/最大下估算"""
    import fitz  # pymupdf
    doc = fitz.open(pdf_path)
    page = doc[0]
    w, h = page.rect.width, page.rect.height

    # 跳过封面/版权页(前2页), 用后续正文页的文本块极值估算边距
    start = 2 if len(doc) > 2 else 0
    pages = doc[start:min(start + 6, len(doc))]
    blocks = [b for p in pages for b in p.get_text("blocks")]
    if blocks:
        ml = min(b[0] for b in blocks)
        mr = w - max(b[2] for b in blocks)
        mt = min(b[1] for b in blocks)
        mb = h - max(b[3] for b in blocks)
    else:
        ml = mr = mt = mb = 72.0

    # 正文字号: 取最常见字号, 就近取 LaTeX 支持值(10/11/12pt)
    sizes = []
    for p in pages:
        for b in p.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    sizes.append(round(s["size"], 1))
    fontsize = 11.0
    if sizes:
        import statistics
        common = statistics.mode(sizes)
        fontsize = min(_LATEX_FONT_SIZES, key=lambda x: abs(x - common))

    print(f"原PDF版式: {w:.0f}x{h:.0f}pt 边距 L={ml:.0f} R={mr:.0f} T={mt:.0f} B={mb:.0f} 字号 {fontsize}pt")
    return w, h, (ml, mr, mt, mb), int(fontsize)


def main():
    parser = argparse.ArgumentParser(description="Markdown -> PDF (pandoc + xelatex)")
    parser.add_argument("src", help="输入 markdown 文件")
    parser.add_argument("dst", nargs="?", default=None, help="输出 PDF 文件(默认与输入同名)")
    parser.add_argument("--template-pdf", default=None,
                        help="对照的原 PDF 路径, 输出将采用其页面尺寸/边距/字号")
    args = parser.parse_args()

    if not os.path.exists(args.src):
        print(f"错误: 输入文件不存在: {args.src}", file=sys.stderr)
        sys.exit(1)

    dst = args.dst or os.path.splitext(args.src)[0] + ".pdf"
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)

    # 检查 pandoc
    if not shutil.which("pandoc"):
        print("错误: 未找到 pandoc", file=sys.stderr)
        sys.exit(1)

    # 检查 xelatex
    xelatex_path = find_xelatex()
    if not xelatex_path:
        print("错误: 未找到 xelatex。请先安装 MiKTeX:", file=sys.stderr)
        print("  https://miktex.org/download", file=sys.stderr)
        sys.exit(1)
    print(f"使用 xelatex: {xelatex_path}")

    cjk_font = find_cjk_font()
    if cjk_font:
        print(f"检测到中文字体: {cjk_font}")
    else:
        print("警告: 未检测到常见中文字体,使用系统默认", file=sys.stderr)

    cmd = [
        "pandoc", args.src,
        "-o", dst,
        "--pdf-engine=" + xelatex_path,
        "-V", "CJKmainfont=" + (cjk_font or "SimSun"),
        "-V", "mainfont=Times New Roman",
        "-V", "colorlinks=true",
        "--standalone",
    ]

    if args.template_pdf:
        if not os.path.exists(args.template_pdf):
            print(f"错误: 原 PDF 不存在: {args.template_pdf}", file=sys.stderr)
            sys.exit(1)
        w, h, (ml, mr, mt, mb), fs = read_template_pdf(args.template_pdf)
        cmd += [
            "-V", f"fontsize={fs}pt",
            "-V", f"geometry:paperwidth={w:.1f}pt,paperheight={h:.1f}pt,"
                  f"left={ml:.1f}pt,right={mr:.1f}pt,top={mt:.1f}pt,bottom={mb:.1f}pt",
        ]
    else:
        cmd += [
            "-V", "fontsize=11pt",
            "-V", "geometry:margin=2.5cm",
        ]

    print("执行:", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print(f"PDF 生成成功: {dst}")
    else:
        print("PDF 生成失败,请检查上方错误信息", file=sys.stderr)
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
