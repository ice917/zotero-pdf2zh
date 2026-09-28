# -*- coding: utf-8 -*-
"""dnt_contract.py — 「文献区禁汉化」(第四断言)判据的**独立机器可读契约**加载器。

契约先于产物、独立于实现: 本模块 + tools/contracts/dnt.json 是这条判据的
**唯一真源**。生产端(seg_export 写声明)与消费端(post_check/adopt 读声明、定
退出码)都从这里取值 —— 改契约即改行为, 无需改代码。

为什么要单列一个**中性模块**(而不是把常量留在 post_check 里):
    生产端若 `import post_check` 才能拿到契约, 就是"生产者依赖消费者" —— 契约便
    不再独立于实现, 退回"规格与实现同源"的自证(这正是本项目要根治的元问题:
    验收物与被验物同源)。故契约由本模块承载, 生产/消费两侧**平级**消费, 互不依赖。

路径覆盖: 环境变量 `P2Z_DNT_CONTRACT` 指定契约文件(多套契约 / 变异测试用)。
**不缓存**: 每次 load() 重新读盘。变异测试(改契约副本 -> 断言实现行为跟随)依赖
这一点; 一旦缓存, "实现确由契约驱动"的证明即失效(`test_dnt_contract.py` 会红)。

失败**响亮**: 缺文件 / 坏 JSON / 缺键 / 值域冲突 一律抛异常, 绝不静默降级成默认值
—— 静默降级 = 制造"假契约", 比没有契约更危险(fail-closed)。
"""
import json
import os

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "contracts", "dnt.json")

_REQUIRED_KEYS = ("contract", "version", "rule", "declaration",
                  "verdicts", "exit_codes", "thresholds")


def contract_path(path=None):
    """契约文件路径: 显式 path > 环境变量 P2Z_DNT_CONTRACT > 仓内默认。"""
    return path or os.environ.get("P2Z_DNT_CONTRACT") or DEFAULT_PATH


def load(path=None):
    """读契约 -> dict(每次重新读盘)。坏文件/缺键/值域冲突 -> 抛异常。"""
    p = contract_path(path)
    with open(p, encoding="utf-8") as f:
        c = json.load(f)
    for key in _REQUIRED_KEYS:
        if key not in c:
            raise ValueError("DNT 契约缺键 %r: %s" % (key, p))
    _validate(c, p)
    return c


def _validate(c, p):
    """契约自洽性守卫: 值域不得自相矛盾。冲突即拒绝加载, 不带着坏契约往下跑。"""
    verdicts = list(c["verdicts"])
    codes = dict(c["exit_codes"])
    for v in codes:
        if v not in verdicts:
            raise ValueError("DNT 契约: exit_codes 含未登记 verdict %r: %s" % (v, p))
    reserved = set(int(x) for x in (c.get("reserved_exit_codes") or {}).values())
    seen = {}
    for v, rc in codes.items():
        rc = int(rc)
        if rc in reserved:
            raise ValueError("DNT 契约: verdict %r 占用保留退出码 %d: %s" % (v, rc, p))
        if rc in seen:
            raise ValueError("DNT 契约: 退出码 %d 被 %r 与 %r 共用: %s"
                             % (rc, seen[rc], v, p))
        seen[rc] = v
    if int(c["thresholds"]["zh_min"]) < 1:
        raise ValueError("DNT 契约: thresholds.zh_min 须 >= 1(存在性硬要求): %s" % p)


# ---------------------------------------------------------------- 生产端(seg_export)
def rule_text(path=None):
    """写进提示词的规则正文(规则 6 全文)。"""
    return load(path)["rule"]["text"]


def seg_token_pattern(path=None):
    """--dnt-seg 合法记号的正则(字符串)。"""
    return load(path)["declaration"]["payload_seg_token_pattern"]


def payload_line_prefix(path=None):
    """载荷里禁翻声明行的前缀。"""
    return load(path)["declaration"]["payload_line_prefix"]


def manifest_top_level_flag(path=None):
    """manifest 顶层"本载荷带声明"布尔字段名。"""
    return load(path)["declaration"]["manifest_top_level_flag"]


def manifest_item_flag(path=None):
    """manifest 逐段"该段禁翻"布尔字段名。"""
    return load(path)["declaration"]["manifest_item_flag"]


# ---------------------------------------------------------------- 消费端(post_check/adopt)
def zh_min(path=None):
    """单页"被汉化文献条目"数的门槛(存在性: 达到即事故)。"""
    return int(load(path)["thresholds"]["zh_min"])


def exit_code(verdict, default=1, path=None):
    """verdict -> 退出码。未登记 verdict 落 default(fail-closed)。"""
    return int(load(path)["exit_codes"].get(verdict, default))


def verdict_for_exit_code(rc, path=None):
    """退出码 -> verdict; 未登记 -> None(调用方自行 fail-closed)。"""
    for v, c in load(path)["exit_codes"].items():
        if int(c) == int(rc):
            return v
    return None
