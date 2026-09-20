# -*- coding: utf-8 -*-
"""engine.py — 引擎接缝 (engine seam): 一条产线, 两个引擎。

【为什么要有这一层】
    「交付的第二公里」= 改一处译文 -> 零 LLM、不破坏版面地重渲染, 且每改必验。
    它对引擎的全部依赖只有四件事:
        ① 译文缓存库落在哪         (决定能不能按段 UPDATE 一行)
        ② 段落身份从哪读           (决定改的是不是那一段)
        ③ 缓存行的定位口径是什么    (决定 UPDATE 该命中哪一行)
        ④ 重渲染从哪个入口进        (决定改完的东西怎么落到页上)
    ①② 登记成**引擎画像**(Profile), ③④ 登记成**接线状态**(wired): 接线完成的阶段
    照跑; 未接线的阶段在入口就被拒绝 —— 不许静默按 1.x 口径跑出个"看起来对"的结果。

【两个画像】
    pdf2zh  现产线 pdf2zh 1.x。八项全接线; 也是缺省画像, 行为与本模块引入前一致。
    next    pdf2zh_next 2.9.0 / BabelDOC (实验)。**八项全接线**(B 类收口):
              段表 = <working_root>/<stem>/translate_tracking.json  (须跑 --debug)
              缓存 = ~/.cache/pdf2zh_next/cache.v1.db  (表结构与 1.x 逐字相同)
              渲染 = pdf2zh_next CLI 子进程(没有服务端) —— 上游出错仍退出码 0, 判成败
                     只能看产物; 且 `--debug` 必带(不带时 working_dir 落
                     tempfile.mkdtemp(), 段表根本不落盘), config 里的 ignore_cache 必须
                     为 false(见 force_rerender 的 _off_in_config)。
              撤销 = **行级**还原(不是拿 inject 前的 .bak 整库回滚 —— 缓存库是全机共用的,
                     整库还原会把别的论文合法落下的译文一并退掉; 见 seg_inject.rollback_next)。
            export/import/inject/rollback 能接线, 是因为 {vN}/<style> 在 next 上是**引擎
            原生协议**(上游 prompt 明写不许动, 渲染器自己替换): 输出侧不需要回锚 —— 载荷
            直接带原生形态发出去, 收回来原样写回缓存即生效(见 tracking_payload_items); 而
            缓存键(**整条 prompt**)的原文就记在段表 llm_translate_trackers[].input 里, 于是
            inject 是"精确匹配行 + 改批次 JSON 里 id==multi_paragraph_index 那一条",
            不需要 1.x 的 {vN} 重编号与 doc_summary_fp 作用域(见 seg_cache_targets)。

【本机实测口径 (2026-09-20, 试验台 D:\\zotero-pdf2zh-eval)】
    - 走 pdf2zh_next CLI 时, 生效的是 pdf2zh_next.translator + ~/.cache/pdf2zh_next/
      cache.v1.db; babeldoc 自己那张 ~/.cache/babeldoc/cache.v1.db 全程 0 行。
    - 非 LLM 引擎(bing): 缓存键 = 段落原文, 逐段一行。
      LLM 引擎(siliconflow): 缓存键 = **整条 prompt**(含术语表/标题上下文), 值 = 一批
      段的 JSON 数组。故段表里的 llm prompt 记作 cache_key **线索**, 不是已接线的定位器。
    - pdf2zh_next 出错时退出码仍是 0 (上游 ignore_error=True) -> 判成败只能看产物。
    - 每次运行都会打一次真实探针 translate("Hello", ignore_cache=True) -> 纯离线重渲染
      在新引擎上做不到(除非打补丁)。

环境变量: P2Z_ENGINE=pdf2zh|next  (工具链读它; 显式参数优先)
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys

HOME = os.path.expanduser("~")
CACHE_TABLE = "_translationcache"     # 1.x 与 BabelDOC 都是这张表, 四列同名同约束

# 缺省引擎名。**写死为 pdf2zh** 是刻意的: 本模块上线时现产线是 1.x, 缺省必须与
# 历史行为逐字节一致, 迁移只能是显式选择的结果。
DEFAULT_ENGINE = "pdf2zh"


class Profile(object):
    """一个引擎画像: ①缓存库 ②段表来源 + 接线状态。"""

    def __init__(self, key, label, cache_db, seg_source, sidecar_dir, working_root,
                 cache_engines, wired, notes):
        self.key = key
        self.label = label
        self.cache_db = cache_db
        self.seg_source = seg_source          # "sidecar" | "tracking_json"
        self.sidecar_dir = sidecar_dir        # 侧车目录(仅 1.x); next 为 None
        self.working_root = working_root      # 中间物根(仅 next); 1.x 为 None
        self.cache_engines = cache_engines    # 缓存行里常见的 translate_engine 值(诊断用, 非白名单)
        self.wired = wired                    # 阶段 -> None(已接线) / 未接线理由
        self.notes = notes

    # ---------------------------------------------------------------- 路径
    @property
    def sidecar(self):
        """1.x 的全局侧车 latest.jsonl; next 没有侧车 -> None(调用方必须显式处理)。"""
        return (os.path.join(self.sidecar_dir, "latest.jsonl")
                if self.sidecar_dir else None)

    def sidecar_of(self, pdf_md5_16):
        """按文档归档的侧车路径 (1.x converter 补丁落的那一份)。"""
        if not self.sidecar_dir or not pdf_md5_16:
            return None
        return os.path.join(self.sidecar_dir, "pdf-%s.jsonl" % pdf_md5_16)

    def tracking_json(self, name=""):
        """next 的段表路径: <working_root>/<stem>/translate_tracking.json。

        name 省略时返回 workroot 下**最新**的一份(调试用; 生产必须显式给 name,
        否则会拿到"最近翻过的那一篇"的段表 —— 与 1.x 误用 latest.jsonl 同类错误)。
        """
        if not self.working_root or not os.path.isdir(self.working_root):
            return None
        if name:
            p = os.path.join(self.working_root, name, "translate_tracking.json")
            return p if os.path.exists(p) else None
        hits = glob.glob(os.path.join(self.working_root, "*", "translate_tracking.json"))
        return max(hits, key=os.path.getmtime) if hits else None

    # ---------------------------------------------------------------- 接线
    def stage_ok(self, stage):
        """-> (能不能跑, 不能跑的理由)。理由会原样进 die 文案, 故要写清"缺什么"。"""
        if stage not in self.wired:
            return False, "本画像未登记该阶段"
        why = self.wired[stage]
        return (True, "") if why is None else (False, why)

    @property
    def ok_stages(self):
        return [k for k, v in self.wired.items() if v is None]

    def as_dict(self):
        return {
            "key": self.key, "label": self.label, "cache_db": self.cache_db,
            "seg_source": self.seg_source, "sidecar": self.sidecar,
            "working_root": self.working_root, "cache_engines": list(self.cache_engines),
            "wired": self.wired, "stage_ok": self.ok_stages, "notes": self.notes,
        }


# 未接线理由(集中放, 免得两处措辞漂移; 都是"要在新引擎上重写"的那几段)。
# [v28.43] **已清空** —— next 的 B 类八项全部接线。这份表是留给**下一个**引擎的:
# 加画像时缺哪一项就在这里写清"为什么现在跑不了", stage_ok 会在入口当场拒答(rc=2),
# 而不是让它按 1.x 口径跑出个"看起来对"的结果。
_NEXT_NO = {}

PROFILES = {
    "pdf2zh": Profile(
        key="pdf2zh",
        label="pdf2zh 1.x (现产线)",
        cache_db=os.path.join(HOME, ".cache", "pdf2zh", "cache.v1.db"),
        seg_source="sidecar",
        sidecar_dir=os.path.join(HOME, ".cache", "pdf2zh", "segflow"),
        working_root=None,
        cache_engines=("silicon", "bing"),
        wired={s: None for s in ("export", "deliver", "import", "inject", "render", "gate",
                                 "rollback", "status")},
        notes="段表 = segflow/latest.jsonl (自研 converter 补丁落盘, 每篇另有按 md5 归档件); "
              "缓存键 = 段落原文, 定位口径与 {vN} 回锚归 seg_export/seg_import/seg_inject",
    ),
    "next": Profile(
        key="next",
        label="pdf2zh_next 2.9.0 / BabelDOC (实验)",
        cache_db=os.path.join(HOME, ".cache", "pdf2zh_next", "cache.v1.db"),
        seg_source="tracking_json",
        sidecar_dir=None,
        working_root=os.path.join(HOME, ".cache", "babeldoc", "working"),
        cache_engines=("siliconflow", "bing", "siliconflowfree"),
        wired=dict({"export": None, "import": None, "inject": None, "deliver": None,
                    "gate": None, "status": None, "render": None, "rollback": None},
                   **_NEXT_NO),
        notes="段表 = working/<stem>/translate_tracking.json (须 --debug; 上游无 working_dir 参数); "
              "缓存键 = **整条 prompt**, 但该 prompt 原文就存在段表的 llm_translate_trackers[].input "
              "里 -> inject 是精确匹配, 不需要 1.x 的文档指纹作用域; "
              "render = pdf2zh_next CLI 子进程(无服务端/不轮询 taskId), 判成败只看产物, "
              "且 config 的 ignore_cache 必须为 false 否则不读也不写缓存; "
              "rollback = 逐段**行级**还原(不整库回滚 .bak: 缓存库全机共用, 会退掉别的篇); "
              "gate(verify_render + post_check)纯读产物, 两个引擎共用",
    ),
}


def select(name=None):
    """按名字取画像; 未知名**报错而不是回落缺省** —— 回落会让 next 的活干到 1.x 库上。"""
    key = (name or os.environ.get("P2Z_ENGINE") or DEFAULT_ENGINE).strip()
    if key not in PROFILES:
        raise ValueError("未知引擎 %r (可选: %s)" % (key, "|".join(sorted(PROFILES))))
    return PROFILES[key]


def active():
    return select(None)


# -------------------------------------------------------------------- 段表
def load_tracking(path):
    """读并校验 translate_tracking.json -> dict(原始结构, 只读)。"""
    if not path or not os.path.exists(path):
        raise IOError("段表不存在: %s" % (path or "(空路径)"))
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or "page" not in d:
        raise ValueError("不是 translate_tracking.json (缺 page 键): %s" % path)
    return d


def _norm_paragraph(p, seq, page, idx):
    """一段(pdf_paragraph) -> 段表契约条目。字段含义见 read_tracking_segments。"""
    lls = [t for t in (p.get("llm_translate_trackers") or []) if isinstance(t, dict)]
    prompt = ((lls[0].get("input") or "") if lls else "")
    return {
        "seq": seq,
        "page": page,
        "idx": idx,
        "src": p.get("input") or "",
        "raw": p.get("pdf_unicode") or "",
        "dst": p.get("output") or "",
        "cache_key": prompt or (p.get("input") or ""),
        "batch": (p.get("multi_paragraph_id"), p.get("multi_paragraph_index")),
        "n_ph": len(p.get("placeholders") or []),
        "err": bool(lls[0].get("has_error")) if lls else False,
    }


def read_tracking_segments(path):
    """读 BabelDOC 的 translate_tracking.json -> 归一化段表(**只读**)。

    返回 list[dict], 字段即本层对外的段表契约:
        seq        全篇连续段号(1-based, 按页序拼)
        page       页码(1-based; tracking 的 page 数组下标 + 1)
        idx        页内段序(0-based)
        src        喂给翻译的原文(= 非 LLM 通道的缓存键)
        raw        原始 unicode(pdf_unicode, 占位符已还原 -> 真正的"原文长什么样")
        dst        现有译文(output)
        cache_key  定位译文缓存行的线索: LLM 通道 = 那条 llm prompt(键就是它),
                   非 LLM 通道 = src。**线索, 不是已接线的定位器**
        batch      (multi_paragraph_id, multi_paragraph_index): 上游把几段并成一次调用时才有
        n_ph       占位符个数
        err        上游这一次是否报错(llm tracker 的 has_error)

    注意: 这里只有 page[] 池。上游把"跨页配对"的两段从 page[] 摘出去单独翻
    (cross_page 池), 那些段**不在**本函数的返回里 —— 要完整载荷用
    tracking_payload_items()。
    """
    d = load_tracking(path)
    out, seq = [], 0
    for pi, pg in enumerate(d.get("page") or []):
        for si, p in enumerate((pg or {}).get("paragraph") or []):
            if not isinstance(p, dict):
                continue
            seq += 1
            out.append(_norm_paragraph(p, seq, pi + 1, si))
    return out


def read_segments(profile=None, tracking="", name=""):
    """按画像取段表。1.x 侧车**故意不复刻**: 它的分块/⋮/合并段口径在
    seg_export/seg_import 里, 在这里重写一份必然漂移。"""
    p = profile or active()
    if p.seg_source == "tracking_json":
        return read_tracking_segments(tracking or p.tracking_json(name))
    raise NotImplementedError(
        "画像 %s 的段表由 seg_export/seg_import 负责(分块与 ⋮ 口径在那边), 本层不复刻"
        % p.key)


# ------------------------------------------------------- 段表 -> 载荷条目 (next export)
# [自研补丁 2026-09-20] next 画像的 export: translate_tracking.json -> `#S` 载荷条目。
#
# 与 1.x 侧车路线的三处**本质**差别(照搬 1.x 口径必错):
#   ① 正文取 `input`(引擎原生形态), **不取** `pdf_unicode`。后者是"占位符已还原的
#      显示形态": 1.x 之所以要回锚 {vN}, 是因为它把显示形态喂给了译者; BabelDOC 的
#      {vN}/<style> 是引擎原生协议(上游 prompt 明写不许动、渲染器自己替换), 载荷
#      直接带原生形态发出去, 收回来的译文里 {vN} 照样落回原字体/原字形。
#      反过来把显示形态发出去 = 显示字形进译文 = 中文字体没有该码位 = 豆腐块 +
#      提取 NUL(v28.38 事故的成因)。
#   ② 不注入 ⋮。⋮ 是 1.x 侧车"跨页续接被合并成一条"的断点标记; 上游把跨页段落整段
#      并成一次调用, 每个段本身就是完整段, 没有断点。
#   ③ cross_page 池必须并进载荷。上游把"上页末段 + 下页首段"从各自 page[] 里**摘掉**
#      (translated_ids)单独翻, 所以它们在 page[] 里一个都找不到 —— 漏掉 = 每页页尾/
#      页首的正文永远译不到、也永远改不了(实测该篇 32 段, 含摘要整段)。
_MARK_TAG = re.compile(r"</?[A-Za-z][^<>]{0,80}>")
_MARK_PH = re.compile(r"\{[^{}\s]{0,40}\}")
_PH_ONLY = re.compile(r"^(?:\{[^{}\s]{0,40}\}\s*)+$")


def literal_text(s):
    """剥掉引擎原生标记(<style id='1'>…</style> 的标签与 {vN}) -> 裸文字。

    **只给"这段有没有可译文字"的判断用, 不进载荷**(载荷要的是原生形态)。
    """
    return re.sub(r"\s+", " ", _MARK_PH.sub(" ", _MARK_TAG.sub(" ", s or ""))).strip()


def literal_runs(s):
    """原文里**连续的字面片段** —— 被 {vN}/<style> 打断的地方切开。

    锚定必须用这种片段: 整段中间有占位符的"空洞"(原文里是公式/换字体的那几个字),
    在 PDF 抽出的文本里对不上, 只有这些连续片段能整段照抄下来。
    """
    t = _MARK_PH.sub("\x00", _MARK_TAG.sub("\x00", s or ""))
    return [p.strip() for p in t.split("\x00") if p.strip()]


def _squash(s):
    """去掉所有空白 —— PDF 抽出的文本会在换行/分栏处插空格, 段表里的原文没有。"""
    return re.sub(r"\s+", "", s or "")


def _dehyph(s):
    """去空白 + 抹掉断词连字符 —— pypdf 会把 "obser-\\nvation" 抽成 "obser- vation"。

    两侧(页文本与锚串)用**同一个**变换, 所以连字符本来就在的复合词(如 Lovett-Doust)
    两边一起被抹平, 照样对得上。
    """
    return re.sub(r"[-\xad]\s*", "", _squash(s))


def anchor_runs(s, want=6, shortest=20):
    """把一段原文切成"在源 PDF 里找页"用的锚串(长 -> 短)。

    优先取**连续字面片段**里最长的几个: 越长越不可能在别页重复。片段本身可能被
    PDF 的换行/断词切开, 故锚定时会再用去空白/去连字符两个变体各试一遍。
    """
    runs = [p for p in literal_runs(s) if len(p) >= shortest]
    runs.sort(key=len, reverse=True)
    if not runs:
        t = literal_text(s)
        runs = [t] if len(t) >= 8 else []
    return runs[:want]


def anchor_page(runs, texts):
    """texts(逐页文本)里**唯一**包含某个锚串的页 -> 1-based 页码; 找不到/不唯一 -> None。

    三轮: ① 原样包含 ② 去空白后包含 ③ 再去掉断词连字符后包含。每一轮都从最长的锚试起,
    只有"恰好一页命中"才算数 —— 多页命中说明这个串没有区分度, 换下一个锚。
    """
    for fn in (None, _squash, _dehyph):
        hay = texts if fn is None else [fn(t) for t in texts]
        for r in runs:
            needle = r if fn is None else fn(r)
            if len(needle) < 12:
                continue
            hits = [i + 1 for i, t in enumerate(hay) if needle in t]
            if len(hits) == 1:
                return hits[0]
    return None


def pdf_page_texts(pdf):
    """源 PDF 逐页文本(1-based 页序)。用 pypdf —— 与 post_check/pre_check 同源, 不引新依赖。"""
    import logging
    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return [(p.extract_text() or "") for p in PdfReader(pdf).pages]


def _skip_para(txt):
    """无可译文字的段: 空 / 整段只是 {vN} / 剥掉标记后一个字母数字都没有。

    next 上公式是 {vN} 占位符(不是 1.x 那种字面字形), 所以"纯公式段"自然落在这里。
    字母数字判据用 **str.isalnum() 而不是 ASCII 正则**: 数学斜体/西里尔等非 ASCII
    字符也是正文, 用 [A-Za-z0-9] 会把它们误判成"纯符号"丢掉(v28.38 的教训)。
    """
    if not txt or _PH_ONLY.match(txt):
        return True
    return not any(ch.isalnum() for ch in literal_text(txt))


def _cross_pages(cross, texts, npg, warns):
    """cross_page 池每条 -> (上一页页码 p0, 下一页页码 p1 = p0 + 1)。

    上游语义(il_translator_llm_only.process_cross_page_paragraph): 段 0 = 第 i 页
    **最后一个**正文段, 段 1 = 第 i+1 页**第一个**正文段, 两段并成一次调用。于是有两条
    硬约束 —— p1 == p0 + 1, 且条目按 i 递增生成故 p1 **严格递增**。这两条既是校验也是
    补全依据: 只锚到一侧时用另一侧推出来, 两侧都锚不到(或锚出来的页码违背约束)时按
    递增猜,**并报警** —— 猜出来的页只影响报告与按页筛选, 不影响正文。
    """
    out, prev = [], 0
    for k, e in enumerate(cross or []):
        ps = (e or {}).get("paragraph") or []
        a0 = a1 = None
        if texts and len(ps) >= 2:
            a0 = anchor_page(anchor_runs(ps[0].get("input") or ""), texts)
            a1 = anchor_page(anchor_runs(ps[1].get("input") or ""), texts)
        if a0 and a1 and a1 == a0 + 1:
            p0, p1 = a0, a1
        elif a0 and a1:
            p0, p1 = a0, a0 + 1                 # 两侧不一致: 以段 0 为准(段 0 是锚在页尾那一段)
            warns.append("cross_page[%d] 两段锚出来的页码不相邻(段0=%d 段1=%d), 取段 0 推"
                         % (k, a0, a1))
        elif a0:
            p0, p1 = a0, a0 + 1
        elif a1:
            p0, p1 = a1 - 1, a1
        else:
            p0, p1 = 0, 0
        if p1 and (p1 < 2 or (npg and p1 > npg)):
            warns.append("cross_page[%d] 锚到的页码越界(段0=%s 段1=%s, 段表 %d 页), 改按递增猜"
                         % (k, a0, a1, npg))
            p1 = 0
        if p1 and p1 <= prev:                       # 违背"p1 严格递增"
            warns.append("cross_page[%d] 锚到的页码(%d)不大于上一条(%d), 改按递增猜" % (k, p1, prev))
            p1 = 0
        if not p1:
            p1 = min(max(prev + 1, 2), npg) if npg >= 2 else 2
            warns.append("cross_page[%d] 页码未定 -> 猜第 %d 页(只影响报告与按页筛选, "
                         "不影响正文; 给 --pdf 让锚定生效)" % (k, p1))
        out.append((p1 - 1, p1))
        prev = p1
    return out


def tracking_payload_items(path, pdf="", pages=None, page_texts=None):
    """translate_tracking.json -> 载荷条目(**阅读序**)。返回 (items, warns)。

    items[i] = {"pool": "page"|"cross_page", "merged": False, "batch": (id, idx)|None,
                "parts": [(page, seg, text)], "src": (池, 下标, 子下标),
                "rec": <段表里的那段原文记录>}
        page  **源 PDF 的 1-based 页码**。next 没有侧车坐标, 所以 manifest 里 page 与
              true_page 同值(下游不许再按 1.x 的"回调计数"理解它)。
        seg   页内序(排序/报告用)。
        text  = `input` **原样**(引擎原生形态, 见本节头 ①)。
        src   **定位键**, 与推导参数无关(见 tracking_segment): 回写侧拿它把 #S 编号
              对回段记录, 而不是"拿同一份段表重推一遍再比坐标" —— 重推依赖 --pdf
              锚定, 参数一变整段错位(实测 129 段里从 #S11 起全错)。
        rec   只给**程序内**用(manifest 只挑 key/parts/pool/batch/src/fp 落盘, 见
              seg_export._emit): inject 要从它读 `llm_translate_trackers`(缓存键) 与
              `multi_paragraph_index`(该段在批次 JSON 里的 id) 与 `output`(改之前引擎
              写下的译文, 用作前置断言)。

    阅读序 = 按页拼, 页内顺序为 **cross_page 的"下页首段" -> 本页正文段 -> cross_page
    的"本页末段"** —— 那两段本来就在 `page.pdf_paragraph` 的首尾, 只是被上游摘去单独翻。

    pages: 要导的**源 PDF 页码**集合(None = 全篇)。跨页配对的条目**两页都在集合里**
           才收 —— 只含一半就塞进去, 等于让译者改一页之外的正文。
    """
    d = load_tracking(path)
    texts = page_texts
    if texts is None and pdf:
        texts = pdf_page_texts(pdf)
    npg = len(d.get("page") or [])
    warns, items = [], []
    if pages and npg and max(pages) > npg:
        warns.append("--pages 里有超出段表页数的页(段表共 %d 页, 所选到 %d 页) —— "
                     "多半是篇名/路径给错了, 请核对" % (npg, max(pages)))

    # ---- page[] 池 ----
    for pi, pg in enumerate(d.get("page") or []):
        page = pi + 1
        if pages and page not in pages:
            continue
        for si, p in enumerate((pg or {}).get("paragraph") or []):
            if not isinstance(p, dict):
                continue
            txt = (p.get("input") or "").strip()
            if _skip_para(txt):
                continue
            items.append({"pool": "page", "merged": False,
                          "batch": (p.get("multi_paragraph_id"),
                                    p.get("multi_paragraph_index")),
                          "parts": [(page, si, txt)], "_k": (page, 1, si),
                          "src": ("page", pi, si), "rec": p})

    # ---- cross_page 池(上页末段 + 下页首段, 不在 page[] 里) ----
    cross = d.get("cross_page") or []
    pairs = _cross_pages(cross, texts, npg, warns)
    for k, e in enumerate(cross):
        ps = (e or {}).get("paragraph") or []
        if len(ps) < 2:
            warns.append("cross_page[%d] 不是两段(上游语义 = 上页末段 + 下页首段), 已跳过" % k)
            continue
        p0, p1 = pairs[k]
        for j, p in enumerate(ps[:2]):
            if not isinstance(p, dict):
                continue
            if pages and (p0 not in pages or p1 not in pages):
                continue
            txt = (p.get("input") or "").strip()
            if _skip_para(txt):
                continue
            items.append({"pool": "cross_page", "merged": False,
                          "batch": (p.get("multi_paragraph_id"), j),
                          "parts": [(p0 if j == 0 else p1, 0, txt)],
                          "_k": ((p0 if j == 0 else p1), 2 if j == 0 else 0, 0),
                          "src": ("cross_page", k, j), "rec": p})
    if pages and cross and not any(it["pool"] == "cross_page" for it in items):
        warns.append("所选页里没有**整对**的跨页配对段 —— 那些段的另一半在所选范围之外, "
                     "按规矩不收(收了就等于改一页之外的正文)")

    items.sort(key=lambda it: it["_k"])
    for it in items:
        it.pop("_k", None)
    return items, warns


def text_fp(s):
    """段正文指纹(16 位): 段表被换篇/被下一次渲染覆盖时, **正文先变**。

    拿它当"还是那一份段表"的判据, 比坐标稳: 坐标会随推导参数变(见 tracking_segment),
    正文不会。
    """
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()[:16]


def tracking_segment(d, loc):
    """段表 + 定位键 -> (段记录, 正文); 定位不到一律抛 ValueError。

    定位键 = `(池, 下标, 子下标)`, 由 tracking_payload_items 在导出时写进 manifest:
        page       池: (段表 page[] 里的页下标, 段在页内的下标)
        cross_page 池: (配对数下标, 0=上页末段 / 1=下页首段)

    【为什么不能"重推一遍再比坐标"】载荷里的页号是**导出时的锚定结果**: 给了 --pdf 才
    按页面文字锚, 不然按序猜; 而 #S 编号的排序又依赖页号。于是同一份段表、同样的段,
    导出时给了 --pdf、回写时没给, 从第一个跨页段起整列错位(实测真样本 129 段里 #S11
    之后全对不上), 把一份完全正常的载荷判成"不是从当前段表导出的"。定位键不含任何
    推导参数, 配 text_fp 才是既不会误报、又能挡住"段表换了"的判据。
    """
    if not isinstance(loc, (list, tuple)) or len(loc) != 3 or loc[0] not in ("page", "cross_page"):
        raise ValueError("定位键缺失或形状不对: %r(旧版导出的 manifest 没有这个字段, "
                         "重新导出一次载荷即可)" % (loc,))
    pool, i, j = loc[0], int(loc[1]), int(loc[2])
    bucket = (d.get("page") if pool == "page" else d.get("cross_page")) or []
    try:
        p = bucket[i]["paragraph"][j]
    except (IndexError, KeyError, TypeError):
        raise ValueError("段表里没有 %s[%d].paragraph[%d](该池共 %d 条)"
                         % (pool, i, j, len(bucket)))
    if not isinstance(p, dict):
        raise ValueError("%s[%d].paragraph[%d] 不是段记录" % (pool, i, j))
    return p, (p.get("input") or "").strip()


def payload_report(items, warns):
    """载荷条目 -> 报告文本行(CLI 与 seg_export 共用同一份口径)。"""
    lines = []
    for i, it in enumerate(items, 1):
        pg, seg, txt = it["parts"][0]
        lines.append("#S%-4d p%-3d %-10s %5dch  %s" % (
            i, pg, it["pool"], len(txt), literal_text(txt)[:52].replace("\n", " ")))
    for w in warns:
        lines.append("[警告] %s" % w)
    return lines


def seg_cache_targets(it):
    """载荷条目 -> (prompts, batch_index): 该段要改的**缓存行键** + 它在批次 JSON 里的 id。

    next 的 LLM 通道键 = 引擎实际发出去的那**整条 prompt**(见节头 ③), 而这条 prompt 的
    原文就记在该段的 `llm_translate_trackers[].input` 里(上游 `set_input(final_input)`
    与 `cache.set(text, ...)` 用的是同一个字符串) —— 于是定位是**精确匹配**,
    不需要 1.x 那套 doc_summary_fp 作用域, 也不需要 {vN} 重编号。

    batch_index 取 `multi_paragraph_index`: 上游把批次里各段按顺序塞进 JSON, id 就是它
    在批次里的下标(实测段表 id 0/1/2 == multi_paragraph_index 0/1/2)。拿不到就返回
    None, 由调用方拒收 —— 绝不猜: 猜错就是改到同批另一段的译文上。
    """
    prompts = []
    for t in (it["rec"].get("llm_translate_trackers") or []):
        s = (t or {}).get("input") or ""
        if s and s not in prompts:
            prompts.append(s)
    return prompts, it["rec"].get("multi_paragraph_index")


def batch_entries(raw):
    """缓存行 translation(= 上游原始回复) -> ([条目...], {id: 译文})。

    上游写库的是 **LLM 原始回复**(它自己在取用时才 strip + 去围栏 + json.loads),
    所以这里也要容忍 markdown 围栏与"单对象而非数组"两种形态 —— 与上游
    `_clean_json_output` + `parsed_output` 归一化那几行同口径。
    解析不了就抛: 调用方一律拒收, 不许瞎猜(改错行的后果是整篇译文乱掉)。
    """
    t = re.sub(r"^```[a-z]*\s*$", "", (raw or "").strip(), flags=re.M).strip()
    d = json.loads(t)
    if isinstance(d, dict):
        d = [d]
    if not isinstance(d, list):
        raise ValueError("不是数组也不是对象: %r" % type(d))
    out = {}
    for e in d:
        if not isinstance(e, dict):
            raise ValueError("数组里有非对象元素: %r" % (e,))
        out[int(e.get("id", -1))] = e.get("output", e.get("input"))
    return d, out


def parse_pages(s):
    """'2-4' / '2,3,4' / 混合 '1,21-22' -> {页码...}; 空串 -> 空集合(调用方当"全篇")。

    与 seg_export 的 `--pages` 同一口径(**真实 PDF 页码**); 放在这里是为了两个引擎
    共用一份写法, 免得 1.x 与 next 对同一个串各解析一次、慢慢漂移。
    """
    want = set()
    for tok in (s or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, b = (int(x) for x in tok.split("-"))
            want.update(range(a, b + 1))
        else:
            want.add(int(tok))
    return want


def cache_report(profile=None):
    """缓存库体检: 存在? 多少行? 各行属于哪个引擎? (只读)"""
    p = profile or active()
    r = {"path": p.cache_db, "exists": os.path.exists(p.cache_db)}
    if not r["exists"]:
        return r
    con = sqlite3.connect("file:%s?mode=ro" % p.cache_db.replace("\\", "/"), uri=True)
    try:
        r["rows"] = con.execute("SELECT COUNT(*) FROM %s" % CACHE_TABLE).fetchone()[0]
        r["engines"] = {e: n for e, n in con.execute(
            "SELECT translate_engine, COUNT(*) FROM %s GROUP BY 1 ORDER BY 2 DESC"
            % CACHE_TABLE)}
    finally:
        con.close()
    return r


def probe_prompts(texts, profile=None, chunk=400):
    """段表的 prompts -> 缓存库命中读数(**只读**)。

    用于重渲染前的零成本自检: 命中 0 = 这次渲染读不到缓存 -> 整篇重译(既花钱又慢,
    1.x 那次 "341 段 / 4.5 分钟 / 术语表失效" 就是同一类事故)。
    两个引擎共用一份写法, 因为定位口径本来就一样(按 original_text 精确匹配), 差别只在
    "传什么": 1.x 的键是段原文, next 的键是**整条 prompt**(由调用方决定)。

    -> {"texts": n, "hit": k, "rows": {引擎: 命中条数}, "miss": [未命中的前 5 条(截断)]}
    """
    p = profile or active()
    texts = [t for t in texts if t]
    r = {"texts": len(texts), "hit": 0, "rows": {}, "miss": []}
    if not r["texts"] or not os.path.exists(p.cache_db):
        r["miss"] = [t[:60] for t in texts[:5]]
        return r
    hit = set()
    con = sqlite3.connect("file:%s?mode=ro" % p.cache_db.replace("\\", "/"), uri=True)
    try:
        for i in range(0, len(texts), chunk):          # SQLite 变量上限, 长论文上千段
            part = texts[i:i + chunk]
            marks = ",".join("?" * len(part))
            for eng, n in con.execute(
                    "SELECT translate_engine, COUNT(*) FROM %s WHERE original_text IN (%s)"
                    " GROUP BY 1" % (CACHE_TABLE, marks), part):
                r["rows"][eng] = r["rows"].get(eng, 0) + n
            for (t,) in con.execute(
                    "SELECT DISTINCT original_text FROM %s WHERE original_text IN (%s)"
                    % (CACHE_TABLE, marks), part):
                hit.add(t)
    finally:
        con.close()
    r["hit"] = len(hit)
    r["miss"] = [t[:60] for t in texts if t not in hit][:5]
    return r


def tracking_report(profile=None):
    p = profile or active()
    r = {"working_root": p.working_root, "exists": bool(p.working_root and os.path.isdir(p.working_root))}
    if not r["exists"]:
        return r
    ds = sorted(glob.glob(os.path.join(p.working_root, "*")))
    r["docs"] = []
    for d in ds:
        t = os.path.join(d, "translate_tracking.json")
        try:
            n = len(read_tracking_segments(t)) if os.path.exists(t) else 0
        except Exception:
            n = -1
        r["docs"].append({"stem": os.path.basename(d), "segments": n})
    return r


# -------------------------------------------------------------------- CLI
def _print_profile(p):
    print("引擎: %s — %s" % (p.key, p.label))
    print("  缓存库:   %s" % p.cache_db)
    print("  段表来源: %s%s" % (p.seg_source,
          ("  (工作根 %s)" % p.working_root) if p.working_root else
          ("  (%s)" % p.sidecar if p.sidecar else "")))
    print("  已接线:   %s" % ", ".join(p.ok_stages))
    for s, why in p.wired.items():
        if why:
            print("  未接线:   %-7s %s" % (s, why))
    print("  注:       %s" % p.notes)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="引擎接缝: 看画像 / 验接线 / 只读段表")
    ap.add_argument("--engine", default="", help="pdf2zh|next; 缺省读 P2Z_ENGINE, 再缺省 pdf2zh")
    ap.add_argument("--list", action="store_true", help="并排列出全部画像")
    ap.add_argument("--json", action="store_true", help="机器可读")
    ap.add_argument("--segments", default="", help="只读列段表: tracking.json 或其所在目录")
    ap.add_argument("--name", default="", help="next: working 下的篇名目录")
    ap.add_argument("--payload", default="", help="next: 把段表整形成载荷条目并预览(阅读序)")
    ap.add_argument("--pdf", default="", help="next: 源 PDF; 跨页池靠它锚定页码(缺了只能按序猜)")
    ap.add_argument("--pages", default="", help="next: 只导所列页(如 2-4); 缺省全篇")
    a = ap.parse_args()
    try:
        p = select(a.engine)
    except ValueError as e:
        print("[engine] %s" % e)
        return 2

    if a.list:
        if a.json:
            print(json.dumps({k: v.as_dict() for k, v in PROFILES.items()},
                             ensure_ascii=False, indent=1))
        else:
            for k in sorted(PROFILES):
                _print_profile(PROFILES[k])
                print("")
        return 0

    if a.segments:
        t = a.segments
        if os.path.isdir(t):
            t = os.path.join(t, "translate_tracking.json")
        segs = read_tracking_segments(t)
        print("[engine] 段表 %s -> %d 段" % (t, len(segs)))
        for s in segs:
            print("  #%-3d p%-3d src(%d) dst(%d)%s  %s" % (
                s["seq"], s["page"], len(s["src"]), len(s["dst"]),
                "  ERR" if s["err"] else "", s["src"][:44].replace("\n", " ")))
        return 0

    if a.payload:
        t = a.payload
        if os.path.isdir(t):
            t = os.path.join(t, "translate_tracking.json")
        pages = parse_pages(a.pages) or None
        items, warns = tracking_payload_items(t, pdf=a.pdf, pages=pages)
        print("[engine] 载荷 %s -> %d 段 (%d 页; 跨页池 %d 段)" % (
            t, len(items), len(load_tracking(t).get("page") or []),
            sum(1 for it in items if it["pool"] == "cross_page")))
        for ln in payload_report(items, warns):
            print("  " + ln)
        return 0

    rep = {"engine": p.as_dict(), "cache": cache_report(p)}
    if p.seg_source == "tracking_json":
        rep["tracking"] = tracking_report(p)
        if not p.tracking_json(a.name):
            print("[engine] 注意: 工作根下没有 translate_tracking.json —— 上游只在 "
                  "--debug(或显式 working_dir)时才落盘; 没拿到段表不等于没翻译过")
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    _print_profile(p)
    c = rep["cache"]
    if c["exists"]:
        print("  体检: 缓存 %d 行 %s" % (c.get("rows", 0), c.get("engines") or {}))
    else:
        print("  体检: 缓存库还不存在 (%s) —— 该引擎还没在本机翻过" % c["path"])
    if "tracking" in rep and rep["tracking"].get("exists"):
        print("  体检: 工作根有 %d 份历史" % len(rep["tracking"].get("docs") or []))
        for d in (rep["tracking"].get("docs") or [])[-5:]:
            print("        %s  (%d 段)" % (d["stem"], d["segments"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
