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
    pdf2zh  现产线 pdf2zh 1.x。六阶段全接线; 也是缺省画像, 行为与本模块引入前一致。
    next    pdf2zh_next 2.9.0 / BabelDOC (实验)。只接线到「只读 + 验收」:
              段表 = <working_root>/<stem>/translate_tracking.json  (须跑 --debug)
              缓存 = ~/.cache/pdf2zh_next/cache.v1.db  (表结构与 1.x 逐字相同)
            export/import/inject/render 未接线 —— 它们是 {vN} 占位回锚与版面重排
            (B 类), 得在新引擎上重写, 不能照搬 1.x 的 sstk/var 侧习惯。

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
import json
import os
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
_NEXT_NO = {
    "export": "段表 -> {vN} 载荷的整形未接线 (B 类: 占位符回锚/变量字形是新引擎的 IL 口径)",
    "import": "回锚写回未接线 (B 类: 1.x 的 var[]/sstk 吸收逻辑在新引擎里没有对应物)",
    "inject": "缓存手术未接线 (缓存表结构相同, 但 LLM 通道的键是整条 prompt/一批段, "
              "定位口径要按新引擎重写)",
    "render": "重渲染入口未接线 (B 类: 需改用 pdf2zh_next CLI, 且上游出错仍退出码 0, "
              "判成败只能看产物)",
    "rollback": "撤销骨架行 = inject 的逆操作, 同样未接线 (B 类)",
}

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
        wired=dict({"deliver": None, "gate": None, "status": None}, **_NEXT_NO),
        notes="段表 = working/<stem>/translate_tracking.json (须 --debug; 上游无 working_dir 参数); "
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
    """
    if not path or not os.path.exists(path):
        raise IOError("段表不存在: %s" % (path or "(空路径)"))
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or "page" not in d:
        raise ValueError("不是 translate_tracking.json (缺 page 键): %s" % path)
    out, seq = [], 0
    for pi, pg in enumerate(d.get("page") or []):
        for si, p in enumerate((pg or {}).get("paragraph") or []):
            if not isinstance(p, dict):
                continue
            seq += 1
            lls = [t for t in (p.get("llm_translate_trackers") or []) if isinstance(t, dict)]
            prompt = ((lls[0].get("input") or "") if lls else "")
            out.append({
                "seq": seq,
                "page": pi + 1,
                "idx": si,
                "src": p.get("input") or "",
                "raw": p.get("pdf_unicode") or "",
                "dst": p.get("output") or "",
                "cache_key": prompt or (p.get("input") or ""),
                "batch": (p.get("multi_paragraph_id"), p.get("multi_paragraph_index")),
                "n_ph": len(p.get("placeholders") or []),
                "err": bool(lls[0].get("has_error")) if lls else False,
            })
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
