# -*- coding: utf-8 -*-
"""
MinerU Markdown 翻译校对脚本(AI 全篇检查 + 上下文修正)
=====================================================
功能: 对照原文整篇检查译文,联系上下文修正翻译失误:
      单位误译(in.=英寸 不能被翻成"在")、数字/数值错误、术语不一致、
      漏译错译、语序不通顺等。只改动译文,保留 markdown 结构与公式。

原理: 把原文和译文按累计字符比例切成相同数量的整行块(比例对齐,边界漂移有界),
      逐块让 DeepSeek 对照原文块重写译文块; 公式占位符数量校验不通过则保留原块。

用法:
    python revise_md.py <原.md> <译文.md> <输出.md> [--config config.json]

依赖: 仅标准库(urllib),与 translate_md.py 同一套 API 配置。
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 默认配置(可用 --config 覆盖) ====================
DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-chat",
    "chunk_size": 2500,               # 每批对原文按字符数切块
    "max_workers": 5,                 # 并发请求数
    "max_retries": 3,                 # 单块失败重试次数
    "request_timeout": 240,           # 单请求超时(秒)
    "temperature": 0.1,               # 校对用低温度,减少改写
}
# ======================================================================

# 公式占位符: 形如 【公式1】【公式2】... (与 translate_md.py 一致)
_PH_PREFIX = "\u3010\u516c\u5f0f"
_PH_SUFFIX = "\u3011"
_PH_RE = re.compile(re.escape(_PH_PREFIX) + r"(\d+)" + re.escape(_PH_SUFFIX))

# 公式块正则: $$..$$ 优先,再匹配单 $..$
_FORMULA_RE = re.compile(r"\$\$[\s\S]*?\$\$|\$(?!\$)[^\$\n]*?\$")


def protect_formulas(text):
    formulas = {}

    def _repl(m):
        idx = len(formulas) + 1
        ph = f"{_PH_PREFIX}{idx}{_PH_SUFFIX}"
        formulas[ph] = m.group(0)
        return ph

    return _FORMULA_RE.sub(_repl, text), formulas


def restore_formulas(text, formulas):
    for ph, formula in formulas.items():
        text = text.replace(ph, formula)
    return text


def line_chunks_by_frac(lines, n_chunks):
    """按累计字符比例把整行序列切成 n_chunks 块(整行不拆, 边界不切开公式占位符)"""
    lens = [len(ln) + 1 for ln in lines]
    total = sum(lens) or 1
    boundaries = [round(i / n_chunks * total) for i in range(n_chunks + 1)]
    chunks, cur, cur_len, bi = [], [], 0, 1
    for ln, lnlen in zip(lines, lens):
        cur.append(ln)
        cur_len += lnlen
        if bi < len(boundaries) and cur_len >= boundaries[bi]:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
            while bi < len(boundaries) and cur_len < boundaries[bi]:
                bi += 1
    if cur:
        chunks.append("\n".join(cur))
    # 补齐到 n_chunks(边界恰好一致时可能少一块)
    while len(chunks) < n_chunks:
        chunks.append("")
    return chunks


def call_llm(text, cfg):
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": (
                "你是严谨的学术论文翻译校对员。用户会给出【原文】和对应的【译文】两段文本。\n"
                "请逐句对照原文检查译文,修正所有翻译错误:\n"
                "1. 单位误译: in.=英寸、ft.=英尺、mi.=英里、acre=英亩,in. 绝不能翻成\"在\"。\n"
                "2. 数字、数值、公式结果必须与原文完全一致(如 1,590.5/4,368 的逗号和数字不能丢)。\n"
                "3. 术语前后一致(如 Voronoi 图、雨量计、Pick's 公式、Heron's 公式)。\n"
                "4. 漏译、错译、语序不通顺处按上下文修正。\n"
                "5. 【公式N】占位符必须原样保留且数量、顺序不变;markdown 标记(# 标题、列表、表格、图片链接)不能改。\n"
                "输出: 修正后的完整译文(必须与输入译文块覆盖相同内容),只输出译文本身,不要输出解释或原文。"
            )},
            {"role": "user", "content": text},
        ],
        "temperature": cfg.get("temperature", 0.1),
    }
    req = urllib.request.Request(
        cfg["base_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=cfg.get("request_timeout", 240)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def call_llm_retry(text, cfg):
    last_err = None
    for attempt in range(1, cfg["max_retries"] + 1):
        try:
            return call_llm(text, cfg)
        except Exception as e:
            last_err = e
            print(f"  [重试 {attempt}/{cfg['max_retries']}] 校对请求失败: {e}", file=sys.stderr)
            time.sleep(3 * attempt)
    raise RuntimeError(f"校对失败(重试 {cfg['max_retries']} 次仍失败): {last_err}")


# 模型拒绝/胡话检测(出现即判失败,保留原译文)
_REFUSAL_PATTERNS = [
    "没有提供", "无法进行", "请提供", "请补充", "未提供", "请给出",
    "抱歉", "我不能", "无法校对", "not provided", "cannot",
]


def placeholders(text):
    return set(_PH_RE.findall(text))


def is_formula_only(text):
    """块内容是否只有公式占位符/空白/孤立$符(无需校对)"""
    t = re.sub(_PH_RE, "", text)
    t = re.sub(r"\s+", "", t)
    t = t.replace("$$", "").replace("$", "")
    return not t


def revise_markdown(src_path, trans_path, dst_path, cfg):
    with open(src_path, "r", encoding="utf-8") as f:
        orig_raw = f.read()
    with open(trans_path, "r", encoding="utf-8") as f:
        trans_raw = f.read()

    print("[1/5] 保护公式(原文与译文)")
    orig_prot, _orig_fm = protect_formulas(orig_raw)
    trans_prot, trans_fm = protect_formulas(trans_raw)

    n_chunks = max(1, math.ceil(len(orig_prot) / cfg["chunk_size"]))
    o_chunks = line_chunks_by_frac(orig_prot.split("\n"), n_chunks)
    t_chunks = line_chunks_by_frac(trans_prot.split("\n"), n_chunks)
    print(f"      切块: {len(o_chunks)} 块(按累计字符比例对齐)")

    def build_prompt(oc, tc):
        return f"【原文】\n{oc}\n\n【译文】\n{tc}"

    def run_chunk(oc, tc):
        if is_formula_only(tc):
            return tc  # 纯公式块无需校对
        resp = call_llm_retry(build_prompt(oc, tc), cfg).strip()
        if not resp:
            return None
        # 校验1: 拒绝/胡话检测
        for kw in _REFUSAL_PATTERNS:
            if kw in resp:
                return None
        # 校验2: 公式占位符完整性(输出必须包含输入译文块的所有占位符)
        if not placeholders(tc) <= placeholders(resp):
            return None
        return resp

    print(f"[2/5] 校对 {len(o_chunks)} 块(并发 {cfg.get('max_workers', 5)} 路)")
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=cfg.get("max_workers", 5)) as pool:
        futures = {pool.submit(run_chunk, oc, tc): i for i, (oc, tc) in enumerate(zip(o_chunks, t_chunks))}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                r = fut.result()
                if r is not None:
                    results[i] = r
                else:
                    print(f"      块 {i + 1}: 校验未通过,保留原译文", file=sys.stderr)
            except Exception as e:
                print(f"      块 {i + 1} 失败: {e},保留原译文", file=sys.stderr)
            done += 1
            print(f"      进度: {done}/{len(futures)} 块完成")

    print("[3/5] 组装")
    out_chunks = []
    for i, tc in enumerate(t_chunks):
        out_chunks.append(results.get(i, tc))

    result = "".join(out_chunks)  # 块内含整行,块与块之间用 \n 连接还原
    print("[4/5] 还原公式")
    result = restore_formulas(result, trans_fm)

    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"[5/5] 完成!输出: {dst_path}")


def main():
    parser = argparse.ArgumentParser(description="MinerU Markdown 翻译校对(对照原文+上下文)")
    parser.add_argument("src", help="原文 markdown 文件")
    parser.add_argument("trans", help="译文 markdown 文件")
    parser.add_argument("dst", help="校对输出 markdown 文件")
    parser.add_argument("--config", help="JSON 配置文件(可选)")
    args = parser.parse_args()

    cfg = dict(DEFAULT_CONFIG)
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))

    if not cfg["api_key"]:
        print("错误: 未设置 api_key。", file=sys.stderr)
        sys.exit(1)

    revise_markdown(args.src, args.trans, args.dst, cfg)


if __name__ == "__main__":
    main()
