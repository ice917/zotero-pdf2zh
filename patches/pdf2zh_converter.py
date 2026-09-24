import concurrent.futures
import hashlib
import json
import logging
import os
import re
import unicodedata
from enum import Enum
from string import Template
from typing import Dict

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTImage, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import apply_matrix_pt, mult_matrix
from pymupdf import Font
from tenacity import retry, wait_fixed

from pdf2zh.translator import (
    AnythingLLMTranslator,
    ArgosTranslator,
    AzureOpenAITranslator,
    AzureTranslator,
    BaseTranslator,
    BingTranslator,
    DeepLTranslator,
    DeepLXTranslator,
    DeepseekTranslator,
    DifyTranslator,
    GeminiTranslator,
    GoogleTranslator,
    GrokTranslator,
    GroqTranslator,
    ModelScopeTranslator,
    OllamaTranslator,
    OpenAIlikedTranslator,
    OpenAITranslator,
    QwenMtTranslator,
    SiliconTranslator,
    TencentTranslator,
    XinferenceTranslator,
    ZhipuTranslator,
)

log = logging.getLogger(__name__)


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)
        # [v24b 军师层] 段序导出状态: 首页时截断 sidecar, 之后逐页追加
        self._segflow_fresh = False
        self._segflow_seq = 0

    def begin_page(self, page, ctm) -> None:
        # 重载替换 cropbox
        (x0, y0, x1, y1) = page.cropbox
        (x0, y0) = apply_matrix_pt(ctm, (x0, y0))
        (x1, y1) = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        self.cur_item = LTPage(page.pageno, mediabox)

    def end_page(self, page):
        # 重载返回指令流
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name, bbox, matrix) -> None:
        # 重载设置 pageid
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> None:
        # 重载返回指令流
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return self.receive_layout(fig)

    def render_char(
        self,
        matrix,
        font,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        # 重载设置 cid 和 font
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
        )
        self.cur_item.add(item)
        item.cid = cid  # hack 插入原字符编码
        item.font = font  # hack 插入原字符字体
        return item.adv


class Paragraph:
    def __init__(self, y, x, x0, x1, y0, y1, size, brk):
        self.y: float = y  # 初始纵坐标
        self.x: float = x  # 初始横坐标
        self.x0: float = x0  # 左边界
        self.x1: float = x1  # 右边界
        self.y0: float = y0  # 上边界
        self.y1: float = y1  # 下边界
        self.size: float = size  # 字体大小
        self.brk: bool = brk  # 换行标记


# fmt: off
class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr,
        vfont: str = None,
        vchar: str = None,
        thread: int = 0,
        layout={},
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        noto_name: str = "",
        noto: Font = None,
        envs: Dict = None,
        prompt: Template = None,
        ignore_cache: bool = False,
    ) -> None:
        super().__init__(rsrcmgr)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = thread
        self.layout = layout
        self.noto_name = noto_name
        self.noto = noto
        self.translator: BaseTranslator = None
        # e.g. "ollama:gemma2:9b" -> ["ollama", "gemma2:9b"]
        param = service.split(":", 1)
        service_name = param[0]
        service_model = param[1] if len(param) > 1 else None
        if not envs:
            envs = {}
        for translator in [GoogleTranslator, BingTranslator, DeepLTranslator, DeepLXTranslator, OllamaTranslator, XinferenceTranslator, AzureOpenAITranslator,
                           OpenAITranslator, ZhipuTranslator, ModelScopeTranslator, SiliconTranslator, GeminiTranslator, AzureTranslator, TencentTranslator, DifyTranslator, AnythingLLMTranslator, ArgosTranslator, GrokTranslator, GroqTranslator, DeepseekTranslator, OpenAIlikedTranslator, QwenMtTranslator,]:
            if service_name == translator.name:
                self.translator = translator(lang_in, lang_out, service_model, envs=envs, prompt=prompt, ignore_cache=ignore_cache)
        if not self.translator:
            raise ValueError("Unsupported translation service")

    def _maybe_summarize_document(self, ltpage, sstk: list, var=None) -> None:
        """[v24-A] 首页文本 → 文档画像, 每文档仅一次(_doc_summary_done 门闩)。

        门闩必须"先验后关": 流式解析下首页若含图形对象, receive_layout 会
        先以 LTFigure 触发(sstk 为空); 若此时提前关门, 整页文字到达时摘要
        将永不生成(无落盘/无打印/无异常, 键里缺失 doc_summary_fp)。文字
        不足同理, 留给后续页再试。失败安全(异常一律吞掉, 不阻断翻译)。

        [v28.21] 拼接前先把 {vN} **还原成真实字形**(var[i] 的字符流), 且
        40 字过滤/4000 字截断都在还原后的文本上做。摘要的落盘键与缓存键都由
        这段文本算出, 而占位符集合随 config(公式字符/字体保护表)变化 ——
        不还原的话, 改一次配置就让摘要键漂移, 全篇缓存跟着换键重译(实测
        CLAP: 491 秒, 且重译盖掉了豆包回路已采纳的译文)。还原后文本与
        config 无关, 摘要落盘键因而稳定。
        """
        try:
            _tr = getattr(self, "translator", None)
            if (_tr is None
                    or not hasattr(_tr, "summarize_document")
                    or getattr(_tr, "_doc_summary_done", False)
                    or not isinstance(ltpage, LTPage)):
                return

            def _unflag(s: str) -> str:
                def _repl(m):
                    i = int(m.group(1))
                    if not var or i >= len(var):
                        return m.group(0)   # 越界: 原样保留, 由下游再剔一次
                    try:
                        return "".join(c.get_text() for c in var[i])
                    except Exception:
                        return m.group(0)
                return re.sub(r"\{v(\d+)\}", _repl, s or "")

            _txt = " ".join(
                t for t in (_unflag(s).strip() for s in sstk) if len(t) > 40
            )[:4000]
            if len(_txt) > 600:
                _tr._doc_summary_done = True
                _tr.summarize_document(_txt)
        except Exception:
            pass

    def _segflow_paths(self):
        """[自研补丁 2026-09-19 v28.9] 侧车双写目标: (latest.jsonl, 按文档归档件/None)。

        为什么: latest.jsonl 是**全局单文件**, 换论文就覆盖 —— 下游要"某一篇的载荷"
        时只能人工归档, 忘了/记错就整轮白干 (实测 2026-09-19: 全项目只留了 3 份归档,
        为拿 Vaswani 的侧车只能白烧一次渲染)。故每个文档再落一份:
            <segflow>/pdf-<md5(PDF 字节)[:16]>.jsonl
        身份取 PDF **内容**散列(不取文件名: 改名/重下同内容仍是同一篇)。
        路径由 server.py 启动 pdf2zh 子进程前注入 P2Z_DOC_PDF 环境变量传入 ——
        转换器自己拿不到输入路径 (上游 TranslateConverter 签名里没有它)。
        取不到就只写 latest.jsonl (行为与 v28.8 前一致, 不阻断翻译)。
        """
        _d = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow")
        os.makedirs(_d, exist_ok=True)
        _latest = os.path.join(_d, "latest.jsonl")
        _doc = getattr(self, "_segflow_doc_path", "-")
        if _doc == "-":                      # 每进程只算一次 (MB 级散列, 可忽略)
            _doc = None
            _src = os.environ.get("P2Z_DOC_PDF") or ""
            if _src and os.path.exists(_src):
                try:
                    _h = hashlib.md5()
                    with open(_src, "rb") as _f:
                        for _chunk in iter(lambda: _f.read(1 << 20), b""):
                            _h.update(_chunk)
                    _doc = os.path.join(_d, "pdf-%s.jsonl" % _h.hexdigest()[:16])
                except OSError:
                    _doc = None
            self._segflow_doc_path = _doc
        return _latest, _doc

    # [v29 扫描件清底] 判据与参数
    RASTER_COVER_RATIO = 0.5   # 位图面积/页面积 ≥ 此值 → 视为「覆盖式位图」(扫描件)
    WHITEN_PAD = 1.0           # 清底矩形外扩(磅), 兜住墨迹溢出行框
    RUN_GAP_EM = 2.0           # 行内墨迹框合并的最大字间水平间隙(倍字号)
    RUN_VOV_RATIO = 0.4        # 行内墨迹框合并所需的最小纵向重叠(占较矮框高度)

    def _raster_covers_page(self, ltpage) -> bool:
        """本页是否有「覆盖式位图」= 扫描件的整页墨迹。

        为什么看位图: pdfinterp 组装成品页时是 `q {ops_base}Q ... cm {ops_new}` —— ops_base
        为原页内容流**滤掉 T* 文字指令**后的产物(pdfinterp.py「过滤 T 系列文字指令」), 位图照留。
        所以「某段底下有没有删不掉的墨迹」== 「本页有没有大幅位图」。有 → 重绘时先清底。

        旁路: 环境变量 PDF2ZH_WHITEN_SCAN=0 关闭本特性(出问题可秒关, 也用于 A/B 对照)。
        """
        if os.environ.get("PDF2ZH_WHITEN_SCAN", "1") == "0":
            return False
        try:
            (px0, py0, px1, py1) = ltpage.bbox
            page_area = abs((px1 - px0) * (py1 - py0))
            if page_area <= 0:
                return False
            for item in ltpage:
                if isinstance(item, LTImage):
                    imgs = [item]
                elif isinstance(item, LTFigure):
                    imgs = [c for c in item if isinstance(c, LTImage)]
                else:
                    continue
                for im in imgs:
                    (x0, y0, x1, y1) = im.bbox
                    if abs((x1 - x0) * (y1 - y0)) / page_area >= self.RASTER_COVER_RATIO:
                        return True
        except Exception:
            log.debug("raster cover 判定失败, 本页不清底", exc_info=True)
        return False

    def _push_ink(self, ink: list, run, child):
        """把字符并进当前墨迹行框 `run`; 跨行或跨大间隙则另起一框, 返回新的 run。

        合并条件(必须同时满足)才认定"同一行同一段连续文字":
          · 与当前框纵向重叠 > RUN_VOV_RATIO × 较矮框高  → 排除换行
          · 与当前框右缘水平间隙 ≤ RUN_GAP_EM × 字号      → 排除跨列/跨大块留白
        于是「散布在一张图里的几个文字层字符」各自成框, 不会并成覆盖整图的大框。
        """
        b = [child.x0, child.y0, child.x1, child.y1]
        if run is None:
            ink[-1].append(b)
            return b
        vov = min(b[3], run[3]) - max(b[1], run[1])
        hmin = min(b[3] - b[1], run[3] - run[1])
        if vov > self.RUN_VOV_RATIO * hmin and (b[0] - run[2]) <= self.RUN_GAP_EM * child.size:
            run[0] = min(run[0], b[0])
            run[1] = min(run[1], b[1])
            run[2] = max(run[2], b[2])
            run[3] = max(run[3], b[3])
            return run
        ink[-1].append(b)
        return b

    def _whiten_ops(self, ink: list, news: list) -> str:
        """为**被重绘的源文字行框**铺白 —— 底图墨迹先让位, 译文再压上。

        与 BabelDOC `ocr_workaround` 同法(白矩形画在最底层), 差别在**范围**: 取逐行紧致
        墨迹框(ink, 见 receive_layout 内 run 合并), 而非「段落包围盒 ∪ 版面区域框」。

        为什么不用段落包围盒: pstk 的 x0..y1 是逐字符取并集得到的, 而**保留区域**
        (layout 里 cls==0: abandon/figure/table/isolate_formula/formula_caption)内的
        OCR 字符也会并进去 —— 一张图里若散布几个文字层字符, 段落盒就被撑到覆盖整张图,
        铺白即抹图(实测 Johnson p2 右侧图区 8600 px 墨迹被一次抹掉)。逐行紧致框不会
        跨越大间隙, 图区自然不被覆盖。

        该段若没渲出任何东西(news 为空), 则不清底 —— 否则源文被抹而译文没来, 涂出空白。
        """
        parts = []
        for i in range(min(len(ink), len(news))):
            if not (news[i] or "").strip():
                continue
            for (x0, y0, x1, y1) in ink[i]:
                w, h = x1 - x0, y1 - y0
                if w <= 0 or h <= 0:
                    continue
                parts.append("%f %f %f %f re f " % (
                    x0 - self.WHITEN_PAD, y0 - self.WHITEN_PAD,
                    w + 2 * self.WHITEN_PAD, h + 2 * self.WHITEN_PAD))
        if not parts:
            return ""
        return "q 1 1 1 rg " + "".join(parts) + "Q "

    def receive_layout(self, ltpage: LTPage):
        # 段落
        sstk: list[str] = []            # 段落文字栈
        pstk: list[Paragraph] = []      # 段落属性栈
        ink: list[list[list[float]]] = []   # [v29] 与 pstk 平行: 每段的「紧致墨迹行框」组
        run = None                      # [v29] 当前正在累积的行框(见 _push_ink)
        vbkt: int = 0                   # 段落公式括号计数
        # 公式组
        vstk: list[LTChar] = []         # 公式符号组
        vlstk: list[LTLine] = []        # 公式线条组
        vfix: float = 0                 # 公式纵向偏移
        # 公式组栈
        var: list[list[LTChar]] = []    # 公式符号组栈
        varl: list[list[LTLine]] = []   # 公式线条组栈
        varf: list[float] = []          # 公式纵向偏移栈
        vlen: list[float] = []          # 公式宽度栈
        # 全局
        lstk: list[LTLine] = []         # 全局线条栈
        xt: LTChar = None               # 上一个字符
        xt_cls: int = -2                # 上一个字符所属段落，保证无论第一个字符属于哪个类别都可以触发新段落
                                        # [v29] 哨兵取 -2: high_level 现在用 -1 表示图/表区(见 graphic_cls)
        vmax: float = ltpage.width / 4  # 行内公式最大宽度
        ops: str = ""                   # 渲染结果

        def vflag(font: str, char: str):    # 匹配公式（和角标）字体
            if isinstance(font, bytes):     # 不一定能 decode，直接转 str
                try:
                    font = font.decode('utf-8')  # 尝试使用 UTF-8 解码
                except UnicodeDecodeError:
                    font = ""
            font = font.split("+")[-1]      # 字体名截断
            if re.match(r"\(cid:", char):
                return True
            # 基于字体名规则的判定
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if re.match(                                            # latex 字体
                    r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Ital|.*Sym|.*Math)",
                    font,
                ):
                    return True
            # 基于字符集规则的判定
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "                                     # 非空格
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]   # 文字修饰符、数学符号、分隔符号
                        or ord(char[0]) in range(0x370, 0x400)          # 希腊字母
                    )
                ):
                    return True
            return False

        ############################################################
        # A. 原文档解析
        for child in ltpage:
            if isinstance(child, LTChar):
                cur_v = False
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前字符在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                # 锚定文档中 bullet 的位置
                if child.get_text() == "•":
                    cls = 0
                # 判定当前字符是否属于公式
                if (                                                                                        # 判定当前字符是否属于公式
                    cls <= 0                                                                                # 1. 类别为保留区域(-1 图/表区, 0 页眉页脚/公式区)
                    or (cls == xt_cls and len(sstk[-1].strip()) > 1 and child.size < pstk[-1].size * 0.79)  # 2. 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                    or vflag(child.fontname, child.get_text())                                              # 3. 公式字体
                    or (child.matrix[0] == 0 and child.matrix[3] == 0)                                      # 4. 垂直字体
                ):
                    cur_v = True
                # 判定括号组是否属于公式
                if not cur_v:
                    if vstk and child.get_text() == "(":
                        cur_v = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        vbkt -= 1
                if (                                                        # 判定当前公式是否结束
                    not cur_v                                               # 1. 当前字符不属于公式
                    or cls != xt_cls                                        # 2. 当前字符与前一个字符不属于同一段落
                    # or (abs(child.x0 - xt.x0) > vmax and cls != 0)        # 3. 段落内换行，可能是一长串斜体的段落，也可能是段内分式换行，这里设个阈值进行区分
                    # 禁止纯公式（代码）段落换行，直到文字开始再重开文字段落，保证只存在两种情况
                    # A. 纯公式（代码）段落（锚定绝对位置）sstk[-1]=="" -> sstk[-1]=="{v*}"
                    # B. 文字开头段落（排版相对位置）sstk[-1]!=""
                    or (sstk[-1] != "" and abs(child.x0 - xt.x0) > vmax)    # 因为 cls==xt_cls==0 一定有 sstk[-1]==""，所以这里不需要再判定 cls!=0
                ):
                    if vstk:
                        if (                                                # 根据公式右侧的文字修正公式的纵向偏移
                            not cur_v                                       # 1. 当前字符不属于公式
                            and cls == xt_cls                               # 2. 当前字符与前一个字符属于同一段落
                            and child.x0 > max([vch.x0 for vch in vstk])    # 3. 当前字符在公式右侧
                        ):
                            vfix = vstk[0].y0 - child.y0
                        if sstk[-1] == "":
                            xt_cls = -1 # 禁止纯公式段落（sstk[-1]=="{v*}"）的后续连接，但是要考虑新字符和后续字符的连接，所以这里修改的是上个字符的类别
                        sstk[-1] += f"{{v{len(var)}}}"
                        var.append(vstk)
                        varl.append(vlstk)
                        varf.append(vfix)
                        vstk = []
                        vlstk = []
                        vfix = 0
                # 当前字符不属于公式或当前字符是公式的第一个字符
                if not vstk:
                    if cls == xt_cls:               # 当前字符与前一个字符属于同一段落
                        if child.x0 > xt.x1 + 1:    # 添加行内空格
                            sstk[-1] += " "
                        elif child.x1 < xt.x0:      # 添加换行空格并标记原文段落存在换行
                            sstk[-1] += " "
                            pstk[-1].brk = True
                    else:                           # 根据当前字符构建一个新的段落
                        sstk.append("")
                        pstk.append(Paragraph(child.y0, child.x0, child.x0, child.x0, child.y0, child.y1, child.size, False))
                        ink.append([])              # [v29] 段落的墨迹行框组
                        run = None                  # [v29] 段落边界处必须断框
                if not cur_v:                                               # 文字入栈
                    if (                                                    # 根据当前字符修正段落属性
                        child.size > pstk[-1].size                          # 1. 当前字符比段落字体大
                        or len(sstk[-1].strip()) == 1                       # 2. 当前字符为段落第二个文字（考虑首字母放大的情况）
                    ) and child.get_text() != " ":                          # 3. 当前字符不是空格
                        pstk[-1].y -= child.size - pstk[-1].size            # 修正段落初始纵坐标，假设两个不同大小字符的上边界对齐
                        pstk[-1].size = child.size
                    sstk[-1] += child.get_text()
                else:                                                       # 公式入栈
                    if (                                                    # 根据公式左侧的文字修正公式的纵向偏移
                        not vstk                                            # 1. 当前字符是公式的第一个字符
                        and cls == xt_cls                                   # 2. 当前字符与前一个字符属于同一段落
                        and child.x0 > xt.x0                                # 3. 前一个字符在公式左侧
                    ):
                        vfix = child.y0 - xt.y0
                    vstk.append(child)
                # 更新段落边界，因为段落内换行之后可能是公式开头，所以要在外边处理
                pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                pstk[-1].y0 = min(pstk[-1].y0, child.y0)
                pstk[-1].y1 = max(pstk[-1].y1, child.y1)
                if ink and cls >= 0:                            # [v29] 记录紧致墨迹行框(清底用)
                    run = self._push_ink(ink, run, child)       #       cls<0 图/表区: 墨迹是图本身, 不入框
                # 更新上一个字符
                xt = child
                xt_cls = cls
            elif isinstance(child, LTFigure):   # 图表
                pass
            elif isinstance(child, LTLine):     # 线条
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前线条在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if vstk and cls == xt_cls:      # 公式线条
                    vlstk.append(child)
                else:                           # 全局线条
                    lstk.append(child)
            else:
                pass
        # 处理结尾
        if vstk:    # 公式出栈
            sstk[-1] += f"{{v{len(var)}}}"
            var.append(vstk)
            varl.append(vlstk)
            varf.append(vfix)
        log.debug("\n==========[VSTACK]==========\n")
        for id, v in enumerate(var):  # 计算公式宽度
            l = max([vch.x1 for vch in v]) - v[0].x0
            log.debug(f'< {l:.1f} {v[0].x0:.1f} {v[0].y0:.1f} {v[0].cid} {v[0].fontname} {len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')
            vlen.append(l)

        ############################################################
        # B. 段落翻译
        log.debug("\n==========[SSTACK]==========\n")

        # [自研补丁 2026-09-12 v23.4] 跨页断句前瞻上下文(瓦片错落式重叠):
        # 长段落被页界切成两段时, 前段以半句结尾(实测 "...three successful"),
        # LLM 只见残文只能猜译(译成"三倍于")。此处检测"前段无句末标点 +
        # 后段小写开头"的续接对, 把后段开头 ~120 字符作为**只读前瞻**注入
        # prompt(不翻译不渲染, 不产生重复文字), 使残句按完整语义译出并在
        # 原断点收住。前瞻指纹经 _cache_key_suffix 编入缓存键: 上下文不同
        # 即重译, 上下文相同则稳定命中。
        def _ends_mid_sentence(s: str) -> bool:
            s = (s or "").rstrip()
            if len(s) < 2 or re.match(r"^\{v\d+\}$", s):
                return False
            return s[-1] not in ".!?。！？…：;；:)]}\"”’"

        def _starts_lowercase(s: str) -> bool:
            s = (s or "").lstrip()
            return bool(s) and "a" <= s[0] <= "z"

        def _lookahead_of(i: int) -> str:
            if i + 1 >= len(sstk):
                return ""
            if not _ends_mid_sentence(sstk[i]):
                return ""
            # 跨页对之间常夹着页码/页眉/页脚小段(实测 "198" "M.C. Mandujano
            # et al."), 不能只看相邻段: 向前最多扫 3 段, 跳过短的非小写段,
            # 遇到小写开头即认定续段; 遇到长的大写开头段判为真新段落放弃。
            # 注意: sstk 按页重建, 跨页对分属两次 receive_layout, 本机制只能
            # 覆盖"页内断句"场景; 跨页断句走缓存手术(改动记录 v23.3)。
            for j in range(i + 1, min(i + 4, len(sstk))):
                nxt = sstk[j]
                if not nxt.strip() or re.match(r"^\{v\d+\}$", nxt.strip()):
                    continue
                if _starts_lowercase(nxt):
                    head = nxt.lstrip()[:120]
                    cut = head.rfind(" ")
                    if cut > 40:
                        head = head[:cut]
                    print(f"[前瞻] 段{i} 尾…{sstk[i].rstrip()[-20:]!r} "
                          f"→ 前瞻={head[:40]!r}", flush=True)
                    return head.strip()
                if len(nxt.strip()) > 80:
                    return ""
            return ""

        heads = [_lookahead_of(i) for i in range(len(sstk))]

        # [自研补丁 2026-09-12 v24-A 文档摘要前置] 流式架构下翻译第 1 页时
        # 全文尚未解析, 用首页文本(标题+摘要+引言, 信息密度最高)生成一次
        # "文档画像", 此后每段 prompt 都携带全局上下文 —— 实现用户要求的
        # "通读全文"意识而不破坏切片/缓存/版面三约束。摘要落盘
        # (~/.cache/pdf2zh/docsummary/), 同文档永远复用同一份, 键不漂移;
        # fp 经 add_params 进键, 旧条目走 v23 旧形态回查, 零重译迁移。
        self._maybe_summarize_document(ltpage, sstk, var)

        @retry(wait=wait_fixed(1))
        def worker(s: str, head: str = ""):  # 多线程翻译
            if not s.strip() or re.match(r"^\{v\d+\}$", s):  # 空白和公式不翻译
                return s
            # [自研补丁] 占位符对照表注入: {vN} -> 公式原文(来自 var[id] 的字符流)。
            # 写入 translator 的线程局部存储(_tls), 润色管线据此告知 LLM 每个占位符
            # 的数学含义, 使带公式句子的译文语序正确; 报告侧也可读它把 {vN} 还原成可读文本。
            # 只读注入, 无竞态; 取不到时静默跳过(不影响翻译)。
            try:
                _tr = self.translator
                if hasattr(_tr, "_tls"):
                    _ids = re.findall(r"\{v(\d+)\}", s)
                    if _ids:
                        _tr._tls.formula_map = {
                            "{v%s}" % _i:
                                "".join(ch.get_text() for ch in var[int(_i)]).strip()
                            for _i in _ids if int(_i) < len(var)
                        }
                    else:
                        _tr._tls.formula_map = {}
            except Exception:
                pass
            # [v23.4] 前瞻上下文注入 (跨页断句瓦片错落): 每段无条件刷新,
            # 防止线程复用把上一段的前瞻泄漏到本段。
            try:
                _tr = self.translator
                if hasattr(_tr, "_tls"):
                    _tr._tls.look_ahead = head or ""
            except Exception:
                pass
            try:
                new = self.translator.translate(s)
                return new
            except BaseException as e:
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                raise e
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.thread
        ) as executor:
            news = list(executor.map(worker, sstk, heads))

        # [自研补丁 2026-09-12 v24b 军师层·段序导出] 全文档顺序的 (原文, 译文)
        # 流水逐页追加到 sidecar (JSONL, 每行一页)。任务完成后军师脚本
        # (tools/strategist.py) 串读全文, 输出跨段指代/接缝/术语/文风修正,
        # 写回缓存后 force 重渲染生效。首页处理时截断旧文件(一子进程一文档)。
        # [v28.9] 双写: 除全局 latest.jsonl 外, 再按**文档内容散列**落一份归档件
        # (见 _segflow_paths), 供 seg_export/adopt export 按篇取用。
        try:
            if sstk:
                _sf_path, _sf_doc = self._segflow_paths()
                if not getattr(self, "_segflow_fresh", False):
                    for _p in (_sf_path, _sf_doc):
                        if _p:
                            with open(_p, "w", encoding="utf-8") as f:
                                pass
                    self._segflow_fresh = True
                # [v24b.2] 占位符图例: {vN} → 背后真实字形文本。军师只看译文字符串,
                # 永远不知道 {v6}/{v7} 其实就是数字 3/2, 会把"丢失的 3:2"当成漏译
                # 而重复插入(实测渲染出 "33:22")。字形本就在 var[] 里, 一并导出
                # 是零成本的"等效视觉", 不必为此上多模态模型。var 是页内局部表,
                # 故图例按页携带(同号跨页含义可不同)。
                _used = set()
                for _s in list(sstk) + list(news):
                    for _m in re.finditer(r"\{v(\d+)\}", _s or ""):
                        _used.add(int(_m.group(1)))
                _legend = {}
                for _i in sorted(_used):
                    try:
                        _legend[str(_i)] = "".join(c.get_text() for c in var[_i])
                    except Exception:
                        pass
                # [自研补丁 2026-09-12 v26-L1] 页号: 军师把 sidecar 的行当成"语段"
                # 而不是"页", 于是敢给页尾残句补字(实测 idx8 补"配子", 与下一页
                # 首行原文重复)。receive_layout 每页回调一次, 故调用次序即页序
                # (首页判定 _segflow_fresh 已在用同一假设)。页号随行导出, 军师侧
                # 在页与页之间插显式页边界标记, 并声明"页边界≠语段边界"。
                _pageno = getattr(self, "_segflow_pageno", 0) + 1
                self._segflow_pageno = _pageno
                # [自研补丁 2026-09-19 v28.23] 文档画像指纹随行导出。
                # 为什么必须带出来: adopt export 原先靠"库内命中行"反探指纹来决定
                # 注入用的键形态; 而暂停档(PAUSE_TRANSLATE=1)第一趟**一列都不写**
                # (死规矩: 写进英文 = 第二轮命中英文 = 全篇英文 PDF), 于是反探必然
                # 0 命中 -> export 拒导 -> 整个两趟回路的第一半直接失效(实测: 服务端
                # 静默回落成付费机器翻译)。指纹本来就在引擎手里(缓存参数), 顺手带
                # 出来最省事, 也让下游不必再从库行反推(反推本就脆弱: 库行是翻过的
                # 产物, 骨架档没有产物)。
                _doc_fp = ""
                try:
                    _tr = getattr(self, "translator", None)
                    _doc_fp = ((getattr(_tr, "cache", None) and _tr.cache.params) or {}).get(
                        "doc_summary_fp") or ""
                except Exception:
                    _doc_fp = ""
                rec = {
                    "page": _pageno,
                    "pageid": ltpage.pageid,
                    "segs": [{"raw": s, "trans": n} for s, n in zip(sstk, news)],
                    "vars": _legend,
                    "doc_fp": _doc_fp,
                }
                _line = json.dumps(rec, ensure_ascii=False) + "\n"
                for _p in (_sf_path, _sf_doc):   # latest.jsonl + 按文档归档件
                    if _p:
                        with open(_p, "a", encoding="utf-8") as f:
                            f.write(_line)
        except Exception as _e:
            print(f"⚠️ [军师·段序导出] 失败: {_e}", flush=True)

        # [自研补丁 2026-09-06] 纯标点占位符消除(翻译后、排版前):
        # 原文 PDF 的连字符/逗号/句点/括号若以 (cid:NN) 形式被 pdfminer 解出,
        # 会命中 vflag 首条规则被判为公式, 包成 {vN} 占位符; LLM 只能围绕占位符
        # 行文, 渲染时原字体标点回填到汉字之间, 形成"滤波-的""九-室""先,，"
        # 等机械伤。该字符藏在占位符里, 译文清洗层不可见, 故在拿到译文后、
        # 排版前, 将"内容为孤立半角标点且紧邻汉字(或全角标点)"的占位符就地
        # 消除: 句读类转全角, 连字符/括号直接删除, 随后收敛重复标点。
        # 安全性: 公式内容(含数字/字母/数学符号)不匹配; 英文/数字邻接不触发;
        # 数学减号(U+2212, Sm 类)不在消除集; 修改发生在翻译之后, 翻译缓存键
        # 不变, 已缓存段落全部命中; 未被引用的 var 条目不会被渲染, 无副作用。
        # [自研补丁 2026-09-10 v22] 半/全角标点等价表: 邻接去重判定用,
        # 使字面半角标点与映射出的全角标点可比较。
        _NB2FULL = str.maketrans(",.;:()", "，。；：()")
        def _strip_punct_placeholders(text: str) -> str:
            def _norm(ch) -> str:
                t = ch.get_text()
                m = re.match(r"\(cid:(\d+)\)", t)
                return chr(int(m.group(1))) if m else t

            def repl(m):
                idx = int(m.group(1))
                if idx >= len(var):
                    return m.group(0)
                content = "".join(_norm(ch) for ch in var[idx]).strip()
                # [2026-09-10] 字符集扩充: 原文连字符常为 Unicode dash 变体
                # (– — ‐ ‑ ‒), 仅 ASCII "-" 会漏网(实测 "侧向力-的项" 残留)。
                # 数学减号 U+2212 不入删除集(公式语义), 但孤立夹汉字间时转"——"。
                _DASH = "-\u2010\u2011\u2012\u2013\u2014\u2015"
                if content in _DASH:
                    return ""
                if content == "\u2212":
                    return "——"
                if content in (",", ".", ";", ":", "(", ")"):
                    pass
                else:
                    return m.group(0)
                start, end = m.span()
                left = text[start - 1] if start > 0 else ""
                right = text[end] if end < len(text) else ""

                def cjkish(c):
                    return bool(c) and (
                        "\u4e00" <= c <= "\u9fff" or c in "，。；：！？、《》（）"
                    )

                if not (cjkish(left) or cjkish(right)):
                    return m.group(0)
                mapped = {",": "，", ".": "。", ";": "；", ":": "：", "(": "（", ")": "）"}[content]
                # [自研补丁 2026-09-10 v22] 邻接冗余标点去重: LLM 重排句式后
                # 自带标点, 占位符标点回填后与之相邻叠加(Melhani 实测
                # "（（EKF））" ×35+"，。" ×14)。判定时半/全角等价
                # (_NB2FULL), 同向括号或同类句读相邻 → 占位符标点冗余, 删除。
                _nb = lambda ch: ch.translate(_NB2FULL) if ch else ch
                if mapped in "（）":
                    if _nb(left) == mapped or _nb(right) == mapped:
                        return ""
                else:
                    if _nb(left) == mapped or _nb(right) == mapped:
                        return ""
                    if mapped in "，；：" and _nb(right) in "。，；":
                        return ""
                    if mapped in "，；：" and _nb(left) == "。":
                        return ""
                return mapped

            return re.sub(r"\{v(\d+)\}", repl, text)

        # [自研补丁 2026-09-07] 混合占位符首尾标点整形(v20 的补集):
        # v20 只消除"孤立纯标点"占位符; 实际 PDF 中标点常与数字/字母连成
        # 同一 var 段(如 ", 2006" "3 (" "(3)"), 整段回填时首尾半角标点
        # 夹在汉字之间形成机械伤(实例: "新泽西州, 2006." "步骤 3 ( 窗口")。
        # 本函数把这类混合段的**首尾标点**抽出来整形(句读转全角, 连字符/
        # 方括号删除, 圆括号转全角), 剩余主体重开 var 条目继续原字体回填。
        # [2026-09-10] 标点集扩充 Unicode dash 变体(– — ‐ ‑ ‒), 与 v20 对齐。
        _MIX_PUNCT = "-\u2010\u2011\u2012\u2013\u2014\u2015[]{}.,;:()"
        _MIX_RE = re.compile(
            r"([-\u2010\u2011\u2012\u2013\u2014\u2015\[\]{}.,;:()\s]*)([0-9A-Za-z][0-9A-Za-z\s]*[0-9A-Za-z%]|[0-9A-Za-z])([-\u2010\u2011\u2012\u2013\u2014\u2015\[\]{}.,;:()\s]*)"
        )

        def _map_head_punct(ch: str) -> str:
            # 段首标点: 句点多为编号/缩写点(Fig. / No.), 删除比转句号安全
            # [2026-09-10 修正] 补 []{} 原样保留: 引用标号 "[23]" 的方括号
            # 此前落入 dict.get 默认 "" 被整批删除, 导致质检引用对账
            # 116→68 FAIL(版面标号丢失)。删除类只留断字符 "-"。
            return {
                ",": "，", ";": "；", ":": "：", ".": "",
                "(": "（", ")": "）",
                "[": "[", "]": "]", "{": "{", "}": "}",
            }.get(ch, "")

        def _map_tail_punct(ch: str) -> str:
            return {
                ",": "，", ".": "。", ";": "；", ":": "：",
                "(": "（", ")": "）",
                "[": "[", "]": "]", "{": "{", "}": "}",
            }.get(ch, "")

        def _split_mixed_placeholders(text: str) -> str:
            def _norm(ch) -> str:
                t = ch.get_text()
                m = re.match(r"\(cid:(\d+)\)", t)
                return chr(int(m.group(1))) if m else t

            def repl(m):
                idx = int(m.group(1))
                if idx >= len(var):
                    return m.group(0)
                chars = var[idx]
                content = "".join(_norm(c) for c in chars).strip()
                if not content or re.fullmatch(
                    r"[-\u2010\u2011\u2012\u2013\u2014\u2015\[\]{}.,;:()]+", content
                ):
                    return m.group(0)  # 纯标点: 交给 v20(已扩 Unicode dash)
                mm = _MIX_RE.fullmatch(content)
                if not mm:
                    return m.group(0)  # 含其他结构 → 真公式等, 不动
                head_raw, _body, tail_raw = mm.group(1), mm.group(2), mm.group(3)
                # 负数守卫: "-5" 的连字符是真负号, 删除会破坏语义
                # (装饰断字符 "-level" 的 body 以字母开头, 不受影响)。
                _D = "-\u2010\u2011\u2012\u2013\u2014\u2015"
                if (head_raw.rstrip()[-1:] in _D and _body[:1].isdigit()) or (
                    tail_raw.lstrip()[:1] in _D and _body[-1:].isdigit()
                ):
                    return m.group(0)
                head = "".join(_map_head_punct(c) for c in head_raw.strip())
                tail = "".join(_map_tail_punct(c) for c in tail_raw.strip())
                # 触发判定用原始标点段(非映射结果): 删除型标点(如连字符)映射后
                # 为空串, 但仍需重开 var 把该字符从回填流中剔除。
                if not head_raw.strip() and not tail_raw.strip():
                    return m.group(0)
                start, end = m.span()
                left = text[start - 1] if start > 0 else ""
                right = text[end] if end < len(text) else ""

                def cjkish(c):
                    return bool(c) and (
                        "\u4e00" <= c <= "\u9fff" or c in "，。；：！？、《》（）"
                    )

                if not (cjkish(left) or cjkish(right)):
                    return m.group(0)
                # 主体重开 var 条目: 保留 body 对应的原字符(原字体回填),
                # 剔除首尾标点字符; 主体内部空格随边界自然收拢。
                body_chars = _extract_body_chars(chars, _body, _norm)
                if not body_chars:
                    return m.group(0)
                new_id = len(var)
                var.append(body_chars)
                varl.append([])
                varf.append(0)
                # 宽度按主体首尾字符的横向跨度重算, 供排版用
                try:
                    _l = max(c.x1 for c in body_chars) - body_chars[0].x0
                except Exception:
                    _l = vlen[idx]
                vlen.append(_l)
                # [自研补丁 2026-09-10 v22] 首尾映射标点与相邻字面标点同类即
                # 冗余(半/全角等价, 与 v20 去重同规则): 逐个剥离首/尾重复标点。
                _lft = left.translate(_NB2FULL) if left else "\x00"
                _rgt = right.translate(_NB2FULL) if right else "\x00"
                while head and head[0].translate(_NB2FULL) == _lft:
                    head = head[1:]
                while tail and tail[-1].translate(_NB2FULL) == _rgt:
                    tail = tail[:-1]
                return f"{head}{{v{new_id}}}{tail}"

            def _extract_body_chars(chars, body, norm):
                """按原字符流顺序取出组成 body 的连续字符(含主体内部空格)。"""
                # 去掉首尾标点/空格后, body 与 chars 的尾段子序列一一对应;
                # 跳过首尾的标点与空格。
                seq = [norm(c) for c in chars]
                body_stripped = body.replace(" ", "")
                # 从左找第一个属于 body 的字符: 逐个消费 head 部分
                target = list(body_stripped)
                ti = 0
                picked = []
                started = False
                for c, n in zip(chars, seq):
                    if not started:
                        if n == " " or n in _MIX_PUNCT:
                            continue  # 尚在 head 区
                        started = True
                    if n == " ":
                        # 主体内部空格: 仅当后面还有 body 字符才保留
                        picked.append(c)
                        continue
                    if ti < len(target) and n == target[ti]:
                        picked.append(c)
                        ti += 1
                    else:
                        # 主体结束后的 tail 字符或异常字符: 停止收取
                        if ti >= len(target):
                            break
                        # 异常(匹配错位): 保守放弃整段整形
                        return []
                if ti != len(target):
                    return []
                # 去掉尾部多余空格字符
                while picked and picked[-1].get_text() == " ":
                    picked.pop()
                return picked

            return re.sub(r"\{v(\d+)\}", repl, text)

        news = [_strip_punct_placeholders(n) for n in news]
        try:
            news = [_split_mixed_placeholders(n) for n in news]
        except Exception:
            pass  # 整形失败不阻塞翻译, 退化为 v20 行为
        news = [re.sub(r"([，。；：！？、])\1+", r"\1", n) for n in news]

        ############################################################
        # C. 新文档排版
        def raw_string(fcur: str, cstk: str):  # 编码字符串
            if fcur == self.noto_name:
                return "".join(["%04x" % self.noto.has_glyph(ord(c)) for c in cstk])
            elif isinstance(self.fontmap[fcur], PDFCIDFont):  # 判断编码长度
                return "".join(["%04x" % ord(c) for c in cstk])
            else:
                return "".join(["%02x" % ord(c) for c in cstk])

        # 根据目标语言获取默认行距
        LANG_LINEHEIGHT_MAP = {
            "zh-cn": 1.4, "zh-tw": 1.4, "zh-hans": 1.4, "zh-hant": 1.4, "zh": 1.4,
            "ja": 1.1, "ko": 1.2, "en": 1.2, "ar": 1.0, "ru": 0.8, "uk": 0.8, "ta": 0.8
        }
        default_line_height = LANG_LINEHEIGHT_MAP.get(self.translator.lang_out.lower(), 1.1) # 小语种默认1.1
        _x, _y = 0, 0
        ops_list = []

        def gen_op_txt(font, size, x, y, rtxt):
            return f"/{font} {size:f} Tf 1 0 0 1 {x:f} {y:f} Tm [<{rtxt}>] TJ "

        def gen_op_line(x, y, xlen, ylen, linewidth):
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        for id, new in enumerate(news):
            x: float = pstk[id].x                       # 段落初始横坐标
            y: float = pstk[id].y                       # 段落初始纵坐标
            x0: float = pstk[id].x0                     # 段落左边界
            x1: float = pstk[id].x1                     # 段落右边界
            height: float = pstk[id].y1 - pstk[id].y0   # 段落高度
            size: float = pstk[id].size                 # 段落字体大小
            brk: bool = pstk[id].brk                    # 段落换行标记
            cstk: str = ""                              # 当前文字栈
            fcur: str = None                            # 当前字体 ID
            lidx = 0                                    # 记录换行次数
            tx = x
            fcur_ = fcur
            ptr = 0
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")

            ops_vals: list[dict] = []

            while ptr < len(new):
                vy_regex = re.match(
                    r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE
                )  # 匹配 {vn} 公式标记
                mod = 0  # 文字修饰符
                if vy_regex:  # 加载公式
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        adv = vlen[vid]
                    except Exception:
                        continue  # 翻译器可能会自动补个越界的公式标记
                    if var[vid][-1].get_text() and unicodedata.category(var[vid][-1].get_text()[0]) in ["Lm", "Mn", "Sk"]:  # 文字修饰符
                        mod = var[vid][-1].width
                else:  # 加载文字
                    ch = new[ptr]
                    fcur_ = None
                    try:
                        if fcur_ is None and self.fontmap["tiro"].to_unichr(ord(ch)) == ch:
                            fcur_ = "tiro"  # 默认拉丁字体
                    except Exception:
                        pass
                    if fcur_ is None:
                        fcur_ = self.noto_name  # 默认非拉丁字体
                    if fcur_ == self.noto_name: # FIXME: change to CONST
                        adv = self.noto.char_lengths(ch, size)[0]
                    else:
                        adv = self.fontmap[fcur_].char_width(ord(ch)) * size
                    ptr += 1
                if (                                # 输出文字缓冲区
                    fcur_ != fcur                   # 1. 字体更新
                    or vy_regex                     # 2. 插入公式
                    or x + adv > x1 + 0.1 * size    # 3. 到达右边界（可能一整行都被符号化，这里需要考虑浮点误差）
                ):
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx
                        })
                        cstk = ""
                if brk and x + adv > x1 + 0.1 * size:  # 到达右边界且原文段落存在换行
                    x = x0
                    lidx += 1
                if vy_regex:  # 插入公式
                    fix = 0
                    if fcur is not None:  # 段落内公式修正纵向偏移
                        fix = varf[vid]
                    for vch in var[vid]:  # 排版公式字符
                        vc = chr(vch.cid)
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": self.fontid[vch.font],
                            "size": vch.size,
                            "x": x + vch.x0 - var[vid][0].x0,
                            "dy": fix + vch.y0 - var[vid][0].y0,
                            "rtxt": raw_string(self.fontid[vch.font], vc),
                            "lidx": lidx
                        })
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(LTLine(0.1, (_x, _y), (x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0)))
                            _x, _y = x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0
                    for l in varl[vid]:  # 排版公式线条
                        if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                            ops_vals.append({
                                "type": OpType.LINE,
                                "x": l.pts[0][0] + x - var[vid][0].x0,
                                "dy": l.pts[0][1] + fix - var[vid][0].y0,
                                "linewidth": l.linewidth,
                                "xlen": l.pts[1][0] - l.pts[0][0],
                                "ylen": l.pts[1][1] - l.pts[0][1],
                                "lidx": lidx
                            })
                else:  # 插入文字缓冲区
                    if not cstk:  # 单行开头
                        tx = x
                        if x == x0 and ch == " ":  # 消除段落换行空格
                            adv = 0
                        else:
                            cstk += ch
                    else:
                        cstk += ch
                adv -= mod # 文字修饰符
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y
            # 处理结尾
            if cstk:
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": fcur,
                    "size": size,
                    "x": tx,
                    "dy": 0,
                    "rtxt": raw_string(fcur, cstk),
                    "lidx": lidx
                })

            line_height = default_line_height

            while (lidx + 1) * size * line_height > height and line_height >= 1:
                line_height -= 0.05

            for vals in ops_vals:
                if vals["type"] == OpType.TEXT:
                    ops_list.append(gen_op_txt(vals["font"], vals["size"], vals["x"], vals["dy"] + y - vals["lidx"] * size * line_height, vals["rtxt"]))
                elif vals["type"] == OpType.LINE:
                    ops_list.append(gen_op_line(vals["x"], vals["dy"] + y - vals["lidx"] * size * line_height, vals["xlen"], vals["ylen"], vals["linewidth"]))

        for l in lstk:  # 排版全局线条
            if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                ops_list.append(gen_op_line(l.pts[0][0], l.pts[0][1], l.pts[1][0] - l.pts[0][0], l.pts[1][1] - l.pts[0][1], l.linewidth))

        ops = f"BT {''.join(ops_list)}ET "
        # [v29 扫描件清底] 扫描页(覆盖式位图)在重绘前先清底; 只遮被重绘的源文字行框
        if isinstance(ltpage, LTPage) and self._raster_covers_page(ltpage):
            ops = self._whiten_ops(ink, news) + ops
        return ops


class OpType(Enum):
    TEXT = "text"
    LINE = "line"
