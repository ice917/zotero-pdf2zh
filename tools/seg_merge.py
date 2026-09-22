# -*- coding: utf-8 -*-
"""seg_merge.py — 按段号合稿: 把「只改了几段」的补丁并进已有交件 (M1 工具)

为什么需要它 (2026-09-20 SILAGE 第四轮):
  门禁每轮只点名几十段, 正确的做法是"只改这些段"。但规范里写着"整篇覆盖重交",
  于是每轮都要把 ~790 段 / ~110KB 全文再吐一遍 —— 输出撞上模型的输出上限被截断,
  下一轮又冒出一批"只译了开头"的新段。这是个自锁回路(豆包自述第 1 条)。
  更坏的是第 2 条: 豆包自己写脚本按段号补 28 段, fills 字典的替换逻辑写错,
  **改完没生效就提交了** —— 全程没有任何东西告诉他"你的替换没落地"。

做什么:
  1. 读已有交件(缺省 out/<name>.webai.txt, 旧件 out/<name>.doubao.txt 同样认)与补丁文件(只含要改的段)
  2. 只替换补丁点到的段, **其余字节一个都不动**(按正文区间切片, 不重排全文)
  3. 写临时文件 -> 回读逐段比对 -> 通过才原子替换正式交件
     (校验不过/写失败时, 正式交件原封不动 —— 要么全生效, 要么完全没动)
  4. 比对是逐段回读: 补丁里每一段在写回的文本里必须**真的**等于新文本,
     不等就列出段号并非零退出。这条就是冲着上面那个"静默失败"去的。
  5. 顺带跑长度比自查(与 tools/adopt.py 的 ratio_audit **同一份实现**),
     报出仍偏低的段 —— 与门禁报告同一口径, 不会漂。
  6. [D 分批交件] 由载荷生成**交件骨架**(skeleton_text / write_skeleton):
     保留全部 #S 编号行与 ⋮ 断点行、正文清空。有了骨架, 首轮就可以**分几批**翻、
     每批只 merge 自己那几百段, 豆包永远不用吐整篇 —— 这才是自锁回路的治本,
     第 1~5 条只是"整篇吐坏之后怎么救"。

用法:
  PY = <venv>/python.exe
  & $PY tools/seg_merge.py --name payload_p2_p4 --patch inbox/patch_p2_p4.txt
  # 另: 骨架由桥的 start_delivery 生成(见 tools/doubao_bridge.py), 不走本 CLI

补丁文件格式 = 与交件同一套编号块, 只写要改的段(其余段不出现):
  #S386
  这一段的新译文, 从头译到尾……
  #S442
  另一段的新译文……

退出码: 合稿并校验通过 0 / 补丁不合法或校验未过 1
"""
import argparse
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 复用 adopt 的口径, 不另写一套:
#   SI.KEY_LINE / SI.parse_blocks —— 段号行怎么认 (与门禁同一套, 容忍 markdown 加粗)
#   ratio_audit                   —— 长度比自查 (与门禁报告同一份实现)
#   INBOX / OUTDIR                —— 路径也认 P2Z_PROJ, 换台电脑不废
#   A.delivery_candidates / A.RN  —— 交件命名的后缀族(doubao|webai)只认
#                                    tools/result_naming.py 那一份契约, 本文件不写死
import adopt as A  # noqa: E402

BOM = b"\xef\xbb\xbf"
BREAK_MARK = "⋮"        # 跨页合并段的断点记号(与 seg_export/seg_import 同一符号)


def scan_spans(text):
    """按出现顺序返回 [(段号, 编号行起点, 正文起点, 正文终点)]。

    正文终点 = 下一个编号行起点(或文件末尾): 那就是"这一段正文的原始字节区间",
    原样搬运即"未点到的段一个字节都不动"。
    重复段号直接报错 —— 门禁的 parse_blocks 只认最后一条, 重复会让"回读校验"
    对着一条看、实际提交另一条, 正是本工具要杜绝的那类静默错。
    """
    lines = text.splitlines(keepends=True)
    marks, pos, seen = [], 0, set()
    for i, ln in enumerate(lines, 1):
        m = A.SI.KEY_LINE.match(ln.rstrip("\r\n"))
        if m:
            k = int(m.group(1))
            if k in seen:
                raise ValueError("交件里 #S%d 出现两次(第二次在第 %d 行): 段号必须唯一, "
                                 "重复会让回读校验与实际提交对不上" % (k, i))
            seen.add(k)
            marks.append((k, pos, pos + len(ln)))
        pos += len(ln)
    return [(k, hs, bs, (marks[i + 1][1] if i + 1 < len(marks) else len(text)))
            for i, (k, hs, bs) in enumerate(marks)]


def merge_text(text, patch):
    """把 patch({段号: 新正文}) 并进 text。

    返回 (新文本, 新文本与原文相同的段号, 交件里不存在的段号)。
    不存在段号非空时, 新文本为 None(调用方据此拒绝合稿)。
    """
    spans = scan_spans(text)
    have = set(k for k, _, _, _ in spans)
    missing = sorted(k for k in patch if k not in have)
    if missing:
        return None, [], missing
    noop, out, cursor = [], [], 0
    for k, _hs, bs, be in spans:
        if k not in patch:
            continue
        old = text[bs:be]
        # 只换"有内容的部分", 原正文尾部的空白(段间空行/换行风格/文件末尾无换行)照旧
        trail = old if not old.strip() else old[len(old.rstrip()):]
        body = patch[k]
        if old.strip() == body.strip():
            noop.append(k)
        out.append(text[cursor:bs])
        out.append((body + trail) if body else trail)
        cursor = be
    out.append(text[cursor:])
    return "".join(out), noop, []


def verify_applied(text, patch):
    """回读校验: 补丁里每一段在 text 里是否**真的**等于新文本; 返回未生效的段号。

    单独拎出来是为了可测 —— 这条判据就是"替换没生效"的唯一哨兵。
    """
    got = A.SI.parse_blocks(text)
    return sorted(k for k, v in patch.items() if got.get(k) != v)


def parse_patch(text):
    """把补丁文本解析成 {段号: 新正文}; 没有 #S编号 块时抛 ValueError(附格式示例)。

    只有这一处解析 —— CLI 与桥都走它, 免得"什么算合法补丁"两处判得不一样。
    """
    patch = A.SI.parse_blocks(text or "")
    if not patch:
        raise ValueError("补丁里没有任何 #S编号 块 —— 只写要改的段, 格式:\n"
                         "#S386\n这一段的新译文……\n#S442\n另一段的新译文……")
    return patch


def die(msg):
    print("[seg_merge] 拒绝执行: %s" % msg)
    return 1


def _read(path):
    """返回 (文本, 是否带 BOM)。BOM 单独记账: 读写都不做换行/编码翻译, 未动的字节才真不动。"""
    raw = open(path, "rb").read()
    return raw.decode("utf-8-sig"), raw.startswith(BOM)


def _write(path, text, bom):
    data = text.encode("utf-8")
    with open(path, "wb") as f:
        f.write((BOM + data) if bom else data)


def merge_file(path, patch):
    """把 patch({段号: 新正文}) 并进交件文件: 临时文件 -> 回读逐段校验 -> 原子替换。

    返回 (ok, info)。ok=False 时**正式交件一定未被改动**, info["error"] 是原因。
    "要么全生效、要么完全没动"只有这一份实现 —— CLI 与桥都走它, 两处不会漂。
    """
    if not os.path.exists(path):
        return False, {"error": "交件不存在: %s" % path}
    text, bom = _read(path)
    try:
        new_text, noop, missing = merge_text(text, patch)
    except ValueError as exc:
        return False, {"error": str(exc)}
    if missing:
        return False, {"error": "补丁点了交件里没有的段号: %s"
                                % ", ".join("#S%d" % k for k in missing),
                       "missing": missing}
    tmp = path + ".merge_tmp"
    _write(tmp, new_text, bom)
    try:
        back, _ = _read(tmp)
        not_applied = verify_applied(back, patch)
    except Exception as exc:                    # 写/读异常一律不许落正式交件
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, {"error": "回读校验无法完成(%s), 正式交件未改动" % exc}
    if not_applied:
        os.remove(tmp)
        return False, {"error": "回读比对不符 —— 已在临时文件上拦下, 正式交件未改动",
                       "not_applied": not_applied}
    os.replace(tmp, path)
    return True, {"applied": len(patch) - len(noop), "noop": noop,
                  "bytes_before": len(text.encode("utf-8")),
                  "bytes_after": len(new_text.encode("utf-8")), "text": new_text}


def skeleton_text(payload_text):
    """由载荷生成「交件骨架」: 只留 #S 编号行与 ⋮ 断点行, 正文一律清空。

    返回 (骨架文本, 段号列表, 合并段数)。

    为什么要骨架 (D: 分批交件)。790 段一次吐会撞上输出上限被截断 -> 只译前半句 ->
    下一轮又冒出一批同病段(自锁回路)。想分批发, 就必须允许"先立段号、后填正文" ——
    而 merge_result 只认交件里**已存在**的段号: 没有骨架, 第二批开始就无处可填。
    有了骨架, 每一批只填自己那几百段, 豆包**永远不用吐整篇**。

    ⋮ 必须留着: 交付侧的断点对账吃它(合并段须恰好 n_parts-1 个), 骨架丢掉它,
    填完就会被判"断点不符" —— 那是我们自己的骨架造出来的假缺陷。它落在该段正文里,
    当作"这里要断一页"的占位; 填稿时会被新译文整段替换掉, 数量照抄即可。
    """
    keys, seen, out, n_merged = [], set(), [], 0
    for line in payload_text.splitlines():
        s = line.strip()
        m = A.SI.KEY_LINE.match(s)
        if m:
            k = int(m.group(1))
            if k in seen:
                raise ValueError("载荷里 #S%d 出现两次: 段号必须唯一" % k)
            seen.add(k)
            keys.append(k)
            out.append("#S%d" % k)
        elif s == BREAK_MARK:
            out.append(BREAK_MARK)
    if not keys:
        raise ValueError("载荷里没有任何 #S编号 块 —— 这不是 seg_export 导出的载荷?")
    # 合并段数按"正文里含 ⋮"的段算, 与交付侧断点对账同一口径
    got = A.SI.parse_blocks(payload_text)
    n_merged = sum(1 for v in got.values() if BREAK_MARK in v)
    return "\n".join(out) + "\n", keys, n_merged


def write_skeleton(path, payload_text):
    """把骨架原子落到 path(临时文件 -> 回读校验 -> os.replace)。

    返回 (段号列表, 合并段数, 字节数)。
    **不负责"要不要覆盖"** —— 那是调用方的决定(桥的 start_delivery 会拒绝覆盖已有交件):
    骨架是"这一版交件的底稿", 把别人已改好的稿子冲掉是最坏的一种失败。
    回读校验盯着两点: 段号一个不多不少、每段正文里**没有正文** —— 骨架里混进译文,
    等于凭空替译者"译"了一段, 必须当场拦下。
    "没有正文"要排除 ⋮ : 合并段的占位 ⋮ 是骨架**故意**留的(见 skeleton_text),
    只有 ⋮ 与空白的段算空; 拿裸的 `if v` 判会把正常骨架自己拒掉。
    """
    text, keys, n_merged = skeleton_text(payload_text)
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".skel_tmp"
    _write(tmp, text, False)
    try:
        back, _ = _read(tmp)
        got = A.SI.parse_blocks(back)
        dirty = [k for k, v in got.items() if v.replace(BREAK_MARK, "").strip()]
        bad = (sorted(got) != sorted(keys)) or dirty
    except Exception as exc:
        bad = True
        dirty = ["回读异常: %s" % exc]
    if bad:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise ValueError("骨架回读校验不符(段号 %d vs %d; 非空正文 %s), 未落盘"
                         % (len(got), len(keys), dirty[:5]))
    os.replace(tmp, path)
    return keys, n_merged, len(text.encode("utf-8"))


def audit_text(name, text=""):
    """合稿后的长度比自查(与 tools/adopt.py 的 ratio_audit **同一份实现**), 返回可展示的若干行。

    为什么合稿之后还要报一次: 补丁只改点到的段, 未被点到的灰区段仍在。这一行是
    "这轮补完了, 但还有 N 段同样像只译了开头"的当场提示 —— 与门禁报告的「附：全篇长度比」
    同一口径, 免得两处漂。text 为空时按名字去 out/ 取最新那份。
    """
    if not text:
        cand = A.delivery_candidates(name)
        if not cand:
            return ["(交件不在, 跳过长度比自查)"]
        text, _ = _read(cand[-1])
    pay_path = os.path.join(A.INBOX, name + ".txt")
    if not os.path.exists(pay_path):
        return ["(载荷 %s 不在, 跳过长度比自查)" % pay_path]
    pay, _ = _read(pay_path)
    src, got = A.SI.parse_blocks(pay), A.SI.parse_blocks(text)
    n_long, mid, bk, cands = A.ratio_audit(src, got)
    if not n_long:
        return ["(载荷里没有长段, 长度比自查无从谈起)"]
    lo, mo, hi = A._ADVISORY_BUCKETS
    lines = ["长度比自查: 长段 %d 个, 中位 %d%% ｜ < %d%% %d 段 ｜ %d%%~%d%% %d 段 ｜ "
             "%d%%~%d%% %d 段 ｜ ≥ %d%% %d 段"
             % (n_long, round(100.0 * mid),
                int(lo * 100), bk[0], int(lo * 100), int(mo * 100), bk[1],
                int(mo * 100), int(hi * 100), bk[2], int(hi * 100), bk[3])]
    if cands:
        lines.append("⚠️ 仍有 %d 段比值偏低(%d%%~%d%%, 门禁不拦, 但同样像只译了开头):"
                     % (len(cands), int(A._GAP_RATIO * 100), int(A._ADVISORY_RATIO * 100)))
        lines.extend("    #S%d  %d -> %d  (%d%%)" % (k, ls, lt, round(100.0 * lt / ls))
                     for k, ls, lt in cands[:A._REPORT_ROWS])
        if len(cands) > A._REPORT_ROWS:
            lines.append("    （只列前 %d 段，共 %d 段）" % (A._REPORT_ROWS, len(cands)))
    else:
        lines.append("长度比自查: 没有偏低的长段 ✅")
    return lines


def main():
    ap = argparse.ArgumentParser(description="按段号把补丁并进已有交件(逐段回读校验)")
    ap.add_argument("--name", required=True, help="run 名(对应 out/<name>.<族>*.txt, 族=doubao|webai)")
    ap.add_argument("--patch", required=True, help="补丁文件: 只含要改的 #S编号 块")
    ap.add_argument("--delivery", default="", help="交件路径; 缺省取 out/<name>.<族>*.txt(须唯一)")
    args = ap.parse_args()

    if args.delivery:
        path = os.path.abspath(args.delivery)
    else:
        cand = A.delivery_candidates(args.name)
        if not cand:
            return die("out/ 下没有 %s 的交件(找 %s) —— 先交一版全文, 之后才谈得上按段号合稿"
                       % (args.name, " / ".join(A.RN.globs(args.name))))
        if len(cand) > 1:
            return die("交件候选 %d 份(%s), 请用 --delivery 指定要合的那一份"
                       % (len(cand), ", ".join(os.path.basename(c) for c in cand)))
        path = cand[0]
    if not os.path.exists(args.patch):
        return die("补丁不存在: %s" % args.patch)

    patch_text, _ = _read(args.patch)
    try:
        patch = parse_patch(patch_text)
    except ValueError as exc:
        return die("%s (补丁: %s)" % (exc, args.patch))

    ok, info = merge_file(path, patch)
    if not ok:
        if info.get("not_applied"):
            print("[seg_merge] 未生效 %d 段: %s"
                  % (len(info["not_applied"]),
                     ", ".join("#S%d" % k for k in info["not_applied"])))
        return die(info["error"])

    print("[seg_merge] 交件: %s" % path)
    print("[seg_merge] 补丁: %s  (%d 段)" % (args.patch, len(patch)))
    print("[seg_merge] 替换生效: %d 段 · 未生效: 0 段 · 字节 %d -> %d (%+d)"
          % (info["applied"], info["bytes_before"], info["bytes_after"],
             info["bytes_after"] - info["bytes_before"]))
    if info["noop"]:
        print("[seg_merge] ⚠️ 这 %d 段的新文本与原文**一模一样**(等于没改, 确认是有意为之?): %s"
              % (len(info["noop"]), ", ".join("#S%d" % k for k in info["noop"])))
    for line in audit_text(args.name, info["text"]):
        print("[seg_merge] %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
