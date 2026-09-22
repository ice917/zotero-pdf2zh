# -*- coding: utf-8 -*-
"""交件命名契约 —— "网页 AI 交回来的稿子"落在 out/ 下叫什么 (v28.68)

契约(三处实现必须完全一致, 由 tools/tests/test_result_naming.py 锁死):
    <篇名>.<族>[轮次].txt
    例: payload_p2_p4.webai.txt / wang2026.webai3.txt / egophys2026.doubao.txt

[v28.68] 为什么把后缀族从 `doubao` 扩成 `doubao|webai`:
  采纳回路里"交件"这一环本来就与厂商无关 —— 粘贴通道谁都能用, MCP 通道谁都能接(桥的
  工具名是 list_inbox / get_payload / submit_result, 本来就不带厂商)。但从 v26.x 起
  文件名把"豆包"焊了进去: 用别家网页 AI 交的稿子也叫 `xxx.doubao.txt`, 名不副实。
  故**新件缺省用 `webai`**, `doubao` 降为**历史兼容族**:
    - 已存在的 out/*.doubao*.txt 一个字节都不动, 照样被 list_results / deliver 认;
    - 谁若习惯性交成 .doubao.txt, 也照样收 —— 改名就把在跑的论文断链, 那是本末倒置。
  两族在同一篇下**同权**: adopt deliver 见到 ≥2 份候选一律拒收(不猜哪份新), 由人
  用 --text 指定 —— 这条安全姿态不为改名让步。

跨进程无法共享 import: server 侧(等交件的轮询)自带一份 server/utils/result_naming.py,
本测试断言两侧 FAMILIES 与生成的 glob 逐项一致。口径只认这两份, 不许第三处再写死。
"""
import os
import re

FAMILIES = ("doubao", "webai")      # doubao=历史兼容(旧件/旧习惯), webai=新件缺省
DEFAULT_FAMILY = "webai"
# 交件名: `<篇名>.<族>[轮次].txt`。组 1 = 篇名, 组 2 = 轮次(可空)。
FILE_RE = re.compile(r"^(.+?)\.(?:doubao|webai)(\d*)\.txt$")
# 只判尾部的"族+轮次"(用在已去掉 .txt 的名字上): .doubao / .webai3 / ...
SUFFIX_RE = re.compile(r"\.(?:doubao|webai)\d*$")


def normalize(name, family=DEFAULT_FAMILY):
    """交件文件名规范化 → `<篇名>.<族>[轮次].txt`。

    为什么要规范化: deliver 缺省只认 out/ 下带族后缀的交件, 而网页 AI 最常见的提交名
    是 inbox 里那个原名(`egophys2026.txt`)—— 直接落盘就落成 out/egophys2026.txt,
    deliver 找不到 → 断在交件这一步。实测(2026-09-19): 历史上没断, 只因为人在对话框里
    额外叮嘱了"存成 xxx.doubao.txt"。**依赖人记得说不是契约**, 这个函数把它变成契约。

    已带族后缀(含轮次)的名字**原样保留**(不叠成 .webai.doubao); 其余情况剥掉最后一节
    扩展名(与历史行为一致: `note.md` → `note.webai.txt`, `paper.v2.txt` →
    `paper.v2.webai.txt`)。
    """
    base = name[:-4] if name.lower().endswith(".txt") else name
    if SUFFIX_RE.search(base):
        return base + ".txt"
    return "%s.%s.txt" % (os.path.splitext(name)[0], family)


def stem_of(name):
    """取"篇名": `payload_x.webai2.txt` / `payload_x.doubao.txt` / `payload_x.txt` /
    `payload_x` → `payload_x`。"""
    return FILE_RE.match(normalize(name)).group(1)


def matches(name):
    """这个文件名是不是一份交件(而不是 out/ 里其它中间产物)。"""
    return bool(FILE_RE.match(name))


def globs(name):
    """扫 out/ 用的 glob 列表(不含目录)。两族都要扫 —— 只扫一族就会漏掉另一半。"""
    return ["%s.%s*.txt" % (name, fam) for fam in FAMILIES]


def belongs(name, stem):
    """`name` 是不是 `stem` 这篇的交件(两族都算)。

    用于"列某一篇的全部交件": 比对上放宽成前缀匹配 —— 交件名由 normalize 规范化而来,
    但历史文件是人手起的, 别因为大小写/多一个空格就一条都列不出来。
    """
    low = name.lower()
    return any(low.startswith((stem + "." + fam).lower()) for fam in FAMILIES)
